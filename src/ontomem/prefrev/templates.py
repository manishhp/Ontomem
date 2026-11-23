"""Sentence templates for PrefRev, in two disjoint sets.

Set ``A`` is used for the train/dev splits; set ``B`` for the test split. The test
split therefore contains phrasings that were never used while developing the
extractor, so Mode-B (end-to-end) numbers are not tuned to the test sentences.
Some B phrasings are deliberately hard (no explicit cue word); extraction failures
on them are reported, not hidden.

Placeholders: {x} surface, {X} capitalised surface, {Who} third-party subject
("My wife"), {ctx} context lead-in ("At work,").
"""

from __future__ import annotations

# (polarity, intent) -> set -> list of first-person templates
FIRST: dict[tuple[str, str], dict[str, list[str]]] = {
    ("likes", "standing"): {
        "A": ["I love {x}.", "I really enjoy {x}.", "{X} is my favourite.", "Big fan of {x}.",
              "I'm really into {x} these days.", "I like {x} a lot."],
        "B": ["Honestly, {x} is the best.", "Can't get enough of {x}.", "I adore {x}.",
              "Lately I've been loving {x}.", "{X} never gets old for me.", "I'm a huge fan of {x}.",
              "I could have {x} every single day."],
    },
    ("dislikes", "standing"): {
        "A": ["I don't like {x}.", "I hate {x}.", "Not a fan of {x}.", "{X} is not my thing.", "I dislike {x}."],
        "B": ["I can't stand {x}.", "{X} does nothing for me.", "I'm not really into {x}.", "I've gone off {x}.",
              "{X} gets on my nerves.", "Ugh, {x}. No thanks."],
    },
    ("avoids", "standing"): {
        "A": ["I avoid {x}.", "I stay away from {x}.", "I'm allergic to {x}.", "I don't eat {x}."],
        "B": ["{X} doesn't agree with me.", "I've cut out {x} completely.", "{X} makes me sick.",
              "I've given up {x}.", "I steer clear of {x} these days.", "No more {x} for me."],
    },
    ("neutral", "standing"): {
        "A": ["I don't mind {x}.", "I'm okay with {x}."],
        "B": ["I can take or leave {x}.", "{X} is fine, I guess."],
    },
    ("likes", "past"): {
        "A": ["We loved our {x} trip last year.", "I had a great time in {x}.", "{X} was amazing when we went."],
        "B": ["Visiting {x} back in 2023 was magical.", "I really enjoyed {x} last summer.",
              "Our holiday in {x} was unforgettable."],
    },
    ("dislikes", "past"): {
        "A": ["I had a terrible time in {x}.", "{X} was a disappointment when we went."],
        "B": ["Honestly {x} was overrated last time.", "We hated {x} on our last trip."],
    },
    ("likes", "plan"): {
        "A": ["Planning to go to {x} next year.", "I want to visit {x} soon.", "I'd love to go to {x} next summer."],
        "B": ["Thinking of heading to {x} in December.", "{X} is on my bucket list.",
              "We're hoping to visit {x} next spring."],
    },
}

# Revisions: the second statement of a "replace" case, by tier
REVISE: dict[tuple[str, str], dict[str, list[str]]] = {
    # (new polarity, tier)
    ("dislikes", "explicit"): {
        "A": ["I don't like {x} anymore.", "I no longer like {x}.", "I've gone off {x} lately."],
        "B": ["I'm over {x} now.", "I'm sick of {x} these days.", "Not into {x} anymore, honestly."],
    },
    ("avoids", "implicit"): {
        "A": ["{X} doesn't agree with me any more.", "{X} makes me sick these days."],
        "B": ["{X} upsets my stomach now.", "Lately {x} just doesn't sit well with me.", "{X} gives me heartburn now."],
    },
    ("avoids", "indirect"): {
        "A": ["My doctor told me to cut out {x}.", "I've had to give up {x} for health reasons."],
        "B": ["Doctor's orders: no more {x}.", "I was told to stay off {x} for a while."],
    },
    ("likes", "explicit"): {
        "A": ["I've come around to {x}.", "These days I actually like {x}.", "I've started enjoying {x}."],
        "B": ["Turns out I love {x} now.", "{X} has grown on me.", "I'm back into {x}."],
    },
    ("dislikes", "implicit"): {
        "A": ["{X} is boring now.", "I'm tired of {x}."],
        "B": ["{X} is overrated, if I'm honest.", "I've had enough of {x}."],
    },
}

THIRD: dict[tuple[str, str], dict[str, list[str]]] = {
    ("likes", "standing"): {
        "A": ["{Who} loves {x}.", "{Who} is a big fan of {x}.", "{Who} really enjoys {x}."],
        "B": ["{Who} is obsessed with {x}.", "{Who} adores {x}.", "{Who} can't get enough of {x}."],
    },
    ("dislikes", "standing"): {
        "A": ["{Who} hates {x}.", "{Who} doesn't like {x}.", "{Who} can't stand {x}."],
        "B": ["{Who} is not a fan of {x}.", "{Who} dislikes {x}.", "{Who} is not into {x}."],
    },
    ("avoids", "standing"): {
        "A": ["{Who} is allergic to {x}.", "{Who} avoids {x}.", "{Who} doesn't eat {x}."],
        "B": ["{Who} won't touch {x}.", "{Who} stays away from {x}.", "{X} makes {whom} sick."],
    },
}

CONTEXT_LEAD: dict[str, dict[str, list[str]]] = {
    "work": {"A": ["At work,", "At the office,"], "B": ["For work lunches,", "During work,"]},
    "home": {"A": ["At home,"], "B": ["When I cook at home,"]},
    "travel": {"A": ["When travelling,", "On trips,"], "B": ["On holiday,", "When I'm on the road,"]},
    "family": {"A": ["With the kids,", "With family,"], "B": ["With my family,", "For family dinners,"]},
    "social": {"A": ["With friends,", "At parties,"], "B": ["On a night out,", "With my friends,"]},
}
CONTEXT_BODY: dict[str, dict[str, list[str]]] = {
    "likes": {"A": ["I love {x}.", "I always go for {x}."], "B": ["I really enjoy {x}.", "I prefer {x}."]},
    "dislikes": {"A": ["I don't like {x}.", "I hate {x}."], "B": ["I can't stand {x}.", "I'm not into {x}."]},
    "avoids": {"A": ["I avoid {x}.", "I stay away from {x}."], "B": ["I steer clear of {x}.", "I don't eat {x}."]},
}

PRICE: dict[str, list[str]] = {
    "A": ["{X} is too expensive for me.", "{X} is way too pricey."],
    "B": ["{X} costs too much these days.", "{X} is overpriced, honestly."],
}

ROLE_SUBJECT = {
    "partner": ["My wife", "My husband", "My partner"],
    "child": ["My son", "My daughter", "The kids"],
    "parent": ["My mom", "My dad"],
    "friend": ["My best friend", "My friend"],
}
ROLE_OBJECT = {"My wife": "her", "My husband": "him", "My partner": "them", "My son": "him", "My daughter": "her",
               "The kids": "them", "My mom": "her", "My dad": "him", "My best friend": "them", "My friend": "them"}

NOISE: dict[str, list[str]] = {
    "A": ["Long day at work today.", "Thanks, that helps!", "What's the weather like this weekend?",
          "I saw a documentary about {x} yesterday.", "My friend went to {x} last week.", "Remind me to call mom.",
          "Ok cool.", "Can you help me plan my week?"],
    "B": ["Ugh, Mondays.", "Got it, thanks.", "I read an article about {x} this morning.",
          "There's a new place near my office that does {x}.", "Haha nice.", "My neighbour keeps talking about {x}.",
          "Let me think about it."],
}

PROBES: dict[str, dict[str, list[str]]] = {
    "Food": {"any": ["Any dinner ideas?", "What should I eat tonight?", "Suggest something for lunch."],
             "work": ["What should I get for a work lunch?"], "family": ["What should we cook for dinner with the kids?"],
             "home": ["What should I cook at home tonight?"], "travel": ["What should I eat when travelling?"],
             "social": ["Where should we go out to eat with friends?"]},
    "Travel": {"any": ["Where should I go for my next holiday?", "Any trip ideas?"],
               "family": ["Where should we go on a family trip?"], "work": ["Any ideas for my work trip?"]},
    "Screen": {"any": ["What should I watch tonight?", "Recommend a show to binge."],
               "family": ["What can we watch with the kids?"], "social": ["What should we watch with friends?"]},
    "Music": {"any": ["Make me a playlist.", "What should I listen to?"], "work": ["What music should I play at work?"],
              "social": ["Music for a party with friends?"]},
    "Books": {"any": ["Recommend a book.", "What should I read next?"], "travel": ["A good book for my trip?"]},
    "Shopping": {"any": ["What should I buy next?", "Any shopping ideas?"], "work": ["What should I buy for work?"]},
}
ROLE_PROBE = {"partner": "for my wife", "child": "for my son", "parent": "for my mom", "friend": "for my friend"}
