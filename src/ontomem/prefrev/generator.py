"""PrefRev generator: synthetic multi-session conversations where every slot key is known.

Each episode is one user's history over ~a year: preference statements of ten case
types, interleaved (as real conversations are) and mixed with noise turns, plus
probe requests ("Any dinner ideas?") at random points. Every statement carries its
**gold slot key**; gold state at each probe is the fixed retirement rule applied to
gold keys (oracle replay), so merge errors can be *counted*, not judged.

    python -m ontomem.prefrev.generator --out data/prefrev --seed 7
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from ..core.model import Aspect, Context, Intent, Polarity, Reason, Statement
from ..core.ontology import Concept, Ontology, default_ontology
from ..core.reasoner import Reasoner, UserMemory
from . import spec
from . import templates as T

GENERATOR_VERSION = "prefrev-2.0"
CASE_WEIGHTS = {
    "replace": 0.20, "reinforce": 0.07, "exception": 0.13, "broad_override": 0.07, "intent": 0.08,
    "context": 0.09, "trap": 0.09, "third_party": 0.10, "price": 0.05, "coverage": 0.12,
}
DESTINATIONS = ["Paris", "Rome", "Venice", "Barcelona", "London", "Switzerland", "Tokyo", "Kyoto", "Bangkok",
                "Phuket", "Goa", "Kerala", "Rajasthan", "Ladakh", "Himachal", "Bali", "Singapore", "Dubai",
                "NewYork", "Peru"]
FOOD_ONLY_WORDS = ("eat", "sick", "stomach", "heartburn", "agree with", "allergic", "doctor", "health", "sit well",
                   "cook", "lunch", "dinner")
EXCEPTION_TPL = {
    ("likes", "A"): ["But I love {x}.", "{X} is the exception though, I love it."],
    ("likes", "B"): ["Except {x} - I really like {x}.", "I make an exception for {x}, I adore it."],
    ("dislikes", "A"): ["But I can't stand {x}.", "I don't like {x} though."],
    ("dislikes", "B"): ["Except {x}, I'm not into {x}.", "{X} I could do without, honestly."],
}
OPPOSITE = {"likes": ["dislikes", "avoids"], "dislikes": ["likes"], "avoids": ["likes"], "neutral": ["likes", "dislikes"]}


def extended_ontology(base: Ontology | None = None) -> Ontology:
    """The *gold* ontology: the shipped one plus the coverage-split concepts the system does not know."""
    base = base or default_ontology()
    concepts = {
        cid: Concept(id=c.id, label=c.label, alt_labels=list(c.alt_labels), broader=set(c.broader),
                     narrower=set(c.narrower), related=set(c.related))
        for cid, c in base.concepts.items()
    }
    o = Ontology(concepts, base.version + "+prefrev-gold")
    for cid, surface, parent in spec.EXTERNAL:
        o.add_concept(cid, surface, [parent])
    return o


@dataclass
class GoldStmt:
    concept: str
    surface: str
    polarity: str
    who: str = "self"
    aspect: str = "general"
    intent: str = "standing"
    context: str = "any"
    reason: str = "unspecified"
    surface_kind: str = "label"  # label | paraphrase | external | trap

    def key(self) -> str:
        return f"{self.who}|{self.concept}|{self.aspect}|{self.intent}|{self.context}"


@dataclass
class PlannedTurn:
    text: str
    case: str
    case_id: str
    tier: str = "plain"
    gold: list[GoldStmt] = field(default_factory=list)


class Generator:
    def __init__(self, seed: int = 7, ontology: Ontology | None = None) -> None:
        self.seed = seed
        self.onto = ontology or default_ontology()
        self.gold_onto = extended_ontology(self.onto)
        self._check_spec()

    def _check_spec(self) -> None:
        for d, pool in spec.POOLS.items():
            for c in pool:
                assert c in self.onto, f"pool concept {c} not in ontology"
        for b, n in spec.HIERARCHY_PAIRS:
            assert self.onto.narrower_than(n, b), f"{n} is not narrower than {b}"
        lex = self.onto.lexicon
        from ..core.text import norm

        for cid, surface, parent in spec.EXTERNAL:
            assert cid not in self.onto and parent in self.onto, cid
            assert norm(surface) not in lex, f"external surface '{surface}' is an ontology label"
        for c, ps in spec.PARAPHRASES.items():
            for p in ps:
                assert norm(p) not in lex, f"paraphrase '{p}' is an ontology label - not held out"

    # ----------------------------------------------------------- surfaces
    def surface(self, rng: random.Random, concept: str, allow_paraphrase: bool = True) -> tuple[str, str]:
        if concept not in self.onto:
            return next(s for c, s, _ in spec.EXTERNAL if c == concept), "external"
        if allow_paraphrase and concept in spec.PARAPHRASES and rng.random() < 0.3:
            return rng.choice(spec.PARAPHRASES[concept]), "paraphrase"
        c = self.onto.concepts[concept]
        labels = [c.label] * 3 + [a for a in c.alt_labels if len(a) > 2 and not a.isupper()]
        return rng.choice(labels), "label"

    @staticmethod
    def render(tpl: str, surface: str, who: str | None = None) -> str:
        whom = T.ROLE_OBJECT.get(who or "", "them")
        out = tpl.replace("{x}", surface).replace("{X}", surface[:1].upper() + surface[1:])
        if who:
            out = out.replace("{Who}", who).replace("{whom}", whom)
        return out

    def pick(self, rng: random.Random, bank: dict[str, list[str]], tset: str, domain: str) -> str:
        options = bank[tset]
        if domain != "Food":
            options = [t for t in options if not any(w in t.lower() for w in FOOD_ONLY_WORDS)] or options
        return rng.choice(options)

    def first_person(self, rng, polarity, intent, tset, domain, surface) -> str:
        bank = T.FIRST.get((polarity, intent)) or T.FIRST[(polarity, "standing")]
        if polarity == "avoids" and domain != "Food":
            bank = {"A": ["I avoid {x}.", "I stay away from {x}."], "B": ["I steer clear of {x}.", "I've given up on {x}."]}
        return self.render(self.pick(rng, bank, tset, domain), surface)

    # ------------------------------------------------------------- cases
    def domain(self, rng: random.Random) -> str:
        ds = list(spec.DOMAIN_WEIGHTS)
        return rng.choices(ds, weights=[spec.DOMAIN_WEIGHTS[d] for d in ds])[0]

    def case(self, rng: random.Random, kind: str, cid: str, tset: str) -> list[PlannedTurn]:
        pol = lambda: rng.choices(["likes", "dislikes", "avoids"], weights=[5, 3, 2])[0]  # noqa: E731

        if kind in ("replace", "reinforce", "coverage"):
            if kind == "coverage":
                concept = rng.choice(spec.EXTERNAL)[0]
                domain = self.gold_onto.domain(concept)
            else:
                domain = self.domain(rng)
                concept = rng.choice(spec.POOLS[domain])
            p1 = pol()
            s1, k1 = self.surface(rng, concept)
            turns = [PlannedTurn(self.first_person(rng, p1, "standing", tset, domain, s1), kind, cid,
                                 gold=[GoldStmt(concept, s1, p1, surface_kind=k1)])]
            if kind == "reinforce" or (kind == "coverage" and rng.random() < 0.3):
                s2, k2 = self.surface(rng, concept)
                turns.append(PlannedTurn(self.first_person(rng, p1, "standing", tset, domain, s2), kind, cid,
                                         tier="reinforce", gold=[GoldStmt(concept, s2, p1, surface_kind=k2)]))
                return turns
            p2 = rng.choice(OPPOSITE[p1])
            tiers = [t for (p, t) in T.REVISE if p == p2 and (domain == "Food" or t != "implicit" or p2 != "avoids")
                     and (domain == "Food" or t != "indirect")]
            tier = rng.choice(tiers) if tiers and rng.random() < 0.75 else "plain"
            s2, k2 = self.surface(rng, concept)
            text = (self.render(self.pick(rng, T.REVISE[(p2, tier)], tset, domain), s2) if tier != "plain"
                    else self.first_person(rng, p2, "standing", tset, domain, s2))
            reason = "health" if tier in ("implicit", "indirect") and p2 == "avoids" else "unspecified"
            turns.append(PlannedTurn(text, kind, cid, tier, [GoldStmt(concept, s2, p2, reason=reason, surface_kind=k2)]))
            if rng.random() < 0.2:  # flip back
                s3, k3 = self.surface(rng, concept)
                turns.append(PlannedTurn(self.first_person(rng, p1, "standing", tset, domain, s3), kind, cid, "flip-back",
                                         [GoldStmt(concept, s3, p1, surface_kind=k3)]))
            return turns

        if kind in ("exception", "broad_override"):
            broad, narrow = rng.choice(spec.HIERARCHY_PAIRS)
            domain = self.onto.domain(broad) or "Food"
            pb = rng.choice(["avoids", "dislikes"]) if rng.random() < 0.7 else "likes"
            pn = "likes" if pb != "likes" else "dislikes"
            sb, kb = self.surface(rng, broad)
            sn, kn = self.surface(rng, narrow)
            turns = [
                PlannedTurn(self.first_person(rng, pb, "standing", tset, domain, sb), kind, cid,
                            gold=[GoldStmt(broad, sb, pb, surface_kind=kb)]),
                PlannedTurn(self.render(rng.choice(EXCEPTION_TPL[(pn, tset)]), sn),
                            kind, cid, "exception", [GoldStmt(narrow, sn, pn, surface_kind=kn)]),
            ]
            if kind == "broad_override":
                sb2, kb2 = self.surface(rng, broad)
                turns.append(PlannedTurn(self.render(self.pick(rng, {"A": ["I avoid {x} completely now.", "No {x} at all for me now."],
                                                                    "B": ["I've cut out all {x} now.", "Absolutely no {x} for me these days."]}
                                                                if pb != "likes" else
                                                                {"A": ["I love all {x} now.", "I really enjoy all kinds of {x} now."],
                                                                 "B": ["These days I like every kind of {x}.", "I'm into all {x} now."]},
                                                                tset, domain), sb2),
                                         kind, cid, "override", [GoldStmt(broad, sb2, pb if pb == "likes" else "avoids", surface_kind=kb2)]))
            return turns

        if kind == "intent":
            dest = rng.choice(DESTINATIONS)
            s1, k1 = self.surface(rng, dest, allow_paraphrase=False)
            p1 = rng.choices(["likes", "dislikes"], weights=[4, 1])[0]
            s2, k2 = self.surface(rng, dest, allow_paraphrase=False)
            second = rng.choice(["plan", "standing"])
            p2 = "likes" if second == "plan" else rng.choice(["likes", "dislikes"])
            t2 = (self.render(self.pick(rng, T.FIRST[("likes", "plan")], tset, "Travel"), s2) if second == "plan"
                  else self.first_person(rng, p2, "standing", tset, "Travel", s2))
            return [
                PlannedTurn(self.render(self.pick(rng, T.FIRST[(p1, "past")], tset, "Travel"), s1), kind, cid, "past",
                            [GoldStmt(dest, s1, p1, intent="past", surface_kind=k1)]),
                PlannedTurn(t2, kind, cid, second, [GoldStmt(dest, s2, p2, intent=second, surface_kind=k2)]),
            ]

        if kind == "context":
            domain = rng.choice(["Food", "Food", "Screen", "Music"])
            concept = rng.choice(spec.POOLS[domain])
            ctx = rng.choice(spec.CONTEXTS_FOR[domain])
            p1 = pol()
            p2 = rng.choice(OPPOSITE[p1])
            s1, k1 = self.surface(rng, concept)
            s2, k2 = self.surface(rng, concept)
            lead = rng.choice(T.CONTEXT_LEAD[ctx][tset])
            body = rng.choice(T.CONTEXT_BODY[p2][tset] if domain == "Food" else
                              [b for b in T.CONTEXT_BODY[p2][tset] if "eat" not in b] or T.CONTEXT_BODY[p2]["A"])
            return [
                PlannedTurn(self.first_person(rng, p1, "standing", tset, domain, s1), kind, cid, "any",
                            [GoldStmt(concept, s1, p1, surface_kind=k1)]),
                PlannedTurn(lead + " " + self.render(body, s2), kind, cid, ctx,
                            [GoldStmt(concept, s2, p2, context=ctx, surface_kind=k2)]),
            ]

        if kind == "trap":
            (ca, sa), (cb, sb) = rng.choice(spec.TRAPS)
            if rng.random() < 0.5:
                (ca, sa), (cb, sb) = (cb, sb), (ca, sa)
            pa = pol()
            pb = rng.choice(OPPOSITE[pa])
            da, db = self.onto.domain(ca) or "Food", self.onto.domain(cb) or "Food"
            return [
                PlannedTurn(self.first_person(rng, pa, "standing", tset, da, sa), kind, cid, "a",
                            [GoldStmt(ca, sa, pa, surface_kind="trap")]),
                PlannedTurn(self.first_person(rng, pb, "standing", tset, db, sb), kind, cid, "b",
                            [GoldStmt(cb, sb, pb, surface_kind="trap")]),
            ]

        if kind == "third_party":
            domain = rng.choice(["Food", "Food", "Food", "Screen", "Music", "Travel"])
            concept = rng.choice(spec.POOLS[domain])
            role = rng.choice(spec.THIRD_PARTY_ROLES)
            subj = rng.choice(T.ROLE_SUBJECT[role])
            p_self = pol()
            p_other = rng.choice(OPPOSITE[p_self])
            if p_other == "avoids" and domain != "Food":
                p_other = "dislikes"
            s1, k1 = self.surface(rng, concept)
            s2, k2 = self.surface(rng, concept)
            bank = T.THIRD[(p_other, "standing")]
            if domain != "Food":
                bank = {k: [t for t in v if not any(w in t.lower() for w in FOOD_ONLY_WORDS)] or v for k, v in bank.items()}
            return [
                PlannedTurn(self.first_person(rng, p_self, "standing", tset, domain, s1), kind, cid, "self",
                            [GoldStmt(concept, s1, p_self, surface_kind=k1)]),
                PlannedTurn(self.render(rng.choice(bank[tset]), s2, subj), kind, cid, role,
                            [GoldStmt(concept, s2, p_other, who=role, surface_kind=k2)]),
            ]

        if kind == "price":
            domain = rng.choice(["Food", "Travel", "Shopping"])
            concept = rng.choice(spec.POOLS[domain])
            s1, k1 = self.surface(rng, concept)
            s2, k2 = self.surface(rng, concept)
            return [
                PlannedTurn(self.first_person(rng, "likes", "standing", tset, domain, s1), kind, cid, "general",
                            [GoldStmt(concept, s1, "likes", surface_kind=k1)]),
                PlannedTurn(self.render(rng.choice(T.PRICE[tset]), s2), kind, cid, "price",
                            [GoldStmt(concept, s2, "dislikes", aspect="price", reason="budget", surface_kind=k2)]),
            ]
        raise ValueError(kind)

    # ----------------------------------------------------------- episodes
    def episode(self, index: int, split: str) -> dict[str, Any]:
        rng = random.Random(f"{self.seed}-{split}-{index}")
        tset = "B" if split == "test" else "A"
        n_cases = rng.randint(7, 12)
        kinds = list(CASE_WEIGHTS)
        cases = [self.case(rng, rng.choices(kinds, weights=[CASE_WEIGHTS[k] for k in kinds])[0], f"c{i}", tset)
                 for i in range(n_cases)]
        # interleave cases (random merge preserving order within each case) and add noise
        queues = [list(c) for c in cases]
        planned: list[PlannedTurn] = []
        while any(queues):
            q = rng.choice([q for q in queues if q])
            planned.append(q.pop(0))
            if rng.random() < 0.22:
                d = self.domain(rng)
                s, _ = self.surface(rng, rng.choice(spec.POOLS[d]), allow_paraphrase=False)
                planned.append(PlannedTurn(self.render(rng.choice(T.NOISE[tset]), s), "noise", "noise"))

        uid = f"{split}-{index:04d}"
        t = datetime(2025, 1, 6, 9, 0) + timedelta(days=rng.randint(0, 60), minutes=rng.randint(0, 600))
        r = Reasoner(self.gold_onto)
        mem = UserMemory(uid)
        node_turn: dict[str, str] = {}
        turns, probes = [], []
        for i, pt in enumerate(planned):
            t = t + timedelta(days=rng.uniform(0.5, 12), minutes=rng.randint(0, 300))
            t = t.replace(microsecond=0)
            tid = f"{uid}-t{i:02d}"
            golds = []
            for g in pt.gold:
                stmt = Statement(surface=g.surface, polarity=Polarity(g.polarity), who=g.who, aspect=Aspect(g.aspect),
                                 intent=Intent(g.intent), context=Context(g.context), reason=Reason(g.reason),
                                 strength=0.8, stated_at=t, turn_id=tid, source_text=pt.text)
                out = r.write(mem, stmt, g.concept, f"{tid}-g{len(golds)}", t)
                node_turn.setdefault(out.node_id, tid)
                golds.append({
                    **g.__dict__,
                    "gold_key": g.key(),
                    "in_ontology": g.concept in self.onto,
                    "domain": self.gold_onto.domain(g.concept),
                    "expected_rules": [f.rule for f in out.firings],
                    "decision": out.decision,
                    "supersedes_turn": node_turn.get(out.retired) if out.retired else None,
                })
            turns.append({"turn_id": tid, "ts": t.isoformat(), "text": pt.text, "case": pt.case, "case_id": pt.case_id,
                          "tier": pt.tier, "template_set": tset, "gold": golds})
            if pt.case != "noise" and rng.random() < 0.3:
                probes.append(self.probe(rng, mem, uid, len(probes), i, t))
        for d in sorted({g["domain"] for tr in turns for g in tr["gold"] if g["domain"]}):
            probes.append(self.probe(rng, mem, uid, len(probes), len(turns) - 1, t, domain=d))
        return {"episode_id": uid, "split": split, "template_set": tset, "turns": turns, "probes": probes}

    def probe(self, rng, mem: UserMemory, uid: str, n: int, after: int, t: datetime, domain: str | None = None) -> dict:
        active = mem.active()
        if domain is None:
            ds = [self.gold_onto.domain(x.key.what) for x in active if x.key.who == "self"] or ["Food"]
            domain = rng.choice(ds)
        who = "self"
        others = [x for x in active if x.key.who != "self" and self.gold_onto.domain(x.key.what) == domain]
        if others and rng.random() < 0.35:
            who = rng.choice(others).key.who
        ctxs = [x.key.context for x in active if x.key.context != "any" and x.key.who == who
                and self.gold_onto.domain(x.key.what) == domain and x.key.context in T.PROBES[domain]]
        context = rng.choice(ctxs) if ctxs and rng.random() < 0.5 else "any"
        text = rng.choice(T.PROBES[domain].get(context) or T.PROBES[domain]["any"])
        if who != "self":
            text = text.rstrip("?.") + " " + T.ROLE_PROBE[who] + "?"
        intents = {"standing", "plan"} if domain == "Travel" else {"standing"}
        gold_active = []
        for x in active:
            if x.key.who != who or x.key.intent not in intents:
                continue
            if self.gold_onto.domain(x.key.what) != domain:
                continue
            if not Context(context).subsumed_by(Context(x.key.context)):
                continue
            gold_active.append({"gold_key": f"{x.key.who}|{x.key.what}|{x.key.aspect}|{x.key.intent}|{x.key.context}",
                                "polarity": x.polarity.value, "flagged": x.id in mem.needs_confirmation})
        return {"probe_id": f"{uid}-p{n:02d}", "after_turn": after, "ts": (t + timedelta(minutes=1)).isoformat(),
                "text": text, "domain": domain, "context": context, "who": who, "intents": sorted(intents),
                "gold_active": sorted(gold_active, key=lambda g: g["gold_key"])}

    def generate(self, sizes: dict[str, int]) -> dict[str, list[dict]]:
        return {split: [self.episode(i, split) for i in range(n)] for split, n in sizes.items()}


def write(out_dir: str | Path, seed: int = 7, sizes: dict[str, int] | None = None) -> dict[str, Any]:
    sizes = sizes or {"train": 150, "dev": 50, "test": 150}
    gen = Generator(seed)
    data = gen.generate(sizes)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {"generator": GENERATOR_VERSION, "seed": seed, "sizes": sizes, "splits": {}}
    for split, eps in data.items():
        p = out / f"{split}.jsonl"
        blob = "\n".join(json.dumps(e) for e in eps) + "\n"
        p.write_text(blob)
        manifest["splits"][split] = summarize(eps) | {"sha256": hashlib.sha256(blob.encode()).hexdigest()[:16]}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest


def summarize(eps: list[dict]) -> dict[str, Any]:
    from collections import Counter

    cases, rules, kinds = Counter(), Counter(), Counter()
    stmts = turns = probes = 0
    for e in eps:
        turns += len(e["turns"])
        probes += len(e["probes"])
        for t in e["turns"]:
            cases[t["case"]] += 1
            for g in t["gold"]:
                stmts += 1
                kinds[g["surface_kind"]] += 1
                for r in g["expected_rules"]:
                    rules[r] += 1
    return {"episodes": len(eps), "turns": turns, "statements": stmts, "probes": probes,
            "turns_by_case": dict(cases), "expected_rules": dict(rules), "surface_kinds": dict(kinds)}


def load(path: str | Path, split: str) -> list[dict]:
    return [json.loads(line) for line in (Path(path) / f"{split}.jsonl").read_text().splitlines() if line.strip()]


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate the PrefRev benchmark")
    ap.add_argument("--out", default="data/prefrev")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--train", type=int, default=150)
    ap.add_argument("--dev", type=int, default=50)
    ap.add_argument("--test", type=int, default=150)
    a = ap.parse_args()
    m = write(a.out, a.seed, {"train": a.train, "dev": a.dev, "test": a.test})
    print(json.dumps(m, indent=2))


if __name__ == "__main__":
    main()
