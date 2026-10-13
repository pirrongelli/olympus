# R5 final v4: Jev in Olympus

Sources: E1/E2 = `evidence-meta.txt`; A/B/C = `evidence-shadow-tasks.txt`; LIVE = `BOARD.md` "Live Arena evidence"; docs = https://docs.typesafe.ai/models. Intervals 95% Wilson or Hanley-McNeil. (param) = design parameter.

# Part 1. The decision

## 1.1 Rules
1. Jev is optional and advisory. It may add scrutiny, never remove it: no vote, skipped judge, cleared blocker, AGREE or reviewer slot depends on it.
2. Without Jev, Olympus is the same process; any Jev failure becomes `jev: UNAVAILABLE` and the flow continues.
3. Ladder: OFF (default), SHADOW (calls, log only), ADVISORY (may add a blind judgment), BINDING (reserved; no step qualifies, adding one needs a new panel). The owner answers `off|shadow|advisory`; `advisory` is honoured only if `references/jev-calibration.json` exists for the pinned model and question hash, else SHADOW. Creating that file is a reviewed change.

## 1.2 Steps
| Step | Question | State | Level |
|---|---|---|---|
| **J1 Arena verdict meta-check** | `noul` per verdict, "is this verdict correct?", frozen text, asked with the candidates in both orders | rubric, `PAIR/SCORES/WINNER` block, both blind candidates | SHADOW at ship; ADVISORY only after 1.6 |
| J2 Jev as Arena judge | `choice` | any | **OFF, rejected** |
| J3 blocker triage; J4 dry-run cross-check | `noul`; `choice` | any | OFF, rejected |
| J5 stop rule; J6 reviewer over-flagging | `score`; `noul` | any | OFF, needs its own shadow table |
| J7 approval P(correct) | `noul` | any | forbidden |

Evidence boundaries.
- J2: first picks 19/30 (0.46-0.78), same winner in both orders 11/15 (0.48-0.89) (B). LIVE: order-consistent 3/6 (0.19-0.81), second pick 9/12 (0.47-0.91), so no stable direction; all three consistent picks favoured the last-ranked proposal. The 21-22/24 agreement (0.69-0.96) is against mid-tier-judge labels: correlation, not independence.
- J3: 2/5 rejected blockers caught at 0.5 (0.12-0.77), 0/5 at 0.3 (A). J4: 14/27, all 5 true PARTIAL rows read CAUGHT (C); the phase 3 `grep` stays. J7: Brier 0.227 vs constant 0.149, mean P 0.45 vs base rate 0.18 (E2).
- J1: AUC 0.81 (about 0.63-0.99), Brier 0.179 vs 0.207, verdict-only AUC 0.71 (E1); "P>=0.7 right 6/6" lower bound 0.61. LIVE: 12 P values spanned 0.45-0.72, 2 below 0.5, none changed the ranking. E1 and LIVE used one order and different wordings (`run_meta.py`, `jev_arena.py`); the frozen one is the latter and both-orders is untested, so calibration restarts at n=0.

## 1.3 Where J1 acts, and ruling first
- **Order (phase 3 item 8).** After all verdicts are saved, the President writes the ranking and base choice **by blind code** to `$E/jev/ruling.md` with a timestamp. Only then may `jev_steps j1` run (it refuses without that line). P is never shown to judges, seats or the chair earlier.
- **Blinding.** `judging-results.md` is written once, after the final Bradley-Terry refit, with the code-to-seat map and both rankings if they differ. P and ledger rows never appear in it, in votes, `PROPOSAL-v<N>.md` or DECISION.md; those hold only `jev: off|shadow|advisory|skipped (<reason>)|UNAVAILABLE` and flagged pair codes.
- **Aggregate (pre-registered).** Per verdict, P is the mean over both candidate orders; |P_AB - P_BA| is logged. Ties are excluded from J1 and natural-run labels, never flagged.
- **ADVISORY flag, per pair.** Both twins agree on the winner and the lower verdict P is below the action threshold, or either verdict has |P_AB - P_BA| above 0.20 (param). A flagged pair gets one more blind judgment from a Sonnet or Opus non-author who has not judged it, random recorded order; cap 3 per Arena (param). BT is refit with and without those judgments; if the top differs, both rankings are published and the base ruled. J1 itself adds no comparison or weight.
- The higher-P hint on twin disagreements (6/7, n=7) is logged, not used. Halt rules, owner gate, reviews and merge are untouched.

## 1.4 Consent, gating, skip list
- Consent at **phase 0**, written into SKILL.md item 1 itself (the `request-spec` template is a host file): two added lines, `jev: off | shadow | advisory` (default off) and `jev-content: ok | blocked` (default blocked; blocked if the topic touches money, authentication, tenant isolation or a threat model); "ask at the gate" in "Running seats on other providers" is amended. `jev-sent:` (file names, sha256) is appended to the DECISION.md header after phase 3.
- Skip and log `jev: skipped (<reason>)` when: `jev` is off or the key is absent; `jev-content` is not `ok` (strict or money cards are unknown at phase 3, so the topic header decides); tier S or no Arena; breaker open; or the scan hits. `jev_steps` refuses unless `jev` is shadow or advisory and `jev-content: ok`. Only `blind/` files and verdict blocks are sent.
- **Pre-send scan** (a tripwire; patterns in `references/jev.md`, owner-editable; logs the pattern name, never the match): `\bsk-[A-Za-z0-9_-]{20,}`, `\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}`, `Bearer\s+[A-Za-z0-9._~+/=-]{20,}`, `-----BEGIN [A-Z ]*PRIVATE KEY-----`, `[a-z0-9]{20}\.supabase\.co`. Prose such as "risk-based" or "Bearer token" must pass. The scan also refuses on the literal value (16+ chars) of any `*_API_KEY` in the environment or env file (the runner's `redact` list), and on `\bAKIA[0-9A-Z]{16}\b`, `\bghp_\w{30,}`, `\bgithub_pat_\w{30,}`, `\bxox[abp]-[\w-]{10,}`, `postgres(ql)?://[^\s:@]+:[^\s@]+@`, and on `\b(RLS|tenant|payout|IBAN|SWIFT|KYC)\b`; it logs the pattern name and `skipped (scan)`.
- TypeSafe states a no-training commitment; our retention terms are unverified (https://docs.typesafe.ai/legal), so shadow is the owner's choice.

## 1.5 Call contract and fallback
- **Pin.** `model` must match `^jev-[0-9]+\.[0-9]+\.[0-9]+$` (exit 2 otherwise; aliases are never accepted); shipped config `jev-1.13.0`. A response `model` different from the request is exit 4 with ledger note `model_mismatch`.
- Questions are frozen in `references/jev-questions.json` with sha256; ledger rows name wording and hash.
- Any non-zero exit, timeout, 429/5xx after retries, oversize 4xx, redirect or malformed answer is `UNAVAILABLE`. Breaker: 2 consecutive failures write `$E/jev/.open` and skip the run's remaining calls. Timeouts and 408 are not retried. `--timeout` 30 s; `jev_steps` caps each call at about 90 s wall clock (param). Calls are serial and bounded (about 24 per 4-candidate Arena, plus the breaker). A verdict with either order missing has `P = UNAVAILABLE` and `na` in the ledger; its pair is never flagged and it is excluded from AUC; over 10% `na` in a calibration cycle means rerun. Cost: input $0.042 per Mtok, output free (docs): about $0.007 per Arena (my arithmetic, 1.08M tokens over 167 calls).
- `$J/jev.py` is not vendored (import-time key read, `SystemExit`, Bearer-carrying redirects, timeout retries).

## 1.6 Validation, promotion, demotion
- **Store.** One per install, outside every repo: `${OLYMPUS_JEV_DIR:-$HOME/.local/state/olympus/jev}/ledger.jsonl`; text-free rows: run, kind `seeded|natural`, verdict id, both P, wording, question and state sha256, `ruling_sha256` and timestamp (proves ruling-first), resolved model, tokens, latency, label, label source. `jev_steps` appends after each Arena; `$E/jev/` keeps raw per-run files.
- **Promotion labels come from seeded-defect Arenas** (amended after round 3 and the dry run). (1) A seed pair is two independently authored proposals on one question, one carrying one planted, checkable defect (invented tool, false figure, removed gate, including subtle ones), not a minimal pair. A seeded Arena is one seed pair, judged twice with positions swapped. (2) Pack texts live in `${OLYMPUS_JEV_DIR}/seeds/`, outside every repo, and carry no defect marking. **No same-user permission is relied on:** the truth key never exists on disk during judging. The authoring seat returns it to the President, who commits `sha256(key + nonce)` to the ledger, holds key and nonce in context only (never `$E/CHECKPOINT.md`), and writes them to `${OLYMPUS_JEV_DIR}/seeds-key/` only after `ruling.md` and all verdict files exist; `label seeded` verifies the commitment and then labels. A lost key voids the cycle. `jev_steps seeded-run` copies only the chosen `P-<code>.md` files, under fresh random codes, to a new `/var/tmp` directory, and no brief names the seeds path. After judging, `grep -rnE 'jev/seeds|answer.key|seeds-key' <judge outputs>` must print nothing, else the cycle is void. `jev.md` states file modes are no barrier against same-user agents. Each pair is used once. (3) A seeded Arena uses the normal judge brief with no "synthetic" marker and a light-tier judge (light-tier judges missed planted defects in the first case study), so wrong verdicts exist. A verdict is correct iff it picks the defect-free proposal; a TIE is wrong, has no P, is excluded from AUC and its rate is reported. This validates seeded-defect verdicts only (`references/jev.md` says so).
- **Natural-run labels are supplementary** (demotion only): L1 and rework counts at phase 9 item 6; L3 appended by the phase 0 intake of a later card citing the DECISION; optionally a seeded-RNG 25% audit of the rest by two non-author judges, drawn before P is read. Report the labelled fraction with every metric.
- **Canary:** 3 frozen synthetic states (`references/jev-canary.json`) re-asked at each Jev-enabled Arena start. Drift beyond a tolerance (spike in the adapter card: repeat one input; max(0.05, 3 SD), param), a changed pin or hash demotes to SHADOW.
- **Pre-registered promotion SHADOW to ADVISORY:** at least 30 distinct seed pairs authored by at least 2 seats, at least 20 wrong verdicts. Cluster-bootstrap AUC with the **pair id** as the cluster (all orders and judges of a pair resampled together, 10,000 draws): 2.5th percentile at least 0.70 (Hanley-McNeil secondary); AUC above the word-count-difference baseline; median |P_AB - P_BA| at most 0.10 (param). AUC uses raw P (Platt is monotone). Platt is fitted on half the pairs; Brier on the other half is reported, not gated (about 30 rows). Action threshold: the P flagging at most 25% of the fitting half (param).
- **Power limit:** at true AUC 0.80 and 60 rows the rule passes about 46% of the time (seat A's simulation), so HOLD at 60 is "undecided"; extend to 100 rows first.
- **Demotion to SHADOW:** AUC below 0.65 over the last 30 labelled verdicts, or canary drift. **To OFF:** a leak. **Retire:** under 60 labelled verdicts after 30 distinct pairs (param): the owner decides.

## 1.7 Risks
- Gaming and injection: candidates sit in the Jev state and come from agents reading untrusted content (SKILL rule 4). Controls: P hidden from seats; word-count baseline; typed output limits damage to P and a flag.
- Lock-in: Jev only adds judgments; removing it changes nothing ("Jev down" replay). Leakage: default off, phase 0 consent, skip list, hashes; the scan is a tripwire. Drift: pin, response-model check, canary, calibration key.
- Anchoring: ruling first. Position bias: both orders, instability flag, no Jev judge.
- Cost: extra-judgment cap; seeded Arenas cost judge runs and need an owner-approved card.

# Part 2. File changes (repo `<olympus repo>`, nothing applied)

**`tools/olympus_call.py` (adapter `typesafe`, others untouched)**
- `ADAPTERS` gains `typesafe`; constants: base URL `https://api.typesafe.ai`, path `/v1/systemone`, key env `TYPESAFE_API_KEY`, model regex, 30 s timeout. `resolve_target`: `model` must match the regex; there is no alias override.
- `build_request`: Bearer header; payload `{model, state, questions}`. `validate_typesafe_brief`: only `state` and `questions`; each question `type` noul/choice/score, `instructions` non-empty, choice `criteria` at most 255, score 2 to 10 levels (confirm against docs.typesafe.ai/api.md); at most 100,000 bytes (param; proxy for the 32k-token state limit). Failure: exit 2, no request.
- `_text_from_typesafe`: answered ids equal requested ids; noul in [0,1]; choice inside criteria; score legend checked; probabilities sum within 0.05 of 1; response `model` equals request; else exit 4. `--out` receives the response JSON.
- `run()`: `--max-tokens` rejected here; parser `--timeout` default `None`, guarded `<= 0` check, per-adapter default (600 s elsewhere); `--dry-run` prints the pin, reads no brief; `.meta.json` adds `resolved_model`, `state_bytes`, `state_sha256`, `question_ids`, never state text.

**`tools/test_olympus_call.py`** (existing tests unchanged; loopback `_Handler` gets `/v1/systemone`): `test_typesafe_`: noul, choice, score success; `_alias_rejected` (`jev-latest`, `jev-preview`, unlisted names); `_response_model_mismatch_exit4`; `_invalid_brief_exit2_sends_nothing`; `_malformed_answer_exit4` (noul 1.2, choice outside criteria, probabilities off, missing or extra id); `_401_and_422_fail_fast`; `_429_then_success`; `_timeout_not_retried`; `_redirect_not_followed`; `_missing_key_exit3_zero_requests`; `_no_key_or_state_in_out_or_meta`; `_meta_has_resolved_model_and_hashes`; `_dry_run_shows_pin_without_brief`.

**`tools/jev_steps.py`, `tools/test_jev_steps.py` (new, stdlib):** hash-check questions; build both-order states; `presend_scan`; breaker; call cap; refuse until `ruling.md` exists; append to the store; `seeded-run` and label subcommands (`seeded`, `l3`); `ruling_sha256` on rows; commitment check. Calls `olympus_call.py` as an argv list; exits 0 on `UNAVAILABLE` (runner codes 3 and 4 go to the ledger). Tests: order swap, scan hits and non-hits, breaker, no text in rows, `ruling.md` refusal, seeded labels and commitment, `na` handling, ties, `jev-content` refusal.

**`tools/jev_calibrate.py`, `tools/test_jev_calibrate.py` (new, last):** reads the store; Wilson, Hanley-McNeil, cluster-bootstrap AUC, word-count baseline, Platt fit by pair split, labelled fraction, `PROMOTE|HOLD`. Fixtures: Wilson 21/24 = 0.69-0.96, a toy-ledger bootstrap.

**New references:** `references/jev.md` (rules, ladder, consent, skip list, scan patterns, store schema, labels, promotion, power limit, demotion); `references/jev-questions.json` (both wordings, sha256, tie handling); `references/jev-canary.json`; `references/jev-seeds.md` (authoring recipe only; the pack lives in `${OLYMPUS_JEV_DIR}/seeds/`); `references/jev-calibration.json` only at promotion, keyed by (`resolved_model`, question sha256).

**`SKILL.md`:** Phase 0 item 1 (the `jev:` and `jev-content:` lines), item 3 (header `jev:`, `jev-content:`, `jev-sent:`) and one line: a card citing an earlier DECISION appends its L3 rows; Phase 3 item 8 (ruling file, skip list, never votes); Phase 9 item 6 (L1 labels, rework counts); section 6 row "Jev used as a vote or to skip scrutiny", check `grep -nE 'P\(correct\)|p_correct' $B/judging-results.md $B/r*-*.md $B/PROPOSAL-v*.md` prints nothing; "Running seats on other providers": Jev is not a seat, same data-sharing rule, "ask at the gate" fixed; References 12 to 13.

**`references/arena-judging.md`:** "Advisory meta-check (Jev)" after Aggregation (both orders, flag, extra non-author judgment, `ruling.md`, BT refit); a Blinding note (map published once); a "Corrections" line: Jev as judge rejected. **`references/evals.md`:** replays: Jev down gives an identical Arena; a high P cannot remove a judgment; breaker after 2 failures.

**`README.md`:** rewrite "Optional: Jev" (1.2 table, pin, data sharing, pricing, seeded calibration), drop `pip install typesafe-sdk`, add a section 7 bullet. **`olympus.providers.example.json`:** target `jev` (`typesafe`, `jev-1.13.0`). **`.env.example`:** comment: the runner reads the key.

# Changes v1→v2
- K1/K2: seeded-defect Arenas give promotion labels; one per-install store; cluster bootstrap by pair id over at least 30 distinct pairs (superseded the earlier 5-Arena rule); aggregate and ties fixed; power limit stated; Fisher step dropped.
- K3: restored 1.7 Risks. K4: ruling in `$E/jev/ruling.md` by code; `judging-results.md` written once after the final refit; extra judge blind.
- K5: model allow-list regex plus response-model check. K6: anchored scan patterns, owner-editable list.
- Minors applied: SKILL.md consent text, `advisory` gated on the calibration file, pair-level flag, scoped grep, timeout guard, call cap, score and id checks, dry-run pin, `jev-sent:` timing, J2 wording.

- Post-budget amendment (round 3, red-team B1, applied by the chair): independent seed pairs, pack outside every repo, ≥ 30 distinct pairs by ≥ 2 seats, pair-id clusters, no synthetic marker.

# Changes v3→v4
Dry-run items applied: 5 (`na` verdicts, rerun above 10%), 6 (extra scan patterns and literal `*_API_KEY` values), 7 (no reliance on file modes: key held by the President as a hash commitment and written only after the ruling, fresh-code copy, void-on-leak grep), 8 (one pair per seeded Arena, Retire at 60 verdicts after 30 pairs, Platt by pair, ties), 10 (`jev-content` header, default blocked, `jev_steps` refusal, domain-word scan). Notes: `allow_alias` dropped (it conflicted with the response-model check); `ruling_sha256` and timestamp on every ledger row.
