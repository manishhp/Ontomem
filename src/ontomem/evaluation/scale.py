"""RQ6: does OntoMem meet serving deadlines as memory grows?

Writes N random statements for one user (spread over every ontology concept, with
who/intent/context/aspect variation so the memory really holds ~N slots), then
measures write latency and read-path latency (parse + recall + rank + explain).

    python -m ontomem.evaluation.scale --sizes 100 1000 10000 --out results/scale
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import time
from datetime import datetime, timedelta
from pathlib import Path

from ..core.engine import OntoMemEngine
from ..core.model import Aspect, Context, Intent, Polarity, Reason, Statement
from ..core.ontology import default_ontology

QUERIES = ["Any dinner ideas?", "What should I watch tonight?", "Any trip ideas?", "Recommend a book.",
           "What should I buy next?", "Make me a playlist.", "What should I get for a work lunch?"]
DEADLINE_MS = 50.0


def pctl(xs: list[float], q: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * len(xs)))]


def run(size: int, seed: int = 0) -> dict:
    rng = random.Random(seed)
    onto = default_ontology()
    concepts = list(onto.concepts)
    eng = OntoMemEngine(ontology=onto)  # in-memory projection; the event log is measured separately in the API
    t0 = datetime(2024, 1, 1)
    writes = []
    for i in range(size):
        c = rng.choice(concepts)
        st = Statement(surface=onto.label(c), polarity=rng.choice(list(Polarity)),
                       who=rng.choices(["self", "partner", "child", "friend"], [6, 2, 1, 1])[0],
                       aspect=rng.choices(list(Aspect), [8, 1, 1, 1, 1])[0],
                       intent=rng.choices(list(Intent), [8, 1, 1])[0],
                       context=rng.choices(list(Context), [6, 1, 1, 1, 1, 1])[0], reason=Reason.UNSPECIFIED,
                       strength=0.7, stated_at=t0 + timedelta(minutes=10 * i), turn_id=f"t{i}")
        a = time.perf_counter()
        eng.write_statement("u", st, c)
        writes.append((time.perf_counter() - a) * 1000)
    mem = eng.memory("u")
    reads = []
    for _ in range(60):
        q = rng.choice(QUERIES)
        a = time.perf_counter()
        eng.ask("u", q)
        reads.append((time.perf_counter() - a) * 1000)
    return {
        "statements": size, "active_slots": len(mem.active_by_key), "nodes": len(mem.nodes),
        "write_ms_p50": round(pctl(writes, 0.5), 3), "write_ms_p95": round(pctl(writes, 0.95), 3),
        "read_ms_p50": round(statistics.median(reads), 2), "read_ms_p95": round(pctl(reads, 0.95), 2),
        "read_ms_p99": round(pctl(reads, 0.99), 2), "deadline_ms": DEADLINE_MS,
        "deadline_miss_rate": round(sum(r > DEADLINE_MS for r in reads) / len(reads), 3),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sizes", type=int, nargs="+", default=[100, 1000, 10000])
    ap.add_argument("--out", default="results/scale")
    a = ap.parse_args()
    rows = [run(n) for n in a.sizes]
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "scale.json").write_text(json.dumps(rows, indent=2))
    for r in rows:
        print(r)


if __name__ == "__main__":
    main()
