# Case study 2: should Olympus use Jev, and how?

The first case study (main README §4) used Olympus to design Olympus itself. This one uses it on a narrower question with a live external model in the loop: whether to integrate [Jev](https://docs.typesafe.ai/introduction), a typed decision model that returns probabilities instead of text, and if so, how.

It is a **single case**. Read the numbers as observations, not results (see "Limits").

Files in this folder:
- `DECISION.md`: the decision record, the vote history and the owner gate.
- `final-decision.md`: the unanimous final text (v4), Parts 1 and 2.
- `arena-result.json`: Arena verdicts, Jev scores and Bradley–Terry rankings. Codes and numbers only, with no proposal or verdict text.

---

## 1. Before the panel: shadow tests

Jev was first run in shadow mode against the labelled record of the first case study. 167 calls were made, with about 0.2 s latency per call.

| Task | Result | Verdict |
|---|---|---|
| Extra Arena judge (15 pairs, both orders) | Agreed with the corrected winner 21/24 without facts and 22/24 with a verified tool list. Picked the clean candidate in 14/14 clean-vs-invented-tool pairs. Picked the first position 63 % of the time. | promising |
| Blocker acceptance (80 blockers, only 5 rejected) | AUC 0.91, but mean P(accept) was 0.69 for accepted vs 0.54 for rejected; at 0.5 it caught 2 of the 5 rejected. The "is it falsifiable?" question said yes 79/80. | not usable |
| Dry-run verdicts (27 rows) | 14/27, below the always-CAUGHT baseline of 22/27. | not usable |

The owner then asked whether Jev could instead act as a **meta-judge**: give each judge's verdict a probability of being correct. This is the annotator-reliability problem of Dawid and Skene (1979).

| Meta-judge task | AUC | Brier (constant baseline) | Reading |
|---|---|---|---|
| Arena verdicts, verdict + both candidates (n = 24) | **0.81** | **0.179** (0.207) | usable. P ≥ 0.7 was 6/6 correct, and when judges disagreed the higher-P verdict was right 6/7 times. |
| Arena verdicts, verdict text only | 0.71 | 0.204 (0.207) | weak |
| Panel AGREE approvals (n = 55, only 10 correct) | 0.66 | 0.227 (0.149) | miscalibrated: too optimistic |

With n = 24, the 95 % interval on that AUC is roughly 0.63–0.99 (Hanley–McNeil), which is why the panel below treats it as a lead, not a fact.

---

## 2. The panel

- **Question:** which Olympus steps should use Jev, how is each one gated, how is Jev called, how is it validated, and what happens when it is unavailable? Jev must stay optional.
- **Tier:** M (decision + build), with a round budget of 3.
- **Seats:** four mid-tier seats, each owning one failure class: evidence and calibration, integration engineering, process architecture, and red team. Four seats give every Arena pair two non-author judges with swapped positions.
- **Jev live, in the roles the shadow tests supported:**
  - P(correct) for every Arena verdict, as a weight, a tie-break and a flag;
  - an extra Arena judge in both orders;
  - shadow P(correct) for every panel approval, logged and revealed only after the chair's tally.

---

## 3. Blind Arena

Four independent proposals (4,738 words in total) were judged in 12 seat verdicts.

| Ranking (Bradley–Terry) | 1st | 2nd | 3rd | 4th |
|---|---|---|---|---|
| Seats, unweighted | **red team 2.95** | integration 0.81 | process 0.23 | calibration 0.01 |
| Weighted by Jev P(correct) | **red team 3.04** | integration 0.76 | process 0.19 | calibration 0.01 |
| With Jev added as a judge | red team 1.41 | calibration 0.96 | integration 0.96 | process 0.66 |

### Jev as meta-judge

- Its P(correct) values were bunched between 0.45 and 0.72.
- It flagged as doubtful (P < 0.5) exactly the two most ambiguous verdicts: a tie (0.45) and a 4-point win (0.48).
- It resolved the single judge disagreement the same way as the majority.
- It did not change the ranking.

### Jev as judge

- **Order consistency:** only 3 of 6 pairs got the same winner in both orders, with a mean confidence of about 0.4.
- **Position bias reversed:** it picked the *second* position 9 of 12 times, against 63 % *first* in the shadow test, so its position bias is unstable.
- **Wrong signal:** all three of its order-consistent picks favoured the proposal that every seat ranked last, in which a seat found a factual misreading. A rule like "count Jev only when both orders agree" would have added three votes for the weakest proposal.

The panel dropped Jev as a judge on this evidence.

---

## 4. Debate and pre-mortem

| Step | AGREE | What happened |
|---|---|---|
| Round 2, draft v1 | 0/4 | 8 blockers, all accepted: no label source for promotion; clustered n; a missing risks section; a blinding leak; an unpinned model alias; a secret scan that fired on ordinary prose |
| Round 3, v2 (budget) | 3/4 | Red-team blocker: the seed answer key was readable by judges, and the effective n was inflated. Applied after the budget; the red team confirmed AGREE |
| Pre-mortem dry run, v3 | — | 5 of 10 known Jev failure modes CAUGHT and 5 PARTIAL: partial outage, key shapes, same-user key access, the unit of analysis, money/security gating |
| Final vote, v4 | **4/4** | unanimous |

### Jev shadow scores on approvals

These were logged only and revealed after the chair's tally.

- **Round 3:** P(correct) of 0.48, 0.48 and 0.45 for the three AGREE votes. All three were below 0.5, and that round did hide a real defect, which the red team found.
- **Final vote:** 0.47, 0.55, 0.50 and **0.24**. The lowest score went to the approval that came with the most reservations.

With these sample sizes this is suggestive at most.

---

## 5. What was decided and built

- **One step only: J1, a meta-check of Arena verdicts.** Jev gives a Noul P(correct) in both candidate orders.
- **It ships at SHADOW**, where it only logs. It can move to ADVISORY only through a pre-registered rule:
  - at least 30 independently authored seed pairs, carrying planted defects, with at least 20 wrong verdicts;
  - a cluster-bootstrap AUC by pair id with a lower bound ≥ 0.70;
  - order stability;
  - Platt scaling on a held-out half.
- **Even at ADVISORY, Jev only adds scrutiny.** It never votes, never gets a Bradley–Terry weight and never removes a check.
- **Safety:**
  - pinned model `jev-1.13.0`, with the response model checked;
  - owner consent to send data, asked in phase 0;
  - no security or money content sent;
  - a raw-text secret scan, which also checks for the literal key;
  - a circuit breaker;
  - if Jev fails, the Arena is identical to one without Jev.
- **Seed answer keys** use commit–reveal and are never visible to judges.

After independent review, two real defects were fixed before the change shipped:
- the secret scan missed tokens at the start of a line, because it scanned the JSON-encoded text;
- seeded Arenas revealed themselves to judges through a directory name and a header file.

The final review killed 21/21 mutants. See `tools/jev_steps.py`, `tools/jev_calibrate.py` and `references/jev.md`.

---

## 6. Lessons

1. **A probability model is a better critic of verdicts than a judge of documents.** Asking Jev "is this verdict right, given both texts?" was useful. Asking it "which text is better?" was not.
2. **Order-swapped judging is not optional.** Jev's position bias flipped direction between two runs.
3. **"Agrees in both orders" is not a quality filter.** Jev was consistent exactly where it was wrong.
4. **Unanimity still needed a pre-mortem.** It found 5 partial gaps after a 3/4 vote.
5. **The red team's own synthesis was the one most attacked by the red team,** and that improved it.

## Limits

- One panel, four seats, all from one model family, and a chair who also ran the Jev scripts.
- Samples are small (12 Arena verdicts; 7 shadow approvals).
- The Arena "truth" is the panel's own aggregate, not an external gold standard.
- Jev results are for `jev-1.13.0` with the exact question texts in `references/jev-questions.json`. Calibration does not transfer to other models or wordings.
