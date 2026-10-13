# DECISION: integrating Jev into Olympus

Status: **panel unanimous (4/4) on `r5-final-v4.md`. Awaiting the owner gate.** Tier M, decision + build, Olympus repo.

## What the panel decided
- **One Jev step only: J1, Arena verdict meta-check.** For each Arena verdict, Jev gives a Noul P(correct). The state is the rubric, the verdict and both candidates, and the question is asked in both candidate orders.
- **Ladder: OFF → SHADOW → ADVISORY → BINDING.** J1 starts in **SHADOW**, where it is logged only. In ADVISORY it can only add scrutiny: it flags low-P verdicts for an extra judge. It never removes a check, never approves, and never gets a Bradley-Terry weight.
- **Rejected on evidence:**
  - Jev as an Arena judge (the live test below);
  - blocker triage;
  - the dry-run cross-check;
  - approval P(correct), which is forbidden inside the skill because it was miscalibrated.
- **Safety:**
  - model pinned to the `^jev-\d+\.\d+\.\d+$` allow-list (`jev-1.13.0`), with the response model checked;
  - owner consent to send data in phase 0;
  - a skip list (security pack, strict or money content, tier S, scan hit);
  - an anchored secret scan that includes the literal key value;
  - any failure → `jev: UNAVAILABLE` and the flow continues;
  - a circuit breaker after 2 consecutive failures;
  - frozen question text hashed with sha256, so calibration is valid only per (model, question hash).
- **Blinding:** the ruling is recorded by code first. `judging-results.md` is written once, after the final BT refit.
- **Promotion to ADVISORY** comes from seeded-defect Arenas, pre-registered:
  - at least 30 distinct, independently authored seed pairs from at least 2 seats, with at least 20 wrong verdicts;
  - cluster-bootstrap AUC by pair id, 2.5th percentile ≥ 0.70, beating the word-count baseline;
  - order stability check;
  - Platt scaling on a held-out half.

  The seed pack and its key live outside every repo and are revealed only after the ruling (commit-reveal). There are demotion and retire rules, and a canary for drift.
- **Files (to build):**
  - `typesafe` adapter in `tools/olympus_call.py`;
  - `tools/jev_steps.py` plus tests;
  - `references/jev.md`, `references/jev-questions.json`, `references/jev-seeds.md` (recipe only);
  - the Arena section in `SKILL.md` and `arena-judging.md`;
  - README and providers example.

## How it was decided
| Step | Result |
|---|---|
| R1 proposals | 4 independent proposals (calibration, integration, process, red team) |
| Blind Arena | 12 seat verdicts. BT: P-RGQ (red team) 2.95 > OYG 0.81 > RII 0.23 > FSF 0.01. Jev-weighted ranking identical. |
| Jev live, meta-judge | P(correct) 0.45–0.72. It flagged the 2 most ambiguous verdicts (a tie and a 4-point gap) and resolved the 1 disagreement with the majority. It did not change the outcome. |
| Jev live, judge | Order-consistent on 3/6 pairs, picked second 9/12, and its 3 consistent picks favoured the last-ranked proposal, which a seat found had a factual misreading. Led to dropping Jev-as-judge. |
| Round 2 | 0/4 AGREE, 8 blockers, all accepted (label source, clustered n, risks, blinding leak, model pin, scan false positives) |
| Round 3 (budget) | 3/4 AGREE. Red-team blocker (seed key readable by judges; inflated n) applied after the budget, and the red team confirmed AGREE |
| Dry run | 5 CAUGHT, 5 PARTIAL, all fixed in v4 |
| Final vote, v4 | **4/4 AGREE** |

**Jev shadow on approvals (logged, not used):**
- **Round 3:** A 0.48, B 0.48, C 0.45. All three were below 0.5, and that round the red team did find a real defect the three had missed.
- **Final:** A 0.47, B 0.55, C 0.50, D 0.24. With n this small, nothing can be concluded.

## Dissent
None open. The red team's round-3 dissent was resolved by the amendment and confirmed.

## Owner decisions (defaults recommended)
1. **Approve the design**, with J1 in SHADOW only. Default: **approve**.
2. **Build it now in the Olympus repo** with builder, independent reviewer and offline tests, like the multi-provider runner. Default: **yes**.
3. **Seed pack:** authoring at least 30 independent seed pairs is a separate calibration run, costing judge runs and Jev calls. Default: **later, as its own run**. J1 stays in SHADOW until then.

## Owner gate (2026-10-13)
The owner answered "sí a las tres, constrúyelo":
1. Design approved.
2. Build now in the Olympus repo.
3. Seed pack deferred to its own run; J1 stays in SHADOW until then.
