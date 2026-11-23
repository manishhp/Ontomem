"""Ontology evolution: grow the concept hierarchy safely from what users actually say.

Pipeline:  unmapped / low-confidence phrase -> ranked placement proposals
           -> automatic consistency checks -> human review -> versioned release
           -> re-keying of earlier statements (replayable, see engine.rekey)

Proposal sources (all optional, combined and de-duplicated):
  * ``lexical``   - head-noun and suffix rules: "cosy mysteries" -> narrower than
                    Mystery_Book; "vietnamese food" -> under Cuisine; "<x> music" -> Music
  * ``evidence``  - concepts the same users mention in the same domain (weak prior)
  * ``llm``       - a model proposes the parent from a short list of candidates
                    (needs ONTOMEM_LLM; world knowledge such as "kombucha is a drink")

Nothing is added without a reviewer's approval, and every check below must pass.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ..core.mapping import FILLER, GENERIC, ConceptMapper
from ..core.ontology import Ontology
from ..core.text import norm, strip_articles

SUFFIX_PARENTS = [
    (r"\b(?:food|cuisine|dishes|dish)$", "Cuisine"),
    (r"\b(?:music|songs|beats)$", "Music"),
    (r"\b(?:novels|books|fiction)$", "Books"),
    (r"\b(?:movies|films|cinema)$", "Movie"),
    (r"\b(?:shows|series|specials)$", "TVShow"),
    (r"\b(?:drinks?|tea|coffee|juice)$", "Beverage"),
    (r"\b(?:holidays?|trips?|tours?|safaris?|getaways?)$", "TravelStyle"),
    (r"\b(?:hotels?|stays|resorts?)$", "Accommodation"),
    (r"\b(?:phones?|laptops?|keyboards?|headphones|gadgets?|cameras?)$", "Electronics"),
    (r"\b(?:shoes|sneakers|boots)$", "Footwear"),
    (r"\b(?:clothes|wear|outfits|shirts|dresses)$", "Clothing"),
]


@dataclass
class Proposal:
    surface: str
    concept_id: str
    label: str
    parent: str
    score: float
    source: str
    reasons: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    status: str = "pending"  # pending | approved | rejected
    checks: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def concept_id_for(label: str) -> str:
    parts = re.findall(r"[a-z0-9]+", norm(label))
    return "".join(p.capitalize() for p in parts) or "Concept"


class Evolver:
    def __init__(self, ontology: Ontology, llm=None) -> None:
        self.ontology = ontology
        self.mapper = ConceptMapper(ontology)
        self.llm = llm
        self.changelog: list[dict[str, Any]] = []

    # ------------------------------------------------------------- triggers
    def needs_review(self, surface: str) -> tuple[bool, str]:
        """Unmapped, or mapped by a head word with meaningful leftover modifiers."""
        m = self.mapper.map(surface)
        if not m.mapped:
            return True, "unmapped"
        if m.method == "head":
            toks = strip_articles(norm(surface)).replace("-", " ").split()
            matched = set(m.matched.split())
            leftover = [t for t in toks if t not in matched and t not in GENERIC and t not in FILLER]
            if leftover:
                return True, f"partial match on '{m.matched}' (unexplained: {' '.join(leftover)})"
        return False, "mapped"

    # ------------------------------------------------------------ proposals
    def propose(self, surface: str, evidence_concepts: list[str] | None = None, k: int = 3) -> list[Proposal]:
        surface_n = strip_articles(norm(surface))
        label = surface_n
        cid = concept_id_for(label)
        props: dict[str, Proposal] = {}

        def add(parent: str, score: float, source: str, reason: str) -> None:
            if parent not in self.ontology:
                return
            p = props.get(parent)
            if p is None:
                props[parent] = Proposal(surface, cid, label, parent, score, source, [reason])
            else:
                p.score = 1 - (1 - p.score) * (1 - score)
                p.reasons.append(reason)
                if source not in p.source:
                    p.source += "+" + source

        m = self.mapper.map(surface)
        if m.mapped and m.method == "head":
            add(m.concept, 0.7, "lexical", f"head word '{m.matched}' is {self.ontology.label(m.concept)}: proposed as a narrower concept")
        for rx, parent in SUFFIX_PARENTS:
            if re.search(rx, surface_n):
                add(parent, 0.45, "lexical", f"phrase ends like a {self.ontology.label(parent)} term")
        for c in evidence_concepts or []:
            if c in self.ontology:
                for anc in [c, *sorted(self.ontology.ancestors(c), key=lambda a: (-self.ontology.depth(a), a))[:1]]:
                    add(anc, 0.15, "evidence", f"mentioned alongside {self.ontology.label(c)}")
        if self.llm is not None:
            for parent, conf in self._llm_parents(surface):
                add(parent, 0.6 * conf, "llm", "language model placement")
        out = sorted(props.values(), key=lambda p: -p.score)[:k]
        for p in out:
            p.checks = self.check(p)
        return out

    def _llm_parents(self, surface: str) -> list[tuple[str, float]]:  # pragma: no cover - needs a model
        cands = [c for c in self.ontology.concepts if self.ontology.depth(c) <= 2]
        schema = {"type": "object", "properties": {"parent": {"type": "string", "enum": cands}}, "required": ["parent"]}
        try:
            ans = self.llm.chat_json([
                {"role": "system", "content": "Place a new concept under the most specific fitting parent. Answer JSON."},
                {"role": "user", "content": f'New concept: "{surface}". Options: '
                 + ", ".join(f"{c} ({self.ontology.label(c)})" for c in cands)}], schema)
            return [(ans["parent"], 1.0)] if ans.get("parent") in self.ontology else []
        except Exception:
            return []

    # --------------------------------------------------------------- checks
    def check(self, p: Proposal) -> dict[str, Any]:
        """Consistency checks that must pass before a reviewer may approve."""
        lex = self.ontology.lexicon
        res = {
            "parent_exists": p.parent in self.ontology,
            "id_free": p.concept_id not in self.ontology,
            "label_free": norm(p.label) not in lex or lex[norm(p.label)] == p.concept_id,
            "not_generic": norm(p.label) not in GENERIC and len(p.label) >= 3,
            "acyclic": True,  # a new leaf cannot create a cycle; kept explicit for the report
        }
        res["ok"] = all(res.values())
        return res

    # ---------------------------------------------------------------- apply
    def alias_candidates(self, surface: str, k: int = 3) -> list[Proposal]:
        """Treat the phrase as a synonym of an existing concept (e.g. "funny films" = Comedy)."""
        from rapidfuzz import fuzz

        s = strip_articles(norm(surface))
        scored = []
        for cid, c in self.ontology.concepts.items():
            best = max(fuzz.token_set_ratio(s, norm(lbl)) for lbl in (c.label, *c.alt_labels))
            scored.append((best / 100, cid))
        scored.sort(reverse=True)
        out = []
        for sc, cid in scored[:k]:
            if sc < 0.75:  # synonyms must look alike; weaker matches are noise
                continue
            p = Proposal(surface, cid, s, cid, round(0.5 * sc, 3), "alias", [f"similar to '{self.ontology.label(cid)}'"])
            p.checks = {"target_exists": True, "label_free": norm(s) not in self.ontology.lexicon}
            p.checks["ok"] = all(p.checks.values())
            out.append(p)
        return out

    def apply_alias(self, p: Proposal, reviewer: str = "reviewer") -> str:
        self.ontology.add_alt_label(p.parent, p.label)
        major, minor, patch = (self.ontology.version.split("+")[0].split(".") + ["0", "0"])[:3]
        self.ontology.version = f"{major}.{minor}.{int(patch) + 1}"
        p.status = "approved"
        self.mapper = ConceptMapper(self.ontology)
        self.changelog.append({"version": self.ontology.version, "alias": p.label, "of": p.parent, "reviewer": reviewer,
                               "at": datetime.now().isoformat(timespec="seconds")})
        return self.ontology.version

    def apply(self, p: Proposal, reviewer: str = "reviewer", alt_labels: list[str] | None = None) -> str:
        if not p.checks:
            p.checks = self.check(p)
        if not p.checks.get("ok"):
            raise ValueError(f"proposal fails consistency checks: {p.checks}")
        self.ontology.add_concept(p.concept_id, p.label, [p.parent], alt_labels or [])
        major, minor, patch = (self.ontology.version.split("+")[0].split(".") + ["0", "0"])[:3]
        self.ontology.version = f"{major}.{int(minor) + 1}.0"
        p.status = "approved"
        self.mapper = ConceptMapper(self.ontology)
        self.changelog.append({"version": self.ontology.version, "added": p.concept_id, "label": p.label,
                               "parent": p.parent, "reviewer": reviewer, "at": datetime.now().isoformat(timespec="seconds"),
                               "source": p.source})
        return self.ontology.version
