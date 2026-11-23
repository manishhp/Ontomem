import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ontomem.core.engine import OntoMemEngine  # noqa: E402
from ontomem.core.events import EventStore  # noqa: E402
from ontomem.core.ontology import default_ontology  # noqa: E402


@pytest.fixture(scope="session")
def ontology():
    return default_ontology()


@pytest.fixture
def engine(ontology):
    return OntoMemEngine(ontology=ontology, store=EventStore())


class Clock:
    """Deterministic, strictly increasing timestamps for scripted conversations."""

    def __init__(self, start=datetime(2026, 1, 5, 19, 0)):
        self.t = start

    def __call__(self, days: float = 1) -> datetime:
        self.t = self.t + timedelta(days=days)
        return self.t


@pytest.fixture
def clock():
    return Clock()


def say(engine, user, clock, *turns, days=7):
    traces = []
    for t in turns:
        traces.append(engine.observe(user, t, clock(days)))
    return traces


def active_view(engine, user):
    mem = engine.memory(user)
    return {(n.key.who, n.key.what, n.key.intent, n.key.context, n.key.aspect): n.polarity.value for n in mem.active()}
