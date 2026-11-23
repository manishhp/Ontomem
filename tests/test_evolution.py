from datetime import datetime

from ontomem.core.engine import OntoMemEngine
from ontomem.core.events import EventStore
from ontomem.core.ontology import default_ontology
from ontomem.evolve import Evolver


def test_review_trigger_and_proposals():
    ev = Evolver(default_ontology().copy())
    assert ev.needs_review("kombucha")[0]
    assert ev.needs_review("cosy mysteries")[0]  # head match with an unexplained modifier
    assert not ev.needs_review("sushi")[0]
    props = ev.propose("bubble tea")
    assert props and props[0].parent == "Tea" and props[0].checks["ok"]


def test_apply_bumps_version_and_rejects_bad_proposals():
    onto = default_ontology().copy()
    ev = Evolver(onto)
    p = ev.propose("bubble tea")[0]
    v = ev.apply(p)
    assert v == "1.1.0" and "BubbleTea" in onto and onto.narrower_than("BubbleTea", "Beverage")
    assert ev.mapper.map("bubble tea").concept == "BubbleTea"
    dup = ev.propose("bubble tea")
    assert all(not x.checks.get("ok") for x in dup if x.concept_id == "BubbleTea")


def test_rekey_after_evolution_merges_old_and_new_statements():
    onto = default_ontology().copy()
    eng = OntoMemEngine(ontology=onto, store=EventStore())
    eng.observe("u", "I love kombucha.", datetime(2026, 1, 1))
    assert eng.memory("u").active()[0].key.what == "unmapped:kombucha"
    ev = Evolver(onto)
    p = ev.propose("kombucha", ["Tea"])
    p = next(x for x in p if x.parent in ("Beverage", "Tea"))
    ev.apply(p)
    eng.refresh_ontology()
    mem = eng.memory("u")
    assert mem.active()[0].key.what == "Kombucha"
    eng.observe("u", "Kombucha doesn't agree with me any more.", datetime(2026, 2, 1))
    mem = eng.memory("u")
    assert len(mem.active()) == 1 and mem.active()[0].polarity.value == "avoids"
    # time travel before the re-key still shows the original value
    assert eng.memory("u", as_of=datetime(2026, 1, 2)).active()[0].polarity.value == "likes"
