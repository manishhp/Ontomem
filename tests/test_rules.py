"""One test per conflict rule, using the examples from the project slides."""

from conftest import active_view, say


def rules_fired(trace):
    return [f["rule"] for c in trace.candidates if c.outcome for f in c.outcome["firings"]]


def test_r1_supersede_spicy_food(engine, clock):
    t1, t2 = say(engine, "u", clock, "I love spicy food.", "Spicy food doesn't agree with me any more.")
    assert rules_fired(t1) == ["NEW"]
    assert "R1" in rules_fired(t2)
    mem = engine.memory("u")
    active = mem.active()
    assert len(active) == 1 and active[0].polarity.value == "avoids"
    old = mem.history()[0]
    assert old.polarity.value == "likes" and old.superseded_by == active[0].id and old.valid_to is not None


def test_r5_reinforce_does_not_create_slot(engine, clock):
    say(engine, "u", clock, "I like jazz.", "I really enjoy jazz.", "Jazz is my favourite.")
    mem = engine.memory("u")
    assert len(mem.nodes) == 1
    n = next(iter(mem.nodes.values()))
    assert len(n.evidence) == 3 and n.strength > 0.85


def test_r2_exception_keeps_both_and_specific_wins(engine, clock):
    _, t2 = say(engine, "u", clock, "I avoid seafood.", "But I love sushi!")
    assert "R2" in rules_fired(t2)
    view = active_view(engine, "u")
    assert view[("self", "Seafood", "standing", "any", "general")] == "avoids"
    assert view[("self", "Sushi", "standing", "any", "general")] == "likes"
    mem = engine.memory("u")
    st = engine.retriever.stance(mem, "Sushi")
    assert st.polarity.value == "likes"
    assert engine.retriever.stance(mem, "Shrimp").polarity.value == "avoids"
    names = [r["item"]["name"] for r in engine.ask("u", "any dinner ideas?")["recommendations"]]
    assert any("nigiri" in n or "maki" in n for n in names)
    assert not any("Prawn" in n for n in names)


def test_r3_broad_override_flags_and_clarifies(engine, clock):
    say(engine, "u", clock, "I avoid seafood.", "I love sushi.")
    (t3,) = say(engine, "u", clock, "I stay away from seafood completely now.")
    assert "R3" in rules_fired(t3)
    mem = engine.memory("u")
    sushi = next(n for n in mem.active() if n.key.what == "Sushi")
    assert sushi.id in mem.needs_confirmation
    # while unconfirmed, the broader statement applies
    assert engine.retriever.stance(mem, "Sushi").polarity.value == "avoids"
    q = engine.clarifications("u")
    assert q and "sushi" in q[0]["question"].lower()
    # user confirms the exception still holds -> flag clears, sushi is liked again
    engine.resolve_clarification("u", sushi.id, keep_exception=True, ts=clock(1))
    mem = engine.memory("u")
    assert not mem.needs_confirmation
    assert engine.retriever.stance(mem, "Sushi").polarity.value == "likes"


def test_r3_clarification_drop(engine, clock):
    say(engine, "u", clock, "I avoid seafood.", "I love sushi.", "I avoid seafood.")
    sushi = next(n for n in engine.memory("u").active() if n.key.what == "Sushi")
    engine.resolve_clarification("u", sushi.id, keep_exception=False, ts=clock(1))
    mem = engine.memory("u")
    assert engine.retriever.stance(mem, "Sushi").polarity.value == "avoids"
    assert not mem.needs_confirmation


def test_r4_different_intent_no_conflict(engine, clock):
    _, t2 = say(engine, "u", clock, "We loved our Paris trip last year!", "Planning to go back to Paris next summer.")
    assert "R4" in rules_fired(t2)
    view = active_view(engine, "u")
    assert view[("self", "Paris", "past", "any", "general")] == "likes"
    assert view[("self", "Paris", "plan", "any", "general")] == "likes"


def test_r4_different_context_is_its_own_slot(engine, clock):
    say(engine, "u", clock, "I don't drink coffee at home.", "At work I love coffee.")
    view = active_view(engine, "u")
    assert view[("self", "Coffee", "standing", "home", "general")] == "avoids"
    assert view[("self", "Coffee", "standing", "work", "general")] == "likes"
    mem = engine.memory("u")
    assert engine.retriever.stance(mem, "Coffee", context="work").polarity.value == "likes"
    assert engine.retriever.stance(mem, "Coffee", context="home").polarity.value == "avoids"


def test_r6_third_party_never_touches_holder(engine, clock):
    say(engine, "u", clock, "I love mushrooms.")
    (t2,) = say(engine, "u", clock, "My wife hates mushrooms.")
    assert "R6" in rules_fired(t2) and "R1" not in rules_fired(t2)
    view = active_view(engine, "u")
    assert view[("self", "Mushroom", "standing", "any", "general")] == "likes"
    assert view[("partner", "Mushroom", "standing", "any", "general")] == "dislikes"
    mem = engine.memory("u")
    assert engine.retriever.stance(mem, "Mushroom").polarity.value == "likes"
    assert engine.retriever.stance(mem, "Mushroom", who="partner").polarity.value == "dislikes"


def test_false_merge_trap_spicy_thrillers(engine, clock):
    say(engine, "u", clock, "Spicy food doesn't agree with me.", "I'm really into spicy thrillers lately.")
    view = active_view(engine, "u")
    assert view[("self", "SpicyFood", "standing", "any", "general")] == "avoids"
    assert view[("self", "ThrillerMovie", "standing", "any", "general")] == "likes"


def test_price_aspect_is_separate_slot(engine, clock):
    say(engine, "u", clock, "I love sushi.", "Sushi is way too expensive though.")
    view = active_view(engine, "u")
    assert view[("self", "Sushi", "standing", "any", "general")] == "likes"
    assert view[("self", "Sushi", "standing", "any", "price")] == "dislikes"


def test_time_travel(engine, clock):
    say(engine, "u", clock, "I love spicy food.")
    middle = clock(3)
    say(engine, "u", clock, "Spicy food doesn't agree with me any more.")
    then = engine.memory("u", as_of=middle)
    assert [n.polarity.value for n in then.active()] == ["likes"]
    assert [n.polarity.value for n in engine.memory("u").active()] == ["avoids"]


def test_erase_user(engine, clock):
    say(engine, "u", clock, "I love sushi.", "I hate horror movies.")
    assert engine.erase("u") == 2
    assert engine.memory("u").active() == []


def test_r3_fires_when_statements_share_a_timestamp(engine):
    """Same-second messages (fast MCP calls, the chat's minute-precision date picker) are ordered by the
    event stream, so a later broad statement still flags the exception and an answer still clears it."""
    from datetime import datetime

    t = datetime(2026, 5, 1, 19, 0)
    engine.observe("u", "I avoid seafood, but I love sushi.", t)
    engine.observe("u", "No seafood at all for me now.", t)
    mem = engine.memory("u")
    sushi = next(n for n in mem.active() if n.key.what == "Sushi")
    assert sushi.id in mem.needs_confirmation
    engine.resolve_clarification("u", sushi.id, keep_exception=True, ts=t)
    assert not engine.memory("u").needs_confirmation
    # and replay of the event log reproduces the same state
    assert engine.replay("u").needs_confirmation == set()
