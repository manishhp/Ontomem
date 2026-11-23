"""Sanity checks on a generated PrefRev release (run in CI and before every experiment).

    python -m ontomem.prefrev.validate src/ontomem/data/prefrev
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

from .generator import extended_ontology, load
from .templates import FIRST


def validate(path: str | Path) -> list[str]:
    errors: list[str] = []
    onto = extended_ontology()
    splits = {s: load(path, s) for s in ("train", "dev", "test")}

    # 1. template sets are disjoint and used by the right splits
    a = {t for v in FIRST.values() for t in v["A"]}
    b = {t for v in FIRST.values() for t in v["B"]}
    if a & b:
        errors.append(f"template sets overlap: {sorted(a & b)[:3]}")
    for s, eps in splits.items():
        want = "B" if s == "test" else "A"
        if any(e["template_set"] != want for e in eps):
            errors.append(f"{s}: wrong template set")

    # 2. no test sentence appears verbatim in train (template + surface collisions are allowed for noise)
    train_text = {t["text"] for e in splits["train"] for t in e["turns"] if t["case"] != "noise"}
    leaks = [t["text"] for e in splits["test"] for t in e["turns"] if t["case"] != "noise" and t["text"] in train_text]
    if leaks:
        errors.append(f"{len(leaks)} test sentences also occur in train, e.g. {leaks[:2]}")

    for s, eps in splits.items():
        ids = Counter()
        for e in eps:
            ts_prev = ""
            for t in e["turns"]:
                ids[t["turn_id"]] += 1
                if t["ts"] <= ts_prev:
                    errors.append(f"{t['turn_id']}: timestamps not strictly increasing")
                ts_prev = t["ts"]
                for g in t["gold"]:
                    # 3. gold concepts exist in the gold ontology, keys are well-formed
                    if g["concept"] not in onto:
                        errors.append(f"{t['turn_id']}: unknown gold concept {g['concept']}")
                    if g["gold_key"].count("|") != 4:
                        errors.append(f"{t['turn_id']}: malformed gold key")
                    # 4. supersession only within a slot
                    if g["supersedes_turn"]:
                        prev = next((x for x in e["turns"] if x["turn_id"] == g["supersedes_turn"]), None)
                        if prev is None or g["gold_key"] not in {pg["gold_key"] for pg in prev["gold"]}:
                            errors.append(f"{t['turn_id']}: supersedes a turn from another slot")
            # 5. probes reference the past only
            for p in e["probes"]:
                if p["after_turn"] >= len(e["turns"]):
                    errors.append(f"{p['probe_id']}: probe after the end of the episode")
        dup = [k for k, v in ids.items() if v > 1]
        if dup:
            errors.append(f"{s}: duplicate turn ids {dup[:3]}")
        # 6. every case type is represented
        cases = Counter(t["case"] for e in eps for t in e["turns"])
        for c in ("replace", "exception", "broad_override", "intent", "context", "trap", "third_party", "price", "coverage"):
            if cases[c] == 0:
                errors.append(f"{s}: no {c} cases")
    return errors


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("path", nargs="?", default="src/ontomem/data/prefrev")
    errs = validate(ap.parse_args().path)
    if errs:
        print("PrefRev validation FAILED:")
        for e in errs[:50]:
            print("  -", e)
        sys.exit(1)
    print("PrefRev validation passed.")


if __name__ == "__main__":
    main()
