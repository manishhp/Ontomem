"""Closed-form model of stale exposure as a function of the missed-merge rate p and the
false-merge rate q, validated against simulation (S2-sim) on PrefRev.

Model
-----
Fix a probe and one of its relevant slots k, with gold values v_0, v_1, ..., v_r
(v_r is current). At write i >= 1 the key assigner:

  * false-merges the item into another slot with probability q -> slot k is not
    updated by this write;
  * otherwise misses the merge with probability p -> the previous node (value v_{i-1})
    is orphaned and stays active forever.

An orphaned value is stale iff v_{i-1} != v_r. If the *last* write to k was a
revision and was false-merged, the previous value is still k's current node, which
is stale iff v_{r-1} != v_r. Treating these events as independent:

    P(k clean) = (1 - q * [v_{r-1} != v_r]) * prod_{i: v_{i-1} != v_r} (1 - (1 - q) p)
    P(probe stale) = 1 - prod_k P(k clean)

Expected stale exposure = mean over probes. The model ignores second-order
interactions (a false merge's victim, repeated orphaning), which is why it is
checked against simulation rather than assumed.

    python -m ontomem.evaluation.analytic --out results/sweep
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..core.ontology import default_ontology
from ..prefrev.generator import extended_ontology, load
from .runner import DEFAULT_DATA, Stream, prepare, run_system
from .systems import sim_llm


def histories(stream: Stream) -> list[dict[str, list[str]]]:
    """For each probe in order: gold value history (polarities) of every relevant slot."""
    hist: dict[str, list[str]] = {}
    out = []
    for kind, ev in stream.events:
        if kind == "write":
            if ev.gold_key:
                hist.setdefault(ev.gold_key, []).append(ev.gold_polarity)
        else:
            out.append({g["gold_key"]: list(hist.get(g["gold_key"], [])) for g in ev["gold_active"]})
    return out


def p_probe_stale(slots: dict[str, list[str]], p: float, q: float) -> float:
    clean = 1.0
    for vals in slots.values():
        if len(vals) < 2:
            continue
        final = vals[-1]
        pk = 1.0 - q if vals[-2] != final else 1.0
        for i in range(1, len(vals)):
            if vals[i - 1] != final:
                pk *= 1.0 - (1.0 - q) * p
        clean *= pk
    return 1.0 - clean


def predict(streams: list[Stream], p: float, q: float) -> float:
    probs = [p_probe_stale(h, p, q) for st in streams for h in histories(st)]
    return sum(probs) / len(probs) if probs else 0.0


def sweep(ps=(0.0, 0.1, 0.2, 0.3, 0.4, 0.5), qs=(0.0, 0.05, 0.1, 0.2), split: str = "test",
          data: str | Path = DEFAULT_DATA, seeds: int = 3) -> list[dict]:
    onto = default_ontology()
    gold = extended_ontology(onto)
    streams = prepare(load(data, split), "A", onto, gold)
    rows = []
    for q in qs:
        for p in ps:
            sims = []
            for seed in range(seeds):
                r = run_system(sim_llm(p, q, seed), streams, onto, gold, "A", downstream=False)
                n = len(r["probes"])
                sims.append(sum(x.stale for x in r["probes"]) / n)
            rows.append({"p": p, "q": q, "simulated": round(sum(sims) / len(sims), 4),
                         "sim_min": round(min(sims), 4), "sim_max": round(max(sims), 4),
                         "analytic": round(predict(streams, p, q), 4)})
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="results/sweep")
    ap.add_argument("--seeds", type=int, default=3)
    a = ap.parse_args()
    rows = sweep(seeds=a.seeds)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "sweep.json").write_text(json.dumps(rows, indent=2))
    err = [abs(r["simulated"] - r["analytic"]) for r in rows]
    print(f"{'p':>5} {'q':>5} {'sim':>8} {'analytic':>9}")
    for r in rows:
        print(f"{r['p']:>5} {r['q']:>5} {r['simulated']:>8} {r['analytic']:>9}")
    print(f"mean |sim - analytic| = {sum(err) / len(err):.4f}, max = {max(err):.4f}")


if __name__ == "__main__":
    main()
