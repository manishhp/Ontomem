"""LLM-based key assigners (need a model: set ONTOMEM_LLM, e.g. ``ollama:qwen2.5:7b-instruct``).

S2  Mem0-style. The LLM sees the new fact and the k most similar stored memories and
    answers ADD / UPDATE <id> / NOOP <id> - exactly the decision Mem0's update phase
    delegates to an LLM. Our fixed retirement rule then applies to its choice.

S2b Ontology-constrained. The LLM must choose the concept from a short list of
    ontology candidates (JSON-schema enum); everything after that is OntoMem's
    deterministic pipeline. Comparing S2b with S3 isolates *who* picks the concept
    (LLM vs deterministic mapper) while keeping the ontology structure.

Both record every call (tokens, latency) for the cost comparison. Recorded cassettes
make LLM runs replayable without the model.
"""

from __future__ import annotations

from typing import Any

from rapidfuzz import fuzz

from ..core.llm import LLMClient
from ..core.mapping import ConceptMapper
from ..core.ontology import UNMAPPED, Ontology
from ..core.reasoner import ReasonerConfig
from ..core.text import norm
from .systems import Item, KeyedSystem, OntoMemSystem

WHO_TEXT = {"self": "User", "partner": "User's partner", "child": "User's child", "parent": "User's parent",
            "friend": "User's friend", "colleague": "User's colleague", "sibling": "User's sibling"}


def fact_text(it: Item) -> str:
    verb = {"likes": "likes", "dislikes": "dislikes", "avoids": "avoids", "neutral": "is neutral about"}[it.polarity]
    s = f"{WHO_TEXT.get(it.who, 'User')} {verb} {it.surface}"
    if it.intent == "past":
        s += " (past experience)"
    elif it.intent == "plan":
        s += " (future plan)"
    if it.context != "any":
        s += f" (when: {it.context})"
    if it.aspect != "general":
        s += f" (aspect: {it.aspect})"
    return s


MEM0_SYSTEM = (
    "You manage a user's long-term preference memory. Given NEW FACT and EXISTING MEMORIES, decide one action:\n"
    "ADD - the new fact is about something not in memory;\n"
    "UPDATE - the new fact changes or replaces an existing memory (give its id);\n"
    "NOOP - the new fact repeats an existing memory (give its id).\n"
    'Answer JSON: {"action": "ADD|UPDATE|NOOP", "id": <memory id or null>}'
)
MEM0_SCHEMA = {
    "type": "object",
    "properties": {"action": {"type": "string", "enum": ["ADD", "UPDATE", "NOOP"]}, "id": {"type": ["integer", "null"]}},
    "required": ["action", "id"],
}


class Mem0Keyer:
    def __init__(self, llm: LLMClient, k: int = 5) -> None:
        self.llm, self.k = llm, k

    def __call__(self, it: Item, sys_: KeyedSystem) -> Any:
        if not sys_.active_by_key:
            return ("new", it.item_id)
        mems = []
        for key, nid in sys_.active_by_key.items():
            last = sys_.item_meta[sys_.items[nid][-1]]
            mems.append((fuzz.token_set_ratio(norm(it.surface), norm(last.surface)), key, fact_text(last)))
        mems.sort(key=lambda m: -m[0])
        top = mems[: self.k]
        listing = "\n".join(f"[{i}] {t}" for i, (_, _, t) in enumerate(top))
        try:
            ans = self.llm.chat_json(
                [{"role": "system", "content": MEM0_SYSTEM},
                 {"role": "user", "content": f"EXISTING MEMORIES:\n{listing}\n\nNEW FACT: {fact_text(it)}\n(user said: \"{it.text}\")"}],
                MEM0_SCHEMA,
            )
            action, idx = str(ans.get("action", "ADD")).upper(), ans.get("id")
        except Exception:
            action, idx = "ADD", None
        if action in ("UPDATE", "NOOP") and isinstance(idx, int) and 0 <= idx < len(top):
            return top[idx][1]
        return ("new", it.item_id)


def mem0_system(llm: LLMClient) -> KeyedSystem:
    s = KeyedSystem(f"S2-LLM({llm.name})", Mem0Keyer(llm))
    s.needs_llm = True
    s.usage = llm.usage  # type: ignore[method-assign]
    return s


class OntologyConstrainedSystem(OntoMemSystem):
    """S2b: the LLM picks the concept from ontology candidates; OntoMem does the rest."""

    needs_llm = True

    def __init__(self, ontology: Ontology, llm: LLMClient, n_candidates: int = 8) -> None:
        super().__init__(ontology, ReasonerConfig(), name=f"S2b-LLM+onto({llm.name})")
        self.llm = llm
        self.n = n_candidates
        self.det = ConceptMapper(ontology)

    def candidates(self, surface: str) -> list[str]:
        s = norm(surface)
        scored = []
        for cid, c in self.ontology.concepts.items():
            best = max(fuzz.token_set_ratio(s, norm(lbl)) for lbl in (c.label, *c.alt_labels))
            scored.append((best, cid))
        scored.sort(reverse=True)
        out = [cid for _, cid in scored[: self.n]]
        m = self.det.map(surface)
        if m.mapped and m.concept not in out:
            out[-1] = m.concept
        return out

    def write(self, it: Item):
        cands = self.candidates(it.surface)
        options = [f"{c}: {self.ontology.label(c)} ({' > '.join(self.ontology.path(c))})" for c in cands]
        schema = {"type": "object", "properties": {"concept": {"type": "string", "enum": [*cands, "NONE"]}},
                  "required": ["concept"]}
        try:
            ans = self.llm.chat_json(
                [{"role": "system", "content": "Pick the ontology concept the user's phrase refers to, or NONE. Answer JSON."},
                 {"role": "user", "content": f'Phrase: "{it.surface}" (sentence: "{it.text}")\nOptions:\n' + "\n".join(options)}],
                schema,
            )
            c = ans.get("concept")
        except Exception:
            c = None
        concept = c if c in cands else UNMAPPED + norm(it.surface)
        it2 = Item(**{**it.__dict__, "concept": concept})
        return super().write(it2)

    def usage(self):
        return {**super().usage(), **self.llm.usage()}
