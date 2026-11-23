"""Load the SKOS concept hierarchy and answer hierarchy queries fast.

Concepts are identified internally by their local name (``"Sushi"``); the full IRI is
``https://w3id.org/ontomem/concept/Sushi``. Statements whose phrase is not in the
ontology get the id ``"unmapped:<normalised phrase>"`` - they still get a key, but no
hierarchy relations (that is the coverage gap measured in the evaluation).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from functools import cache, cached_property
from importlib import resources
from pathlib import Path

from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.namespace import OWL, RDF, SKOS

from .text import norm

CONCEPT_NS = Namespace("https://w3id.org/ontomem/concept/")
PREF_NS = Namespace("https://w3id.org/ontomem/pref#")
UNMAPPED = "unmapped:"


def data_path(name: str) -> Path:
    return Path(str(resources.files("ontomem") / "data" / "ontology" / name))


@dataclass
class Concept:
    id: str
    label: str
    alt_labels: list[str] = field(default_factory=list)
    broader: set[str] = field(default_factory=set)
    narrower: set[str] = field(default_factory=set)
    related: set[str] = field(default_factory=set)
    domain: str = ""

    @property
    def iri(self) -> str:
        return str(CONCEPT_NS[self.id])

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "iri": self.iri,
            "label": self.label,
            "alt_labels": self.alt_labels,
            "broader": sorted(self.broader),
            "narrower": sorted(self.narrower),
            "related": sorted(self.related),
            "domain": self.domain,
        }


def is_unmapped(concept_id: str) -> bool:
    return concept_id.startswith(UNMAPPED)


class Ontology:
    """In-memory view of the SKOS hierarchy with cached transitive closure."""

    def __init__(self, concepts: dict[str, Concept], version: str = "1.0.0") -> None:
        self.concepts = concepts
        self.version = version
        self._rebuild()

    # ------------------------------------------------------------------ loading
    @classmethod
    def load(cls, path: str | Path | None = None) -> Ontology:
        path = Path(path) if path else data_path("concepts.ttl")
        g = Graph()
        g.parse(path, format="turtle")
        return cls.from_graph(g)

    @classmethod
    def from_graph(cls, g: Graph) -> Ontology:
        def local(u: URIRef) -> str:
            return str(u).removeprefix(str(CONCEPT_NS))

        concepts: dict[str, Concept] = {}
        for s in g.subjects(RDF.type, SKOS.Concept):
            cid = local(s)
            pref = g.value(s, SKOS.prefLabel)
            concepts[cid] = Concept(
                id=cid,
                label=str(pref) if pref else cid,
                alt_labels=sorted(str(a) for a in g.objects(s, SKOS.altLabel)),
            )
        for s, o in g.subject_objects(SKOS.broader):
            a, b = local(s), local(o)
            if a in concepts and b in concepts:
                concepts[a].broader.add(b)
                concepts[b].narrower.add(a)
        for s, o in g.subject_objects(SKOS.related):
            a, b = local(s), local(o)
            if a in concepts and b in concepts:
                concepts[a].related.add(b)
        version = "unknown"
        for o in g.objects(None, OWL.versionInfo):
            version = str(o)
        return cls(concepts, version)

    # ------------------------------------------------------------- derived data
    def _rebuild(self) -> None:
        for name in ("roots", "lexicon"):
            self.__dict__.pop(name, None)
        self.ancestors.cache_clear()
        self.descendants.cache_clear()
        for c in self.concepts.values():
            c.domain = self._domain(c.id)

    def _domain(self, cid: str) -> str:
        c = self.concepts[cid]
        if not c.broader:
            return cid
        return self._domain(sorted(c.broader)[0])

    @cached_property
    def roots(self) -> list[str]:
        return sorted(c.id for c in self.concepts.values() if not c.broader)

    @cached_property
    def lexicon(self) -> dict[str, str]:
        """normalised label -> concept id (prefLabels and altLabels)."""
        lex: dict[str, str] = {}
        for c in self.concepts.values():
            for label in (c.label, *c.alt_labels):
                lex.setdefault(norm(label), c.id)
        return lex

    @cache  # noqa: B019 - ontology objects are long-lived
    def ancestors(self, cid: str) -> frozenset[str]:
        """All strictly broader concepts (transitive)."""
        c = self.concepts.get(cid)
        if c is None:
            return frozenset()
        out: set[str] = set()
        for b in c.broader:
            out.add(b)
            out |= self.ancestors(b)
        return frozenset(out)

    @cache  # noqa: B019
    def descendants(self, cid: str) -> frozenset[str]:
        c = self.concepts.get(cid)
        if c is None:
            return frozenset()
        out: set[str] = set()
        for n in c.narrower:
            out.add(n)
            out |= self.descendants(n)
        return frozenset(out)

    # ------------------------------------------------------------------ queries
    def __contains__(self, cid: str) -> bool:
        return cid in self.concepts

    def __len__(self) -> int:
        return len(self.concepts)

    def label(self, cid: str) -> str:
        if is_unmapped(cid):
            return cid.removeprefix(UNMAPPED)
        c = self.concepts.get(cid)
        return c.label if c else cid

    def domain(self, cid: str) -> str | None:
        c = self.concepts.get(cid)
        return c.domain if c else None

    def narrower_than(self, a: str, b: str) -> bool:
        """``a ⊏ b``: a is strictly more specific than b."""
        return b in self.ancestors(a)

    def subsumed_by(self, a: str, b: str) -> bool:
        """``a ⊑ b`` (reflexive)."""
        return a == b or self.narrower_than(a, b)

    def path(self, cid: str) -> list[str]:
        """One root-to-concept path, e.g. ``["Food", "Seafood", "Sushi"]``."""
        if cid not in self.concepts:
            return [cid]
        out = [cid]
        cur = cid
        while self.concepts[cur].broader:
            cur = sorted(self.concepts[cur].broader, key=lambda x: (len(self.ancestors(x)), x))[0]
            out.append(cur)
        return list(reversed(out))

    def depth(self, cid: str) -> int:
        return len(self.path(cid)) - 1

    def expand(self, cid: str, related: bool = True) -> set[str]:
        """Query expansion: the concept, its ancestors, descendants and related concepts."""
        out = {cid} | set(self.ancestors(cid)) | set(self.descendants(cid))
        if related and cid in self.concepts:
            for r in self.concepts[cid].related:
                out.add(r)
                out |= set(self.descendants(r))
        return out

    def in_domain(self, domain: str) -> set[str]:
        return {domain} | set(self.descendants(domain))

    def search(self, q: str, limit: int = 20) -> list[Concept]:
        nq = norm(q)
        hits = []
        for c in self.concepts.values():
            labels = [norm(x) for x in (c.label, *c.alt_labels)]
            if any(nq == x for x in labels):
                score = 0
            elif any(x.startswith(nq) for x in labels):
                score = 1
            elif any(nq in x for x in labels) or nq in c.id.lower():
                score = 2
            else:
                continue
            hits.append((score, self.depth(c.id), c.id))
        hits.sort()
        return [self.concepts[h[2]] for h in hits[:limit]]

    def tree(self, root: str | None = None) -> list[dict]:
        def node(cid: str, seen: frozenset[str]) -> dict:
            c = self.concepts[cid]
            kids = sorted(n for n in c.narrower if n not in seen)
            return {
                "id": cid,
                "label": c.label,
                "alt_labels": c.alt_labels,
                "children": [node(k, seen | {k}) for k in kids],
            }

        roots = [root] if root else self.roots
        return [node(r, frozenset({r})) for r in roots]

    # ---------------------------------------------------------------- mutation
    def add_concept(
        self, cid: str, label: str, broader: Iterable[str], alt_labels: Iterable[str] = ()
    ) -> Concept:
        """Used by ontology evolution after a proposal is approved."""
        if cid in self.concepts:
            raise ValueError(f"concept {cid} already exists")
        parents = list(broader)
        for p in parents:
            if p not in self.concepts:
                raise ValueError(f"unknown parent {p}")
        c = Concept(id=cid, label=label, alt_labels=list(alt_labels), broader=set(parents))
        self.concepts[cid] = c
        for p in parents:
            self.concepts[p].narrower.add(cid)
        self._rebuild()
        return c

    def add_alt_label(self, cid: str, label: str) -> None:
        """Register a synonym (ontology evolution: alias proposals)."""
        if cid not in self.concepts:
            raise ValueError(f"unknown concept {cid}")
        if label not in self.concepts[cid].alt_labels:
            self.concepts[cid].alt_labels.append(label)
        self._rebuild()

    def copy(self) -> Ontology:
        return Ontology({cid: Concept(id=c.id, label=c.label, alt_labels=list(c.alt_labels), broader=set(c.broader),
                                      narrower=set(c.narrower), related=set(c.related))
                         for cid, c in self.concepts.items()}, self.version)

    def without_hierarchy(self) -> Ontology:
        """A flat copy (same concepts and labels, no broader/narrower) for the -SKOS ablation."""
        flat = {
            cid: Concept(id=cid, label=c.label, alt_labels=list(c.alt_labels))
            for cid, c in self.concepts.items()
        }
        o = Ontology(flat, self.version + "+flat")
        for c in o.concepts.values():
            c.domain = self.concepts[c.id].domain  # keep domains for retrieval
        return o

    def to_graph(self) -> Graph:
        g = Graph()
        g.bind("skos", SKOS)
        g.bind("c", CONCEPT_NS)
        onto = URIRef("https://w3id.org/ontomem/concepts")
        g.add((onto, RDF.type, OWL.Ontology))
        g.add((onto, OWL.versionInfo, Literal(self.version)))
        for c in self.concepts.values():
            s = CONCEPT_NS[c.id]
            g.add((s, RDF.type, SKOS.Concept))
            g.add((s, SKOS.prefLabel, Literal(c.label, lang="en")))
            for a in c.alt_labels:
                g.add((s, SKOS.altLabel, Literal(a, lang="en")))
            for b in c.broader:
                g.add((s, SKOS.broader, CONCEPT_NS[b]))
            for r in c.related:
                g.add((s, SKOS.related, CONCEPT_NS[r]))
        return g


_DEFAULT: Ontology | None = None


def default_ontology() -> Ontology:
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = Ontology.load()
    return _DEFAULT
