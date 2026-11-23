"""Core data model: slot keys, extracted statements, preference nodes and traces.

Design decision D1 (see docs/formal_semantics.md): *polarity is the value, not part
of the key*. The key says which preference slot a statement is about; polarity and
strength are what the user currently thinks about it. Otherwise "I love spicy food"
and "spicy food doesn't agree with me" would land in different slots and the update
could never retire the old value.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class Polarity(str, Enum):
    LIKES = "likes"
    DISLIKES = "dislikes"
    AVOIDS = "avoids"  # hard constraint: never recommend
    NEUTRAL = "neutral"

    @property
    def sign(self) -> int:
        return {"likes": 1, "dislikes": -1, "avoids": -1, "neutral": 0}[self.value]


class Aspect(str, Enum):
    """Which facet of the concept the statement judges."""

    GENERAL = "general"  # the thing itself ("I love sushi")
    PRICE = "price"  # "sushi is too expensive for me"
    QUALITY = "quality"
    SETTING = "setting"  # ambience, location, crowding
    TIMING = "timing"


class Intent(str, Enum):
    """Temporal / modal stance of the statement."""

    STANDING = "standing"  # an ongoing preference
    PAST = "past"  # a past experience ("loved our Paris trip")
    PLAN = "plan"  # a future goal ("planning to go back to Paris")


class Context(str, Enum):
    """Situation the preference applies to. ``ANY`` is broader than every other value."""

    ANY = "any"
    HOME = "home"
    WORK = "work"
    TRAVEL = "travel"
    SOCIAL = "social"
    FAMILY = "family"

    def subsumed_by(self, other: Context) -> bool:
        """``self ⊑ other``: a preference stated for ``other`` also covers ``self``."""
        return other is Context.ANY or self is other


class Reason(str, Enum):
    """Why the user holds the preference. Not part of the key."""

    TASTE = "taste"
    HEALTH = "health"
    ALLERGY = "allergy"
    ETHICS = "ethics"
    RELIGION = "religion"
    BUDGET = "budget"
    UNSPECIFIED = "unspecified"


class RawValue(str):
    """An unvalidated field value (only exists when SHACL validation is switched off)."""

    @property
    def value(self) -> str:
        return str(self)

    @property
    def sign(self) -> int:
        return 0

    def subsumed_by(self, other: object) -> bool:
        return str(other) == "any" or str(other) == str(self)


SELF = "self"
ROLES = ("self", "partner", "child", "parent", "friend", "colleague", "sibling")


@dataclass(frozen=True, order=True)
class SlotKey:
    """The 5-part key ``who · what · aspect · intent · context``.

    Two statements compete for the same slot iff their keys are equal.
    """

    who: str
    what: str
    aspect: str = Aspect.GENERAL.value
    intent: str = Intent.STANDING.value
    context: str = Context.ANY.value

    def __str__(self) -> str:
        return f"{self.who}·{self.what}·{self.aspect}·{self.intent}·{self.context}"

    def frame(self) -> tuple[str, str, str]:
        """Everything except *what* and *context*: the dimensions along which
        hierarchy-based relations (exception / broad override) are allowed."""
        return (self.who, self.aspect, self.intent)


@dataclass
class Statement:
    """One preference extracted from a user turn, before keying.

    ``concept`` is a full concept IRI once mapped, or ``None`` when the surface
    phrase is not in the ontology (it then lives under an ``unmapped:`` key).
    """

    surface: str  # concept phrase as the user said it, e.g. "anything spicy"
    polarity: Polarity
    who: str = SELF
    aspect: Aspect = Aspect.GENERAL
    intent: Intent = Intent.STANDING
    context: Context = Context.ANY
    reason: Reason = Reason.UNSPECIFIED
    strength: float = 0.7
    hedged: bool = False
    concept: str | None = None
    mapping_score: float = 0.0
    mapping_method: str = "none"
    source_text: str = ""
    stated_at: datetime | None = None
    turn_id: str | None = None
    extractor: str = "unknown"

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        for k in ("polarity", "aspect", "intent", "context", "reason"):
            d[k] = getattr(self, k).value
        d["stated_at"] = self.stated_at.isoformat() if self.stated_at else None
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any], strict: bool = True) -> Statement:
        """Parse a candidate. ``strict=False`` is only used by the -SHACL ablation: invalid
        values are kept verbatim (as :class:`RawValue`) instead of raising, which is what a
        schema-less memory does - the garbage then ends up inside slot keys."""
        d = dict(d)

        def enum(e, k, default):
            v = d.get(k, default)
            v = default if v is None else v
            try:
                return e(v)
            except ValueError:
                if strict:
                    raise
                return RawValue(str(v))

        d["polarity"] = enum(Polarity, "polarity", "neutral")
        d["aspect"] = enum(Aspect, "aspect", "general")
        d["intent"] = enum(Intent, "intent", "standing")
        d["context"] = enum(Context, "context", "any")
        d["reason"] = enum(Reason, "reason", "unspecified")
        if d.get("who") is None:
            d["who"] = SELF
        if not strict:
            try:
                d["strength"] = float(d.get("strength", 0.5))
            except (TypeError, ValueError):
                d["strength"] = 0.5
            if isinstance(d.get("stated_at"), str):
                try:
                    datetime.fromisoformat(d["stated_at"])
                except ValueError:
                    d["stated_at"] = None
        if d.get("stated_at") and isinstance(d["stated_at"], str):
            d["stated_at"] = datetime.fromisoformat(d["stated_at"])
        allowed = cls.__dataclass_fields__.keys()
        return cls(**{k: v for k, v in d.items() if k in allowed})


class Status(str, Enum):
    ACTIVE = "active"
    RETIRED = "retired"


@dataclass
class PreferenceNode:
    """A reified preference: the RDF node that carries a value and its metadata."""

    id: str
    key: SlotKey
    polarity: Polarity
    strength: float
    reason: Reason
    surface: str
    source_text: str
    stated_at: datetime
    valid_from: datetime
    status: Status = Status.ACTIVE
    valid_to: datetime | None = None
    superseded_by: str | None = None
    supersedes: str | None = None
    evidence: list[str] = field(default_factory=list)  # turn ids that support this value
    evidence_times: list[datetime] = field(default_factory=list)
    last_confirmed: datetime | None = None
    last_order: int = 0  # position of the latest supporting statement in the event stream (tie-break for equal times)
    created_by_rule: str = "NEW"
    extractor: str = "unknown"
    ontology_version: str = ""
    mapped: bool = True

    @property
    def active(self) -> bool:
        return self.status is Status.ACTIVE

    @property
    def concept(self) -> str:
        return self.key.what

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "key": asdict(self.key),
            "key_str": str(self.key),
            "polarity": self.polarity.value,
            "strength": round(self.strength, 3),
            "reason": self.reason.value,
            "surface": self.surface,
            "source_text": self.source_text,
            "stated_at": self.stated_at.isoformat(),
            "valid_from": self.valid_from.isoformat(),
            "valid_to": self.valid_to.isoformat() if self.valid_to else None,
            "status": self.status.value,
            "superseded_by": self.superseded_by,
            "supersedes": self.supersedes,
            "evidence": list(self.evidence),
            "last_confirmed": self.last_confirmed.isoformat() if self.last_confirmed else None,
            "created_by_rule": self.created_by_rule,
            "extractor": self.extractor,
            "ontology_version": self.ontology_version,
            "mapped": self.mapped,
        }


@dataclass
class RuleFiring:
    """One rule application, recorded for explanations and the UI pipeline inspector."""

    rule: str  # R1..R6, NEW
    message: str
    nodes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"rule": self.rule, "message": self.message, "nodes": self.nodes}


RULE_NAMES = {
    "NEW": "New slot",
    "R1": "Supersede",
    "R2": "Exception (specific beats general)",
    "R3": "Broad override → needs confirmation",
    "R4": "Orthogonal (different intent/context/aspect)",
    "R5": "Reinforce",
    "R6": "Third-party (separate subject)",
}
