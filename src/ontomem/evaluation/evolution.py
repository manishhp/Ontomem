"""RQ5 experiment: can the ontology grow from usage without hurting consistency?

1. Run the train split through the mapper; collect phrases that need review
   (unmapped, or matched only by their head word).
2. Generate proposals (new narrower concept / synonym of an existing concept).
3. A simulated reviewer (who knows the gold concept, like a domain expert would)
   approves a proposal only if it is correct; when no proposal is correct the expert
   places the concept by hand (counted separately as manual effort).
4. Re-run the TEST split with the evolved ontology and compare with the original.
   H5: coverage-case errors fall and the false-merge rate does not rise.

    python -m ontomem.evaluation.evolution --out results/evolution
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from ..core.ontology import default_ontology
from ..core.text import norm, strip_articles
from ..evolve import Evolver
from ..prefrev import spec
from ..prefrev.generator import extended_ontology, load
from . import metrics as M
from .runner import DEFAULT_DATA, prepare, run_system
from .systems import OntoMemSystem

GOLD_PARENT = {cid: parent for cid, _, parent in spec.EXTERNAL}


def collect(data: str | Path, split: str = "train") -> dict[str, dict[str, Any]]:
    base = default_ontology()
    ev = Evolver(base)
    found: dict[str, dict[str, Any]] = {}
    for ep in load(data, split):
        domain_concepts: dict[str, list[str]] = defaultdict(list)
        for t in ep["turns"]:
            for g in t["gold"]:
                if g["concept"] in base:
                    domain_concepts[g["domain"]].append(g["concept"])
        for t in ep["turns"]:
            for g in t["gold"]:
                need, why = ev.needs_review(g["surface"])
                if not need:
                    continue
                key = strip_articles(norm(g["surface"]))
                rec = found.setdefault(key, {"surface": g["surface"], "why": why, "gold": g["concept"], "count": 0,
                                             "evidence": []})
                rec["count"] += 1
                rec["evidence"] = list(dict.fromkeys(rec["evidence"] + domain_concepts[g["domain"]][:3]))[:6]
    return found


def review(ev: Evolver, items: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Simulated expert review. Returns the decisions and effort counts."""
    log = []
    auto = manual = skipped = top1 = 0
    for key, rec in sorted(items.items(), key=lambda kv: -kv[1]["count"]):
        gold = rec["gold"]
        new_props = ev.propose(rec["surface"], rec["evidence"])
        alias_props = ev.alias_candidates(rec["surface"])
        decision, chosen = None, None
        if gold in ev.ontology and not gold.startswith("unmapped"):
            # the phrase is a synonym of an existing concept
            chosen = next((p for p in alias_props if p.parent == gold and p.checks.get("ok")), None)
            if chosen:
                ev.apply_alias(chosen)
                decision = "auto-alias"
            elif norm(rec["surface"]) not in ev.ontology.lexicon:
                ev.ontology.add_alt_label(gold, strip_articles(norm(rec["surface"])))
                decision = "manual-alias"
        else:
            parent = GOLD_PARENT.get(gold)
            # the expert accepts the right parent, or one level coarser - never a domain root
            ok_parents = set()
            if parent:
                ok_parents = {parent} | {b for b in ev.ontology.concepts[parent].broader if ev.ontology.concepts[b].broader}
            chosen = next((p for p in new_props if p.parent in ok_parents and p.checks.get("ok")), None)
            if chosen:
                chosen.concept_id = gold  # keep the gold id so the benchmark can score it
                ev.apply(chosen)
                decision = "auto-new"
            elif parent and gold not in ev.ontology:
                ev.ontology.add_concept(gold, strip_articles(norm(rec["surface"])), [parent])
                decision = "manual-new"
        top = (alias_props[:1] if gold in ev.ontology else new_props[:1])
        if top and (top[0].parent == gold or (gold not in ev.ontology and top[0].parent == GOLD_PARENT.get(gold))):
            top1 += 1
        if decision is None:
            skipped += 1
            decision = "skipped"
        elif decision.startswith("auto"):
            auto += 1
        else:
            manual += 1
        log.append({"phrase": rec["surface"], "count": rec["count"], "why": rec["why"], "gold": gold, "decision": decision,
                    "top_proposals": [{"kind": "new", "parent": p.parent, "score": round(p.score, 2), "source": p.source}
                                      for p in new_props[:3]]
                                     + [{"kind": "alias", "of": p.parent, "score": p.score} for p in alias_props[:2]]})
    ev.mapper.__init__(ev.ontology)  # refresh after manual edits
    return {"reviewed": len(items), "accepted_from_top3": auto, "manual": manual, "skipped": skipped,
            "top1_correct": top1, "log": log}


def run(data: str | Path = DEFAULT_DATA, out: str | Path | None = None) -> dict[str, Any]:
    base = default_ontology()
    gold = extended_ontology(base)
    items = collect(data, "train")
    evolved = base.copy()
    ev = Evolver(evolved)
    rv = review(ev, items)
    evolved.version = "1.1.0"
    test = load(data, "test")
    rows = {}
    for name, onto in [("before (v1.0.0)", base), ("after evolution (v1.1.0)", evolved)]:
        streams = prepare(test, "A", onto, gold)
        r = run_system(OntoMemSystem(onto, name=f"S3 {name}"), streams, onto, gold, "A")
        w = r["writes"]
        cov = [x for x in w if x.case == "coverage"]
        para = [x for x in w if x.surface_kind == "paraphrase"]
        mapped = sum(1 for s in streams for k, e in s.events if k == "write" and not e.concept.startswith("unmapped:"))
        total = sum(1 for s in streams for k, e in s.events if k == "write")
        rows[name] = {
            "concepts": len(onto),
            "test_statements_mapped": round(mapped / total, 4),
            **{k: v for k, v in M.summarize_writes(w).items() if k in ("missed_merge_rate_p", "false_merge_rate_q", "valid_erasures")},
            **{k: v for k, v in M.summarize_probes(r["probes"]).items() if k in ("stale_exposure", "clean_retrieval", "ndcg5", "harmful5")},
            "coverage_case": M.summarize_writes(cov),
            "paraphrase_surfaces": M.summarize_writes(para),
        }
    res = {"review": {k: v for k, v in rv.items() if k != "log"}, "results": rows, "review_log": rv["log"]}
    if out:
        o = Path(out)
        o.mkdir(parents=True, exist_ok=True)
        (o / "evolution.json").write_text(json.dumps(res, indent=2))
    return res


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="results/evolution")
    a = ap.parse_args()
    r = run(out=a.out)
    print(json.dumps(r["review"], indent=1))
    for k, v in r["results"].items():
        print(k, {kk: vv for kk, vv in v.items() if kk not in ("coverage_case", "paraphrase_surfaces")})
        print("   coverage case:", {kk: v["coverage_case"][kk] for kk in ("missed_merge_rate_p", "false_merge_rate_q")},
              " paraphrases:", {kk: v["paraphrase_surfaces"][kk] for kk in ("missed_merge_rate_p", "false_merge_rate_q")})


if __name__ == "__main__":
    main()
