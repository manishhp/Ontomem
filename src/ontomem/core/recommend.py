"""Explainable recommendations over a concept-tagged catalog.

An item is tagged with SKOS concepts (``Prawn curry`` -> Shrimp, Curry, IndianCuisine,
SpicyFood). Its score is the sum of the user's stances on those concepts, computed by
the default-logic read path; any ``avoids`` stance vetoes the item. Each suggestion
carries a **proof tree** (which statements decided, which were defeated and why) and
a **counterfactual** ("without your June statement, this would have been ...").
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from importlib import resources
from pathlib import Path
from typing import Any

from .model import Polarity
from .ontology import Ontology
from .reasoner import UserMemory
from .retrieve import Query, Retriever, Stance


def load_catalog(path: str | Path | None = None) -> list[dict[str, Any]]:
    p = Path(path) if path else Path(str(resources.files("ontomem") / "data" / "catalog.json"))
    return json.loads(p.read_text())["items"]


@dataclass
class Recommendation:
    item: dict[str, Any]
    score: float
    vetoed: bool
    stances: list[Stance] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    proof: dict[str, Any] = field(default_factory=dict)
    counterfactual: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "item": self.item,
            "score": round(self.score, 3),
            "vetoed": self.vetoed,
            "stances": [s.to_dict() for s in self.stances],
            "reasons": self.reasons,
            "proof": self.proof,
            "counterfactual": self.counterfactual,
        }


class Recommender:
    def __init__(self, ontology: Ontology, retriever: Retriever, catalog: list[dict[str, Any]] | None = None) -> None:
        self.ontology = ontology
        self.retriever = retriever
        self.catalog = catalog if catalog is not None else load_catalog()

    def score_item(self, mem: UserMemory, item: dict[str, Any], query: Query, now: datetime | None = None,
                   exclude: set[str] | None = None) -> Recommendation:
        stances = []
        vetoed, reasons = False, []
        contrib: dict[str, float] = {}  # one statement counts once per item, however many tags it decides
        for c in item["concepts"]:
            st = self.retriever.stance(mem, c, query.context, query.who, now=now, exclude=exclude)
            stances.append(st)
            if st.polarity is None:
                continue
            label = self.ontology.label(st.concept)
            if st.vetoed:
                vetoed = True
                reasons.append(f"you avoid {label}")
                continue
            key = ",".join(sorted(st.decided_by))
            if key not in contrib or abs(st.score) > abs(contrib[key]):
                contrib[key] = st.score
                if st.polarity is Polarity.LIKES:
                    reasons.append(f"you like {label}")
                elif st.polarity is Polarity.DISLIKES:
                    reasons.append(f"you dislike {label}")
            if item.get("price") == "high":
                pst = self.retriever.stance(mem, c, query.context, query.who, aspect="price", now=now, exclude=exclude)
                if pst.polarity is not None and pst.polarity.sign < 0:
                    contrib["price:" + ",".join(pst.decided_by)] = -0.5 * pst.confidence
                    reasons.append(f"you find {label} too expensive")
        score = sum(contrib.values())
        reasons = list(dict.fromkeys(reasons))
        if query.concepts:
            targets = set()
            for qc in query.concepts:
                targets |= self.ontology.expand(qc)
            if targets & set(item["concepts"]):
                score += 0.3
        if query.context != "any" and query.context in item.get("contexts", []):
            score += 0.2
        return Recommendation(item, score, vetoed, stances, reasons)

    def recommend(self, mem: UserMemory, query: Query, k: int = 5, now: datetime | None = None,
                  explain: bool = True) -> dict[str, Any]:
        pool = [it for it in self.catalog if not query.domains or it["domain"] in query.domains]
        scored = [self.score_item(mem, it, query, now) for it in pool]
        ok = sorted((r for r in scored if not r.vetoed), key=lambda r: (-r.score, r.item["id"]))
        top = ok[:k]
        if explain:
            for r in top:
                r.proof = self.proof(mem, r)
                r.counterfactual = self.counterfactual(mem, r, query, now)
        excluded = [r for r in scored if r.vetoed]
        return {
            "query": query.to_dict(),
            "recommendations": [r.to_dict() for r in top],
            "excluded": [{"item": r.item, "reasons": r.reasons} for r in excluded[:10]],
            "excluded_count": len(excluded),
        }

    def rank(self, mem: UserMemory, query: Query, now: datetime | None = None) -> list[tuple[str, float, bool]]:
        """Compact ranking for the benchmark: (item id, score, vetoed)."""
        pool = [it for it in self.catalog if not query.domains or it["domain"] in query.domains]
        out = []
        for it in pool:
            r = self.score_item(mem, it, query, now)
            out.append((it["id"], r.score, r.vetoed))
        out.sort(key=lambda x: (x[2], -x[1], x[0]))
        return out

    # ------------------------------------------------------------ explanation
    def _cite(self, mem: UserMemory, nid: str) -> dict[str, Any]:
        n = mem.nodes[nid]
        return {
            "node_id": nid,
            "statement": f"{n.polarity.value} {self.ontology.label(n.key.what)}",
            "key": str(n.key),
            "source_text": n.source_text,
            "stated_at": n.stated_at.date().isoformat(),
            "strength": round(n.strength, 2),
            "path": " ⊃ ".join(self.ontology.path(n.key.what)),
        }

    def proof(self, mem: UserMemory, rec: Recommendation) -> dict[str, Any]:
        children = []
        for st in rec.stances:
            label = self.ontology.label(st.concept)
            if st.polarity is None and not st.suspended:
                continue
            node: dict[str, Any] = {
                "claim": f"stance on {label} = {st.polarity.value if st.polarity else 'unknown'}",
                "rule": "specificity default (most specific applicable preference wins)",
                "premises": [self._cite(mem, i) for i in st.decided_by],
                "defeated": [
                    {**self._cite(mem, i), "why": "less specific than the deciding preference"} for i in st.defeated
                ],
                "suspended": [
                    {**self._cite(mem, i), "why": "contradicted by a later, broader statement (R3) - awaiting confirmation"}
                    for i in st.suspended
                ],
            }
            if st.tie_break:
                node["rule"] += f"; tie-break: {st.tie_break}"
            children.append(node)
        verdict = "excluded (hard constraint)" if rec.vetoed else f"score {rec.score:+.2f}"
        return {"claim": f"{rec.item['name']}: {verdict}", "rule": "sum of concept stances; any 'avoids' vetoes", "children": children}

    def counterfactual(self, mem: UserMemory, rec: Recommendation, query: Query, now: datetime | None) -> dict[str, Any] | None:
        """Remove the single most influential statement and re-score."""
        best = None
        for st in rec.stances:
            for nid in st.decided_by:
                if best is None or abs(st.score) > abs(best[1]):
                    best = (nid, st.score)
        if best is None:
            return None
        nid = best[0]
        alt = self.score_item(mem, rec.item, query, now, exclude={nid})
        n = mem.nodes[nid]
        return {
            "without": self._cite(mem, nid),
            "score_before": round(rec.score, 3),
            "score_after": round(alt.score, 3),
            "vetoed_after": alt.vetoed,
            "summary": (
                f"Without your statement '{n.source_text}' ({n.stated_at.date().isoformat()}), "
                f"{rec.item['name']} would score {alt.score:+.2f} instead of {rec.score:+.2f}"
                + (" and would be excluded." if alt.vetoed else ".")
            ),
        }
