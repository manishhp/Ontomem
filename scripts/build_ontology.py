"""Compile scripts/concepts.tree into the SKOS Turtle file shipped with the package.

    python scripts/build_ontology.py            # writes src/ontomem/data/ontology/concepts.ttl

The build fails loudly on duplicate labels (after normalisation), unknown parents,
or cycles - the same consistency checks the ontology-evolution review queue runs.
"""

from __future__ import annotations

import sys
from pathlib import Path

from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.namespace import DCTERMS, OWL, RDF, SKOS

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ontomem.core.text import norm  # noqa: E402

C = Namespace("https://w3id.org/ontomem/concept/")
VERSION = "1.0.0"


def parse(path: Path):
    concepts: dict[str, dict] = {}
    order: list[str] = []
    related: list[tuple[str, str]] = []
    stack: list[tuple[int, str]] = []
    for lineno, raw in enumerate(path.read_text().splitlines(), 1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if raw.startswith("@related"):
            _, a, b = raw.split()
            related.append((a, b))
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        if indent % 2:
            raise SystemExit(f"line {lineno}: odd indentation")
        depth = indent // 2
        body = raw.strip()
        while stack and stack[-1][0] >= depth:
            stack.pop()
        parent = stack[-1][1] if stack else None
        if body.startswith("^"):  # extra parent for an existing concept
            name = body[1:]
            concepts.setdefault(name, {"broader": [], "pref": None, "alt": []})
            concepts[name]["broader"].append(parent)
            continue
        name, rest = body.split(":", 1)
        pref, _, alts = rest.partition("|")
        node = concepts.setdefault(name, {"broader": [], "pref": None, "alt": []})
        node["pref"] = pref.strip()
        node["alt"] = [a.strip() for a in alts.split(",") if a.strip()]
        if parent:
            node["broader"].append(parent)
        node["domain"] = stack[0][1] if stack else name
        order.append(name)
        stack.append((depth, name))
    return concepts, order, related


def check(concepts, related):
    errors = []
    for name, c in concepts.items():
        if c["pref"] is None:
            errors.append(f"{name}: referenced with ^ but never defined")
        for b in c["broader"]:
            if b not in concepts:
                errors.append(f"{name}: unknown parent {b}")
    for a, b in related:
        for x in (a, b):
            if x not in concepts:
                errors.append(f"@related: unknown concept {x}")
    # label collisions
    seen: dict[str, str] = {}
    for name, c in concepts.items():
        for label in [c["pref"], *c["alt"]]:
            key = norm(label)
            if key in seen and seen[key] != name:
                errors.append(f"label '{label}' used by both {seen[key]} and {name}")
            seen[key] = name
    # cycles
    def ancestors(n, path=()):
        if n in path:
            errors.append(f"cycle: {' -> '.join(path + (n,))}")
            return
        for b in concepts[n]["broader"]:
            ancestors(b, path + (n,))

    for n in concepts:
        ancestors(n)
    if errors:
        raise SystemExit("ontology errors:\n  " + "\n  ".join(sorted(set(errors))))


def build(concepts, order, related) -> Graph:
    g = Graph()
    g.bind("skos", SKOS)
    g.bind("c", C)
    g.bind("dct", DCTERMS)
    g.bind("owl", OWL)
    onto = URIRef("https://w3id.org/ontomem/concepts")
    g.add((onto, RDF.type, OWL.Ontology))
    g.add((onto, OWL.versionInfo, Literal(VERSION)))
    g.add((onto, DCTERMS.title, Literal("OntoMem preference concept hierarchy", lang="en")))
    domains = sorted({c["domain"] for c in concepts.values()})
    for d in domains:
        scheme = C[f"scheme/{d}"]
        g.add((scheme, RDF.type, SKOS.ConceptScheme))
        g.add((scheme, SKOS.prefLabel, Literal(concepts[d]["pref"], lang="en")))
        g.add((scheme, SKOS.hasTopConcept, C[d]))
        g.add((C[d], SKOS.topConceptOf, scheme))
    for name in order:
        c = concepts[name]
        node = C[name]
        g.add((node, RDF.type, SKOS.Concept))
        g.add((node, SKOS.inScheme, C[f"scheme/{c['domain']}"]))
        g.add((node, SKOS.prefLabel, Literal(c["pref"], lang="en")))
        for a in c["alt"]:
            g.add((node, SKOS.altLabel, Literal(a, lang="en")))
        for b in c["broader"]:
            g.add((node, SKOS.broader, C[b]))
            g.add((C[b], SKOS.narrower, node))
    for a, b in related:
        g.add((C[a], SKOS.related, C[b]))
        g.add((C[b], SKOS.related, C[a]))
    return g


def main() -> None:
    src = ROOT / "scripts" / "concepts.tree"
    concepts, order, related = parse(src)
    check(concepts, related)
    g = build(concepts, order, related)
    out = ROOT / "src" / "ontomem" / "data" / "ontology" / "concepts.ttl"
    header = (
        f"# GENERATED by scripts/build_ontology.py from scripts/concepts.tree - do not edit by hand.\n"
        f"# {len(concepts)} concepts, version {VERSION}\n"
    )
    out.write_text(header + g.serialize(format="turtle"))
    print(f"wrote {out.relative_to(ROOT)}: {len(concepts)} concepts, {len(g)} triples")


if __name__ == "__main__":
    main()
