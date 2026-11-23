# Formal semantics of OntoMem

This document states precisely what the memory does, so it can be checked. It covers what counts as a
slot, which preference replaces which, and what the system believes at read time. Two independent
implementations follow it: `core/reasoner.py` (Python, incremental) and `core/asp/write.lp` (Answer Set
Programming, declarative). They are differential-tested against each other.

## 1. Vocabulary

**Ontology.** A finite set of concepts *C* with a direct-broader relation *B ⊆ C × C* (SKOS `skos:broader`;
polyhierarchy allowed, acyclic). Write *c ⊏ d* for the transitive closure of *B* (c is strictly narrower than d)
and *c ⊑ d* for its reflexive closure. Phrases outside the ontology get a concept `unmapped:<phrase>`, which
relates to nothing but itself.

**Contexts.** *X = {any, home, work, travel, social, family}* with *x ⊑ any* for all *x* and *x ⊑ x*.

**Statement.** *s = (who, what, aspect, intent, context, polarity, t)*:

| Field | Values | Role |
|---|---|---|
| who | self, partner, child, parent, friend, colleague, sibling | key |
| what | *C* ∪ unmapped | key |
| aspect | general, price, quality, setting, timing | key |
| intent | standing, past, plan | key |
| context | *X* | key |
| polarity | likes, dislikes, avoids, neutral | **value** |
| t | timestamp (strict total order on statements; ties broken by event sequence number) | time |

**Slot key.** *key(s) = (who, what, aspect, intent, context)*. **Frame.** *frame(s) = (who, aspect, intent)*.

**Sign.** *σ(likes) = +1*, *σ(dislikes) = σ(avoids) = −1*, *σ(neutral) = 0*. Two polarities are **opposite** iff
*σ(p)·σ(q) < 0*. They are **concordant** iff *σ(p) = σ(q) ≠ 0*.

### Design decision D1: polarity is the value, not part of the key

If polarity were part of the key, "I love spicy food" and "spicy food doesn't agree with me" would have
different keys. The second statement could then never retire the first, which is exactly the missed merge
the project exists to remove. The key says *which question* a statement answers; polarity is *the answer*.

### Design decision D1b: the reason is not part of the key

"Doesn't agree with me" (health) and "I love it" (taste) answer the same question about the same thing.
The reason is kept on the node as metadata. Hard constraints come from the polarity `avoids`, not from the reason.

## 2. The product order on keys

For keys *k₁, k₂* in the same frame:

    k₁ ⊏ k₂   iff   what₁ ⊑ what₂  and  context₁ ⊑ context₂  and  k₁ ≠ k₂

This is a strict partial order (irreflexive, transitive). Keys in different frames are incomparable.
`(Sushi, any) ⊏ (Seafood, any)`, `(Coffee, work) ⊏ (Coffee, any)`, but `(Sushi, any)` and `(Seafood, work)` are incomparable.

## 3. Write-time theory

Given a history *H = s₁ … sₙ* in time order:

**Nodes (R5 Reinforce).** For each *sⱼ*, let *prev(sⱼ)* be the latest earlier statement with the same key.
*sⱼ* is **absorbed** if *prev(sⱼ)* exists and has the same polarity. Otherwise *sⱼ* creates a **node**.
An absorbed statement adds evidence and strength to the slot's current node and creates nothing.

*Lemma 1.* The polarity of a slot's current node always equals the polarity of the slot's latest statement.
(Induction on the statements of the slot. This is why comparing with the immediately preceding statement
is enough, and it makes the ASP encoding stratified.)

**Supersession (R1).** A node *n* is **retired** iff a later node with the same key exists. Its successor is the
earliest such node. All other nodes are **active**.

**Derived relations** (on active nodes *n, m* in the same frame):

- **R2 exception:** *key(n) ⊏ key(m)* and the polarities are opposite. Both are kept; *n* wins for everything at or below its key.
- **Refinement:** *key(n) ⊏ key(m)* and the polarities are concordant.
- **R3 needs confirmation:** *n* has an exception-shaped relation to *m* (*key(n) ⊏ key(m)*, opposite polarities), and *last(m) > last(n)*,
  where *last(x)* is the time of the latest statement in *x*'s slot. A broad statement made *after* the specific one
  casts doubt on the exception. The narrower node is kept but **suspended** until the user confirms.
- **R4 orthogonal:** same *what*, keys incomparable (different intent, aspect, or incomparable contexts). No relation; both coexist.
- **R6 third party:** *who ≠ self*. Falls out of the key: a partner's statement can never touch the holder's slots.

R2, R3 and refinement are **functions of the current state**, not decisions recorded at write time. This is what
makes the projection independent of the order in which ties are processed (I3), and what lets every baseline get
the same R3 behaviour at read time in the evaluation.

## 4. Read-time default logic

To decide the stance on concept *c* in context *x* for *(who, aspect, intent)*:

1. **Applicable:** active nodes *n* in the frame with *c ⊑ what(n)* and *x ⊑ context(n)*.
2. **Suspended:** remove nodes flagged by R3.
3. **Minimal:** keep the nodes with no other applicable node strictly below them in the product order (specificity).
4. **Tie-break** between incomparable minimal nodes that disagree: `avoids` wins (safety first), then the most recent.

The decider's polarity is the stance. Nodes removed in step 3 are *defeated* and appear in the proof tree, as do
suspended nodes. An item is **vetoed** if any of its concepts has stance `avoids`.

## 5. Invariants

Each invariant is checked by property-based tests (`tests/test_invariants.py`, Hypothesis, hundreds of random
histories each), and I1/I2 also by SHACL over the RDF projection (`shapes.ttl`).

| | Invariant | Checked by |
|---|---|---|
| I1 | **Key uniqueness:** at most one active node per slot key, at all times | Hypothesis; SHACL `KeyUniquenessShape` |
| I2 | **No silent loss:** every statement is a node or evidence of one; every retired node names a successor with the same key and has `validTo` | Hypothesis; SHACL `RetiredShape`, `SameSlotSupersessionShape`, `NoCycleShape` |
| I3 | **Deterministic replay:** the same timestamped events inserted in any order project to the same graph | Hypothesis (shuffled insertion into the event store) |
| I4 | **Specificity monotonicity:** adding a narrower statement never changes the stance on any concept outside its subtree | Hypothesis |
| I5 | **Idempotence:** repeating a statement never creates a slot or changes a value | Hypothesis |

## 6. Two implementations, one theory

- `core/reasoner.py` is incremental. It keeps indexes by key, by frame-and-concept and by (who, what). After each write
  it recomputes R3 flags only for the written node and the active nodes below it. That is sufficient, because a
  node's flag depends only on the nodes above it.
- `core/asp/write.lp` states sections 3–4 directly as rules over the whole history (about 40 lines of clingo).

`core/asp/crosscheck.py` generates random histories over a sub-hierarchy where slots deliberately collide, runs both,
and compares node sets, active sets, supersession links, exceptions, refinements and R3 flags. Half of the histories
give several statements the same timestamp, so the event-order tie-break (section 2) is exercised too.

**Result:** 3,000 sequences / 42,196 events / 3,168 supersessions / 4,784 exceptions / 2,254 R3 flags. Zero disagreements.

**Bugs it found.** One in each implementation during development, and a third in the pre-release audit:
1. Python counted two `neutral` statements as a refinement. The theory says refinement needs a non-zero shared sign.
2. ASP wrote the context `any` as a bare constant while event facts used the string `"any"`, so context-based
   exceptions never fired.
3. Python compared R3 recency by timestamp alone, so a broad statement made in the same second as an exception never
   raised a clarification. The ASP encoding already used event order, as section 2 specifies. The original generator
   gave every event a distinct hour, so it could not see the difference; with shared timestamps it found 5
   disagreements. Python now breaks ties by event order, and `tests/test_rules.py` covers the case.

`tests/test_verification.py` also plants a bug in the Python reasoner and asserts that the cross-check catches it,
so the differential test is known to be able to fail.

## 7. Relation to belief revision

The write rule is a revision operator in the AGM sense, restricted to a fixed partition of beliefs into slots:

- **Success:** the new value is believed (the latest statement in a slot always decides its value; Lemma 1).
- **Minimal change:** only the node in the revised slot is retired; nothing else is touched (I1, I2, I4).
- **Consistency:** within a slot, at most one value is active (I1).
- **Deliberately dropped: inclusion across slots.** A later broad statement does *not* retract narrower beliefs (R3 keeps them, flagged).
  Silently retracting them would be a false merge by another name, so the system asks instead.

## 8. Belief strength (outside the theory)

Strength and confidence never change *which* node is active. They only affect ranking and packing.

- **Initial strength:** cue intensity × 0.6 if hedged.
- **Reinforcement:** damped noisy-OR, *s' = 1 − (1 − s)(1 − 0.5·s_new)*.
- **Confidence at read time:** *strength · 0.5^(age / half-life)*, with half-life 365 days for standing preferences,
  120 days for plans, and no decay for past experiences.
