"""Statistics: cluster bootstrap CIs, McNemar tests, Holm correction, effect sizes.

Probes from the same user are correlated (one bad write can spoil several probes),
so confidence intervals resample whole *episodes* (cluster bootstrap), not probes.
"""

from __future__ import annotations

import math
import random
from collections import defaultdict
from collections.abc import Callable, Sequence
from typing import Any

from scipy.stats import binomtest, chi2


def cluster_bootstrap(records: Sequence[Any], cluster: Callable[[Any], str], stat: Callable[[list[Any]], float | None],
                      b: int = 1000, seed: int = 0, alpha: float = 0.05) -> tuple[float | None, float | None, float | None]:
    groups: dict[str, list[Any]] = defaultdict(list)
    for r in records:
        groups[cluster(r)].append(r)
    keys = sorted(groups)
    point = stat(list(records))
    if point is None or not keys:
        return point, None, None
    rng = random.Random(seed)
    vals = []
    for _ in range(b):
        sample: list[Any] = []
        for _k in keys:
            sample.extend(groups[keys[rng.randrange(len(keys))]])
        v = stat(sample)
        if v is not None:
            vals.append(v)
    vals.sort()
    lo = vals[int(alpha / 2 * len(vals))]
    hi = vals[min(len(vals) - 1, int((1 - alpha / 2) * len(vals)))]
    return point, lo, hi


def mcnemar(a: Sequence[bool], b: Sequence[bool]) -> dict[str, Any]:
    """Paired test on the same items. ``a``/``b`` = success of system A / B per item."""
    if len(a) != len(b):
        raise ValueError("paired samples must have equal length")
    n10 = sum(1 for x, y in zip(a, b) if x and not y)
    n01 = sum(1 for x, y in zip(a, b) if y and not x)
    nd = n10 + n01
    if nd == 0:
        p = 1.0
    elif nd < 25:
        p = binomtest(n10, nd, 0.5).pvalue
    else:
        stat = (abs(n10 - n01) - 1) ** 2 / nd
        p = float(chi2.sf(stat, 1))
    return {"a_only": n10, "b_only": n01, "discordant": nd, "p_value": p,
            "odds_ratio": (n10 + 0.5) / (n01 + 0.5)}


def holm(pvals: dict[str, float]) -> dict[str, float]:
    """Holm-Bonferroni adjusted p-values."""
    items = sorted(pvals.items(), key=lambda kv: kv[1])
    m = len(items)
    out, running = {}, 0.0
    for i, (k, p) in enumerate(items):
        adj = min(1.0, (m - i) * p)
        running = max(running, adj)
        out[k] = running
    return out


def cohens_h(p1: float, p2: float) -> float:
    """Effect size for two proportions."""
    return 2 * math.asin(math.sqrt(p1)) - 2 * math.asin(math.sqrt(p2))
