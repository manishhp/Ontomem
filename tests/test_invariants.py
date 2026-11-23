"""Property-based tests of the invariants stated in docs/formal_semantics.md.

Hypothesis generates thousands of random statement histories (with deliberately
colliding slots) and checks that every invariant holds after every write.
"""

from datetime import datetime, timedelta

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from ontomem.core.engine import OntoMemEngine
from ontomem.core.events import EventStore
from ontomem.core.model import Aspect, Context, Intent, Polarity, Reason, Statement
from ontomem.core.ontology import default_ontology
from ontomem.core.rdf import memory_graph
from ontomem.core.reasoner import Reasoner, UserMemory
from ontomem.core.validation import validate_graph

ONTO = default_ontology()
CONCEPTS = ["Food", "Seafood", "Fish", "Salmon", "Shellfish", "Shrimp", "Sushi", "JapaneseCuisine",
            "SpicyFood", "Curry", "Meat", "unmapped:durian smoothie"]

statement = st.fixed_dictionaries({
    "what": st.sampled_from(CONCEPTS),
    "who": st.sampled_from(["self", "self", "self", "partner"]),
    "aspect": st.sampled_from(["general", "general", "price"]),
    "intent": st.sampled_from(["standing", "standing", "past", "plan"]),
    "context": st.sampled_from(["any", "any", "work", "travel"]),
    "pol": st.sampled_from(["likes", "dislikes", "avoids", "neutral"]),
})
histories = st.lists(statement, min_size=1, max_size=30)


def mk(e: dict, ts: datetime, tid: str) -> Statement:
    return Statement(surface=e["what"], polarity=Polarity(e["pol"]), who=e["who"], aspect=Aspect(e["aspect"]),
                     intent=Intent(e["intent"]), context=Context(e["context"]), reason=Reason.UNSPECIFIED,
                     strength=0.7, stated_at=ts, turn_id=tid)


def run(hist):
    r = Reasoner(ONTO)
    mem = UserMemory("x")
    t0 = datetime(2026, 1, 1)
    for i, e in enumerate(hist):
        ts = t0 + timedelta(hours=i)
        r.write(mem, mk(e, ts, f"t{i}"), e["what"], f"n{i}", ts)
        yield r, mem


def signature(mem: UserMemory):
    return (
        frozenset((n.id, str(n.key), n.polarity.value, n.status.value, n.superseded_by) for n in mem.nodes.values()),
        frozenset(mem.needs_confirmation),
    )


@settings(max_examples=400, deadline=None)
@given(histories)
def test_I1_key_uniqueness(hist):
    for _, mem in run(hist):
        active = mem.active()
        assert len({n.key for n in active}) == len(active)
        assert all(mem.nodes[i].active and mem.nodes[i].key == k for k, i in mem.active_by_key.items())


@settings(max_examples=400, deadline=None)
@given(histories)
def test_I2_no_silent_loss(hist):
    for _, mem in run(hist):
        pass
    # every statement is still in the graph, as a node or as evidence of one
    evidence = [e for n in mem.nodes.values() for e in n.evidence]
    assert sorted(evidence, key=lambda x: int(x[1:])) == [f"t{i}" for i in range(len(hist))]
    for n in mem.nodes.values():
        if not n.active:
            succ = mem.nodes[n.superseded_by]
            assert succ.key == n.key and n.valid_to is not None and succ.supersedes == n.id


@settings(max_examples=150, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(histories, st.randoms(use_true_random=False))
def test_I3_deterministic_replay(hist, rnd):
    """Inserting the same timestamped events in any order projects to the same graph."""
    t0 = datetime(2026, 1, 1)
    stamped = [(t0 + timedelta(hours=i), e, f"t{i}") for i, e in enumerate(hist)]

    def project(order):
        eng = OntoMemEngine(ontology=ONTO, store=EventStore())
        for ts, e, tid in order:
            eng.store.append("u", ts, "statement",
                             {"statement": mk(e, ts, tid).to_dict(), "concept": e["what"], "ontology_version": "t"})
        mem = eng.replay("u")
        eng.store.close()
        # node ids come from sequence numbers, so compare by the turn that created each node
        return (
            frozenset((n.evidence[0], str(n.key), n.polarity.value, n.status.value) for n in mem.nodes.values()),
            frozenset(mem.nodes[i].evidence[0] for i in mem.needs_confirmation),
        )

    shuffled = list(stamped)
    rnd.shuffle(shuffled)
    assert project(stamped) == project(shuffled)


@settings(max_examples=300, deadline=None)
@given(histories, st.sampled_from(["Salmon", "Shrimp", "Sushi"]), st.sampled_from(["likes", "avoids"]))
def test_I4_specificity_monotonicity(hist, narrow, pol):
    """Adding a narrow exception never changes the stance on concepts outside its subtree."""
    from ontomem.core.retrieve import Retriever

    *_, (r, mem) = run(hist)
    ret = Retriever(ONTO, r)
    outside = [c for c in ONTO.concepts if not ONTO.subsumed_by(c, narrow)][:60]
    before = {c: ret.stance(mem, c).polarity for c in outside}
    ts = datetime(2027, 1, 1)
    r.write(mem, mk({"what": narrow, "who": "self", "aspect": "general", "intent": "standing",
                     "context": "any", "pol": pol}, ts, "tx"), narrow, "nx", ts)
    after = {c: ret.stance(mem, c).polarity for c in outside}
    assert before == after


@settings(max_examples=400, deadline=None)
@given(histories)
def test_I5_idempotence(hist):
    """Repeating the last statement never creates a new slot or changes any value."""
    *_, (r, mem) = run(hist)
    before = {k: (mem.nodes[i].polarity, mem.nodes[i].id) for k, i in mem.active_by_key.items()}
    e = hist[-1]
    ts = datetime(2027, 1, 1)
    r.write(mem, mk(e, ts, "again"), e["what"], "n-again", ts)
    after = {k: (mem.nodes[i].polarity, mem.nodes[i].id) for k, i in mem.active_by_key.items()}
    assert before == after
    assert "n-again" not in mem.nodes


@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(histories)
def test_projection_passes_whole_graph_shacl(hist):
    """The RDF projection of any history satisfies every SHACL shape, incl. graph-level I1/I2."""
    *_, (r, mem) = run(hist)
    for n in mem.nodes.values():  # give provenance text like the real pipeline does
        n.source_text = n.source_text or "x"
    report = validate_graph(memory_graph("x", mem, r, ONTO))
    assert report.conforms, report.to_dict()
