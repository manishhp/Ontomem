# Evaluation protocol and pre-registration

This is the Gate G3 artefact: research questions, hypotheses, metrics and analysis, fixed **before** the test-split
runs. Development used the train and dev splits only; Gate G2 (`make gate-g2`) runs on dev.

## Research questions and hypotheses

| | Question | Hypothesis | Outcome (test split) |
|---|---|---|---|
| RQ1 | Does ontology keying reduce merge errors vs LLM/rule keys? | H1: S3 has lower *p* and *q* than S1 and S2 | **Supported vs S1** (p 1.9% vs 44.6%; q 0.13% vs 6.9%). S2 needs an LLM run. |
| RQ2 | Is it the ontology structure or just determinism / supervision? | H2: S5 (trained, no hierarchy) does not reach S3; S2b closes most of S2's gap | **Supported for S5** (clean 84.6% vs 98.5%; p 30% vs 1.9%). S2b needs an LLM run. |
| RQ3 | Which components carry the effect? | H3: −SKOS hurts exception cases most; −intent hurts Paris-type cases; −SHACL raises corrupt writes | **Supported:** −SKOS +6.6 pts harmful; −intent −11.5 pts clean with 11.8% contamination; −SHACL +3.4 pts stale under noise |
| RQ4 | Does cleaner memory improve downstream outcomes? | H4: lower stale exposure → higher NDCG@5 and fewer harmful suggestions | **Supported:** NDCG@5 0.992 (S3) vs 0.905 (S1); harmful@5 0.5% vs 1.3% |
| RQ5 | Can the ontology grow without breaking consistency? | H5: evolution raises coverage with no rise in false merges | **Supported:** mapped 89% → 99.8%; q 0.13% → 0.07% |
| RQ6 | Does it meet serving deadlines and scale? | H6: read p95 < 50 ms at 10k statements; write p95 < 200 ms (excl. LLM) | **Supported, narrowly:** read p95 47 ms, write p95 0.28 ms |

## Data: PrefRev v2

- **Generator:** `ontomem.prefrev.generator`, seed 7, version `prefrev-2.0`. Train 150, dev 50, test 150 users. Each user has 7–12 cases,
  interleaved and mixed with noise turns (18% of turns), over about a year.
- **Ten case types:** replace, reinforce, exception, broad_override, intent, context, trap (false-merge bait), third_party, price, coverage.
- **Gold:** every statement has a gold slot key. Gold state is the fixed retirement rule applied to gold keys
  (oracle replay over an ontology extended with the coverage concepts).
- **Leakage controls:** template set A (train/dev) and B (test) are disjoint, and the validator checks it. No test sentence appears in train.
  Paraphrases are not ontology labels, also checked. 12.1% of test statements use 23 concepts absent from the shipped ontology.
- **Size justification** (`ontomem.prefrev.power`): McNemar with 10% discordant pairs needs 870 paired probes to detect a 3-point
  difference (α = 0.05, power 0.8). The test split has 1,506 probes.

## Settings

- **Mode A (gold extraction):** every system receives the gold statement fields; only the key decision differs. This is the base paper's setting.
- **Mode B (end-to-end):** every system receives the rule extractor's output on the raw turn. Candidates are aligned to gold statements by
  concept, then who, then surface overlap. Unaligned candidates are *spurious*.
- **Noise:** Mode A with 15% of candidates corrupted the way small LLMs fail (invalid enum values, "high" for a number, percentages).

## Systems (retirement rule identical in all)

S0 append-only · S1 rule extractor with surface-phrase key · S2 Mem0-style LLM (ADD/UPDATE/NOOP over top-5 similar memories) ·
S2b LLM choosing the concept from ontology candidates, then OntoMem's rules · S2-sim gold keys with injected *p*, *q* ·
**S3 OntoMem** · S4 oracle keys · S5 gradient-boosted pairwise "same slot?" classifier on 13 lexical/field features, no ontology,
merge threshold tuned on dev.

All systems share the same concept mapper and the same reader. Every system's read view gets the same state-derived R3 flags,
so differences come only from keying.

## Metrics (`evaluation/metrics.py`)

- **Missed-merge rate *p*:** over revisions (gold slot already has a different value) where the system had an active node for that slot,
  the share not merged into it.
- **False-merge rate *q*:** over all writes, the share merged into a node of a different gold slot. *Valid erasures* are false supersedes.
- **Probe metrics:** retrieval is held at oracle *topic* relevance (gold domain), filtered by the system's own who/intent/context.
  - *Stale exposure:* the block contains a value that is no longer its slot's gold value.
  - *Contamination:* the block contains a node about another person, intent or context, or a spurious one.
  - *Completeness:* every gold-active slot is present with its current value.
  - *Clean retrieval:* complete, not stale and not contaminated.
- **Downstream:** NDCG@5 against relevance computed from the gold memory with the same reader. *Harmful@5:* a top-5 item the gold memory vetoes.
- **Cost:** ms per write, ms per probe. LLM calls and tokens are recorded by the LLM systems.

## Analysis

- 95% confidence intervals by **cluster bootstrap over users** (1,000 resamples). Probes of one user are correlated.
- Paired **McNemar** tests on per-probe clean retrieval, S3 vs every other system, **Holm** correction across comparisons.
- Error attribution by pipeline stage: extraction field, spurious extraction, mapping (unmapped / wrong concept / earlier statement),
  malformed (noise), keying.

## Deviations from plan, stated plainly

- Ontology size: 229 concepts and 792 labels (the plan said about 400 concepts). Enough for the 6 domains; evolution grows it.
- Coverage split: 12.1% of test statements, not 15%.
- The small user study, Wikidata alignment and a Locust load test were cut, in the planned cut order.
  RQ6 is answered with an in-process scale benchmark (`evaluation/scale.py`) instead of HTTP load.
- S2/S2b and PERMA answer accuracy require a language model and were not run.
