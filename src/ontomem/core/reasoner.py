"""The write-time reasoner: decides what a new statement does to the memory graph.

This is the part of the pipeline that Mem0/Zep delegate to an LLM. Here it is a
small, deterministic defeasible theory (formal version: docs/formal_semantics.md,
independent ASP encoding: core/asp/write.lp):

R1  Supersede     same slot key, different value   -> retire old (kept as history)
R5  Reinforce     same slot key, same value        -> strengthen existing node
R2  Exception     new key strictly narrower than an active one, opposite polarity
                  -> keep both; the specific one wins at read time
R3  Broad override  an active key strictly broader than this one, with opposite
                  polarity, was stated more recently -> keep both, flag the narrower
                  one ``needs_confirmation`` (and ask the user)
R4  Orthogonal    same concept, different aspect / intent / incomparable context -> coexist
R6  Third party   statements about someone else (partner, child, ...) live under
                  their own ``who`` and never touch the holder's slots

"Strictly narrower" is the product order on (concept, context):
(c1, x1) ⊏ (c2, x2)  iff  c1 ⊑ c2 and x1 ⊑ x2 and (c1, x1) ≠ (c2, x2),
within the same frame (who, aspect, intent).

Relations R2 / R3 / refinement are *derived from the current state* rather than
stored as one-off decisions, which is what makes replay order-independent (I3).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

from . import belief
from .model import (
    SELF,
    Aspect,
    Context,
    Intent,
    PreferenceNode,
    RuleFiring,
    SlotKey,
    Statement,
    Status,
)
from .ontology import Ontology


@dataclass(frozen=True)
class ReasonerConfig:
    """Switches used by the ablation study. Defaults = the full method."""

    use_hierarchy: bool = True  # -SKOS ablation: no broader/narrower relations
    use_who: bool = True  # -who: everything is about the holder (misattribution)
    use_aspect: bool = True
    use_intent: bool = True  # -intent: past trips and plans collapse into one slot
    use_context: bool = True  # -context
    use_r3: bool = True  # -R3: later broad statements never flag exceptions

    def label(self) -> str:
        off = [k.removeprefix("use_") for k, v in self.__dict__.items() if not v]
        return "full" if not off else "-" + ",-".join(off)


Frame = tuple[str, str, str]


def make_key(stmt: Statement, concept: str, cfg: ReasonerConfig) -> SlotKey:
    return SlotKey(
        who=stmt.who if cfg.use_who else SELF,
        what=concept,
        aspect=stmt.aspect.value if cfg.use_aspect else Aspect.GENERAL.value,
        intent=stmt.intent.value if cfg.use_intent else Intent.STANDING.value,
        context=stmt.context.value if cfg.use_context else Context.ANY.value,
    )


@dataclass
class WriteOutcome:
    decision: str  # new | supersede | reinforce
    node_id: str
    key: SlotKey
    retired: str | None = None
    firings: list[RuleFiring] = field(default_factory=list)
    flagged: list[str] = field(default_factory=list)
    unflagged: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "decision": self.decision,
            "node_id": self.node_id,
            "key": str(self.key),
            "retired": self.retired,
            "firings": [f.to_dict() for f in self.firings],
            "flagged": self.flagged,
            "unflagged": self.unflagged,
        }


class UserMemory:
    """Projection of one user's event stream: every preference node, active and retired."""

    def __init__(self, user_id: str) -> None:
        self.user_id = user_id
        self.nodes: dict[str, PreferenceNode] = {}
        self.active_by_key: dict[SlotKey, str] = {}
        # frame -> concept -> active node ids (index for hierarchy relations)
        self.by_frame: dict[Frame, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
        self.by_who_what: dict[tuple[str, str], set[str]] = defaultdict(set)
        self.needs_confirmation: set[str] = set()
        self.last_event_time: datetime | None = None
        self.events_applied = 0

    # ------------------------------------------------------------- accessors
    def active(self) -> list[PreferenceNode]:
        return [self.nodes[i] for i in self.active_by_key.values()]

    def history(self) -> list[PreferenceNode]:
        return [n for n in self.nodes.values() if not n.active]

    def get(self, node_id: str) -> PreferenceNode:
        return self.nodes[node_id]

    def chain(self, node_id: str) -> list[PreferenceNode]:
        """All values a slot has had, oldest first, ending at ``node_id``'s current successor."""
        n = self.nodes[node_id]
        while n.supersedes:
            n = self.nodes[n.supersedes]
        out = [n]
        while n.superseded_by:
            n = self.nodes[n.superseded_by]
            out.append(n)
        return out

    def _index(self, n: PreferenceNode) -> None:
        self.active_by_key[n.key] = n.id
        self.by_frame[n.key.frame()][n.key.what].add(n.id)
        self.by_who_what[(n.key.who, n.key.what)].add(n.id)

    def _unindex(self, n: PreferenceNode) -> None:
        self.active_by_key.pop(n.key, None)
        self.by_frame[n.key.frame()][n.key.what].discard(n.id)
        self.by_who_what[(n.key.who, n.key.what)].discard(n.id)
        self.needs_confirmation.discard(n.id)


def last_time(n: PreferenceNode) -> tuple[datetime, int]:
    """When a slot was last confirmed, as (time, event order). Statements with equal
    timestamps (same second, or a date picker with minute precision) are ordered by the
    event stream, as the formal semantics requires - otherwise R3 could never fire for them."""
    return (n.evidence_times[-1] if n.evidence_times else n.stated_at, n.last_order)


class Reasoner:
    def __init__(self, ontology: Ontology, config: ReasonerConfig | None = None) -> None:
        self.ontology = ontology
        self.config = config or ReasonerConfig()

    # --------------------------------------------------------------- orders
    def concept_le(self, a: str, b: str) -> bool:
        """a ⊑ b on concepts (reflexive)."""
        if a == b:
            return True
        return self.config.use_hierarchy and self.ontology.narrower_than(a, b)

    def key_lt(self, a: SlotKey, b: SlotKey) -> bool:
        """a ⊏ b: strictly narrower in the product order, same frame."""
        if a.frame() != b.frame() or a == b:
            return False
        try:
            ctx_ok = Context(a.context).subsumed_by(Context(b.context))
        except ValueError:  # unvalidated context value (-SHACL ablation)
            ctx_ok = a.context == b.context or b.context == Context.ANY.value
        return self.concept_le(a.what, b.what) and ctx_ok

    def _related_ids(self, mem: UserMemory, key: SlotKey) -> list[str]:
        """Active nodes in the same frame whose concept is comparable with ``key.what``."""
        frame = mem.by_frame.get(key.frame())
        if not frame:
            return []
        concepts = {key.what}
        if self.config.use_hierarchy:
            concepts |= self.ontology.ancestors(key.what) | self.ontology.descendants(key.what)
        out: list[str] = []
        for c in concepts:
            out.extend(frame.get(c, ()))
        return out

    # ------------------------------------------------------------- derived
    def broader_active(self, mem: UserMemory, n: PreferenceNode) -> list[PreferenceNode]:
        return [mem.nodes[i] for i in self._related_ids(mem, n.key) if self.key_lt(n.key, mem.nodes[i].key)]

    def narrower_active(self, mem: UserMemory, n: PreferenceNode) -> list[PreferenceNode]:
        return [mem.nodes[i] for i in self._related_ids(mem, n.key) if self.key_lt(mem.nodes[i].key, n.key)]

    def should_flag(self, mem: UserMemory, n: PreferenceNode) -> bool:
        if not self.config.use_r3 or not n.active:
            return False
        t = last_time(n)
        return any(
            m.polarity.sign * n.polarity.sign < 0 and last_time(m) > t for m in self.broader_active(mem, n)
        )

    def relations(self, mem: UserMemory, n: PreferenceNode) -> dict[str, list[str]]:
        """Derived relations of an active node, for explanations and the RDF projection."""
        exc, ref = [], []
        for m in self.broader_active(mem, n):
            if m.polarity.sign * n.polarity.sign < 0:
                exc.append(m.id)
            elif m.polarity.sign == n.polarity.sign != 0:
                ref.append(m.id)
        return {"exception_to": sorted(exc), "refines": sorted(ref)}

    # ---------------------------------------------------------------- write
    def write(
        self,
        mem: UserMemory,
        stmt: Statement,
        concept: str,
        node_id: str,
        ts: datetime,
        ontology_version: str = "",
    ) -> WriteOutcome:
        key = make_key(stmt, concept, self.config)
        firings: list[RuleFiring] = []
        existing_id = mem.active_by_key.get(key)
        turn = stmt.turn_id or node_id

        if existing_id is not None and mem.nodes[existing_id].polarity == stmt.polarity:
            node = mem.nodes[existing_id]
            node.strength = belief.reinforce(node.strength, stmt.strength)
            node.evidence.append(turn)
            node.evidence_times.append(ts)
            node.last_confirmed = ts
            node.last_order = mem.events_applied + 1
            firings.append(
                RuleFiring("R5", f"Same slot and same value: reinforced (strength {node.strength:.2f}).", [node.id])
            )
            outcome = WriteOutcome("reinforce", node.id, key, firings=firings)
        else:
            node = PreferenceNode(
                id=node_id,
                key=key,
                polarity=stmt.polarity,
                strength=stmt.strength,
                reason=stmt.reason,
                surface=stmt.surface,
                source_text=stmt.source_text or stmt.surface,
                stated_at=ts,
                valid_from=ts,
                evidence=[turn],
                evidence_times=[ts],
                last_confirmed=ts,
                last_order=mem.events_applied + 1,
                extractor=stmt.extractor,
                ontology_version=ontology_version,
                mapped=not concept.startswith("unmapped:"),
            )
            retired = None
            if existing_id is not None:
                old = mem.nodes[existing_id]
                mem._unindex(old)
                old.status = Status.RETIRED
                old.valid_to = ts
                old.superseded_by = node.id
                node.supersedes = old.id
                node.created_by_rule = "R1"
                retired = old.id
                firings.append(
                    RuleFiring(
                        "R1",
                        f"Same slot {key}: '{old.polarity.value}' retired to history, now '{node.polarity.value}'.",
                        [old.id, node.id],
                    )
                )
                outcome = WriteOutcome("supersede", node.id, key, retired=retired, firings=firings)
            else:
                node.created_by_rule = "NEW"
                firings.append(RuleFiring("NEW", f"New slot {key}.", [node.id]))
                outcome = WriteOutcome("new", node.id, key, firings=firings)
            mem.nodes[node.id] = node
            mem._index(node)

        if key.who != SELF:
            firings.append(RuleFiring("R6", f"About '{key.who}', not the user: kept in a separate slot.", [node.id]))

        self._derive_after_write(mem, node, outcome)
        mem.last_event_time = ts
        mem.events_applied += 1
        return outcome

    def _derive_after_write(self, mem: UserMemory, node: PreferenceNode, out: WriteOutcome) -> None:
        # R2 / refinement relative to broader active slots
        for m in self.broader_active(mem, node):
            if m.polarity.sign * node.polarity.sign < 0:
                out.firings.append(
                    RuleFiring(
                        "R2",
                        f"'{self.ontology.label(node.key.what)}' is narrower than "
                        f"'{self.ontology.label(m.key.what)}': kept both, the specific one wins.",
                        [node.id, m.id],
                    )
                )
        # R3 relative to narrower active slots, then recompute their flags
        affected = [node, *self.narrower_active(mem, node)]
        for n in affected:
            flag = self.should_flag(mem, n)
            if flag and n.id not in mem.needs_confirmation:
                mem.needs_confirmation.add(n.id)
                out.flagged.append(n.id)
                if n.id != node.id:
                    out.firings.append(
                        RuleFiring(
                            "R3",
                            f"Later, broader statement about '{self.ontology.label(node.key.what)}' contradicts "
                            f"'{self.ontology.label(n.key.what)}': kept, but needs confirmation.",
                            [n.id, node.id],
                        )
                    )
            elif not flag and n.id in mem.needs_confirmation:
                mem.needs_confirmation.discard(n.id)
                out.unflagged.append(n.id)
        # R4: same concept active under a different frame/context -> coexist (informational)
        others = [
            mem.nodes[i]
            for i in mem.by_who_what.get((node.key.who, node.key.what), ())
            if i != node.id
            and not self.key_lt(mem.nodes[i].key, node.key)
            and not self.key_lt(node.key, mem.nodes[i].key)
        ]
        if others:
            out.firings.append(
                RuleFiring(
                    "R4",
                    "Same concept with a different aspect/intent/context: no conflict, both kept.",
                    [node.id, *[o.id for o in others]],
                )
            )
