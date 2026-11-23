"""OntoMem engine: the write path and read path behind one facade.

Write path (one user turn)::

    text -> extract -> map to SKOS concept -> SHACL validate (repair loop)
         -> 5-part slot key -> reasoner (R1-R6) -> event log -> memory projection

Every step is recorded in a :class:`TurnTrace`, which the web UI shows as the
"pipeline inspector" and the evaluation uses for error attribution.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .events import Event, EventStore
from .extract.base import Extractor
from .extract.rules import RuleExtractor
from .mapping import ConceptMapper
from .model import Polarity, Statement
from .ontology import Ontology, default_ontology, is_unmapped
from .reasoner import Reasoner, ReasonerConfig, UserMemory, WriteOutcome
from .recommend import Recommender
from .retrieve import Retriever
from .validation import NativeValidator, ShaclValidator


@dataclass
class CandidateTrace:
    raw: dict[str, Any]
    mapping: dict[str, Any] = field(default_factory=dict)
    validation: dict[str, Any] = field(default_factory=dict)
    repairs: list[dict[str, Any]] = field(default_factory=list)
    accepted: bool = False
    key: str | None = None
    outcome: dict[str, Any] | None = None
    node: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass
class TurnTrace:
    user_id: str
    text: str
    ts: str
    turn_id: str
    extractor: str
    candidates: list[CandidateTrace] = field(default_factory=list)
    clarifications: list[dict[str, Any]] = field(default_factory=list)
    timings_ms: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = {k: v for k, v in self.__dict__.items() if k != "candidates"}
        d["candidates"] = [c.to_dict() for c in self.candidates]
        return d


class OntoMemEngine:
    def __init__(
        self,
        ontology: Ontology | None = None,
        store: EventStore | None = None,
        config: ReasonerConfig | None = None,
        extractor: Extractor | None = None,
        validator: str = "native",
        max_repairs: int = 2,
        use_validation: bool = True,
        llm=None,
        catalog: list[dict[str, Any]] | None = None,
    ) -> None:
        self.ontology = ontology or default_ontology()
        self.store = store
        self.config = config or ReasonerConfig()
        self.reasoner = Reasoner(self.ontology, self.config)
        self.mapper = ConceptMapper(self.ontology)
        self.extractor = extractor or RuleExtractor()
        self.validator = ShaclValidator(self.ontology) if validator == "shacl" else NativeValidator(self.ontology)
        self.retriever = Retriever(self.ontology, self.reasoner, self.mapper)
        self.recommender = Recommender(self.ontology, self.retriever, catalog)
        self.llm = llm
        self.max_repairs = max_repairs
        self.use_validation = use_validation
        self._memories: dict[str, UserMemory] = {}
        self._counters: dict[str, int] = {}
        self.rejected: list[dict[str, Any]] = []
        self.unmapped_log: list[dict[str, Any]] = []

    # ------------------------------------------------------------ projection
    def memory(self, user_id: str, as_of: datetime | None = None) -> UserMemory:
        if as_of is not None:
            return self.replay(user_id, until=as_of)
        if user_id not in self._memories:
            self._memories[user_id] = self.replay(user_id) if self.store else UserMemory(user_id)
        return self._memories[user_id]

    def replay(self, user_id: str, until: datetime | None = None, reasoner: Reasoner | None = None) -> UserMemory:
        """Rebuild a projection from the event log (time travel, invariant I3)."""
        mem = UserMemory(user_id)
        if self.store is None:
            return mem
        r = reasoner or self.reasoner
        events = list(self.store.events(user_id, until))
        # re-keying (after ontology evolution) applies to the whole history, so collect it first
        remap: dict = {}
        for e in events:
            if e.type == "rekey":  # keyed by (phrase, old concept): only that phrase moves
                remap[(e.payload.get("surface"), e.payload["from"])] = e.payload["to"]
        for ev in events:
            self._apply(mem, ev, r, remap)
        return mem

    def _apply(self, mem: UserMemory, ev: Event, reasoner: Reasoner | None = None,
               remap: dict[str, str] | None = None) -> WriteOutcome | None:
        if ev.type != "statement":
            return None
        r = reasoner or self.reasoner
        stmt = Statement.from_dict(ev.payload["statement"])
        concept = ev.payload["concept"]
        if remap:
            from .text import norm

            concept = remap.get((norm(stmt.surface), concept), remap.get((None, concept), concept))
        return r.write(mem, stmt, concept, f"n{ev.seq}", ev.ts, ev.payload.get("ontology_version", ""))

    # ------------------------------------------------------ ontology change
    def refresh_ontology(self) -> None:
        """Call after the ontology changed (evolution): rebuild mapper/reader and re-key memories."""
        self.mapper = ConceptMapper(self.ontology)
        self.retriever.mapper = self.mapper
        for user in self.users():
            self.rekey(user)

    def rekey(self, user_id: str) -> list[dict[str, str]]:
        """Re-map earlier unmapped statements that the current ontology now understands."""
        if self.store is None:
            return []
        from .text import norm

        done = []
        seen = set()
        events = list(self.store.events(user_id))
        already = {(e.payload.get("surface"), e.payload["from"]) for e in events if e.type == "rekey"}
        for ev in events:
            if ev.type != "statement":
                continue
            c = ev.payload["concept"]
            surface = norm(ev.payload["statement"]["surface"])
            if (surface, c) in seen or (surface, c) in already:
                continue
            seen.add((surface, c))
            m = self.mapper.map(ev.payload["statement"]["surface"])
            # move a statement only when the ontology now understands it better:
            # unmapped -> mapped, or a coarse head match -> a new, narrower concept
            better = m.mapped and m.concept != c and (
                c.startswith("unmapped:") or self.ontology.narrower_than(m.concept, c))
            if better:
                self.store.append(user_id, datetime.now().replace(microsecond=0), "rekey",
                                  {"surface": surface, "from": c, "to": m.concept})
                done.append({"surface": surface, "from": c, "to": m.concept})
        if done:
            self._memories.pop(user_id, None)
        return done

    def users(self) -> list[str]:
        return self.store.users() if self.store else list(self._memories)

    # ---------------------------------------------------------- write path
    def write_statement(self, user_id: str, stmt: Statement, concept: str | None = None) -> WriteOutcome:
        """Write an already-extracted statement (used by the benchmark and by clarification answers)."""
        ts = stmt.stated_at or datetime.now().replace(microsecond=0)
        if concept is None:
            concept = stmt.concept or self.mapper.map(stmt.surface).concept
        mem = self.memory(user_id)
        if self.store is not None:
            ev = self.store.append(
                user_id, ts, "statement",
                {"statement": stmt.to_dict(), "concept": concept, "ontology_version": self.ontology.version},
            )
            return self._apply(mem, ev)  # type: ignore[return-value]
        n = self._counters.get(user_id, 0) + 1
        self._counters[user_id] = n
        return self.reasoner.write(mem, stmt, concept, f"n{n}", ts, self.ontology.version)

    def observe(self, user_id: str, text: str, ts: datetime | None = None, turn_id: str | None = None) -> TurnTrace:
        """Full write path for one user turn."""
        ts = ts or datetime.now().replace(microsecond=0)
        turn_id = turn_id or f"t{ts.strftime('%Y%m%d%H%M%S')}-{hashlib.sha1(text.encode()).hexdigest()[:6]}"
        trace = TurnTrace(user_id, text, ts.isoformat(), turn_id, self.extractor.name)

        t0 = time.perf_counter()
        raws = self.extractor.extract(text, ts, turn_id)
        trace.timings_ms["extract"] = round((time.perf_counter() - t0) * 1000, 2)
        t_map = t_val = t_reason = 0.0

        for raw in raws:
            ct = CandidateTrace(raw=dict(raw))
            trace.candidates.append(ct)
            cand = dict(raw)
            cand.setdefault("stated_at", ts.isoformat())
            cand.setdefault("source_text", text)

            t1 = time.perf_counter()
            m = self.mapper.map(str(cand.get("surface", "")))
            cand["concept"] = m.concept
            ct.mapping = {
                "surface": cand.get("surface"),
                "concept": m.concept,
                "label": self.ontology.label(m.concept),
                "path": self.ontology.path(m.concept) if m.mapped else [],
                "method": m.method,
                "score": round(m.score, 3),
                "matched": m.matched,
            }
            if not m.mapped or (m.method == "head" and _leftover(str(cand.get("surface", "")), m.matched)):
                # unmapped, or matched only by its head word ("bubble tea" -> Tea): queue for ontology review
                self.unmapped_log.append({"user_id": user_id, "surface": cand.get("surface"), "text": text,
                                          "ts": ts.isoformat(), "partial": m.mapped})
            t_map += time.perf_counter() - t1

            t2 = time.perf_counter()
            if self.use_validation:
                report = self.validator.validate(cand)
                ct.validation = report.to_dict()
                attempt = 0
                while not report.conforms and attempt < self.max_repairs:
                    attempt += 1
                    fixed = self.extractor.repair(cand, [v.to_dict() for v in report.violations], text)
                    if fixed is None:
                        break
                    if fixed.get("concept") is None:
                        fixed["concept"] = self.mapper.map(str(fixed.get("surface", ""))).concept
                    report = self.validator.validate(fixed)
                    ct.repairs.append({"attempt": attempt, "candidate": fixed, "validation": report.to_dict()})
                    cand = fixed
                t_val += time.perf_counter() - t2
                if not report.conforms:
                    self.rejected.append({"user_id": user_id, "raw": raw, "violations": report.to_dict()["violations"]})
                    continue
            ct.accepted = True

            t3 = time.perf_counter()
            stmt = Statement.from_dict({**cand, "turn_id": turn_id, "stated_at": ts}, strict=self.use_validation)
            stmt.concept = cand["concept"]
            stmt.mapping_score, stmt.mapping_method = m.score, m.method
            out = self.write_statement(user_id, stmt, cand["concept"])
            t_reason += time.perf_counter() - t3
            ct.key = str(out.key)
            ct.outcome = out.to_dict()
            ct.node = self.memory(user_id).nodes[out.node_id].to_dict()

        trace.timings_ms.update(map=round(t_map * 1000, 2), validate=round(t_val * 1000, 2), reason=round(t_reason * 1000, 2))
        trace.clarifications = self.clarifications(user_id)
        return trace

    # ------------------------------------------------------------ read path
    def ask(self, user_id: str, text: str, context: str | None = None, who: str | None = None,
            k: int = 5, budget_tokens: int = 160, now: datetime | None = None) -> dict[str, Any]:
        """Answer a request: parse it, build the memory block, rank the catalog, explain."""
        t0 = time.perf_counter()
        mem = self.memory(user_id)
        q = self.retriever.parse(text, context, who)
        recall = self.retriever.recall(mem, q, budget_tokens, now)
        recs = self.recommender.recommend(mem, q, k, now)
        reply = self._reply(q, recall, recs)
        return {
            "query": q.to_dict(),
            "memory_block": recall,
            "recommendations": recs["recommendations"],
            "excluded": recs["excluded"],
            "excluded_count": recs["excluded_count"],
            "clarifications": self.clarifications(user_id),
            "reply": reply,
            "latency_ms": round((time.perf_counter() - t0) * 1000, 2),
        }

    def _reply(self, q, recall: dict[str, Any], recs: dict[str, Any]) -> str:
        top = recs["recommendations"][:3]
        if self.llm is not None:  # optional: phrase the deterministic result naturally
            try:
                return self.llm.chat([
                    {"role": "system", "content": "You are a helpful assistant. Use ONLY the memory and suggestions given. "
                     "Mention why each suggestion fits, citing the user's own statements. Be brief."},
                    {"role": "user", "content": f"Request: {q.text}\nMemory:\n{recall['block']}\nSuggestions: "
                     + "; ".join(f"{r['item']['name']} ({', '.join(r['reasons']) or 'no strong signal'})" for r in top)},
                ])
            except Exception:  # fall back to the template
                pass
        if not top:
            return "I don't have anything in that area yet - tell me a bit about what you like?"
        parts = []
        for r in top:
            why = "; ".join(r["reasons"][:2])
            parts.append(f"**{r['item']['name']}** ({why})" if why else f"**{r['item']['name']}**")
        msg = "Here are some ideas: " + "; ".join(parts) + "."
        if recs["excluded_count"]:
            msg += f" I left out {recs['excluded_count']} option(s) because of things you avoid."
        return msg

    # -------------------------------------------------------- clarification
    def clarifications(self, user_id: str) -> list[dict[str, Any]]:
        """Open questions for preferences flagged by rule R3."""
        mem = self.memory(user_id)
        out = []
        for nid in sorted(mem.needs_confirmation):
            n = mem.nodes[nid]
            broader = [
                b for b in self.reasoner.broader_active(mem, n) if b.polarity.sign * n.polarity.sign < 0
            ]
            if not broader:
                continue
            b = max(broader, key=lambda x: x.last_confirmed or x.stated_at)
            out.append({
                "node_id": n.id,
                "broader_id": b.id,
                "question": (
                    f"You recently said you {b.polarity.value.rstrip('s')} {self.ontology.label(b.key.what).lower()}. "
                    f"Does that include {self.ontology.label(n.key.what).lower()}, which you said you "
                    f"{n.polarity.value.rstrip('s')}?"
                ),
                "keep_label": f"No, I still {n.polarity.value.rstrip('s')} {self.ontology.label(n.key.what).lower()}",
                "drop_label": f"Yes, {self.ontology.label(n.key.what).lower()} too",
            })
        return out

    def resolve_clarification(self, user_id: str, node_id: str, keep_exception: bool, ts: datetime | None = None) -> WriteOutcome:
        """Answering a clarification is just another statement (so it is replayable)."""
        mem = self.memory(user_id)
        n = mem.nodes[node_id]
        broader = [b for b in self.reasoner.broader_active(mem, n) if b.polarity.sign * n.polarity.sign < 0]
        polarity = n.polarity if keep_exception else (broader[0].polarity if broader else Polarity.NEUTRAL)
        from .model import Aspect, Context, Intent, Reason

        stmt = Statement(
            surface=n.surface,
            polarity=polarity,
            who=n.key.who,
            aspect=Aspect(n.key.aspect),
            intent=Intent(n.key.intent),
            context=Context(n.key.context),
            reason=n.reason if keep_exception else (broader[0].reason if broader else Reason.UNSPECIFIED),
            strength=0.95,
            source_text=f"(clarification) {'keep' if keep_exception else 'drop'} {self.ontology.label(n.key.what)}",
            stated_at=ts or datetime.now().replace(microsecond=0),
            turn_id=f"clarify-{node_id}",
            extractor="clarification",
        )
        return self.write_statement(user_id, stmt, n.key.what)

    # ------------------------------------------------------------- privacy
    def erase(self, user_id: str) -> int:
        n = self.store.erase_user(user_id) if self.store else 0
        self._memories.pop(user_id, None)
        self._counters.pop(user_id, None)
        return n

    # ---------------------------------------------------------------- misc
    def stats(self, user_id: str) -> dict[str, Any]:
        mem = self.memory(user_id)
        nodes = list(mem.nodes.values())
        return {
            "nodes": len(nodes),
            "active": len(mem.active_by_key),
            "retired": sum(1 for n in nodes if not n.active),
            "needs_confirmation": len(mem.needs_confirmation),
            "unmapped_active": sum(1 for n in mem.active() if is_unmapped(n.key.what)),
            "third_party_active": sum(1 for n in mem.active() if n.key.who != "self"),
            "events": self.store.count(user_id) if self.store else mem.events_applied,
        }


def _leftover(surface: str, matched: str) -> list[str]:
    from .mapping import FILLER, GENERIC
    from .text import norm, strip_articles

    toks = strip_articles(norm(surface)).replace("-", " ").split()
    got = set(matched.split())
    return [t for t in toks if t not in got and t not in GENERIC and t not in FILLER]
