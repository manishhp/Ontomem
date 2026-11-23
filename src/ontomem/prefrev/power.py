"""Power analysis: how many paired observations does PrefRev need?

Systems are compared on the *same* items (paired binary outcomes, e.g. "this probe's
memory block exposes a stale value"), so the right test is McNemar's. Its power
depends on the discordant-pair probabilities p10 (A fails, B succeeds) and p01.
Normal approximation (Connor 1987):

    n = ( z_{1-a/2} sqrt(pd) + z_{1-b} sqrt(pd - d^2) )^2 / d^2,
    pd = p10 + p01 (discordant proportion),  d = p10 - p01 (effect).

    python -m ontomem.prefrev.power --effect 0.03 --discordant 0.10
"""

from __future__ import annotations

import argparse
import math

from scipy.stats import norm


def mcnemar_n(effect: float, discordant: float, alpha: float = 0.05, power: float = 0.8) -> int:
    if not 0 < abs(effect) <= discordant <= 1:
        raise ValueError("need 0 < |effect| <= discordant <= 1")
    za = norm.ppf(1 - alpha / 2)
    zb = norm.ppf(power)
    n = (za * math.sqrt(discordant) + zb * math.sqrt(discordant - effect**2)) ** 2 / effect**2
    return math.ceil(n)


def table(alpha: float = 0.05, power: float = 0.8) -> list[dict]:
    rows = []
    for effect in (0.02, 0.03, 0.05, 0.10):
        for disc in (0.05, 0.10, 0.20):
            if effect <= disc:
                rows.append({"effect": effect, "discordant": disc, "n": mcnemar_n(effect, disc, alpha, power)})
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--effect", type=float, default=0.03)
    ap.add_argument("--discordant", type=float, default=0.10)
    ap.add_argument("--alpha", type=float, default=0.05)
    ap.add_argument("--power", type=float, default=0.8)
    a = ap.parse_args()
    n = mcnemar_n(a.effect, a.discordant, a.alpha, a.power)
    print(f"detect a {a.effect:.0%} difference with {a.discordant:.0%} discordant pairs "
          f"(alpha={a.alpha}, power={a.power}): need {n} paired observations")
    for r in table(a.alpha, a.power):
        print(f"  effect {r['effect']:.0%}  discordant {r['discordant']:.0%}  ->  n = {r['n']}")


if __name__ == "__main__":
    main()
