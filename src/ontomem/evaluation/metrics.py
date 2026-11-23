"""Metric definitions. Every headline number is computed from gold keys - no LLM judge.

Write-level (one record per candidate a system writes)
------------------------------------------------------
Each memory node "belongs" to the gold slot of the item that created it.

* **missed-merge rate p** - over *revisions* (an item whose gold slot already has a
  value, and whose value differs) where the system still had an active node for that
  slot: the share where the system did NOT merge into that slot's node. A missed
  revision leaves the outdated value active (staleness).
* **false-merge rate q** - over all writes: the share merged into a node of a
  DIFFERENT gold slot (or a spurious node). A false *supersede* retires a valid
  value (valid erasure); a false *reinforce* silently swallows the new statement.
* **valid erasures** - count of false supersedes.

Probe-level (the memory block before generation, as in the base paper)
---------------------------------------------------------------------
Retrieval is held at oracle *topic* relevance so differences come only from the
update step: the block for a probe is every active node whose system-side who /
intent / context make it apply to the probe and whose gold domain is the probe's.

* **stale exposure** - block contains a node whose value is no longer the gold
  current value of its slot.
* **contamination** - block contains a node about someone else / another intent /
  another context (misattribution), or a spurious node.
* **complete** - every gold-active slot for the probe is present with its current value.
* **clean retrieval** - complete AND no stale AND no contamination.

Downstream
----------
* **NDCG@5** of the system's ranking of catalog items, against relevance computed
  from the gold memory with the same reader.
* **harmful@5** - a top-5 suggestion that the gold memory vetoes (the user avoids it).
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any


@dataclass
class WriteRec:
    system: str
    episode: str
    item_id: str
    case: str
    tier: str
    surface_kind: str
    gold_key: str | None
    is_revision: bool
    is_reinforcement: bool
    eligible: bool  # system had an active node for this gold slot before the write
    decision: str
    target_gold: str | None
    correct_merge: bool
    false_merge: bool
    destructive: bool
    stage: str = ""  # error attribution (Mode B / S3)


@dataclass
class ProbeRec:
    system: str
    episode: str
    probe_id: str
    domain: str
    context: str
    who: str
    stale: bool
    contaminated: bool
    complete: bool
    clean: bool
    block_size: int
    ndcg5: float | None = None
    harmful5: bool | None = None
    details: dict[str, Any] = field(default_factory=dict)


def ndcg_at_k(ranked: list[str], rel: dict[str, int], k: int = 5) -> float:
    dcg = sum((2 ** rel.get(i, 0) - 1) / math.log2(r + 2) for r, i in enumerate(ranked[:k]))
    ideal = sorted(rel.values(), reverse=True)[:k]
    idcg = sum((2**g - 1) / math.log2(r + 2) for r, g in enumerate(ideal))
    return dcg / idcg if idcg > 0 else 1.0


def _rate(num: int, den: int) -> float | None:
    return round(num / den, 4) if den else None


def summarize_writes(recs: list[WriteRec]) -> dict[str, Any]:
    revisions = [r for r in recs if r.is_revision and r.eligible]
    reinf = [r for r in recs if r.is_reinforcement and r.eligible]
    writes = [r for r in recs if r.decision != "rejected"]
    fm = [r for r in writes if r.false_merge]
    return {
        "writes": len(writes),
        "revisions": len(revisions),
        "missed_merge_rate_p": _rate(sum(not r.correct_merge for r in revisions), len(revisions)),
        "missed_reinforce_rate": _rate(sum(not r.correct_merge for r in reinf), len(reinf)),
        "false_merge_rate_q": _rate(len(fm), len(writes)),
        "valid_erasures": sum(r.destructive for r in fm),
        "valid_erasure_rate": _rate(sum(r.destructive for r in fm), len(writes)),
        "rejected": sum(r.decision == "rejected" for r in recs),
    }


def summarize_probes(recs: list[ProbeRec]) -> dict[str, Any]:
    n = len(recs)
    nd = [r.ndcg5 for r in recs if r.ndcg5 is not None]
    hm = [r.harmful5 for r in recs if r.harmful5 is not None]
    return {
        "probes": n,
        "stale_exposure": _rate(sum(r.stale for r in recs), n),
        "contamination": _rate(sum(r.contaminated for r in recs), n),
        "completeness": _rate(sum(r.complete for r in recs), n),
        "clean_retrieval": _rate(sum(r.clean for r in recs), n),
        "mean_block_size": round(sum(r.block_size for r in recs) / n, 2) if n else None,
        "ndcg5": round(sum(nd) / len(nd), 4) if nd else None,
        "harmful5": _rate(sum(hm), len(hm)) if hm else None,
    }


def breakdown(recs: list[WriteRec], by: str) -> dict[str, dict[str, Any]]:
    groups: dict[str, list[WriteRec]] = defaultdict(list)
    for r in recs:
        groups[str(getattr(r, by))].append(r)
    return {k: summarize_writes(v) for k, v in sorted(groups.items())}


def stage_counts(recs: list[WriteRec]) -> dict[str, int]:
    out: dict[str, int] = defaultdict(int)
    for r in recs:
        if r.stage:
            out[r.stage] += 1
    return dict(out)
