"""RDF projection of the memory: reified preference nodes, SKOS links and provenance.

The event log is the source of truth; this module renders a user's projected memory
as RDF (for SPARQL, SHACL whole-graph validation, export as JSON-LD/Turtle).
Oxigraph is used for SPARQL when installed (fast, SPARQL 1.1); rdflib otherwise.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from rdflib import BNode, Graph, Literal, Namespace, URIRef
from rdflib.namespace import PROV, RDF, SKOS, XSD

from .model import PreferenceNode
from .ontology import CONCEPT_NS, PREF_NS, Ontology, is_unmapped

USER_NS = Namespace("https://w3id.org/ontomem/user/")
PREF = PREF_NS


def _dt(d: datetime) -> Literal:
    return Literal(d.isoformat(), datatype=XSD.dateTime)


def user_iri(user_id: str) -> URIRef:
    return USER_NS[user_id]


def node_iri(user_id: str, node_id: str) -> URIRef:
    return USER_NS[f"{user_id}/pref/{node_id}"]


def preference_triples(
    g: Graph,
    user_id: str,
    n: PreferenceNode,
    *,
    needs_confirmation: bool = False,
    relations: dict[str, list[str]] | None = None,
) -> URIRef:
    s = node_iri(user_id, n.id)
    g.add((s, RDF.type, PREF.Preference))
    g.add((s, PREF.holder, user_iri(user_id)))
    g.add((s, PREF.subjectRole, Literal(n.key.who)))
    if is_unmapped(n.key.what):
        g.add((s, PREF.unmappedLabel, Literal(n.key.what.removeprefix("unmapped:"), datatype=XSD.string)))
    else:
        g.add((s, PREF.concept, CONCEPT_NS[n.key.what]))
    g.add((s, PREF.aspect, Literal(n.key.aspect)))
    g.add((s, PREF.intent, Literal(n.key.intent)))
    g.add((s, PREF.context, Literal(n.key.context)))
    g.add((s, PREF.slotKey, Literal(str(n.key), datatype=XSD.string)))
    g.add((s, PREF.polarity, Literal(n.polarity.value)))
    g.add((s, PREF.strength, Literal(Decimal(str(round(n.strength, 3))), datatype=XSD.decimal)))
    g.add((s, PREF.reason, Literal(n.reason.value)))
    g.add((s, PREF.statedAt, _dt(n.stated_at)))
    g.add((s, PREF.validFrom, _dt(n.valid_from)))
    if n.valid_to:
        g.add((s, PREF.validTo, _dt(n.valid_to)))
    if n.last_confirmed:
        g.add((s, PREF.lastConfirmed, _dt(n.last_confirmed)))
    g.add((s, PREF.status, Literal(n.status.value)))
    g.add((s, PREF.sourceText, Literal(n.source_text, datatype=XSD.string)))
    g.add((s, PREF.surface, Literal(n.surface)))
    g.add((s, PREF.createdByRule, Literal(n.created_by_rule)))
    g.add((s, PREF.extractedBy, Literal(n.extractor)))
    if n.ontology_version:
        g.add((s, PREF.ontologyVersion, Literal(n.ontology_version)))
    for e in n.evidence:
        g.add((s, PREF.evidence, Literal(e)))
    if n.superseded_by:
        g.add((s, PREF.supersededBy, node_iri(user_id, n.superseded_by)))
    if n.supersedes:
        g.add((s, PREF.supersedes, node_iri(user_id, n.supersedes)))
    if needs_confirmation:
        g.add((s, PREF.needsConfirmation, Literal(True)))
    for other in (relations or {}).get("exception_to", []):
        g.add((s, PREF.exceptionTo, node_iri(user_id, other)))
    for other in (relations or {}).get("refines", []):
        g.add((s, PREF.refines, node_iri(user_id, other)))
    # PROV-O: the extraction activity that produced this value
    act = BNode()
    g.add((s, PROV.wasGeneratedBy, act))
    g.add((act, RDF.type, PREF.Extraction))
    g.add((act, PROV.atTime, _dt(n.stated_at)))
    g.add((act, PROV.wasAssociatedWith, Literal(n.extractor)))
    return s


def memory_graph(user_id: str, memory, reasoner, ontology: Ontology, include_concepts: bool = True) -> Graph:
    """Full RDF graph for one user (active + history)."""
    g = Graph()
    g.bind("pref", PREF)
    g.bind("skos", SKOS)
    g.bind("c", CONCEPT_NS)
    g.bind("u", USER_NS)
    g.bind("prov", PROV)
    g.add((user_iri(user_id), RDF.type, PREF.Person))
    used: set[str] = set()
    for n in memory.nodes.values():
        rel = reasoner.relations(memory, n) if n.active else None
        preference_triples(g, user_id, n, needs_confirmation=n.id in memory.needs_confirmation, relations=rel)
        if not is_unmapped(n.key.what):
            used.add(n.key.what)
    if include_concepts:
        for cid in used:
            for c in {cid, *ontology.ancestors(cid)}:
                s = CONCEPT_NS[c]
                g.add((s, RDF.type, SKOS.Concept))
                g.add((s, SKOS.prefLabel, Literal(ontology.label(c), lang="en")))
                for b in ontology.concepts[c].broader:
                    g.add((s, SKOS.broader, CONCEPT_NS[b]))
    return g


class SparqlStore:
    """Read-only SPARQL over a user's projected graph (Oxigraph if available)."""

    def __init__(self, graph: Graph) -> None:
        self.graph = graph
        try:
            import pyoxigraph as ox

            self._ox = ox.Store()
            self._ox.load(graph.serialize(format="nt").encode(), format=ox.RdfFormat.N_TRIPLES)
            self.engine = "oxigraph"
        except Exception:  # pragma: no cover - fallback path
            self._ox = None
            self.engine = "rdflib"

    def query(self, sparql: str) -> dict:
        # Store.query / Graph.query only run SPARQL queries; updates need .update(),
        # which this class never calls, so the endpoint is read-only by construction.
        if self._ox is not None:
            res = self._ox.query(sparql)
            if isinstance(res, bool):
                return {"type": "ask", "boolean": res}
            if hasattr(res, "variables"):
                vars_ = [v.value for v in res.variables]
                rows = []
                for sol in res:
                    rows.append({v: (_term(sol[v]) if sol[v] is not None else None) for v in vars_})
                return {"type": "select", "vars": vars_, "rows": rows}
            triples = [[_term(t.subject), _term(t.predicate), _term(t.object)] for t in res]
            return {"type": "graph", "triples": triples}
        res = self.graph.query(sparql)
        if res.type == "ASK":
            return {"type": "ask", "boolean": bool(res.askAnswer)}
        if res.type == "SELECT":
            vars_ = [str(v) for v in res.vars]
            return {
                "type": "select",
                "vars": vars_,
                "rows": [{v: (str(r[v]) if r[v] is not None else None) for v in vars_} for r in res],
            }
        return {"type": "graph", "triples": [[str(s), str(p), str(o)] for s, p, o in res]}


def _term(t) -> str:
    return getattr(t, "value", str(t))
