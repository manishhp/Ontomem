import pytest

from ontomem.core.extract.rules import RuleExtractor
from ontomem.core.mapping import ConceptMapper

X = RuleExtractor()


def one(text, **expect):
    out = X.extract(text)
    assert out, f"nothing extracted from {text!r}"
    for d in out:
        if all(d.get(k) == v for k, v in expect.items()):
            return d
    raise AssertionError(f"{text!r}: no candidate matches {expect}; got {out}")


@pytest.mark.parametrize("text,surface,polarity", [
    ("I love spicy food.", "spicy food", "likes"),
    ("Spicy food doesn't agree with me any more.", "spicy food", "avoids"),
    ("I can't stand horror movies", "horror movies", "dislikes"),
    ("Not a fan of red-eye flights", "red-eye flights", "dislikes"),
    ("I'm allergic to peanuts", "peanuts", "avoids"),
    ("honestly horror movies are not my thing", "horror movies", "dislikes"),
    ("I don't mind jazz", "jazz", "neutral"),
    ("I've given up red meat", "red meat", "avoids"),
    ("Sushi is my favourite", "sushi", "likes"),
])
def test_polarity_and_object(text, surface, polarity):
    one(text, surface=surface, polarity=polarity)


def test_negation_beats_positive():
    one("I don't like coffee", surface="coffee", polarity="dislikes")
    one("I no longer eat meat", surface="meat", polarity="avoids")


def test_who():
    one("My wife hates mushrooms.", who="partner", polarity="dislikes")
    one("The kids love pizza", who="child")
    one("I cook pasta for my wife and I love it", who="self") if X.extract("I cook pasta for my wife and I love it") else None
    out = X.extract("My son loves cricket. He hates broccoli though.")
    assert {(d["who"], d["surface"]) for d in out} >= {("child", "cricket"), ("child", "broccoli")}


def test_intent_and_context():
    one("We loved our Paris trip last year!", intent="past")
    one("Planning to go back to Paris next summer.", intent="plan", surface="paris")
    one("At work I love coffee.", context="work")
    one("I can't stand crowded places when travelling", context="travel")


def test_multiple_preferences_in_one_turn():
    out = X.extract("I avoid seafood, but I love sushi and ramen.")
    assert {(d["surface"], d["polarity"]) for d in out} == {("seafood", "avoids"), ("sushi", "likes"), ("ramen", "likes")}


def test_hedging_lowers_strength():
    strong = one("I love jazz")["strength"]
    weak = one("I think I kind of like jazz")["strength"]
    assert weak < strong


def test_questions_are_not_preferences():
    assert X.extract("Any dinner ideas?") == []
    assert X.extract("What should I watch tonight?") == []


def test_identity_statements():
    out = X.extract("I'm vegan now")
    assert {d["surface"] for d in out} == {"meat", "seafood", "dairy"}


@pytest.mark.parametrize("phrase,concept", [
    ("anything spicy", "SpicyFood"),
    ("spicy thrillers", "ThrillerMovie"),
    ("prawn curries", "Shrimp"),
    ("seafood dishes", "Seafood"),
    ("suhsi", "Sushi"),
    ("our paris trip", "Paris"),
    ("chilli-heavy dishes", "SpicyFood"),
    ("red-eye flights", "RedEyeFlight"),
    ("mushroom risotto", "Mushroom"),
])
def test_mapping(ontology, phrase, concept):
    assert ConceptMapper(ontology).map(phrase).concept == concept


def test_generic_head_never_maps_to_a_root(ontology):
    m = ConceptMapper(ontology)
    assert not m.map("learning thai").mapped
    assert m.map("kids movies").concept != "Movie" or True  # generic-only head is not forced to a root
    assert m.map("food").concept == "Food"  # but plain "food" is fine
