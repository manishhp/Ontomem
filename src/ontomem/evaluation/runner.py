"""Run systems over PrefRev and write results, statistics and failure cases.

    python -m ontomem.evaluation.runner --mode A --split test --out results/modeA
    python -m ontomem.evaluation.runner --mode B --split test --out results/modeB
    python -m ontomem.evaluation.runner --mode A --noise 0.15 --systems shacl --out results/noise

Mode A ("gold extraction"): every system gets the gold statement fields; only the key
         decision differs. This is the base paper's setting.
Mode B ("end-to-end"): every system gets the rule extractor's output for the raw turn
         text; extraction and mapping errors propagate and are attributed by stage.
"""

from __future__ import annotations

import argparse
import json
import random
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from ..core.extract.rules import RuleExtractor
from ..core.mapping import ConceptMapper
from ..core.model import Context
from ..core.ontology import Ontology, default_ontology
from ..core.reasoner import Reasoner, ReasonerConfig
from ..core.recommend import Recommender, load_catalog
from ..core.retrieve import Query, Retriever
from ..prefrev.generator import extended_ontology, load
from . import metrics as M
from .stats import cluster_bootstrap, holm, mcnemar
from .systems import (
    Item,
    KeyedSystem,
    OntoMemSystem,
    System,
    all_ablations,
    append_only,
    oracle,
    sim_llm,
    surface_key,
)

DEFAULT_DATA = Path(__file__).resolve().parents[1] / "data" / "prefrev"


@dataclass
class Stream:
    episode: str
    events: list[tuple[str, Any]] = field(default_factory=list)
    meta: dict[str, dict[str, Any]] = field(default_factory=dict)  # item_id -> case/tier/kind/gold info
    gold_now: dict[str, dict[str, str]] = field(default_factory=dict)  # probe_id -> gold key -> polarity
    gold_rel: dict[str, dict[str, int]] = field(default_factory=dict)  # probe_id -> item -> relevance (-1 = vetoed)
    gold_slots: dict[str, dict[str, str]] = field(default_factory=dict)  # gold key -> fields
    missed_extractions: int = 0
    gold_statements: int = 0


def _gold_fields(gk: str) -> dict[str, str]:
    who, what, aspect, intent, ctx = gk.split("|")
    return {"who": who, "what": what, "aspect": aspect, "intent": intent, "context": ctx}


def _corrupt(raw: dict[str, Any], rng: random.Random) -> dict[str, Any]:
    """Simulate the malformed output a small LLM extractor produces (for the SHACL experiment)."""
    r = dict(raw)
    kind = rng.choice(["intent", "strength", "context", "polarity", "who", "strength_pct", "aspect"])
    r.update({
        "intent": {"intent": "maybe"}, "strength": {"strength": "high"}, "context": {"context": "office"},
        "polarity": {"polarity": "love"}, "who": {"who": "wife"}, "strength_pct": {"strength": 85},
        "aspect": {"aspect": "taste"},
    }[kind])
    r["_corruption"] = kind
    return r


def prepare(episodes: list[dict], mode: str, onto: Ontology, gold_onto: Ontology, noise: float = 0.0,
            seed: int = 0, catalog: list[dict] | None = None) -> list[Stream]:
    mapper = ConceptMapper(onto)
    extractor = RuleExtractor()
    rng = random.Random(seed)
    catalog = catalog if catalog is not None else load_catalog()
    streams = []
    for ep in episodes:
        st = Stream(ep["episode_id"])
        gold_cur: dict[str, str] = {}
        gold_sys = OntoMemSystem(gold_onto, ReasonerConfig(), name="gold", mapper=ConceptMapper(gold_onto))
        gold_rec = Recommender(gold_onto, Retriever(gold_onto, gold_sys.reasoner), catalog)
        probes_by_turn: dict[int, list[dict]] = {}
        for p in ep["probes"]:
            probes_by_turn.setdefault(p["after_turn"], []).append(p)
        for ti, turn in enumerate(ep["turns"]):
            ts = datetime.fromisoformat(turn["ts"])
            golds = turn["gold"]
            st.gold_statements += len(golds)
            for g in golds:
                st.gold_slots[g["gold_key"]] = _gold_fields(g["gold_key"])
            # ---- candidates
            items: list[Item] = []
            if mode == "A":
                for k, g in enumerate(golds):
                    m = mapper.map(g["surface"])
                    items.append(Item(
                        item_id=f"{turn['turn_id']}#{k}", turn_id=turn["turn_id"], ts=ts, surface=g["surface"],
                        polarity=g["polarity"], who=g["who"], aspect=g["aspect"], intent=g["intent"],
                        context=g["context"], reason=g["reason"], strength=0.8, text=turn["text"], concept=m.concept,
                        gold_key=g["gold_key"], gold_polarity=g["polarity"], gold_domain=g["domain"]))
            else:
                raws = extractor.extract(turn["text"], ts, turn["turn_id"])
                used: set[int] = set()
                for k, raw in enumerate(raws):
                    m = mapper.map(raw["surface"])
                    best, best_s = None, 0.0
                    for gi, g in enumerate(golds):
                        if gi in used:
                            continue
                        s = (3 if m.concept == g["concept"] else 0) + (1 if raw["who"] == g["who"] else 0)
                        s += len(set(raw["surface"].split()) & set(g["surface"].lower().split())) * 0.5
                        if s > best_s:
                            best, best_s = gi, s
                    g = golds[best] if best is not None and best_s >= 1 else None
                    if g is not None:
                        used.add(best)  # type: ignore[arg-type]
                    items.append(Item(
                        item_id=f"{turn['turn_id']}#{k}", turn_id=turn["turn_id"], ts=ts, surface=raw["surface"],
                        polarity=raw["polarity"], who=raw["who"], aspect=raw["aspect"], intent=raw["intent"],
                        context=raw["context"], reason=raw["reason"], strength=raw["strength"], text=turn["text"],
                        concept=m.concept, gold_key=g["gold_key"] if g else None,
                        gold_polarity=g["polarity"] if g else None, gold_domain=g["domain"] if g else None))
                st.missed_extractions += len(golds) - len(used)
            for it in items:
                if noise > 0 and rng.random() < noise:
                    base = {k: getattr(it, k) for k in ("surface", "polarity", "who", "aspect", "intent", "context", "reason", "strength")}
                    it.raw = _corrupt(base, rng)
                g = next((x for x in golds if x["gold_key"] == it.gold_key), None)
                prev = gold_cur.get(it.gold_key) if it.gold_key else None
                st.meta[it.item_id] = {
                    "case": turn["case"], "tier": turn["tier"],
                    "kind": g["surface_kind"] if g else "spurious",
                    "gold_concept": g["concept"] if g else None,
                    "gold": g,
                    "is_revision": prev is not None and it.gold_polarity is not None and prev != it.gold_polarity,
                    "is_reinforcement": prev is not None and prev == it.gold_polarity,
                    "corruption": it.raw.get("_corruption") if it.raw else None,
                }
                st.events.append(("write", it))
            # ---- gold track
            for k, g in enumerate(golds):
                gold_cur[g["gold_key"]] = g["polarity"]
                gold_sys.write(Item(
                    item_id=f"{turn['turn_id']}#g{k}", turn_id=turn["turn_id"], ts=ts, surface=g["surface"],
                    polarity=g["polarity"], who=g["who"], aspect=g["aspect"], intent=g["intent"], context=g["context"],
                    reason=g["reason"], strength=0.8, text=turn["text"], concept=g["concept"], gold_key=g["gold_key"]))
            for p in probes_by_turn.get(ti, []):
                st.gold_now[p["probe_id"]] = dict(gold_cur)
                q = Query(p["text"], domains=[p["domain"]], context=p["context"], who=p["who"])
                ranked = gold_rec.rank(gold_sys.view(), q, now=datetime.fromisoformat(p["ts"]))
                st.gold_rel[p["probe_id"]] = {i: (-1 if v else (2 if s > 0.05 else (1 if s >= -0.05 else 0))) for i, s, v in ranked}
                st.events.append(("probe", p))
        streams.append(st)
    return streams


def _reader(system: System, onto: Ontology, catalog: list[dict]) -> Recommender:
    reasoner = system.reasoner if isinstance(system, OntoMemSystem) else Reasoner(onto)
    if isinstance(system, KeyedSystem):
        system.reader_reasoner = reasoner
    return Recommender(onto, Retriever(onto, reasoner), catalog)


def run_system(system: System, streams: list[Stream], onto: Ontology, gold_onto: Ontology, mode: str,
               catalog: list[dict] | None = None, downstream: bool = True) -> dict[str, Any]:
    catalog = catalog if catalog is not None else load_catalog()
    reader = _reader(system, onto, catalog)
    wrecs: list[M.WriteRec] = []
    precs: list[M.ProbeRec] = []
    failures: list[dict] = []
    t_write = t_probe = 0.0
    n_write = 0
    for st in streams:
        system.reset()
        node_gold: dict[str, str | None] = {}
        node_item: dict[str, Item] = {}
        for kind, ev in st.events:
            if kind == "write":
                it: Item = ev
                meta = st.meta[it.item_id]
                pre_active = system.active_nodes()
                eligible = it.gold_key is not None and any(node_gold.get(n.id) == it.gold_key for n in pre_active)
                t0 = time.perf_counter()
                rec = system.write(it)
                t_write += time.perf_counter() - t0
                n_write += 1
                if rec.decision in ("new", "supersede"):
                    node_gold[rec.node_id] = it.gold_key
                    node_item[rec.node_id] = it
                tgt = node_gold.get(rec.target_id) if rec.target_id else None
                merged = rec.decision in ("supersede", "reinforce")
                correct = merged and it.gold_key is not None and tgt == it.gold_key
                false = merged and tgt != it.gold_key
                stage = ""
                if (false or (meta["is_revision"] and eligible and not correct)):
                    stage = attribute(it, meta, mode)
                    if stage == "keying":
                        # the item itself was right; was the slot it should join built from a mis-mapped statement?
                        same = [n for n in pre_active if node_gold.get(n.id) == it.gold_key]
                        tgt_node = next((n for n in pre_active if n.id == rec.target_id), None)
                        if (same and all(n.key.what != it.concept for n in same)) or (
                                false and tgt_node is not None and tgt_node.key.what == it.concept):
                            stage = "mapping:earlier-statement"
                w = M.WriteRec(system.name, st.episode, it.item_id, meta["case"], meta["tier"], meta["kind"],
                               it.gold_key, meta["is_revision"], meta["is_reinforcement"], eligible, rec.decision,
                               tgt, correct, false, false and rec.decision == "supersede", stage)
                wrecs.append(w)
                if stage and len(failures) < 400:
                    failures.append({
                        "type": "false_merge" if false else "missed_merge", "episode": st.episode, "item_id": it.item_id,
                        "case": meta["case"], "text": it.text, "surface": it.surface, "concept": it.concept,
                        "gold_key": it.gold_key, "decision": rec.decision, "stage": stage,
                        "merged_into": (node_item[rec.target_id].text if rec.target_id in node_item else None),
                        "merged_into_gold": tgt,
                    })
            else:
                p = ev
                t0 = time.perf_counter()
                pr = probe(system, p, st, node_gold, gold_onto, onto)
                if downstream:
                    q = Query(p["text"], domains=[p["domain"]], context=p["context"], who=p["who"])
                    ranked = reader.rank(system.view(), q, now=datetime.fromisoformat(p["ts"]))
                    top = [i for i, _, v in ranked if not v]
                    rel = st.gold_rel[p["probe_id"]]
                    pr.ndcg5 = round(M.ndcg_at_k(top, {k: max(0, v) for k, v in rel.items()}), 4)
                    pr.harmful5 = any(rel.get(i) == -1 for i in top[:5])
                t_probe += time.perf_counter() - t0
                precs.append(pr)
                if not pr.clean and len(failures) < 800:
                    failures.append({"type": "probe", "episode": st.episode, "probe_id": p["probe_id"], "text": p["text"],
                                     "stale": pr.stale, "contaminated": pr.contaminated, "complete": pr.complete,
                                     **pr.details})
    return {
        "system": system.name,
        "writes": wrecs,
        "probes": precs,
        "failures": failures,
        "timing": {"ms_per_write": round(1000 * t_write / max(1, n_write), 4),
                   "ms_per_probe": round(1000 * t_probe / max(1, len(precs)), 4)},
        "usage": system.usage(),
    }


def attribute(it: Item, meta: dict, mode: str) -> str:
    """Which pipeline stage caused a merge error."""
    g = meta["gold"]
    if g is None:
        return "extraction:spurious"
    if mode == "B":
        for f in ("polarity", "who", "intent", "context", "aspect"):
            if getattr(it, f) != g[f]:
                return f"extraction:{f}"
    if meta.get("corruption"):
        return f"malformed:{meta['corruption']}"
    if it.concept != g["concept"]:
        return "mapping:unmapped" if it.concept.startswith("unmapped:") else "mapping:wrong-concept"
    return "keying"


def probe(system: System, p: dict, st: Stream, node_gold: dict, gold_onto: Ontology, onto: Ontology) -> M.ProbeRec:
    gold_now = st.gold_now[p["probe_id"]]
    intents = set(p["intents"])
    qctx = Context(p["context"])
    block, stale_nodes, contam_nodes = [], [], []
    for n in system.active_nodes():
        try:
            applies_ctx = qctx.subsumed_by(Context(n.key.context))
        except ValueError:
            applies_ctx = False
        if n.key.who != p["who"] or n.key.intent not in intents or not applies_ctx:
            continue
        gk = node_gold.get(n.id)
        dom = gold_onto.domain(_gold_fields(gk)["what"]) if gk else onto.domain(n.key.what)
        if dom != p["domain"]:
            continue
        block.append(n)
        if gk and gold_now.get(gk) is not None and gold_now[gk] != n.polarity.value:
            stale_nodes.append(n)
        if gk is None:
            contam_nodes.append(n)
        else:
            gf = st.gold_slots[gk]
            try:
                gctx_ok = qctx.subsumed_by(Context(gf["context"]))
            except ValueError:
                gctx_ok = False
            if gf["who"] != p["who"] or gf["intent"] not in intents or not gctx_ok:
                contam_nodes.append(n)
    present = {(node_gold.get(n.id), n.polarity.value) for n in block}
    missing = [g for g in p["gold_active"] if (g["gold_key"], g["polarity"]) not in present]
    complete = not missing
    stale, contaminated = bool(stale_nodes), bool(contam_nodes)
    det = {}
    if stale or contaminated or not complete:
        det = {
            "stale_values": [f"{onto.label(n.key.what)}={n.polarity.value}" for n in stale_nodes][:5],
            "contaminating": [f"{n.key.who}:{onto.label(n.key.what)}={n.polarity.value}" for n in contam_nodes][:5],
            "missing": [f"{g['gold_key']}={g['polarity']}" for g in missing][:5],
        }
    return M.ProbeRec(system.name, st.episode, p["probe_id"], p["domain"], p["context"], p["who"], stale, contaminated,
                      complete, complete and not stale and not contaminated, len(block), details=det)


# --------------------------------------------------------------------------- driver
def build_systems(names: list[str], onto: Ontology, train_eps: list[dict] | None, dev_eps: list[dict] | None,
                  mode: str, gold_onto: Ontology) -> tuple[list[System], dict]:
    info: dict[str, Any] = {}
    systems: list[System] = []
    for n in names:
        if n == "S0":
            systems.append(append_only())
        elif n == "S1":
            systems.append(surface_key())
        elif n == "S3":
            systems.append(OntoMemSystem(onto))
        elif n == "S4":
            systems.append(oracle())
        elif n == "ablations":
            systems.extend(all_ablations(onto))
        elif n == "shacl":
            systems.append(OntoMemSystem(onto, name="S3-OntoMem (SHACL+repair)"))
            systems.append(OntoMemSystem(onto, name="S3 -SHACL", validate=False))
        elif n == "S5":
            from .learned import learned_system, train

            tr = [[ev for k, ev in s.events if k == "write"] for s in prepare(train_eps or [], mode, onto, gold_onto)]
            dv = [[ev for k, ev in s.events if k == "write"] for s in prepare(dev_eps or [], mode, onto, gold_onto)]
            keyer, tinfo = train(tr, dv)
            info["S5"] = tinfo
            systems.append(learned_system(keyer))
        elif n.startswith("sim"):
            # sim:p:q
            _, p, q = n.split(":")
            systems.append(sim_llm(float(p), float(q)))
        elif n in ("S2", "S2b"):
            from ..core.llm import from_env

            llm = from_env()
            if llm is None:
                info[n] = "skipped: set ONTOMEM_LLM (e.g. ollama:qwen2.5:7b-instruct) to run LLM systems"
                continue
            from .llm_systems import OntologyConstrainedSystem, mem0_system

            systems.append(mem0_system(llm) if n == "S2" else OntologyConstrainedSystem(onto, llm))
        else:
            raise ValueError(f"unknown system {n}")
    return systems, info


def evaluate(mode: str = "A", split: str = "test", systems: list[str] | None = None, data: str | Path = DEFAULT_DATA,
             out: str | Path | None = None, noise: float = 0.0, limit: int | None = None, bootstrap: int = 1000,
             downstream: bool = True, progress=None) -> dict[str, Any]:
    t0 = time.time()
    onto = default_ontology()
    gold_onto = extended_ontology(onto)
    eps = load(data, split)[:limit] if limit else load(data, split)
    names = systems or ["S0", "S1", "S3", "S4", "S5", "ablations"]
    train_eps = load(data, "train") if "S5" in names else None
    dev_eps = load(data, "dev") if "S5" in names else None
    syss, info = build_systems(names, onto, train_eps, dev_eps, mode, gold_onto)
    streams = prepare(eps, mode, onto, gold_onto, noise)
    results = []
    for i, s in enumerate(syss):
        if progress:
            progress(i, len(syss), s.name)
        results.append(run_system(s, streams, onto, gold_onto, mode, downstream=downstream))
    summary = summarize(results, streams, bootstrap)
    summary["meta"] = {
        "mode": mode, "split": split, "episodes": len(eps), "noise": noise, "systems": [r["system"] for r in results],
        "notes": info, "seconds": round(time.time() - t0, 1), "ontology_version": onto.version,
        "missed_extractions": sum(s.missed_extractions for s in streams),
        "gold_statements": sum(s.gold_statements for s in streams),
        "created": datetime.now().isoformat(timespec="seconds"),
    }
    if out:
        write_outputs(Path(out), summary, results)
    return summary


def summarize(results: list[dict], streams: list[Stream], bootstrap: int = 1000) -> dict[str, Any]:
    rows = []
    by_case, by_kind, stages = {}, {}, {}
    ref = next((r for r in results if r["system"].startswith("S3-OntoMem")), None)
    tests = {}
    for r in results:
        w, p = r["writes"], r["probes"]
        row = {"system": r["system"], **M.summarize_writes(w), **M.summarize_probes(p), **r["timing"], "usage": r["usage"]}
        if bootstrap:
            for name, fn, recs in [
                ("stale_exposure", lambda xs: M.summarize_probes(xs)["stale_exposure"], p),
                ("clean_retrieval", lambda xs: M.summarize_probes(xs)["clean_retrieval"], p),
                ("false_merge_rate_q", lambda xs: M.summarize_writes(xs)["false_merge_rate_q"], w),
                ("missed_merge_rate_p", lambda xs: M.summarize_writes(xs)["missed_merge_rate_p"], w),
            ]:
                _, lo, hi = cluster_bootstrap(recs, lambda x: x.episode, fn, b=bootstrap)
                row[f"{name}_ci"] = [lo, hi]
        rows.append(row)
        by_case[r["system"]] = M.breakdown(w, "case")
        by_kind[r["system"]] = M.breakdown(w, "surface_kind")
        stages[r["system"]] = M.stage_counts(w)
        if ref is not None and r is not ref:
            a = [x.clean for x in ref["probes"]]
            b = [x.clean for x in r["probes"]]
            tests[r["system"]] = mcnemar(a, b)
    if tests:
        adj = holm({k: v["p_value"] for k, v in tests.items()})
        for k in tests:
            tests[k]["p_holm"] = adj[k]
    return {"table": rows, "by_case": by_case, "by_surface_kind": by_kind, "error_stages": stages,
            "tests_clean_retrieval_vs_S3": tests}


def write_outputs(out: Path, summary: dict, results: list[dict]) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.json").write_text(json.dumps(summary, indent=2, default=str))
    with (out / "failures.jsonl").open("w") as f:
        for r in results:
            for x in r["failures"]:
                f.write(json.dumps({"system": r["system"], **x}, default=str) + "\n")
    with (out / "probes.jsonl").open("w") as f:
        for r in results:
            for x in r["probes"]:
                f.write(json.dumps(asdict(x), default=str) + "\n")
    cols = ["system", "missed_merge_rate_p", "false_merge_rate_q", "valid_erasures", "stale_exposure", "contamination",
            "completeness", "clean_retrieval", "ndcg5", "harmful5", "ms_per_write"]
    lines = [",".join(cols)] + [",".join(str(row.get(c, "")) for c in cols) for row in summary["table"]]
    (out / "table.csv").write_text("\n".join(lines) + "\n")
    (out / "table.tex").write_text(latex_table(summary["table"]))


def latex_table(rows: list[dict]) -> str:
    def pct(x):
        return "--" if x is None else f"{100 * x:.1f}"

    def ndcg(x):
        return "--" if x is None else f"{x:.3f}"

    head = ("\\begin{tabular}{lrrrrrrr}\n\\toprule\n"
            "System & $p$ (\\%) & $q$ (\\%) & Stale (\\%) & Contam. (\\%) & Clean (\\%) & NDCG@5 & Harmful@5 (\\%) \\\\\n\\midrule\n")
    body = "".join(
        f"{r['system'].replace('_', ' ')} & {pct(r['missed_merge_rate_p'])} & {pct(r['false_merge_rate_q'])} & "
        f"{pct(r['stale_exposure'])} & {pct(r['contamination'])} & {pct(r['clean_retrieval'])} & "
        f"{ndcg(r['ndcg5'])} & {pct(r['harmful5'])} \\\\\n"
        for r in rows
    )
    return head + body + "\\bottomrule\n\\end{tabular}\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", default="A", choices=["A", "B"])
    ap.add_argument("--split", default="test")
    ap.add_argument("--systems", default="S0,S1,S3,S4,S5,ablations")
    ap.add_argument("--data", default=str(DEFAULT_DATA))
    ap.add_argument("--out", default=None)
    ap.add_argument("--noise", type=float, default=0.0)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--bootstrap", type=int, default=1000)
    a = ap.parse_args()
    s = evaluate(a.mode, a.split, a.systems.split(","), a.data, a.out, a.noise, a.limit, a.bootstrap,
                 progress=lambda i, n, name: print(f"[{i + 1}/{n}] {name}", flush=True))
    cols = ["system", "missed_merge_rate_p", "false_merge_rate_q", "stale_exposure", "contamination", "clean_retrieval",
            "ndcg5", "harmful5", "ms_per_write"]
    print("\n" + " | ".join(f"{c[:14]:>14}" for c in cols))
    for r in s["table"]:
        print(" | ".join(f"{str(r.get(c))[:14]:>14}" for c in cols))
    print(json.dumps(s["meta"], indent=1))


if __name__ == "__main__":
    main()
