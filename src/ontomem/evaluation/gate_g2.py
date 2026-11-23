"""Decision gate G2 (Week 6): does the pilot show real merge errors, and is ontology coverage adequate?

Runs on the DEV split only (the test split stays untouched until the pre-registered run).

    python -m ontomem.evaluation.gate_g2
"""

from __future__ import annotations

import sys

from .runner import evaluate

MIN_BASELINE_ERRORS = 0.10  # the problem must exist: baseline missed+false merges on revisions
MIN_COVERAGE = 0.85  # share of statements the ontology maps to a concept


def main() -> None:
    s = evaluate("A", "dev", ["S1", "S3"], bootstrap=0, downstream=False)
    rows = {r["system"]: r for r in s["table"]}
    base = rows["S1-rule-surface"]
    onto = rows["S3-OntoMem"]
    baseline_err = (base["missed_merge_rate_p"] or 0) + (base["false_merge_rate_q"] or 0)
    from ..core.mapping import ConceptMapper
    from ..core.ontology import default_ontology
    from ..prefrev.generator import load

    m = ConceptMapper(default_ontology())
    stm = [g for e in load("src/ontomem/data/prefrev", "dev") for t in e["turns"] for g in t["gold"]]
    coverage = sum(m.map(g["surface"]).mapped for g in stm) / len(stm)
    checks = {
        f"baseline merge errors >= {MIN_BASELINE_ERRORS:.0%} (got {baseline_err:.1%})": baseline_err >= MIN_BASELINE_ERRORS,
        f"ontology coverage >= {MIN_COVERAGE:.0%} (got {coverage:.1%})": coverage >= MIN_COVERAGE,
        f"OntoMem beats baseline on clean retrieval ({onto['clean_retrieval']:.1%} vs {base['clean_retrieval']:.1%})":
            (onto["clean_retrieval"] or 0) > (base["clean_retrieval"] or 0),
    }
    for k, v in checks.items():
        print(("PASS  " if v else "FAIL  ") + k)
    ok = all(checks.values())
    print("\nGate G2:", "PASSED" if ok else "FAILED - narrow scope to conflict-type detection (fallback plan)")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
