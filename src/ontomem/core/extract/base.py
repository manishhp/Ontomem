"""Extractor interface.

An extractor turns one user turn into *raw candidates*: plain dicts with the fields
below. They are deliberately untyped - an LLM can return anything, and catching
malformed output is SHACL's job, not the extractor's.

    {"surface": "spicy food", "polarity": "avoids", "who": "self", "aspect": "general",
     "intent": "standing", "context": "any", "reason": "health", "strength": 0.85,
     "hedged": false, "source_text": "...", "stated_at": "2026-06-02T19:00:00"}
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

CANDIDATE_FIELDS = (
    "surface", "polarity", "who", "aspect", "intent", "context", "reason", "strength", "hedged",
)


class Extractor(ABC):
    name = "extractor"

    @abstractmethod
    def extract(self, text: str, stated_at: datetime | None = None, turn_id: str | None = None) -> list[dict[str, Any]]:
        """Return raw candidates for every preference stated in ``text``."""

    def repair(self, candidate: dict[str, Any], violations: list[dict[str, Any]], text: str) -> dict[str, Any] | None:
        """Fix a candidate that failed SHACL. Default: deterministic coercion of
        the offending fields; LLM extractors override this with a repair prompt."""
        return coerce(candidate, violations)


_SYNONYMS = {
    "polarity": {"love": "likes", "like": "likes", "positive": "likes", "hate": "dislikes", "dislike": "dislikes",
                 "negative": "dislikes", "avoid": "avoids", "never": "avoids", "ok": "neutral", "none": "neutral"},
    "intent": {"current": "standing", "present": "standing", "ongoing": "standing", "preference": "standing",
               "future": "plan", "goal": "plan", "planned": "plan", "maybe": "plan", "experience": "past",
               "experienced": "past", "history": "past"},
    "who": {"me": "self", "user": "self", "i": "self", "wife": "partner", "husband": "partner", "kid": "child",
            "kids": "child", "son": "child", "daughter": "child", "mom": "parent", "dad": "parent"},
    "context": {"anywhere": "any", "always": "any", "general": "any", "office": "work", "kids": "family",
                "trip": "travel", "holiday": "travel", "friends": "social"},
    "aspect": {"overall": "general", "taste": "general", "cost": "price", "money": "price", "time": "timing",
               "ambience": "setting", "place": "setting"},
}
_DEFAULTS = {"who": "self", "aspect": "general", "intent": "standing", "context": "any", "polarity": "neutral"}


def coerce(candidate: dict[str, Any], violations: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Deterministic repair: map synonyms onto the allowed values, clamp strength,
    fill missing defaults. Returns ``None`` when the candidate cannot be saved
    (e.g. no source text and no concept)."""
    c = dict(candidate)
    for v in violations:
        f = v["field"]
        val = c.get(f)
        if f in _SYNONYMS or f in _DEFAULTS:
            key = str(val).strip().lower() if val is not None else ""
            c[f] = _SYNONYMS.get(f, {}).get(key, _DEFAULTS.get(f) if key not in _SYNONYMS.get(f, {}).values() else key)
            if c[f] is None:
                return None
        elif f == "strength":
            try:
                x = float(str(val).strip().rstrip("%"))
                if x > 1:
                    x = x / 100 if x <= 100 else 1.0
                c[f] = min(1.0, max(0.0, x))
            except (TypeError, ValueError):
                c[f] = {"high": 0.9, "strong": 0.9, "medium": 0.6, "low": 0.3, "weak": 0.3}.get(str(val).lower(), 0.6)
        elif f == "reason":
            c[f] = "unspecified"
        elif f == "stated_at":
            return None  # only the caller knows the turn's time; the pipeline fills it before validation
        elif f == "concept":
            c["concept"] = None  # fall back to the unmapped label
            if not c.get("surface"):
                return None
        elif f == "source_text":
            return None
    return c
