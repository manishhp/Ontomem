# Ten-minute demo script

Start: `ontomem serve` (or `docker compose up`) → http://localhost:8000. Works fully offline.

**1. The problem (1 min). Login page.**
Read the four-line story on the left: March, June, June, October. "Mem0 and Zep let a language model decide which old
fact a new one replaces. When it guesses wrong, either a stale fact stays, or a valid one disappears."

**2. The write path (2 min). Continue as Asha Rao → Chat.**
- Set *Message date* to a past date, type **"I love coffee."** Show the rail: extract → map (Food › Beverage › Coffee) →
  SHACL → slot key → **NEW**.
- Move the date forward and type **"Coffee doesn't agree with me any more."** → **R1**: same slot, the old value is retired.
- Type **"My wife hates mushrooms."** → **R6**: a separate subject.

**3. Exceptions and the clarifying question (2 min).**
- **"I avoid seafood, but I love sushi."** → R2.
- **"No seafood at all for me now."** → R3, the purple question appears. Answer it.
- Ask **"Any dinner ideas?"** → open *Why?* on a suggestion: the proof tree shows which statement decided, which was
  defeated, and the counterfactual.

**4. Memory over time (1 min).**
- *Memory graph*: drag the slider back. Retired values reappear as the belief of that moment.
- *Slot timeline*: the spicy-food row shows the March value ending in June.

**5. Why not just match words? (1 min). Compare → "Someone else's preference" → Run both.**
The baseline overwrites "you like mushrooms" with your wife's dislike. OntoMem keeps both.

**6. The ontology grows (1 min). Ontology → Review queue.**
"kombucha" (from Asha's history) is unmapped. Approve a placement or place it under *Beverage*. The change log shows
the new version, and Asha's earlier kombucha statement is re-keyed.

**7. The evidence (2 min). Evaluation lab.**
- Main results: 98.5% vs 52% / 85% clean retrieval; p-values.
- Ablation: what each part buys.
- p/q model: the closed form matches simulation.
- Failure browser: every remaining OntoMem error is a mapping miss you can read.

**Backup if anything fails:** `ontomem scenario override` prints the R3 scenario in a terminal, and
`docs/screenshots/` has every screen.
