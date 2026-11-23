# Viva FAQ: questions an examiner is likely to ask

**Q1. Isn't this just a lookup table with extra steps? Why an ontology?**
A table can say "same key → replace". It cannot say that sushi is a kind of seafood. That is what turns
"avoid seafood + love sushi" into an *exception* rather than a contradiction, and what vetoes prawn curry for someone who
avoids seafood. Ablation: removing the SKOS hierarchy leaves merge rates unchanged (keys are still exact) but adds
**6.6 points of harmful suggestions**. The hierarchy matters at read time.

**Q2. You wrote the benchmark *and* the ontology. Isn't the result circular?**
Partly, and we say so. Mitigations:
- disjoint train/test sentence templates, verified by the validator;
- paraphrases that are not ontology labels, verified;
- 12% of test statements are about concepts deliberately left out of the ontology;
- every OntoMem error is attributed to a pipeline stage.

The residual errors are exactly where you would expect circularity to be exposed: unseen paraphrases. The honest claim is
*"given a concept hierarchy that covers the domain, deterministic keying is near-oracle"*. How much coverage costs is
measured in RQ5.

**Q3. Is the gain from the ontology, or just from being deterministic?**
That is RQ2. S1 is deterministic too (surface key) and gets 52% clean retrieval. S5 learns from 14,829 labelled pairs
without the ontology and gets 85%. OntoMem gets 98.5%. S2b (an LLM constrained to ontology candidates) is implemented to
separate "who chooses the concept" from "having a hierarchy". It needs an LLM run.

**Q4. How do you know your rules are correct?**
Three ways:
1. A formal specification (`formal_semantics.md`) with five invariants, each property-tested on hundreds of random histories.
2. A second, independent implementation in Answer Set Programming. The two agree on 3,000 random histories.
3. A test that plants a bug and checks the cross-check catches it.

The cross-check found one real bug in each implementation during development.

**Q5. What happens when a broad statement comes *after* the exception?**
Rule R3. "Love sushi" (April), then "no seafood at all now" (July). We keep both, but the sushi preference is suspended:
the broader, later statement applies until the user confirms. The assistant asks "Does that include sushi?", and the
answer is stored as a statement, so it is replayable. Without R3, harmful suggestions rise by 2.1 points.

**Q6. Why is polarity not part of the slot key, when the slides show `intent = likes`?**
Because then "love spicy food" and "spicy food doesn't agree with me" have different keys and can never replace each
other. That is the missed merge we set out to remove. The key is the question; polarity is the answer (decision D1).

**Q7. Your end-to-end numbers are much lower (61%). Is the method weak?**
Every system drops, including oracle keys (61.3%). With the same extractor, OntoMem is statistically indistinguishable
from oracle keys (p = 0.12). The drop comes from the offline rule extractor missing 18% of test statements, whose
phrasings it was never shown. That is a finding: once keying is solved, extraction is the bottleneck. The architecture
lets an LLM extractor plug in, with SHACL guarding its output.

**Q8. What does SHACL actually buy you?**
Closed-world guarantees on what enters memory. Under 15% malformed extractions (the way small LLMs get JSON wrong),
SHACL + repair cuts stale exposure from 10.6% to 7.2% and fixes all 451 malformed candidates. It also checks graph-level
invariants (no two active values per slot; every retired value names its successor) over the whole memory.

**Q9. Why should we believe the p/q analysis?**
We derive a closed form for stale exposure as a function of the missed-merge rate *p* and false-merge rate *q*. We do not
assume it: we simulate 24 (p, q) settings with 3 seeds each, and it matches within 0.75 points on average (max 1.5).
Any LLM key assigner's measured *p, q* can be plugged in to predict its staleness.

**Q10. How is this different from Zep's bi-temporal graph?**
Zep also closes validity windows instead of deleting, and so do we (`validFrom` / `validTo` / `supersededBy`, plus
event-sourced time travel). The difference is *who decides* that two facts conflict. In Zep it is an LLM judgement; in
OntoMem it is the slot key plus the hierarchy, deterministic and explainable.

**Q11. Can it handle a concept that is not in the ontology?**
Yes, in two ways:
1. It is keyed as `unmapped:<phrase>`, so repeated statements still merge.
2. It is queued for review.

Offline proposals get the right placement first-ranked 40% of the time and within the top 3 72% of the time.
Approving them raised mapped coverage from 89% to 99.8%, and false merges *fell* (RQ5). Earlier statements are
re-keyed by an event, so history stays replayable.

**Q12. Does it scale?**
At 10,000 statements (about 6,400 live slots), a write takes 0.28 ms (p95) and a full read (parse, recall, rank, explain)
takes 47 ms (p95), just inside the 50 ms target. The read path scans all active nodes; a domain index would make it
sub-linear. That is listed as future work.

**Q13. Privacy?**
Each account's memory is isolated (tested). Users can export everything as JSON-LD/Turtle and erase it in one call,
which leaves only a content-free tombstone. See `threat_model.md`.

**Q14. What would you do with more time?**
- Run S2/S2b and an LLM extractor on Ollama, and report their *p, q* against the analytic model.
- Evaluate on PERMA answer accuracy.
- Add a domain index on the read path.
- Run a small user study on whether the explanations help.
