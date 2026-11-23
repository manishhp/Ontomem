"""SHACL validation of candidate preferences before they enter the graph.

Two validators implement the *same* node-level shape (data/ontology/shapes.ttl):

* ``ShaclValidator`` - real W3C SHACL via pyshacl on the candidate's focus subgraph
  (incremental validation: only the new node + its concept, not the whole memory).
* ``NativeValidator`` - the shape compiled to plain Python; ~1000x faster, used in the
  benchmark loops. ``tests/test_validation.py`` checks both agree on thousands of
  random candidates, so the speed-up costs no correctness.

Whole-graph constraints (I1 key uniqueness, I2 retired-has-successor, no cycles) are
checked by ``validate_graph`` with pyshacl on the full RDF projection.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from rdflib import Graph, Literal, URIRef
from rdflib.namespace import RDF, SKOS, XSD

from .model import ROLES, Aspect, Context, Intent, Polarity, Reason
from .ontology import CONCEPT_NS, PREF_NS, Ontology, data_path

PREF = PREF_NS
CANDIDATE = URIRef("https://w3id.org/ontomem/candidate")
HOLDER = URIRef("https://w3id.org/ontomem/user/_candidate_holder")

ENUMS = {
    "who": set(ROLES),
    "aspect": {a.value for a in Aspect},
    "intent": {i.value for i in Intent},
    "context": {c.value for c in Context},
    "polarity": {p.value for p in Polarity},
    "reason": {r.value for r in Reason},
}
PATH = {
    "who": "subjectRole",
    "aspect": "aspect",
    "intent": "intent",
    "context": "context",
    "polarity": "polarity",
    "reason": "reason",
    "strength": "strength",
    "stated_at": "statedAt",
    "source_text": "sourceText",
    "concept": "concept",
}


@dataclass
class Violation:
    field: str
    message: str
    value: Any = None

    def to_dict(self) -> dict:
        return {"field": self.field, "message": self.message, "value": self.value}


@dataclass
class ValidationReport:
    conforms: bool
    violations: list[Violation] = field(default_factory=list)
    validator: str = "native"

    def fields(self) -> set[str]:
        return {v.field for v in self.violations}

    def to_dict(self) -> dict:
        return {
            "conforms": self.conforms,
            "validator": self.validator,
            "violations": [v.to_dict() for v in self.violations],
        }


def _parse_strength(v: Any) -> Decimal | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float, Decimal)):
        return Decimal(str(v))
    return None


def _parse_dt(v: Any) -> datetime | None:
    if isinstance(v, datetime):
        return v
    if isinstance(v, str):
        try:
            return datetime.fromisoformat(v)
        except ValueError:
            return None
    return None


def candidate_graph(c: dict[str, Any], ontology: Ontology) -> Graph:
    """Render a raw candidate dict as the focus subgraph SHACL validates."""
    g = Graph()
    s = CANDIDATE
    g.add((s, RDF.type, PREF.Preference))
    g.add((s, PREF.holder, HOLDER))
    g.add((s, PREF.status, Literal("active")))
    g.add((s, PREF.slotKey, Literal("candidate", datatype=XSD.string)))
    for f in ("who", "aspect", "intent", "context", "polarity", "reason"):
        if c.get(f) is not None:
            g.add((s, PREF[PATH[f]], Literal(str(c[f]))))
    if c.get("strength") is not None:
        d = _parse_strength(c["strength"])
        lit = Literal(d, datatype=XSD.decimal) if d is not None else Literal(str(c["strength"]))
        g.add((s, PREF.strength, lit))
    if c.get("stated_at") is not None:
        dt = _parse_dt(c["stated_at"])
        lit = Literal(dt.isoformat(), datatype=XSD.dateTime) if dt else Literal(str(c["stated_at"]))
        g.add((s, PREF.statedAt, lit))
    if c.get("source_text"):
        g.add((s, PREF.sourceText, Literal(str(c["source_text"]), datatype=XSD.string)))
    concept = c.get("concept")
    if concept and not str(concept).startswith("unmapped:"):
        node = CONCEPT_NS[str(concept)]
        g.add((s, PREF.concept, node))
        if str(concept) in ontology:
            g.add((node, RDF.type, SKOS.Concept))
    elif c.get("surface"):
        g.add((s, PREF.unmappedLabel, Literal(str(c["surface"]), datatype=XSD.string)))
    return g


class NativeValidator:
    """The node-level PreferenceShape, compiled to Python."""

    name = "native"

    def __init__(self, ontology: Ontology) -> None:
        self.ontology = ontology

    def validate(self, c: dict[str, Any]) -> ValidationReport:
        v: list[Violation] = []
        for f in ("who", "aspect", "intent", "context", "polarity"):
            val = c.get(f)
            if val is None:
                v.append(Violation(f, f"{f} is required", None))
            elif str(val) not in ENUMS[f]:
                v.append(Violation(f, f"{f} must be one of {sorted(ENUMS[f])}", val))
        if c.get("reason") is not None and str(c["reason"]) not in ENUMS["reason"]:
            v.append(Violation("reason", f"reason must be one of {sorted(ENUMS['reason'])}", c["reason"]))
        s = c.get("strength")
        d = _parse_strength(s) if s is not None else None
        if s is None:
            v.append(Violation("strength", "strength must be a decimal in [0, 1]", None))
        elif d is None:
            v.append(Violation("strength", "strength must be a decimal in [0, 1]", s))
        else:
            try:
                if not (Decimal("0") <= d <= Decimal("1")):
                    v.append(Violation("strength", "strength must be a decimal in [0, 1]", s))
            except InvalidOperation:  # pragma: no cover
                v.append(Violation("strength", "strength must be a decimal in [0, 1]", s))
        if c.get("stated_at") is None or _parse_dt(c["stated_at"]) is None:
            v.append(Violation("stated_at", "statedAt (date of the turn) is required", c.get("stated_at")))
        if not c.get("source_text"):
            v.append(Violation("source_text", "the user's own sentence must be kept as provenance", None))
        concept = c.get("concept")
        if concept and not str(concept).startswith("unmapped:"):
            if str(concept) not in self.ontology:
                v.append(Violation("concept", "concept must be a skos:Concept in the ontology", concept))
        elif not c.get("surface"):
            v.append(Violation("concept", "either a known concept or an unmapped label is required", None))
        return ValidationReport(not v, v, self.name)


class ShaclValidator:
    """Real SHACL (pyshacl) on the candidate's focus subgraph."""

    name = "shacl"

    def __init__(self, ontology: Ontology) -> None:
        self.ontology = ontology
        shapes = Graph()
        shapes.parse(data_path("shapes.ttl"), format="turtle")
        # keep only the node-level shape for per-write validation (graph-level shapes are global)
        self.node_shapes = Graph()
        for prefix, ns in shapes.namespaces():
            self.node_shapes.bind(prefix, ns)
        keep = PREF.PreferenceShape
        todo = [keep]
        seen = set()
        while todo:
            n = todo.pop()
            if n in seen:
                continue
            seen.add(n)
            for p, o in shapes.predicate_objects(n):
                self.node_shapes.add((n, p, o))
                if not isinstance(o, Literal):
                    todo.append(o)

    def validate(self, c: dict[str, Any]) -> ValidationReport:
        from pyshacl import validate

        data = candidate_graph(c, self.ontology)
        conforms, results, _ = validate(data, shacl_graph=self.node_shapes, advanced=True)
        v: list[Violation] = []
        sh = "http://www.w3.org/ns/shacl#"
        inv = {f"{PREF}{p}": f for f, p in PATH.items()}
        for r in results.subjects(RDF.type, URIRef(sh + "ValidationResult")):
            path = results.value(r, URIRef(sh + "resultPath"))
            msg = results.value(r, URIRef(sh + "resultMessage"))
            val = results.value(r, URIRef(sh + "value"))
            fld = inv.get(str(path), "concept" if path is None else str(path))
            v.append(Violation(fld, str(msg) if msg else "constraint violated", str(val) if val is not None else None))
        return ValidationReport(bool(conforms), v, self.name)


def validate_graph(g: Graph) -> ValidationReport:
    """Whole-memory integrity check with all shapes (I1, I2, no cycles, same-slot supersession)."""
    from pyshacl import validate

    shapes = Graph()
    shapes.parse(data_path("shapes.ttl"), format="turtle")
    conforms, results, text = validate(g, shacl_graph=shapes, advanced=True)
    sh = "http://www.w3.org/ns/shacl#"
    v = []
    for r in results.subjects(RDF.type, URIRef(sh + "ValidationResult")):
        msg = results.value(r, URIRef(sh + "resultMessage"))
        focus = results.value(r, URIRef(sh + "focusNode"))
        v.append(Violation(str(results.value(r, URIRef(sh + "resultPath")) or "graph"), str(msg), str(focus)))
    return ValidationReport(bool(conforms), v, "shacl-graph")
