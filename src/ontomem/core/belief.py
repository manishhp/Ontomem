"""Belief model: how strongly the memory holds a preference right now.

* **Initial strength** comes from the extractor (cue intensity, hedging).
* **Reinforcement** (rule R5) combines evidence with a damped noisy-OR, so repeated
  statements raise strength with diminishing returns and never exceed 1.
* **Time decay**: a standing preference that has not been confirmed for a long time is
  held with less confidence; plans expire faster; past experiences do not decay (they
  are facts about the past).

Strength is stored on the node (it is what the user said); confidence is derived at
read time (it is how much we should trust it today).
"""

from __future__ import annotations

from datetime import datetime

from .model import Intent, PreferenceNode

HALF_LIFE_DAYS = {
    Intent.STANDING.value: 365.0,
    Intent.PLAN.value: 120.0,
    Intent.PAST.value: float("inf"),
}
REINFORCE_DAMPING = 0.5
HEDGE_FACTOR = 0.6


def initial_strength(base: float, hedged: bool) -> float:
    s = base * (HEDGE_FACTOR if hedged else 1.0)
    return round(min(1.0, max(0.05, s)), 3)


def reinforce(old: float, new: float) -> float:
    """Damped noisy-OR: 0.7 reinforced by 0.7 -> 0.805; by 0.9 -> 0.835."""
    return round(1.0 - (1.0 - old) * (1.0 - REINFORCE_DAMPING * new), 3)


def confidence(node: PreferenceNode, now: datetime) -> float:
    last = node.last_confirmed or node.stated_at
    age_days = max(0.0, (now - last).total_seconds() / 86400.0)
    half_life = HALF_LIFE_DAYS.get(node.key.intent, 365.0)
    decay = 1.0 if half_life == float("inf") else 0.5 ** (age_days / half_life)
    return round(node.strength * decay, 3)
