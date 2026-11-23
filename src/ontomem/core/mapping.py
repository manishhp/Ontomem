"""Map a surface phrase ("anything spicy", "prawn curries", "spicy thrillers") to a SKOS concept.

Order of attempts (the method is recorded so errors can be attributed by stage):

1. ``exact``  - the whole normalised phrase is a prefLabel/altLabel
2. ``head``   - the right-most lexicon n-gram inside the phrase (English noun phrases are
                head-final, so "spicy thrillers" -> Thriller, not SpicyFood); generic heads
                such as "food" or "dish" only win when nothing else matches
3. ``fuzzy``  - typo-tolerant match against the lexicon (rapidfuzz)
4. ``embedding`` - optional, when an embedding function is configured (Ollama on your machine)
5. otherwise ``unmapped`` - the statement is keyed as ``unmapped:<phrase>`` and the phrase is
   queued for ontology evolution
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from .ontology import UNMAPPED, Ontology
from .text import norm, strip_articles

try:  # optional, falls back to difflib
    from rapidfuzz import fuzz, process

    _HAVE_RAPIDFUZZ = True
except ImportError:  # pragma: no cover
    import difflib

    _HAVE_RAPIDFUZZ = False

GENERIC = {
    "food", "dish", "meal", "cuisine", "cooking", "eating", "stuff", "thing", "item",
    "option", "kind", "type", "place", "music", "movie", "film", "book", "show",
    "trip", "travel", "drink", "song", "product", "series",
}
FILLER = {"anything", "something", "everything", "thing", "stuff", "lot", "very", "really", "much"}


@dataclass
class Mapping:
    concept: str  # concept id, or "unmapped:<phrase>"
    score: float
    method: str  # exact | head | fuzzy | embedding | unmapped
    matched: str = ""

    @property
    def mapped(self) -> bool:
        return not self.concept.startswith(UNMAPPED)


class ConceptMapper:
    def __init__(
        self,
        ontology: Ontology,
        fuzzy_threshold: float = 86.0,
        embed: Callable[[str], list[float]] | None = None,
        embed_threshold: float = 0.80,
    ) -> None:
        self.ontology = ontology
        self.fuzzy_threshold = fuzzy_threshold
        self.embed = embed
        self.embed_threshold = embed_threshold
        self._label_vectors: dict[str, list[float]] | None = None
        self._lex_nohyphen = {k.replace("-", " "): v for k, v in ontology.lexicon.items()}
        self._max_n = max(len(k.split()) for k in self._lex_nohyphen) if ontology.lexicon else 1

    def map(self, surface: str) -> Mapping:
        lex = self.ontology.lexicon
        phrase = strip_articles(norm(surface))
        if not phrase:
            return Mapping(UNMAPPED + "?", 0.0, "unmapped")
        if phrase in lex:
            return Mapping(lex[phrase], 1.0, "exact", phrase)

        dehyph = phrase.replace("-", " ")
        if dehyph in self._lex_nohyphen:
            return Mapping(self._lex_nohyphen[dehyph], 1.0, "exact", dehyph)
        toks = dehyph.split()
        cands: list[tuple[int, int, str, str]] = []  # (end, length, ngram, concept)
        for i in range(len(toks)):
            for j in range(i + 1, min(len(toks), i + self._max_n) + 1):
                ng = " ".join(toks[i:j])
                if ng in self._lex_nohyphen:
                    cands.append((j, j - i, ng, self._lex_nohyphen[ng]))
        specific = [c for c in cands if c[2] not in GENERIC]
        if specific:
            end, length, ng, cid = max(specific, key=lambda c: (c[0], c[1]))
            return Mapping(cid, 0.9, "head", ng)
        if cands:
            # Only a generic head ("dish", "food") matched. Accept it only if nothing
            # meaningful is left over - otherwise "chilli-heavy dishes" would become
            # the root concept Food and "avoid chilli-heavy dishes" would mean "avoid all food".
            leftover = [t for t in toks if t not in GENERIC and t not in FILLER]
            if not leftover:
                end, length, ng, cid = max(cands, key=lambda c: (c[0], c[1]))
                return Mapping(cid, 0.6, "head", ng)

        fz = self._fuzzy(phrase)
        if fz:
            return fz
        if self.embed is not None:
            em = self._embedding(phrase)
            if em:
                return em
        core = " ".join(t for t in toks if t not in FILLER and t not in GENERIC) or phrase
        return Mapping(UNMAPPED + core, 0.0, "unmapped")

    # -------------------------------------------------------------- fallbacks
    def _fuzzy(self, phrase: str) -> Mapping | None:
        keys = list(self.ontology.lexicon)
        if _HAVE_RAPIDFUZZ:
            best = process.extractOne(phrase, keys, scorer=fuzz.ratio)
            if best and best[1] >= self.fuzzy_threshold:
                return Mapping(self.ontology.lexicon[best[0]], best[1] / 100, "fuzzy", best[0])
            # token-level typo repair: "love suhsi rolls" -> sushi
            for tok in phrase.split():
                if len(tok) < 5 or tok in GENERIC:
                    continue
                best = process.extractOne(tok, keys, scorer=fuzz.ratio)
                # typo repair only: same first letter and near-equal length, so real words
                # ("better") are not "corrected" into labels ("beer")
                if (best and best[1] >= 80 and " " not in best[0] and best[0][0] == tok[0]
                        and abs(len(best[0]) - len(tok)) <= 1):
                    return Mapping(self.ontology.lexicon[best[0]], best[1] / 100 * 0.9, "fuzzy", best[0])
            return None
        m = difflib.get_close_matches(phrase, keys, n=1, cutoff=self.fuzzy_threshold / 100)
        if m:
            return Mapping(self.ontology.lexicon[m[0]], 0.85, "fuzzy", m[0])
        return None

    def _embedding(self, phrase: str) -> Mapping | None:  # pragma: no cover - needs a model
        import math

        assert self.embed is not None
        if self._label_vectors is None:
            self._label_vectors = {k: self.embed(k) for k in self.ontology.lexicon}
        q = self.embed(phrase)

        def cos(a: list[float], b: list[float]) -> float:
            dot = sum(x * y for x, y in zip(a, b))
            na = math.sqrt(sum(x * x for x in a))
            nb = math.sqrt(sum(y * y for y in b))
            return dot / (na * nb) if na and nb else 0.0

        best_k, best_s = max(((k, cos(q, v)) for k, v in self._label_vectors.items()), key=lambda t: t[1])
        if best_s >= self.embed_threshold:
            return Mapping(self.ontology.lexicon[best_k], best_s, "embedding", best_k)
        return None
