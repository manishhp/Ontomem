"""Read models for the web UI: memory graph, slot timelines, node details, compare mode."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ..core.engine import OntoMemEngine
from ..core.extract.rules import RuleExtractor
from ..core.mapping import ConceptMapper
from ..core.model import RULE_NAMES
from ..core.ontology import is_unmapped
from ..core.rdf import memory_graph
from ..core.reasoner import Reasoner, UserMemory
from ..core.recommend import Recommender
from ..core.retrieve import Retriever


def node_view(engine: OntoMemEngine, mem: UserMemory, n) -> dict[str, Any]:
    d = n.to_dict()
    d["label"] = engine.ontology.label(n.key.what)
    d["path"] = engine.ontology.path(n.key.what) if not is_unmapped(n.key.what) else []
    d["needs_confirmation"] = n.id in mem.needs_confirmation
    d["confidence"] = round(__import__("ontomem.core.belief", fromlist=["confidence"]).confidence(n, mem.last_event_time or n.stated_at), 3)
    d["rule_name"] = RULE_NAMES.get(n.created_by_rule, n.created_by_rule)
    if n.active:
        d["relations"] = engine.reasoner.relations(mem, n)
    return d


def memory_view(engine: OntoMemEngine, uid: str, as_of: datetime | None = None) -> dict[str, Any]:
    mem = engine.memory(uid, as_of)
    nodes = sorted(mem.nodes.values(), key=lambda n: n.stated_at)
    return {
        "as_of": as_of.isoformat() if as_of else None,
        "active": [node_view(engine, mem, n) for n in nodes if n.active],
        "history": [node_view(engine, mem, n) for n in nodes if not n.active],
        "clarifications": engine.clarifications(uid) if as_of is None else [],
        "stats": {
            "active": len(mem.active_by_key), "retired": sum(1 for n in nodes if not n.active),
            "needs_confirmation": len(mem.needs_confirmation),
            "third_party": sum(1 for n in mem.active() if n.key.who != "self"),
            "unmapped": sum(1 for n in mem.active() if is_unmapped(n.key.what)),
        },
        "first_event": nodes[0].stated_at.isoformat() if nodes else None,
        "last_event": mem.last_event_time.isoformat() if mem.last_event_time else None,
    }


def graph_view(engine: OntoMemEngine, uid: str, as_of: datetime | None = None, include_history: bool = True,
               ancestors: bool = False) -> dict[str, Any]:
    """Cytoscape-ready elements: user, preference nodes, SKOS concepts and every relation."""
    o = engine.ontology
    mem = engine.memory(uid, as_of)
    els_n: dict[str, dict] = {}
    edges: list[dict] = []
    els_n["user"] = {"id": "user", "kind": "user", "label": "You"}
    roles = set()
    for n in mem.nodes.values():
        if not n.active and not include_history:
            continue
        pid = f"p:{n.id}"
        els_n[pid] = {
            "id": pid, "kind": "preference", "node_id": n.id, "status": n.status.value, "polarity": n.polarity.value,
            "who": n.key.who, "intent": n.key.intent, "context": n.key.context, "aspect": n.key.aspect,
            "label": f"{n.polarity.value} {o.label(n.key.what)}", "needs_confirmation": n.id in mem.needs_confirmation,
            "stated_at": n.stated_at.isoformat(), "strength": round(n.strength, 2),
        }
        holder = "user" if n.key.who == "self" else f"role:{n.key.who}"
        if n.key.who != "self" and n.key.who not in roles:
            roles.add(n.key.who)
            els_n[holder] = {"id": holder, "kind": "role", "label": n.key.who.capitalize()}
            edges.append({"id": f"e:user-{holder}", "source": "user", "target": holder, "kind": "knows"})
        edges.append({"id": f"e:h-{pid}", "source": holder, "target": pid, "kind": "holds"})
        cid = f"c:{n.key.what}"
        if cid not in els_n:
            els_n[cid] = {"id": cid, "kind": "concept" if not is_unmapped(n.key.what) else "unmapped",
                          "label": o.label(n.key.what), "concept": n.key.what}
        edges.append({"id": f"e:c-{pid}", "source": pid, "target": cid, "kind": "about"})
        if n.superseded_by:
            edges.append({"id": f"e:s-{pid}", "source": pid, "target": f"p:{n.superseded_by}", "kind": "supersededBy"})
        if n.active:
            rel = engine.reasoner.relations(mem, n)
            for other in rel["exception_to"]:
                edges.append({"id": f"e:x-{pid}-{other}", "source": pid, "target": f"p:{other}", "kind": "exceptionTo"})
            for other in rel["refines"]:
                edges.append({"id": f"e:r-{pid}-{other}", "source": pid, "target": f"p:{other}", "kind": "refines"})
    used = [e["concept"] for e in list(els_n.values()) if e.get("kind") == "concept"]
    if not ancestors:
        # compact view: only concepts the user talks about, linked when one is narrower than another
        for a in used:
            for b in used:
                if a != b and o.narrower_than(a, b) and not any(o.narrower_than(a, m) and o.narrower_than(m, b) for m in used):
                    edges.append({"id": f"e:b-{a}-{b}", "source": f"c:{a}", "target": f"c:{b}", "kind": "broader"})
        return {"nodes": list(els_n.values()), "edges": edges, "as_of": as_of.isoformat() if as_of else None}
    # full view: SKOS ancestry for every concept used
    for c in used:
        path = o.path(c)
        for child, parent in zip(path[1:][::-1], path[:-1][::-1]):
            for x in (child, parent):
                if f"c:{x}" not in els_n:
                    els_n[f"c:{x}"] = {"id": f"c:{x}", "kind": "concept", "label": o.label(x), "concept": x, "ancestor": True}
            eid = f"e:b-{child}-{parent}"
            if not any(e["id"] == eid for e in edges):
                edges.append({"id": eid, "source": f"c:{child}", "target": f"c:{parent}", "kind": "broader"})
    return {"nodes": list(els_n.values()), "edges": edges, "as_of": as_of.isoformat() if as_of else None}


def timeline_view(engine: OntoMemEngine, uid: str) -> list[dict[str, Any]]:
    mem = engine.memory(uid)
    slots: dict[str, dict[str, Any]] = {}
    for n in sorted(mem.nodes.values(), key=lambda n: n.stated_at):
        k = str(n.key)
        s = slots.setdefault(k, {"key": k, "who": n.key.who, "concept": n.key.what, "label": engine.ontology.label(n.key.what),
                                 "aspect": n.key.aspect, "intent": n.key.intent, "context": n.key.context, "values": []})
        s["values"].append({
            "node_id": n.id, "polarity": n.polarity.value, "from": n.valid_from.isoformat(),
            "to": n.valid_to.isoformat() if n.valid_to else None, "status": n.status.value, "rule": n.created_by_rule,
            "source_text": n.source_text, "strength": round(n.strength, 2), "evidence": len(n.evidence),
            "confirmations": [t.isoformat() for t in n.evidence_times],
            "needs_confirmation": n.id in mem.needs_confirmation,
        })
    out = list(slots.values())
    out.sort(key=lambda s: (-len(s["values"]), s["values"][-1]["from"]))
    return out


def node_detail(engine: OntoMemEngine, uid: str, node_id: str) -> dict[str, Any]:
    mem = engine.memory(uid)
    if node_id not in mem.nodes:
        raise KeyError(node_id)
    n = mem.nodes[node_id]
    g = memory_graph(uid, mem, engine.reasoner, engine.ontology, include_concepts=False)
    from ..core.rdf import node_iri

    sub = type(g)()
    for prefix, ns in g.namespaces():
        sub.bind(prefix, ns)
    s = node_iri(uid, node_id)
    for t in g.triples((s, None, None)):
        sub.add(t)
    return {
        "node": node_view(engine, mem, n),
        "chain": [node_view(engine, mem, x) for x in mem.chain(node_id)],
        "broader": [node_view(engine, mem, x) for x in engine.reasoner.broader_active(mem, n)] if n.active else [],
        "narrower": [node_view(engine, mem, x) for x in engine.reasoner.narrower_active(mem, n)] if n.active else [],
        "turtle": sub.serialize(format="turtle"),
    }


# ------------------------------------------------------------------- compare
def compare(engine: OntoMemEngine, turns: list[dict[str, str]], baseline: str, query: str | None = None) -> dict[str, Any]:
    """Run one scripted conversation through OntoMem and a baseline, side by side."""
    from ..evaluation.systems import Item, OntoMemSystem, append_only, surface_key

    onto = engine.ontology
    mapper = ConceptMapper(onto)
    extractor = RuleExtractor()
    ours = OntoMemSystem(onto, name="OntoMem")
    base = {"S0": append_only, "S1": surface_key}.get(baseline, surface_key)()
    reader = Recommender(onto, Retriever(onto, Reasoner(onto)))
    base.reader_reasoner = Reasoner(onto)
    steps = []
    for i, t in enumerate(turns):
        ts = datetime.fromisoformat(t["ts"]) if t.get("ts") else datetime(2026, 1, 1 + i)
        raws = extractor.extract(t["text"], ts, f"t{i}")
        decisions = {"ours": [], "base": []}
        for k, raw in enumerate(raws):
            m = mapper.map(raw["surface"])
            it = Item(item_id=f"t{i}#{k}", turn_id=f"t{i}", ts=ts, surface=raw["surface"], polarity=raw["polarity"],
                      who=raw["who"], aspect=raw["aspect"], intent=raw["intent"], context=raw["context"], reason=raw["reason"],
                      strength=raw["strength"], text=t["text"], concept=m.concept)
            for name, sysm in (("ours", ours), ("base", base)):
                rec = sysm.write(it)
                decisions[name].append({"surface": raw["surface"], "decision": rec.decision,
                                        "target": _fmt(onto, sysm, rec.target_id) if rec.target_id and rec.decision == "supersede" else None})

        def snap(sysm) -> list[str]:
            return sorted(_fmt(onto, sysm, n.id) for n in sysm.active_nodes())

        a, b = snap(ours), snap(base)
        steps.append({"text": t["text"], "ts": ts.isoformat(), "ours": {"decisions": decisions["ours"], "active": a},
                      "base": {"decisions": decisions["base"], "active": b},
                      "only_ours": sorted(set(a) - set(b)), "only_base": sorted(set(b) - set(a))})
    out: dict[str, Any] = {"baseline": base.name, "steps": steps}
    if query:
        q = engine.retriever.parse(query)
        out["query"] = query
        out["ours_recommendations"] = [r["item"]["name"] for r in
                                       Recommender(onto, Retriever(onto, ours.reasoner)).recommend(ours.view(), q, 5, explain=False)["recommendations"]]
        out["base_recommendations"] = [r["item"]["name"] for r in reader.recommend(base.view(), q, 5, explain=False)["recommendations"]]
    return out


def _fmt(onto, sysm, node_id: str) -> str:
    n = sysm.nodes[node_id] if hasattr(sysm, "nodes") and node_id in getattr(sysm, "nodes", {}) else sysm.mem.nodes[node_id]
    who = "" if n.key.who == "self" else f"{n.key.who}: "
    extra = [x for x in (n.key.intent if n.key.intent != "standing" else "", n.key.context if n.key.context != "any" else "",
                         n.key.aspect if n.key.aspect != "general" else "") if x]
    return f"{who}{n.polarity.value} {onto.label(n.key.what)}" + (f" ({', '.join(extra)})" if extra else "")
