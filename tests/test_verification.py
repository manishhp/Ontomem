"""Cross-checks between independent implementations."""

import random

from hypothesis import given, settings
from hypothesis import strategies as st

from ontomem.core.asp.crosscheck import crosscheck
from ontomem.core.validation import NativeValidator, ShaclValidator


def test_python_reasoner_matches_asp_encoding(ontology):
    res = crosscheck(n_sequences=400, seed=11, ontology=ontology)
    assert res["disagreements"] == 0, res["failures"][:1]
    assert res["stats"]["supersessions"] > 100 and res["stats"]["exceptions"] > 100 and res["stats"]["flags"] > 50


def test_crosscheck_detects_a_planted_bug(ontology, monkeypatch):
    """The differential test must be able to fail: break R5 in Python and expect disagreements."""
    from ontomem.core import reasoner as R

    orig = R.Reasoner.write

    def buggy(self, mem, stmt, concept, node_id, ts, ontology_version=""):
        out = orig(self, mem, stmt, concept, node_id, ts, ontology_version)
        mem.needs_confirmation.clear()  # "forget" every R3 flag
        return out

    monkeypatch.setattr(R.Reasoner, "write", buggy)
    res = crosscheck(n_sequences=200, seed=3, ontology=ontology)
    assert res["disagreements"] > 0


VALUES = {
    "who": ["self", "partner", "child", "wife", None, 7],
    "aspect": ["general", "price", "taste", None],
    "intent": ["standing", "past", "plan", "maybe", None],
    "context": ["any", "work", "office", None],
    "polarity": ["likes", "avoids", "love", None],
    "reason": ["health", "unspecified", "vibes", None],
    "strength": [0.0, 0.5, 1.0, 1.2, -0.1, "high", "0.4", True, None],
    "stated_at": ["2026-03-02T19:00:00", "yesterday", None],
    "source_text": ["I love it", "", None],
    "concept": ["Sushi", "NotAConcept", "unmapped:x", None],
    "surface": ["sushi", "", None],
}
candidate = st.fixed_dictionaries({k: st.sampled_from(v) for k, v in VALUES.items()})


@settings(max_examples=300, deadline=None)
@given(candidate)
def test_native_validator_agrees_with_pyshacl(c):
    from ontomem.core.ontology import default_ontology

    o = default_ontology()
    c = {k: v for k, v in c.items() if v is not None}
    a, b = NativeValidator(o).validate(c), ShaclValidator(o).validate(c)
    assert a.conforms == b.conforms, (c, a.to_dict(), b.to_dict())


def test_shacl_rejects_and_repair_fixes(ontology):
    from ontomem.core.extract.base import coerce

    bad = {"surface": "sushi", "concept": "Sushi", "who": "me", "aspect": "taste", "intent": "future",
           "context": "office", "polarity": "love", "strength": "high", "stated_at": "2026-03-02T19:00:00",
           "source_text": "I love sushi"}
    v = ShaclValidator(ontology)
    rep = v.validate(bad)
    assert not rep.conforms and {"who", "intent", "polarity", "strength", "context"} <= rep.fields()
    fixed = coerce(bad, [x.to_dict() for x in rep.violations])
    assert v.validate(fixed).conforms
    assert fixed["who"] == "self" and fixed["intent"] == "plan" and fixed["polarity"] == "likes" and fixed["strength"] == 0.9


def test_random_seed_independence(ontology):
    r = random.Random(0)
    assert crosscheck(n_sequences=50, seed=r.randint(0, 10_000), ontology=ontology)["disagreements"] == 0
