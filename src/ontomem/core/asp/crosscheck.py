"""Differential testing: the Python reasoner vs the ASP encoding (write.lp).

Both implementations get the same random event sequence. Any difference in the
resulting structure (which events became nodes, which are active, supersession links,
exceptions, refinements, R3 flags) is a bug in one of them.

    python -m ontomem.core.asp.crosscheck --n 2000 --seed 1
"""

from __future__ import annotations

import argparse
import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from importlib import resources
from pathlib import Path

from ..model import Aspect, Context, Intent, Polarity, Reason, Statement
from ..ontology import Ontology, default_ontology
from ..reasoner import Reasoner, UserMemory

SIGNS = {"likes": 1, "dislikes": -1, "avoids": -1, "neutral": 0}
DEFAULT_CONCEPTS = [
    "Food", "Seafood", "Fish", "Salmon", "Shellfish", "Shrimp", "Sushi", "JapaneseCuisine", "Ramen",
    "SpicyFood", "Curry", "IndianCuisine", "Meat", "Poultry",
]


@dataclass
class Structure:
    nodes: frozenset
    active: frozenset
    succ: frozenset
    needs_conf: frozenset
    exception: frozenset
    refines: frozenset

    def diff(self, other: Structure) -> dict[str, tuple]:
        out = {}
        for f in ("nodes", "active", "succ", "needs_conf", "exception", "refines"):
            a, b = getattr(self, f), getattr(other, f)
            if a != b:
                out[f] = (sorted(a - b), sorted(b - a))
        return out


def random_events(rng: random.Random, n: int, concepts: list[str], *, who=("self", "partner"),
                  aspects=("general", "price"), intents=("standing", "past"),
                  contexts=("any", "work", "travel"), ties: bool = False) -> list[dict]:
    """``t`` (ASP) is the event order. With ``ties`` the Python timestamps collide in groups of
    three, so the Python reasoner must fall back to event order exactly like the ASP program."""
    t0 = datetime(2026, 1, 1)
    evs = []
    for i in range(n):
        evs.append({
            "id": f"e{i}",
            "t": i + 1,
            "ts": t0 + timedelta(hours=i // 3 if ties else i),
            # skewed choices so that slots actually collide
            "who": rng.choices(who, weights=[5, 1][: len(who)])[0],
            "what": rng.choice(concepts),
            "aspect": rng.choices(aspects, weights=[5, 1][: len(aspects)])[0],
            "intent": rng.choices(intents, weights=[5, 1][: len(intents)])[0],
            "context": rng.choices(contexts, weights=[4, 1, 1][: len(contexts)])[0],
            "pol": rng.choice(list(SIGNS)),
        })
    return evs


def run_python(evs: list[dict], ontology: Ontology) -> Structure:
    r = Reasoner(ontology)
    mem = UserMemory("x")
    for e in evs:
        stmt = Statement(
            surface=e["what"], polarity=Polarity(e["pol"]), who=e["who"], aspect=Aspect(e["aspect"]),
            intent=Intent(e["intent"]), context=Context(e["context"]), reason=Reason.UNSPECIFIED,
            strength=0.7, stated_at=e["ts"], turn_id=e["id"],
        )
        r.write(mem, stmt, e["what"], e["id"], e["ts"])
    exc, ref = set(), set()
    for n in mem.active():
        rel = r.relations(mem, n)
        exc |= {(n.id, m) for m in rel["exception_to"]}
        ref |= {(n.id, m) for m in rel["refines"]}
    return Structure(
        nodes=frozenset(mem.nodes),
        active=frozenset(n.id for n in mem.active()),
        succ=frozenset((n.id, n.superseded_by) for n in mem.nodes.values() if n.superseded_by),
        needs_conf=frozenset(mem.needs_confirmation),
        exception=frozenset(exc),
        refines=frozenset(ref),
    )


def asp_program_path() -> Path:
    return Path(str(resources.files("ontomem.core.asp") / "write.lp"))


def facts(evs: list[dict], ontology: Ontology) -> str:
    q = lambda s: '"' + str(s).replace('"', '') + '"'  # noqa: E731
    lines = []
    concepts = {e["what"] for e in evs}
    closure = set(concepts)
    for c in concepts:
        closure |= set(ontology.ancestors(c))
    for c in closure:
        if c in ontology.concepts:
            for b in ontology.concepts[c].broader:
                lines.append(f"broader({q(c)},{q(b)}).")
    for p, s in SIGNS.items():
        lines.append(f"sign({q(p)},{s}).")
    for e in evs:
        lines.append(
            f"event({e['id']},{e['t']},{q(e['who'])},{q(e['what'])},{q(e['aspect'])},"
            f"{q(e['intent'])},{q(e['context'])},{q(e['pol'])})."
        )
    return "\n".join(lines)


def run_asp(evs: list[dict], ontology: Ontology) -> Structure:
    import clingo

    ctl = clingo.Control(["--warn=none"])
    ctl.load(str(asp_program_path()))
    ctl.add("base", [], facts(evs, ontology))
    ctl.ground([("base", [])])
    got: dict[str, set] = {k: set() for k in ("node", "active", "retired", "succ", "needs_conf", "exception", "refines")}
    models = 0
    with ctl.solve(yield_=True) as h:
        for m in h:
            models += 1
            for sym in m.symbols(shown=True):
                args = tuple(str(a) for a in sym.arguments)
                got[sym.name].add(args[0] if len(args) == 1 else args)
    if models != 1:
        raise AssertionError(f"ASP program must be stratified with exactly one answer set, got {models}")
    return Structure(
        nodes=frozenset(got["node"]),
        active=frozenset(got["active"]),
        succ=frozenset(got["succ"]),
        needs_conf=frozenset(got["needs_conf"]),
        exception=frozenset(got["exception"]),
        refines=frozenset(got["refines"]),
    )


def crosscheck(n_sequences: int = 500, length: tuple[int, int] = (3, 25), seed: int = 0,
               ontology: Ontology | None = None, concepts: list[str] | None = None, ties: float = 0.5) -> dict:
    ontology = ontology or default_ontology()
    concepts = concepts or DEFAULT_CONCEPTS
    rng = random.Random(seed)
    failures = []
    stats = {"events": 0, "nodes": 0, "supersessions": 0, "exceptions": 0, "flags": 0}
    for i in range(n_sequences):
        evs = random_events(rng, rng.randint(*length), concepts, ties=rng.random() < ties)
        py, asp = run_python(evs, ontology), run_asp(evs, ontology)
        stats["events"] += len(evs)
        stats["nodes"] += len(py.nodes)
        stats["supersessions"] += len(py.succ)
        stats["exceptions"] += len(py.exception)
        stats["flags"] += len(py.needs_conf)
        d = py.diff(asp)
        if d:
            failures.append({"sequence": i, "events": evs, "diff": d})
            if len(failures) >= 5:
                break
    return {"sequences": n_sequences, "disagreements": len(failures), "failures": failures, "stats": stats}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--n", type=int, default=500)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
    res = crosscheck(args.n, seed=args.seed)
    print(f"{res['sequences']} sequences, {res['stats']['events']} events: "
          f"{res['stats']['supersessions']} supersessions, {res['stats']['exceptions']} exceptions, "
          f"{res['stats']['flags']} R3 flags checked; disagreements: {res['disagreements']}")
    for f in res["failures"]:
        print("sequence", f["sequence"], f["diff"])


if __name__ == "__main__":
    main()
