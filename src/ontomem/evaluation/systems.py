"""The systems compared on PrefRev.

Everything except *how the slot key is chosen* is held fixed:
  * the same candidates (gold fields in Mode A, rule-extractor output in Mode B),
  * the same concept mapper,
  * the same retirement rule (same key + different value -> retire; same value -> reinforce),
  * the same reader for downstream recommendations.

    S0  append-only             no update resolution
    S1  rule surface key        key = normalised surface phrase (attribute-value store)
    S2  LLM key (Mem0-style)    LLM picks ADD / UPDATE / NOOP against similar memories   [needs an LLM]
    S2b LLM + ontology          LLM must pick concept & key fields from ontology candidates [needs an LLM]
    S2-sim                      gold keys with injected missed-merge rate p / false-merge rate q
    S3  OntoMem                 ontology keying + defeasible rules (this project)
    S4  oracle                  gold keys
    S5  learned keyer           gradient-boosted pairwise "same slot?" classifier, lexical features only
"""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ..core import belief
from ..core.mapping import ConceptMapper
from ..core.model import (
    SELF,
    Aspect,
    Context,
    Intent,
    Polarity,
    PreferenceNode,
    Reason,
    SlotKey,
    Statement,
    Status,
)
from ..core.ontology import Ontology
from ..core.reasoner import Reasoner, ReasonerConfig, UserMemory
from ..core.text import norm, strip_articles


@dataclass
class Item:
    """One candidate preference fed to every system (same input for all)."""

    item_id: str  # "<turn_id>#k"
    turn_id: str
    ts: datetime
    surface: str
    polarity: str
    who: str
    aspect: str
    intent: str
    context: str
    reason: str
    strength: float
    text: str
    concept: str  # shared concept mapping
    gold_key: str | None = None  # None = spurious (Mode B extraction false positive)
    gold_polarity: str | None = None
    gold_domain: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)  # possibly corrupted candidate (noise condition)


@dataclass
class WriteRecord:
    decision: str  # new | supersede | reinforce
    node_id: str
    target_id: str | None = None  # node merged into / retired


class MemoryView:
    """Duck-typed stand-in for UserMemory so the shared Retriever can read any system."""

    def __init__(self, nodes: list[PreferenceNode], needs_confirmation: set[str] | None = None,
                 last_event_time: datetime | None = None, reasoner: Reasoner | None = None) -> None:
        from collections import defaultdict

        self.nodes = {n.id: n for n in nodes}
        self.by_frame: dict = defaultdict(lambda: defaultdict(set))
        for n in nodes:
            self.by_frame[n.key.frame()][n.key.what].add(n.id)
        self.last_event_time = last_event_time
        self.needs_confirmation = needs_confirmation or set()
        if reasoner is not None and needs_confirmation is None:
            # R3 is a function of the memory *state*, so every system gets the same derived
            # flags at read time - the comparison stays about keying, not about reasoning.
            self.needs_confirmation = {n.id for n in nodes if reasoner.should_flag(self, n)}

    def active(self) -> list[PreferenceNode]:
        return list(self.nodes.values())


class System:
    name = "system"
    needs_llm = False

    def reset(self) -> None: ...

    def write(self, item: Item) -> WriteRecord:
        raise NotImplementedError

    def active_nodes(self) -> list[PreferenceNode]:
        raise NotImplementedError

    def item_ids(self, node_id: str) -> list[str]:
        raise NotImplementedError

    def view(self) -> Any:
        return MemoryView(self.active_nodes())

    def usage(self) -> dict[str, Any]:
        return {}


# --------------------------------------------------------------------------- keyed
class KeyedSystem(System):
    """Generic system: a *keyer* maps each item to a system key; the fixed retirement rule does the rest."""

    reader_reasoner: Reasoner | None = None  # set by the runner so views carry derived R3 flags

    def __init__(self, name: str, keyer: Callable[[Item, KeyedSystem], Any], fields: set[str] | None = None,
                 read_gold_concept: bool = False) -> None:
        self.name = name
        self.keyer = keyer
        self.read_gold_concept = read_gold_concept
        # which extracted fields this system represents at all (others collapse to defaults)
        self.fields = fields if fields is not None else {"who", "aspect", "intent", "context"}
        self.reset()

    def reset(self) -> None:
        self.nodes: dict[str, PreferenceNode] = {}
        self.syskey: dict[str, Any] = {}
        self.active_by_key: dict[Any, str] = {}
        self.items: dict[str, list[str]] = {}
        self.item_meta: dict[str, Item] = {}
        self.counter = 0
        self.order = 0
        self.last_time: datetime | None = None

    def reading_key(self, it: Item) -> SlotKey:
        what = it.concept
        if self.read_gold_concept and it.gold_key:
            what = it.gold_key.split("|")[1]
        return SlotKey(
            who=it.who if "who" in self.fields else SELF,
            what=what,
            aspect=it.aspect if "aspect" in self.fields else Aspect.GENERAL.value,
            intent=it.intent if "intent" in self.fields else Intent.STANDING.value,
            context=it.context if "context" in self.fields else Context.ANY.value,
        )

    def write(self, it: Item) -> WriteRecord:
        key = self.keyer(it, self)
        self.item_meta[it.item_id] = it
        self.last_time = it.ts
        self.order += 1
        existing = self.active_by_key.get(key)
        pol = Polarity(it.polarity)
        if existing is not None and self.nodes[existing].polarity is pol:
            n = self.nodes[existing]
            n.strength = belief.reinforce(n.strength, it.strength)
            n.evidence.append(it.item_id)
            n.evidence_times.append(it.ts)
            n.last_confirmed = it.ts
            n.last_order = self.order
            self.items[existing].append(it.item_id)
            return WriteRecord("reinforce", existing, existing)
        self.counter += 1
        nid = f"{self.name}-{self.counter}"
        node = PreferenceNode(
            id=nid, key=self.reading_key(it), polarity=pol, strength=it.strength, reason=Reason(it.reason),
            surface=it.surface, source_text=it.text, stated_at=it.ts, valid_from=it.ts, evidence=[it.item_id],
            evidence_times=[it.ts], last_confirmed=it.ts, last_order=self.order,
        )
        self.nodes[nid] = node
        self.items[nid] = [it.item_id]
        self.syskey[nid] = key
        target = None
        decision = "new"
        if existing is not None:
            old = self.nodes[existing]
            old.status, old.valid_to, old.superseded_by = Status.RETIRED, it.ts, nid
            node.supersedes = existing
            target, decision = existing, "supersede"
        self.active_by_key[key] = nid
        return WriteRecord(decision, nid, target)

    def active_nodes(self) -> list[PreferenceNode]:
        return [self.nodes[i] for i in self.active_by_key.values()]

    def item_ids(self, node_id: str) -> list[str]:
        return self.items[node_id]

    def view(self) -> MemoryView:
        return MemoryView(self.active_nodes(), last_event_time=self.last_time, reasoner=self.reader_reasoner)


def append_only() -> KeyedSystem:
    return KeyedSystem("S0-append-only", lambda it, s: ("item", it.item_id))


def surface_key() -> KeyedSystem:
    """The base paper's rule extractor style: an attribute-value store keyed on the
    normalised object phrase. No notion of who / intent / context / aspect."""
    return KeyedSystem("S1-rule-surface", lambda it, s: strip_articles(norm(it.surface)), fields=set())


def oracle() -> KeyedSystem:
    """Gold keys and gold concepts: the upper bound for any key assigner."""
    return KeyedSystem("S4-oracle", lambda it, s: it.gold_key or ("spurious", it.item_id), read_gold_concept=True)


class SimLLMKeyer:
    """Gold keys with injected errors at controlled rates (the base paper's p/q model).

    * missed merge (prob p): an update gets a fresh key; the previous value stays active.
    * false merge  (prob q): any write is filed under the key of another active memory,
      preferring one from the same domain (the confusions an LLM actually makes).
    """

    def __init__(self, p: float, q: float, seed: int = 0) -> None:
        self.p, self.q = p, q
        self.rng = random.Random(seed)
        self.bind: dict[str, Any] = {}

    def __call__(self, it: Item, sys_: KeyedSystem) -> Any:
        gk = it.gold_key or f"spurious:{it.item_id}"
        active = [k for k in sys_.active_by_key if k != self.bind.get(gk)]
        if active and self.rng.random() < self.q:
            same_dom = [k for k in active if sys_.item_meta[sys_.items[sys_.active_by_key[k]][0]].gold_domain == it.gold_domain]
            return self.rng.choice(same_dom or active)
        if gk in self.bind and self.bind[gk] in sys_.active_by_key and self.rng.random() < self.p:
            self.bind[gk] = (gk, it.item_id)  # missed merge: re-bind the gold slot to a fresh key
            return self.bind[gk]
        self.bind.setdefault(gk, gk)
        return self.bind[gk]


def sim_llm(p: float, q: float, seed: int = 0) -> KeyedSystem:
    k = SimLLMKeyer(p, q, seed)
    s = KeyedSystem(f"S2-sim(p={p:.2f},q={q:.2f})", k)
    orig_reset = s.reset

    def reset() -> None:
        orig_reset()
        k.bind = {}

    s.reset = reset  # type: ignore[method-assign]
    return s


# --------------------------------------------------------------------------- OntoMem
class OntoMemSystem(System):
    """S3: the real OntoMem reasoner (same code the API serves), plus its ablations."""

    def __init__(self, ontology: Ontology, config: ReasonerConfig | None = None, name: str | None = None,
                 mapper: ConceptMapper | None = None, validate: bool = True) -> None:
        self.ontology = ontology
        self.config = config or ReasonerConfig()
        self.reasoner = Reasoner(ontology, self.config)
        self.mapper = mapper or ConceptMapper(ontology)
        self.validate = validate
        lbl = self.config.label()
        self.name = name or ("S3-OntoMem" if lbl == "full" else f"S3{lbl}") + ("" if validate else "-SHACL")
        from ..core.extract.base import coerce
        from ..core.validation import NativeValidator

        self._validator = NativeValidator(ontology)
        self._coerce = coerce
        self.rejected = 0
        self.repaired = 0
        self.reset()

    def reset(self) -> None:
        self.mem = UserMemory("u")
        self.counter = 0

    def write(self, it: Item) -> WriteRecord:
        raw = dict(it.raw) if it.raw else None
        if raw is not None:  # noisy-extraction condition: candidate may be malformed
            raw.setdefault("source_text", it.text)
            raw.setdefault("stated_at", it.ts.isoformat())  # the pipeline knows the turn time
            raw["concept"] = it.concept
            if self.validate:
                rep = self._validator.validate(raw)
                tries = 0
                while not rep.conforms and tries < 2:
                    tries += 1
                    fixed = self._coerce(raw, [v.to_dict() for v in rep.violations])
                    if fixed is None:
                        break
                    if fixed.get("stated_at") is None:
                        fixed["stated_at"] = it.ts.isoformat()
                    if fixed.get("concept") is None:
                        fixed["concept"] = it.concept
                    raw, rep = fixed, self._validator.validate(fixed)
                if not rep.conforms:
                    self.rejected += 1
                    return WriteRecord("rejected", "")
                if tries:
                    self.repaired += 1
            stmt = Statement.from_dict({**raw, "stated_at": it.ts, "turn_id": it.item_id}, strict=self.validate)
            concept = raw.get("concept") or it.concept
        else:
            stmt = Statement(surface=it.surface, polarity=Polarity(it.polarity), who=it.who, aspect=Aspect(it.aspect),
                             intent=Intent(it.intent), context=Context(it.context), reason=Reason(it.reason),
                             strength=it.strength, stated_at=it.ts, turn_id=it.item_id, source_text=it.text)
            concept = it.concept
        self.counter += 1
        out = self.reasoner.write(self.mem, stmt, concept, f"S3-{self.counter}", it.ts)
        dec = out.decision
        target = out.retired if dec == "supersede" else (out.node_id if dec == "reinforce" else None)
        return WriteRecord(dec, out.node_id, target)

    def active_nodes(self) -> list[PreferenceNode]:
        return self.mem.active()

    def item_ids(self, node_id: str) -> list[str]:
        return self.mem.nodes[node_id].evidence

    def view(self) -> UserMemory:
        return self.mem

    def usage(self) -> dict[str, Any]:
        return {"rejected": self.rejected, "repaired": self.repaired}


def all_ablations(ontology: Ontology) -> list[OntoMemSystem]:
    return [
        OntoMemSystem(ontology, ReasonerConfig(use_hierarchy=False), name="S3 -SKOS"),
        OntoMemSystem(ontology, ReasonerConfig(use_intent=False), name="S3 -intent"),
        OntoMemSystem(ontology, ReasonerConfig(use_context=False), name="S3 -context"),
        OntoMemSystem(ontology, ReasonerConfig(use_who=False), name="S3 -who"),
        OntoMemSystem(ontology, ReasonerConfig(use_aspect=False), name="S3 -aspect"),
        OntoMemSystem(ontology, ReasonerConfig(use_r3=False), name="S3 -R3"),
    ]
