"""Read path: understand a query, evaluate stances with default logic, pack a memory block.

Default logic over the hierarchy ("specific beats general")
----------------------------------------------------------
To decide the user's stance on concept ``c`` in context ``x``:

1. *Applicable* preferences: active nodes in the frame (who, aspect, intent) whose
   key covers (c, x), i.e. ``c ⊑ node.what`` and ``x ⊑ node.context``.
2. Preferences flagged by R3 (``needs_confirmation``) are *suspended*: a later, broader
   statement contradicted them, so until the user confirms, the broader one applies.
3. The *minimal* applicable nodes (no other applicable node is strictly narrower)
   decide. Everything else applicable is *defeated* and reported in the proof.
4. If several incomparable minimal nodes disagree (Sushi is both Seafood and Japanese
   food; "avoid seafood" + "love Japanese food"), the conservative tie-break applies:
   ``avoids`` wins (safety first), then the most recently confirmed statement.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from . import belief
from .extract.rules import CONTEXT_RE, ROLE_RE, normalise
from .mapping import ConceptMapper
from .model import SELF, Context, Intent, Polarity, PreferenceNode
from .ontology import Ontology, is_unmapped
from .reasoner import Reasoner, UserMemory, last_time

DOMAIN_WORDS = {
    "Food": r"\b(?:eat|eating|dinner|lunch|breakfast|brunch|snack|snacks|food|meal|meals|cook|cooking|recipe|restaurant|hungry|dish|dishes|order in|takeaway|cuisine|dessert|drink|drinks)\b",
    "Travel": r"\b(?:trip|trips|travel|travelling|traveling|holiday|holidays|vacation|getaway|destination|visit|weekend away|honeymoon|tour|go next|where should i go)\b",
    "Screen": r"\b(?:watch|movie|movies|film|films|show|shows|series|netflix|tv|binge|cinema)\b",
    "Music": r"\b(?:listen|music|song|songs|playlist|album|concert|gig|band)\b",
    "Books": r"\b(?:read|reading|book|books|novel|novels|author)\b",
    "Shopping": r"\b(?:buy|buying|shop|shopping|gift|purchase|gadget|wear|outfit|phone|laptop)\b",
}
DOMAIN_RE = {d: re.compile(p) for d, p in DOMAIN_WORDS.items()}


@dataclass
class Query:
    text: str
    domains: list[str] = field(default_factory=list)
    concepts: list[str] = field(default_factory=list)
    context: str = Context.ANY.value
    who: str = SELF
    intent: str = Intent.STANDING.value

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass
class Stance:
    concept: str
    polarity: Polarity | None
    strength: float = 0.0
    confidence: float = 0.0
    decided_by: list[str] = field(default_factory=list)
    defeated: list[str] = field(default_factory=list)
    suspended: list[str] = field(default_factory=list)
    tie_break: str | None = None

    @property
    def score(self) -> float:
        if self.polarity is None:
            return 0.0
        return self.polarity.sign * self.confidence

    @property
    def vetoed(self) -> bool:
        return self.polarity is Polarity.AVOIDS

    def to_dict(self) -> dict[str, Any]:
        return {
            "concept": self.concept,
            "polarity": self.polarity.value if self.polarity else None,
            "strength": self.strength,
            "confidence": self.confidence,
            "decided_by": self.decided_by,
            "defeated": self.defeated,
            "suspended": self.suspended,
            "tie_break": self.tie_break,
        }


class Retriever:
    def __init__(self, ontology: Ontology, reasoner: Reasoner, mapper: ConceptMapper | None = None) -> None:
        self.ontology = ontology
        self.reasoner = reasoner
        self.mapper = mapper or ConceptMapper(ontology)

    # ---------------------------------------------------------- query parsing
    def parse(self, text: str, context: str | None = None, who: str | None = None) -> Query:
        t = normalise(text)
        q = Query(text=text)
        q.domains = [d for d, rx in DOMAIN_RE.items() if rx.search(t)]
        # concept mentions: scan for lexicon n-grams (longest first)
        from .text import norm

        toks = norm(t.replace("-", " ")).split()
        lex = {k.replace("-", " "): v for k, v in self.ontology.lexicon.items()}
        found: list[str] = []
        i = 0
        while i < len(toks):
            hit = None
            for j in range(min(len(toks), i + 5), i, -1):
                ng = " ".join(toks[i:j])
                if ng in lex and ng not in {"food", "dish", "meal", "show", "trip", "drink", "book", "music", "movie"}:
                    hit = (j, lex[ng])
                    break
            if hit:
                found.append(hit[1])
                i = hit[0]
            else:
                i += 1
        q.concepts = list(dict.fromkeys(found))
        for c in q.concepts:
            d = self.ontology.domain(c)
            if d and d not in q.domains:
                q.domains.append(d)
        q.context = context or next((c for c, rx in CONTEXT_RE if rx.search(t)), Context.ANY.value)
        if who:
            q.who = who
        else:
            m = re.search(r"\bfor (?:my|our) ", t)
            if m:
                for role, rx in ROLE_RE:
                    if rx.search(t[m.start():]):
                        q.who = role
                        break
        if re.search(r"\b(?:next|upcoming|planning|plan)\b", t) and "Travel" in q.domains:
            q.intent = Intent.PLAN.value
        if not q.domains:
            q.domains = ["Food"] if re.search(r"\b(?:idea|ideas|suggest|recommend)\b", t) else []
        return q

    # --------------------------------------------------------------- stance
    def applicable(self, mem: UserMemory, concept: str, context: str, who: str = SELF,
                   aspect: str = "general", intent: str = "standing") -> list[PreferenceNode]:
        frame = mem.by_frame.get((who, aspect, intent))
        if not frame:
            return []
        if self.reasoner.config.use_hierarchy:
            cands = {concept} | set(self.ontology.ancestors(concept))
        else:
            cands = {concept}
        try:
            qctx = Context(context)
        except ValueError:
            qctx = Context.ANY
        out = []
        for c in cands:
            for nid in frame.get(c, ()):
                n = mem.nodes[nid]
                try:
                    ok = qctx.subsumed_by(Context(n.key.context))
                except ValueError:
                    ok = False
                if ok:
                    out.append(n)
        return out

    def stance(self, mem: UserMemory, concept: str, context: str = "any", who: str = SELF,
               aspect: str = "general", intent: str = "standing", now: datetime | None = None,
               exclude: set[str] | None = None) -> Stance:
        nodes = [n for n in self.applicable(mem, concept, context, who, aspect, intent) if not exclude or n.id not in exclude]
        suspended = [n for n in nodes if n.id in mem.needs_confirmation]
        live = [n for n in nodes if n.id not in mem.needs_confirmation] or []
        if not live:
            return Stance(concept, None, suspended=[n.id for n in suspended])
        minimal = [n for n in live if not any(self.reasoner.key_lt(m.key, n.key) for m in live if m is not n)]
        tie = None
        if len({n.polarity for n in minimal}) > 1:
            avoid = [n for n in minimal if n.polarity is Polarity.AVOIDS]
            if avoid:
                minimal, tie = avoid, "avoids wins (safety first)"
            else:
                latest = max(minimal, key=last_time)
                minimal, tie = [latest], "most recent wins"
        decider = max(minimal, key=last_time)
        now = now or mem.last_event_time or datetime.now()
        return Stance(
            concept,
            decider.polarity,
            strength=decider.strength,
            confidence=belief.confidence(decider, now),
            decided_by=[n.id for n in minimal],
            defeated=[n.id for n in live if n not in minimal],
            suspended=[n.id for n in suspended],
            tie_break=tie,
        )

    # --------------------------------------------------------------- recall
    def recall(self, mem: UserMemory, query: Query, budget_tokens: int = 160, now: datetime | None = None) -> dict[str, Any]:
        """The memory block an LLM would receive: relevant active preferences, packed under a token budget."""
        now = now or mem.last_event_time or datetime.now()
        targets: set[str] = set()
        for c in query.concepts:
            targets |= self.ontology.expand(c) if self.reasoner.config.use_hierarchy else {c}
        domains = set(query.domains)
        items = []
        for n in mem.active():
            if n.key.who != query.who:
                continue
            try:
                applies = Context(query.context).subsumed_by(Context(n.key.context))
            except ValueError:
                applies = False
            if not applies:
                continue
            dom = self.ontology.domain(n.key.what) if not is_unmapped(n.key.what) else None
            if n.key.what in targets:
                rel = 1.0
            elif dom in domains:
                rel = 0.7
            elif is_unmapped(n.key.what) and any(w in n.surface for w in normalise(query.text).split() if len(w) > 3):
                rel = 0.6
            else:
                continue
            if n.key.intent == Intent.PAST.value:
                rel *= 0.5
            elif n.key.intent == Intent.PLAN.value and query.intent != Intent.PLAN.value:
                rel *= 0.6
            if n.polarity is Polarity.AVOIDS:
                rel = min(1.0, rel + 0.2)  # safety-critical constraints first
            conf = belief.confidence(n, now)
            line = self.line(n, mem)
            cost = max(1, math.ceil(len(line.split()) * 1.3))
            items.append({"node": n, "relevance": round(rel, 3), "confidence": conf, "value": rel * (0.5 + conf), "cost": cost, "line": line})
        chosen = pack(items, budget_tokens)
        chosen.sort(key=lambda it: -it["value"])
        return {
            "query": query.to_dict(),
            "budget_tokens": budget_tokens,
            "used_tokens": sum(it["cost"] for it in chosen),
            "candidates": len(items),
            "block": "\n".join(it["line"] for it in chosen),
            "items": [
                {"node_id": it["node"].id, "line": it["line"], "relevance": it["relevance"],
                 "confidence": it["confidence"], "cost": it["cost"]}
                for it in chosen
            ],
            "dropped": [it["node"].id for it in items if it not in chosen],
        }

    def line(self, n: PreferenceNode, mem: UserMemory) -> str:
        label = self.ontology.label(n.key.what)
        bits = [f"{n.polarity.value} {label}"]
        if n.key.who != SELF:
            bits[0] = f"{n.key.who} {bits[0]}"
        if n.key.context != "any":
            bits.append(f"({n.key.context})")
        if n.key.aspect != "general":
            bits.append(f"[{n.key.aspect}]")
        if n.key.intent != "standing":
            bits.append(f"<{n.key.intent}>")
        if n.reason.value not in ("unspecified", "taste"):
            bits.append(f"reason: {n.reason.value}")
        bits.append(f"since {n.stated_at.date().isoformat()}")
        if n.id in mem.needs_confirmation:
            bits.append("UNCONFIRMED - ask before relying on it")
        return "- " + " ".join(bits)


def pack(items: list[dict[str, Any]], budget: int) -> list[dict[str, Any]]:
    """0/1 knapsack (exact DP) maximising total value under the token budget.
    Falls back to greedy value-per-token for very large inputs."""
    if not items:
        return []
    if sum(it["cost"] for it in items) <= budget:
        return list(items)
    if len(items) * budget > 2_000_000:
        out, used = [], 0
        for it in sorted(items, key=lambda x: -x["value"] / x["cost"]):
            if used + it["cost"] <= budget:
                out.append(it)
                used += it["cost"]
        return out
    n = len(items)
    best = [[0.0] * (budget + 1) for _ in range(n + 1)]
    for i, it in enumerate(items, 1):
        c, v = it["cost"], it["value"]
        prev, row = best[i - 1], best[i]
        for b in range(budget + 1):
            row[b] = prev[b]
            if c <= b and prev[b - c] + v > row[b]:
                row[b] = prev[b - c] + v
    out, b = [], budget
    for i in range(n, 0, -1):
        if best[i][b] != best[i - 1][b]:
            out.append(items[i - 1])
            b -= items[i - 1]["cost"]
    return out
