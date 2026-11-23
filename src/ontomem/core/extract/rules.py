"""Deterministic, offline preference extractor.

It turns a user turn into zero or more *raw candidates* (plain dicts, the same shape
an LLM extractor returns as JSON). Concept mapping and SHACL validation happen
afterwards in the pipeline, so extraction errors and mapping errors can be measured
separately.

Coverage, by design:
* clause splitting on but / although / ; / sentence ends, and on "and" when the
  right-hand side carries its own preference cue
* ~150 cue phrases for likes / dislikes / avoids / neutral, with negation handled
  before the positive form ("don't like" is matched before "like")
* cues whose object comes *before* them ("spicy food doesn't agree with me")
* who (partner, child, parent, friend, colleague, sibling) from the clause subject,
  with simple pronoun carry-over inside a turn
* intent (standing / past / plan), context (work, home, travel, social, family),
  aspect (price, quality, setting, timing), reason (allergy, health, ethics, ...),
  hedging ("I think I kind of like ...")
* identity statements ("I'm vegan now", "lactose intolerant")
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .base import Extractor

# ---------------------------------------------------------------- normalising
_CONTRACTIONS = [
    (r"\bi'm\b", "i am"), (r"\bi've\b", "i have"), (r"\bi'd\b", "i would"), (r"\bi'll\b", "i will"),
    (r"\bwe're\b", "we are"), (r"\bwe've\b", "we have"), (r"\bwe'd\b", "we would"),
    (r"\bthey're\b", "they are"), (r"\bthey've\b", "they have"),
    (r"\byou're\b", "you are"), (r"\bit's\b", "it is"), (r"\bthat's\b", "that is"),
    (r"\bshe's\b", "she is"), (r"\bhe's\b", "he is"),
    (r"\bdon't\b", "do not"), (r"\bdoesn't\b", "does not"), (r"\bdidn't\b", "did not"),
    (r"\bcan't\b", "cannot"), (r"\bcouldn't\b", "could not"), (r"\bwon't\b", "will not"),
    (r"\bwouldn't\b", "would not"), (r"\bisn't\b", "is not"), (r"\baren't\b", "are not"),
    (r"\bwasn't\b", "was not"), (r"\bweren't\b", "were not"), (r"\bhaven't\b", "have not"),
    (r"\bhasn't\b", "has not"), (r"\bshouldn't\b", "should not"), (r"\bain't\b", "is not"),
    (r"\bgonna\b", "going to"), (r"\bwanna\b", "want to"), (r"\bkinda\b", "kind of"),
    (r"\bsorta\b", "sort of"), (r"\bfav(?:e|ourite|orite)?\b", "favourite"),
]
_CONTRACTIONS_RE = [(re.compile(p), r) for p, r in _CONTRACTIONS]


def normalise(text: str) -> str:
    t = re.sub(r"<[^<>]{0,200}>", " ", text)  # markup pasted into a message is not content
    t = t.lower().replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    for rx, rep in _CONTRACTIONS_RE:
        t = rx.sub(rep, t)
    t = re.sub(r"\s+", " ", t)
    return t.strip()


# ---------------------------------------------------------------------- cues
@dataclass(frozen=True)
class Cue:
    pattern: str
    polarity: str
    strength: float
    mode: str = "post"  # post: object after cue; pre: object before cue
    subject: bool = True  # post cues: require a subject before the cue
    intent: str | None = None
    reason: str | None = None
    aspect: str | None = None


def _alt(*xs: str) -> str:
    return "(?:" + "|".join(sorted(xs, key=len, reverse=True)) + ")"


PLAN_VERBS = _alt(
    "go back to", "go to", "visit", "travel to", "fly to", "head to", "try", "see", "explore",
    "check out", "book", "take", "do", "watch", "read", "get", "buy", "start", "learn", "eat",
    "going back to", "going to", "visiting", "travelling to", "traveling to", "trying", "seeing",
    "exploring", "heading to", "doing", "getting", "buying", "go", "return to", "spend time in",
)

CUES: list[Cue] = [
    # ---- past experiences (checked first: "loved our trip" is not a standing "love")
    Cue(_alt("used to love", "used to like", "used to enjoy"), "likes", 0.6, intent="past"),
    Cue(_alt("used to hate", "used to dislike"), "dislikes", 0.6, intent="past"),
    Cue(_alt("had a great time in", "had a great time at", "had an amazing time in", "had an amazing time at",
             "had a blast in", "had a blast at", "had fun in", "had a lovely time in", "had a wonderful time in",
             "had the best time in", "fell in love with", "really enjoyed", "really loved", "absolutely loved",
             "loved", "enjoyed", "adored"), "likes", 0.8, intent="past"),
    Cue(_alt("had a terrible time in", "had a bad time in", "had an awful time in", "really hated",
             "did not like", "did not enjoy", "hated", "disliked", "regretted"), "dislikes", 0.7, intent="past"),
    # ---- plans and goals
    Cue(_alt("am planning to", "are planning to", "planning to", "planning on", "plan to", "plans to",
             "want to", "wants to", "would love to", "would like to", "am hoping to", "hope to", "hoping to",
             "cannot wait to", "can not wait to", "am thinking of", "thinking of", "am thinking about",
             "thinking about", "am dying to", "dream of", "dreaming of", "am going to", "are going to",
             "intend to", "have always wanted to", "always wanted to", "really want to", "will")
        + r"\s+" + PLAN_VERBS, "likes", 0.75, subject=False, intent="plan"),
    Cue(_alt("am going to", "are going to", "am heading to", "are heading to", "am flying to",
             "am travelling to", "am traveling to", "will be in", "are off to", "am off to"),
        "likes", 0.6, intent="plan"),
    # ---- avoids (hard constraints)
    Cue(_alt("am allergic to", "is allergic to", "are allergic to", "allergic to", "have an allergy to",
             "has an allergy to"), "avoids", 1.0, subject=False, reason="allergy"),
    Cue(_alt("cannot eat", "can not eat", "could not eat", "cannot have", "cannot drink", "cannot tolerate",
             "cannot handle", "cannot stomach", "cannot digest", "am not allowed", "is not allowed"),
        "avoids", 0.9, reason="health"),
    Cue(_alt("have been avoiding", "am avoiding", "try to avoid", "tries to avoid", "avoiding", "avoids",
             "avoid"), "avoids", 0.9),
    Cue(_alt("stay away from", "stays away from", "staying away from", "steer clear of", "steers clear of"),
        "avoids", 0.9),
    Cue(_alt("no longer eat", "no longer eats", "no longer drink", "no longer drinks", "do not eat",
             "does not eat", "do not drink", "does not drink", "never eat", "never eats", "never drink",
             "will not eat", "will not touch", "refuse to eat", "do not touch", "does not touch"),
        "avoids", 0.9),
    Cue(_alt("have given up", "has given up", "gave up", "have quit", "quit", "have cut out", "cut out",
             "stopped eating", "stopped drinking", "have stopped eating", "have stopped drinking", "am off",
             "have sworn off", "swore off"), "avoids", 0.85),
    # ---- dislikes
    Cue(_alt("hate", "hates", "loathe", "loathes", "detest", "detests", "cannot stand", "can not stand",
             "could not stand", "despise", "despises", "really hate", "absolutely hate"), "dislikes", 0.9),
    Cue(_alt("dislike", "dislikes", "do not like", "does not like", "do not really like", "does not really like",
             "do not enjoy", "does not enjoy", "do not love", "does not love", "do not care for",
             "does not care for", "am not into", "is not into", "are not into", "am not really into",
             "not really into", "am not a fan of", "is not a fan of", "am not a big fan of", "is not a big fan of",
             "not a fan of", "not a big fan of", "not really a fan of", "am not keen on", "not keen on",
             "am sick of", "am tired of", "am bored of", "got bored of", "am fed up with", "have had enough of",
             "no longer like", "no longer enjoy", "no longer love", "am over", "have gone off", "went off",
             "cut down on", "have cut down on", "am cutting down on", "am done with", "have gone off of"),
        "dislikes", 0.6, subject=False),
    # ---- neutral
    Cue(_alt("do not mind", "does not mind", "am okay with", "am ok with", "am fine with", "am indifferent to",
             "can take or leave"), "neutral", 0.4),
    # ---- likes
    Cue(_alt("love", "loves", "adore", "adores", "am obsessed with", "is obsessed with", "obsessed with",
             "am crazy about", "am mad about", "am a huge fan of", "is a huge fan of", "huge fan of",
             "am a big fan of", "is a big fan of", "big fan of", "cannot get enough of", "can never say no to",
             "am passionate about", "really love", "absolutely love", "now love", "still love"),
        "likes", 0.9),
    Cue(_alt("like", "likes", "really like", "enjoy", "enjoys", "really enjoy", "am into", "is into", "are into",
             "am really into", "am a fan of", "is a fan of", "am fond of", "is fond of", "am keen on",
             "is keen on", "prefer", "prefers", "crave", "craves", "have been craving", "have been enjoying",
             "have been loving", "have gotten into", "have got into", "got into", "am getting into",
             "have started liking", "started liking", "have started enjoying", "started enjoying",
             "have come around to", "came around to", "am warming up to", "have warmed up to", "am back into",
             "am now into", "now like", "now enjoy", "always go for", "always order", "always get",
             "can eat", "am open to", "go for", "still like", "still enjoy"),
        "likes", 0.7),
    Cue(_alt("fan of", "fond of"), "likes", 0.7, subject=False),
    # ---- object-before-cue forms
    Cue(_alt("does not agree with me", "do not agree with me", "no longer agrees with me",
             "does not agree with him", "does not agree with her", "do not agree with us",
             "does not sit well with me", "do not sit well with me"), "avoids", 0.85, mode="pre", reason="health"),
    Cue(_alt("makes me sick", "make me sick", "made me sick", "makes me ill", "make me ill", "makes me bloated",
             "make me bloated", "makes her sick", "makes him sick", "make her sick", "make him sick",
             "gives me heartburn", "give me heartburn", "gives me a headache", "give me a headache"),
        "avoids", 0.85, mode="pre", reason="health"),
    Cue(_alt("upsets my stomach", "upset my stomach", "upsets her stomach", "upsets his stomach"),
        "avoids", 0.85, mode="pre", reason="health"),
    Cue(_alt("is off the table", "are off the table", "is off limits", "are off limits", "is off-limits",
             "are off-limits", "is a no-go", "are a no-go", "is a no go", "are a no go", "is out for me",
             "are out for me"), "avoids", 0.8, mode="pre"),
    Cue(_alt("is not for me", "are not for me", "is not my thing", "are not my thing", "is not my cup of tea",
             "are not my cup of tea", "is not really my thing", "is overrated", "are overrated", "is boring",
             "are boring", "is gross", "are gross", "is disgusting", "are disgusting", "does nothing for me",
             "do nothing for me", "gets on my nerves", "get on my nerves"), "dislikes", 0.6, mode="pre"),
    Cue(r"(?:is|are) (?:way |far |much |a bit |a little |just )?too (?:expensive|pricey|costly)|(?:is|are) overpriced|costs? too much",
        "dislikes", 0.6, mode="pre", aspect="price", reason="budget"),
    Cue(_alt("was amazing", "were amazing", "was awesome", "was great", "were great", "was wonderful",
             "was incredible", "was fantastic", "was the best", "was magical", "was unforgettable"),
        "likes", 0.8, mode="pre", intent="past"),
    Cue(_alt("was terrible", "was awful", "was a disappointment", "was disappointing", "was horrible",
             "was overrated"), "dislikes", 0.7, mode="pre", intent="past"),
    Cue(_alt("is my favourite", "are my favourite", "is the best", "are the best", "is amazing", "are amazing",
             "is my go-to", "are my go-to", "is my jam", "is my thing", "are my thing", "is my comfort food",
             "has grown on me", "have grown on me", "is growing on me", "are growing on me", "is a must for me",
             "never gets old", "never get old"), "likes", 0.85, mode="pre"),
]

SUBJECT = r"(?:\bi\b|\bwe\b|\bhe\b|\bshe\b|\bthey\b|\bmy (?:[a-z-]+ ){0,2}?[a-z-]+\b|\bour (?:kids|children|son|daughter)\b|\bthe kids\b)"
ADVERBS = _alt(
    "really", "also", "still", "actually", "totally", "absolutely", "kind of", "sort of", "honestly",
    "definitely", "just", "now", "always", "genuinely", "seriously", "truly", "pretty much", "certainly",
    "somewhat", "a bit", "a little", "slightly", "personally", "generally", "usually", "mostly", "do",
    "does", "think i", "guess i", "feel like i", "suppose i", "have to say i", "must say i", "am", "is",
    "are", "have", "has", "probably", "maybe", "lately", "recently", "these days", "nowadays", "both",
    "all", "too", "secretly", "low-key", "lowkey", "officially",
)
_PRE_ADV = r"(?:\s+" + ADVERBS + r")*"

COMPILED: list[tuple[Cue, re.Pattern[str]]] = []
for _c in CUES:
    if _c.mode == "post" and _c.subject:
        _rx = re.compile(SUBJECT + _PRE_ADV + r"\s+(" + _c.pattern + r")\b")
    else:
        _rx = re.compile(r"(?<![a-z])(" + _c.pattern + r")\b")
    COMPILED.append((_c, _rx))

# ----------------------------------------------------------------- attributes
ROLE_PATTERNS = [
    ("partner", r"\b(?:my|our) (?:wife|husband|partner|spouse|girlfriend|boyfriend|fiance|fiancee|better half)\b"),
    ("child", r"\b(?:my|our|the) (?:son|daughter|kid|kids|children|child|little one|little ones|boy|girl|toddler|teenager)\b"),
    ("parent", r"\b(?:my|our) (?:mom|mum|mother|dad|father|parents|parent|mother-in-law|father-in-law|grandma|grandpa|grandmother|grandfather)\b"),
    ("friend", r"\b(?:my|our) (?:friend|friends|best friend|buddy|roommate|flatmate|mate)\b"),
    ("colleague", r"\b(?:my|our) (?:colleague|colleagues|boss|coworker|coworkers|co-worker|manager|team|teammate)\b"),
    ("sibling", r"\b(?:my|our) (?:brother|sister|sibling|siblings|twin)\b"),
]
ROLE_RE = [(r, re.compile(p)) for r, p in ROLE_PATTERNS]
PRONOUN_RE = re.compile(r"^(?:(?:and|but|so|also|oh|well)\s+)*(?:he|she|they)\b")

CONTEXT_PATTERNS = [
    ("work", r"\b(?:at work|at the office|in the office|for work|during work|work lunch(?:es)?|office lunch(?:es)?|on workdays|at my desk|team lunch(?:es)?|work dinner(?:s)?|client dinner(?:s)?)\b"),
    ("home", r"\b(?:at home|cooking at home|home-cooked|home cooked|in my kitchen|on the couch|when i cook)\b"),
    ("travel", r"\b(?:when (?:i am |we are )?travel(?:l)?ing|when i travel|when we travel|on trips|on a trip|on holiday|on holidays|on vacation|while abroad|when abroad|on the road|on work trips|on business trips)\b"),
    ("social", r"\b(?:with friends|with my friends|at parties|at a party|going out|on a night out|on nights out|at social events|with colleagues)\b"),
    ("family", r"\b(?:with (?:the|my|our) kids|with (?:the|my) family|with family|family dinner(?:s)?|family trip(?:s)?|family holiday(?:s)?|with (?:my|our) (?:son|daughter|children))\b"),
]
CONTEXT_RE = [(c, re.compile(p)) for c, p in CONTEXT_PATTERNS]

ASPECT_PATTERNS = [
    ("price", r"\b(?:price|prices|pricey|cost|costs|costly|expensive|overpriced|cheap|cheaper|afford|paying for|pay for|budget)\b"),
    ("quality", r"\b(?:quality|well made|well-made|badly made|cheaply made|build quality|durable|flimsy)\b"),
    ("setting", r"\b(?:atmosphere|ambience|ambiance|vibe|vibes|decor|seating|music there)\b"),
    ("timing", r"\b(?:at night|late at night|in the evening|in the morning|early morning|after (?:\d+|six|seven|eight|nine|ten) ?(?:pm)?|before bed|on weekdays|during the week)\b"),
]
ASPECT_RE = [(a, re.compile(p)) for a, p in ASPECT_PATTERNS]

REASON_PATTERNS = [
    ("allergy", r"\b(?:allerg\w*|intoleran\w*|anaphyla\w*|celiac|coeliac|epipen)\b"),
    ("health", r"\b(?:health\w*|doctor|stomach|digest\w*|cholesterol|diet|dieting|acidity|acid reflux|heartburn|blood pressure|sugar levels|diabet\w*|weight|gym|fitness|bloat\w*|sick|ill|agree with)\b"),
    ("ethics", r"\b(?:ethic\w*|animal\w*|cruelty|environment\w*|sustainab\w*|planet|climate|exploitative)\b"),
    ("religion", r"\b(?:religio\w*|fasting|navratri|ramadan|lent|shravan|temple|halal|kosher|jain)\b"),
    ("budget", r"\b(?:expensive|budget|afford|pricey|cost\w*|saving money|save money|overpriced)\b"),
]
REASON_RE = [(r, re.compile(p)) for r, p in REASON_PATTERNS]

HEDGES = re.compile(
    r"\b(?:i think|i guess|i suppose|kind of|sort of|somewhat|a bit|a little|maybe|probably|not sure|"
    r"i feel like|slightly|perhaps|might|semi|fairly|moderately|i reckon|so-so|low-key|lowkey)\b"
)
PAST_HINT = re.compile(r"\b(?:last (?:year|summer|winter|month|week|time|trip)|when we went|when i went|we went|i went|back in \d{4}|years ago|as a kid|in college|our trip|my trip)\b")
PLAN_HINT = re.compile(r"\b(?:next (?:year|summer|winter|month|week|trip|holiday)|this (?:summer|winter|weekend|december|year)|upcoming|bucket list|someday|one day)\b")
QUESTION = re.compile(r"^(?:what|which|any|can|could|should|would|do|does|is|are|how|where|when|who|why|recommend|suggest|got|tell me|give me|help|please)\b")

IDENTITY = [
    (re.compile(r"\b(?:i am|i have become|i have gone|i went|i turned|we are|we have gone) (?:now |fully |strictly |completely )?(?:a )?vegan\b"),
     [("meat", "avoids", "ethics"), ("seafood", "avoids", "ethics"), ("dairy", "avoids", "ethics")]),
    (re.compile(r"\b(?:i am|i have become|i have gone|i went|i turned|we are) (?:now |fully |strictly |completely |pure )?(?:a )?vegetarian\b"),
     [("meat", "avoids", "ethics"), ("seafood", "avoids", "ethics")]),
    (re.compile(r"\b(?:i am|i have been) (?:a )?pescatarian\b"), [("meat", "avoids", "ethics")]),
    (re.compile(r"\blactose intolerant\b"), [("dairy", "avoids", "allergy")]),
    (re.compile(r"\b(?:gluten intolerant|gluten-intolerant|celiac|coeliac)\b"), [("gluten", "avoids", "allergy")]),
    (re.compile(r"\b(?:i am|i have become|i went) (?:now )?teetotal\b|\bi (?:do not|no longer) drink(?: alcohol)?(?:\s*(?:any ?more|at all|now))?\s*$"),
     [("alcohol", "avoids", "unspecified")]),
]

SANDWICH = [
    # "No seafood at all for me now", "Absolutely no dairy for me"
    (re.compile(r"^(?:(?:absolutely|definitely|so|and|but|ok|okay)\s+)*no (?:more )?(?P<obj>.+?)(?: at all)? for me\b"), "avoids", 0.85),
]
PRONOUN_OBJ = re.compile(r"^(?:it|them|that|those|this|these)$")

OBJ_STOP = re.compile(
    r"\s*(?:,|!|\.|\bbecause\b|\bsince\b|\bas long as\b|\bbut\b|\bthough\b|\balthough\b|\bwhen\b|\bwhile\b|"
    r"\bwhenever\b|\bif\b|\bunless\b|\bso\b|\bwhich\b|\bthat is\b|\bthese days\b|\bany ?more\b|\bnowadays\b|"
    r"\blately\b|\brecently\b|\bright now\b|\bat the moment\b|\bfor now\b|\bat all\b|\bso much\b|\ba lot\b|"
    r"\bvery much\b|\btoo\b|\bas well\b|\beither\b|\bagain\b|\bhonestly\b|\btbh\b|\blol\b|\bhaha\b|\bnow\b|"
    r"\bactually\b|\banyway\b|\bsometimes\b|\boften\b|\bevery (?:day|week|weekend|time)\b|\bon weekends\b|"
    r"\b(?:this|next|last) (?:summer|winter|spring|autumn|fall|monsoon|weekend|year|month|week|trip|holiday|time)\b|"
    r"\bin (?:january|february|march|april|may|june|july|august|september|october|november|december|\d{4})\b|\bsoon\b|"
    r"\blast (?:year|summer|winter|month|week|time)\b|\bthough\b|\bfor (?:me|us|him|her|them)\b|\bwith (?:me|us)\b|"
    r"\bafter (?:all|that)\b|\bmore than\b|\bover\b|\binstead\b|\bso far\b|\bin general\b|\boverall\b|"
    r"\bmost of all\b|\bfor (?:dinner|lunch|breakfast)\b|\bto be honest\b|\bif i am honest\b|\bfor sure\b|"
    r"\bthan\b|\bfrom now on\b|\bgoing forward\b|\bthere\b)"
)
LEADING = re.compile(
    r"^(?:to |go to |going to |go back to |going back to |visit |visiting |travel to |travelling to |traveling to |"
    r"try |trying |eat |eating |having |have |watch |watching |read |reading |listen to |listening to |"
    r"drink |drinking |play |playing |the idea of |the thought of |anything with |any |all |all kinds of |"
    r"most |some |the |a |an |my |our |your |their |his |her |these |those |that |this |kinds of |types of |"
    r"sorts of |kind of |sort of |lots of |a lot of |eating out at |going for |buying |wearing |using |"
    r"staying in |staying at |flying |taking |getting |doing |seeing |exploring |heading to |about |"
    r"over |on |in |at |with |for |of |more |less |too much |much |really |good |great |nice |decent |"
    r"proper |real |authentic |fresh |homemade |home-made |just |even |especially |particularly |mostly |)+"
)
SPEECH = re.compile(r"^.*?\b(?:says|said|thinks|thought|feels|felt|told me|tells me|mentioned|insists|reckons|swears|claims)\b(?: that)?\s+")
PRE_STRIP = re.compile(
    r"^(?:(?:and|but|so|also|oh|well|honestly|ok|okay|anyway|actually|sadly|unfortunately|lately|"
    r"these days|nowadays|now|though|i think|i guess|i feel like|i have to say|turns out|it turns out|"
    r"i have realised|i have realized|i realised|i realized|i found that|i find that|i noticed|i have noticed)"
    r"\s*,?\s+)+"
)
SPLIT_LIST = re.compile(r"\s*(?:,\s*(?:and|or)?\s*|\s+and\s+|\s+or\s+|\s*&\s*|\s*/\s*|\s+as well as\s+|\s+plus\s+)\s*")


def _clean_object(obj: str) -> str:
    obj = obj.strip(" .,!?:;'\"")
    m = OBJ_STOP.search(obj)
    if m:
        obj = obj[: m.start()]
    obj = obj.strip(" .,!?:;'\"")
    prev = None
    while prev != obj:
        prev = obj
        obj = LEADING.sub("", obj).strip()
        for ctx_rx in [r for _, r in CONTEXT_RE] + [r for _, r in ASPECT_RE if True]:
            obj = ctx_rx.sub("", obj).strip()
    obj = re.sub(r"\s+", " ", obj).strip(" .,!?:;'\"")
    obj = re.sub(r"\b(?:it|them|that|this|one|ones|things?|stuff)$", "", obj).strip()
    return obj


def split_clauses(text: str) -> list[str]:
    t = normalise(text)
    parts = re.split(r"(?<=[.!?;])\s+|\s*;\s*|\s*\.\s+|\s*!\s*|\s*\?\s+|\s+but\s+|,\s*but\s+|\s+although\s+|"
                     r"\s+though\s+(?=i\b|we\b|my\b)|\s+whereas\s+|\s+however,?\s+|\s+while\s+(?=i\b|my\b|we\b)|"
                     r",\s*(?=(?:and\s+)?(?:i|we|my)\s)|\s+and\s+(?=(?:i|we|my \w+|he|she|they|the kids)\s)", t)
    return [p.strip(" ,") for p in parts if p and p.strip(" ,")]


class RuleExtractor(Extractor):
    """Deterministic, offline extractor. ``name`` is recorded as provenance on every node."""

    name = "rule-v1"
    _last_np: str | None = None

    def extract(self, text: str, stated_at: datetime | None = None, turn_id: str | None = None) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        carried_role: str | None = None
        carried_ctx: str | None = None
        self._last_np: str | None = None
        for clause in split_clauses(text):
            if clause.endswith("?") or (QUESTION.match(clause) and not re.search(r"\bi\b", clause)):
                continue
            stmts, carried_role = self._clause(clause, carried_role, carried_ctx)
            lead = re.match(r"^(?:(?:and|but|so|also|honestly|well)\s+)*(?P<np>[a-z][a-z' -]{1,40}?)\s+(?:is|are|was|were)\b", clause)
            self._last_np = lead.group("np") if lead and not stmts else None
            # a lead-in like "On a night out," is its own clause after splitting: carry its context forward
            ctx_here = next((c for c, rx in CONTEXT_RE if rx.search(clause)), None)
            carried_ctx = ctx_here if (ctx_here and not stmts) else None
            for s in stmts:
                s.update(
                    source_text=text.strip(),
                    stated_at=stated_at.isoformat() if stated_at else None,
                    turn_id=turn_id,
                    extractor=self.name,
                )
                out.append(s)
        # de-duplicate identical (surface, who, intent, context, aspect) - keep the strongest
        seen: dict[tuple, dict] = {}
        for s in out:
            k = (s["surface"], s["who"], s["intent"], s["context"], s["aspect"])
            if k not in seen or s["strength"] > seen[k]["strength"]:
                seen[k] = s
        return list(seen.values())

    # ------------------------------------------------------------------ clause
    def _clause(self, clause: str, carried_role: str | None, carried_ctx: str | None = None) -> tuple[list[dict[str, Any]], str | None]:
        results: list[dict[str, Any]] = []
        context = next((c for c, rx in CONTEXT_RE if rx.search(clause)), carried_ctx or "any")
        hedged = bool(HEDGES.search(clause))
        reason_hint = next((r for r, rx in REASON_RE if rx.search(clause)), None)

        for rx, items in IDENTITY:
            if rx.search(clause):
                who = self._who(clause, len(clause), carried_role)
                for surface, pol, reason in items:
                    results.append(self._mk(surface, pol, 0.9, who, "general", "standing", context, reason, hedged, "identity"))
                return results, who if who != "self" else carried_role

        for srx, spol, sstr in SANDWICH:
            sm = srx.search(clause)
            if sm:
                obj = _clean_object(sm.group("obj"))
                if obj:
                    who = self._who(clause, sm.start("obj"), carried_role)
                    reason = reason_hint or "unspecified"
                    return [self._mk(o, spol, sstr, who, "general", "standing", context, reason, hedged, "no-x-for-me")
                            for o in [_clean_object(p) for p in SPLIT_LIST.split(obj)] if o], carried_role

        best = None
        for cue, rx in COMPILED:
            m = rx.search(clause)
            if m:
                best = (cue, m)
                break
        if best is None:
            return results, carried_role
        cue, m = best
        cue_start, cue_end = m.start(1), m.end(1)

        if cue.mode == "post":
            raw_obj = clause[cue_end:]
            who = self._who(clause, cue_start, carried_role)
        else:
            raw_obj = clause[:cue_start]
            raw_obj = SPEECH.sub("", raw_obj)
            who = self._who(clause, cue_start, carried_role)
            for _, rrx in ROLE_RE:
                raw_obj = rrx.sub("", raw_obj)
            raw_obj = PRE_STRIP.sub("", raw_obj.strip())
        if PRONOUN_OBJ.match(raw_obj.strip(" .,!?")) and self._last_np:
            raw_obj = self._last_np  # "Sushi is the exception though, I love it." -> it = sushi
        obj = _clean_object(raw_obj)
        if not obj:
            return results, carried_role

        intent = cue.intent or ("past" if PAST_HINT.search(clause) and cue.polarity != "neutral" and "used to" not in clause and re.search(r"\b(?:was|were|went|had|did|loved|liked|enjoyed|hated)\b", clause) else None)
        if intent is None and PLAN_HINT.search(clause) and cue.intent is None and re.search(r"\b(?:going|go|visit|travel|trip|plan|want)\b", clause):
            intent = "plan"
        intent = intent or "standing"
        aspect = cue.aspect or next((a for a, arx in ASPECT_RE if arx.search(clause)), "general")
        reason = cue.reason or reason_hint or ("taste" if cue.polarity in ("likes", "dislikes") and intent == "standing" else "unspecified")
        if reason == "budget" and aspect == "general" and cue.polarity != "likes":
            aspect = "price"
        for surface in [p for p in SPLIT_LIST.split(obj) if p]:
            surface = _clean_object(surface)
            if surface and len(surface) <= 60:
                results.append(self._mk(surface, cue.polarity, cue.strength, who, aspect, intent, context, reason, hedged, cue.pattern[:40]))
        return results, (who if who != "self" else carried_role)

    @staticmethod
    def _who(clause: str, cue_start: int, carried_role: str | None) -> str:
        head = clause[:cue_start]
        for role, rx in ROLE_RE:
            m = rx.search(head)
            if m:
                # "I cook for my wife" - the role must be the subject, i.e. no "i"/"we" before it
                before = head[: m.start()]
                if not re.search(r"\b(?:i|we)\b", before):
                    return role
        if PRONOUN_RE.match(head.strip()) and carried_role:
            return carried_role
        return "self"

    @staticmethod
    def _mk(surface, polarity, strength, who, aspect, intent, context, reason, hedged, cue) -> dict[str, Any]:
        from ..belief import initial_strength

        return {
            "surface": surface,
            "polarity": polarity,
            "who": who,
            "aspect": aspect,
            "intent": intent,
            "context": context,
            "reason": reason,
            "strength": initial_strength(strength, hedged),
            "hedged": hedged,
            "cue": cue,
        }
