"""Small, dependency-free text normalisation shared by the ontology, mapper and extractor.

Everything that compares surface strings goes through :func:`norm` so that the
ontology lexicon and extracted phrases are normalised identically.
"""

from __future__ import annotations

import re

_PUNCT = re.compile(r"[^\w\s&'-]+")
_SPACE = re.compile(r"\s+")

# Plurals that the suffix rules below would get wrong.
_IRREGULAR = {
    "mangoes": "mango",
    "tomatoes": "tomato",
    "potatoes": "potato",
    "series": "series",
    "news": "news",
    "chips": "chips",
    "glass": "glass",
    "jeans": "jeans",
    "clothes": "clothes",
    "sweets": "sweet",
    "dumplings": "dumpling",
    "movies": "movie",
    "cookies": "cookie",
    "pastries": "pastry",
    "biographies": "biography",
    "autobiographies": "autobiography",
    "documentaries": "documentary",
    "comedies": "comedy",
    "mysteries": "mystery",
    "curries": "curry",
    "veggies": "veggie",
    "smoothies": "smoothie",
    "selfies": "selfie",
    "mussels": "mussel",
    "sardines": "sardine",
    "headphones": "headphones",
    "earphones": "earphones",
    "earbuds": "earbud",
    "trainers": "trainer",
    "sneakers": "sneaker",
    "heels": "heel",
    "sandals": "sandal",
    "slippers": "slipper",
    "ramen": "ramen",
    "sushi": "sushi",
    "analysis": "analysis",
    "bus": "bus",
    "buses": "bus",
    "pass": "pass",
    "chess": "chess",
    "dress": "dress",
    "dresses": "dress",
    "lens": "lens",
    "always": "always",
    "this": "this",
    "is": "is",
    "was": "was",
    "has": "has",
    "does": "does",
    "yes": "yes",
    "us": "us",
    "its": "its",
    "less": "less",
    "plus": "plus",
    "gas": "gas",
    "physics": "physics",
    "electronics": "electronics",
    "cosmetics": "cosmetics",
    "athletics": "athletics",
    "classics": "classic",
    "comics": "comic",
    "nightlife": "nightlife",
    "tacos": "taco",
    "momos": "momo",
    "nachos": "nacho",
    "burritos": "burrito",
    "bbq": "bbq",
    "kimchi": "kimchi",
    "hummus": "hummus",
    "lotus": "lotus",
    "bonus": "bonus",
    "museums": "museum",
    "oysters": "oyster",
    "shrimps": "shrimp",
    "prawns": "prawn",
    "noodles": "noodle",
    "greens": "green",
    "crowds": "crowd",
}

_KEEP_S = ("ss", "us", "is", "ics", "sics")


def singular(token: str) -> str:
    """Very small English singulariser, good enough for preference vocabulary."""
    if token in _IRREGULAR:
        return _IRREGULAR[token]
    if len(token) <= 3 or not token.endswith("s") or token.endswith(_KEEP_S):
        return token
    if token.endswith("ies") and len(token) > 4:
        return token[:-3] + "y"
    if token.endswith(("ches", "shes", "xes", "zzes")):
        return token[:-2]
    if token.endswith("oes"):
        return token[:-2]
    return token[:-1]


def tokens(text: str) -> list[str]:
    """Lower-case, strip punctuation, split, singularise."""
    text = text.lower().replace("’", "'")
    text = _PUNCT.sub(" ", text)
    out = []
    for t in _SPACE.split(text.strip()):
        if not t:
            continue
        t = t.strip("'-")
        if t:
            out.append(singular(t))
    return out


def norm(text: str) -> str:
    """Canonical form used as a lexicon key: ``"Spicy Dishes!" -> "spicy dish"``."""
    return " ".join(tokens(text))


ARTICLES = {"the", "a", "an", "some", "any", "my", "our", "all", "kind", "of", "sort"}


def strip_articles(phrase: str) -> str:
    toks = phrase.split()
    while toks and toks[0] in ARTICLES:
        toks = toks[1:]
    return " ".join(toks)
