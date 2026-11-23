"""S5: a supervised key assigner that uses NO ontology.

For a new item and each active memory it predicts "same slot?" from lexical and
extracted-field features with gradient-boosted trees, then merges into the most
likely memory if the probability clears a threshold tuned on the dev split.

This answers RQ2's second half: can labelled data replace the ontology's structure?
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
from rapidfuzz import fuzz

from ..core.text import norm, strip_articles
from .systems import Item, KeyedSystem

FEATURES = [
    "surf_eq", "head_eq", "tok_jaccard", "ratio", "token_set", "partial", "who_eq", "intent_eq",
    "context_eq", "aspect_eq", "sign_flip", "log_gap_days", "len_diff",
]


def _sign(p: str) -> int:
    return {"likes": 1, "dislikes": -1, "avoids": -1}.get(p, 0)


def pair_features(a: Item, b: Item) -> list[float]:
    sa, sb = strip_articles(norm(a.surface)), strip_articles(norm(b.surface))
    ta, tb = set(sa.split()), set(sb.split())
    gap = abs((a.ts - b.ts).total_seconds()) / 86400
    return [
        float(sa == sb),
        float(sa.split()[-1:] == sb.split()[-1:]),
        len(ta & tb) / max(1, len(ta | tb)),
        fuzz.ratio(sa, sb) / 100,
        fuzz.token_set_ratio(sa, sb) / 100,
        fuzz.partial_ratio(sa, sb) / 100,
        float(a.who == b.who),
        float(a.intent == b.intent),
        float(a.context == b.context),
        float(a.aspect == b.aspect),
        float(_sign(a.polarity) * _sign(b.polarity) < 0),
        math.log1p(gap),
        abs(len(sa) - len(sb)) / max(1, len(sa) + len(sb)),
    ]


class LearnedKeyer:
    def __init__(self, model: Any = None, threshold: float = 0.5) -> None:
        self.model = model
        self.threshold = threshold

    def __call__(self, it: Item, sys_: KeyedSystem) -> Any:
        if not sys_.active_by_key or self.model is None:
            return ("new", it.item_id)
        keys, feats = [], []
        for k, nid in sys_.active_by_key.items():
            last = sys_.item_meta[sys_.items[nid][-1]]
            keys.append(k)
            feats.append(pair_features(it, last))
        probs = self.model.predict_proba(np.array(feats))[:, 1]
        i = int(np.argmax(probs))
        return keys[i] if probs[i] >= self.threshold else ("new", it.item_id)


def training_pairs(episodes_items: list[list[Item]], max_neg: int = 6, seed: int = 0) -> tuple[np.ndarray, np.ndarray]:
    """For every item, pair it with the latest item of each earlier slot (positives: same gold key)."""
    import random

    rng = random.Random(seed)
    X, y = [], []
    for items in episodes_items:
        latest: dict[str, Item] = {}
        for it in items:
            if it.gold_key is None:
                continue
            pos = latest.get(it.gold_key)
            if pos is not None:
                X.append(pair_features(it, pos))
                y.append(1)
            negs = [v for k, v in latest.items() if k != it.gold_key]
            for v in rng.sample(negs, min(max_neg, len(negs))):
                X.append(pair_features(it, v))
                y.append(0)
            latest[it.gold_key] = it
    return np.array(X), np.array(y)


def train(train_items: list[list[Item]], dev_items: list[list[Item]], seed: int = 0) -> tuple[LearnedKeyer, dict]:
    from sklearn.ensemble import HistGradientBoostingClassifier

    X, y = training_pairs(train_items, seed=seed)
    clf = HistGradientBoostingClassifier(max_iter=200, learning_rate=0.08, random_state=seed)
    clf.fit(X, y)
    # tune the merge threshold on dev to minimise missed + false merges
    best = (1e9, 0.5)
    for th in [0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]:
        keyer = LearnedKeyer(clf, th)
        errs = 0
        for items in dev_items:
            s = KeyedSystem("tune", keyer)
            gold_latest: dict[str, str] = {}
            for it in items:
                rec = s.write(it)
                if it.gold_key is None:
                    continue
                if rec.target_id is not None:
                    tgt_gk = s.item_meta[s.items[rec.target_id][0]].gold_key
                    errs += tgt_gk != it.gold_key
                elif it.gold_key in gold_latest and gold_latest[it.gold_key] in s.active_by_key.values():
                    errs += 1
                gold_latest[it.gold_key] = rec.node_id if rec.decision != "reinforce" else gold_latest.get(it.gold_key, rec.node_id)
        if errs < best[0]:
            best = (errs, th)
    info = {"pairs": int(len(y)), "positives": int(y.sum()), "threshold": best[1], "dev_errors": best[0],
            "train_accuracy": float(clf.score(X, y)), "features": FEATURES}
    return LearnedKeyer(clf, best[1]), info


def learned_system(keyer: LearnedKeyer) -> KeyedSystem:
    return KeyedSystem("S5-learned", keyer)
