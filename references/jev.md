# Jev in Olympus (optional)

Purpose: the rules for the one optional Jev step, J1, the Arena verdict meta-check. Serves SKILL.md phase 0 (consent), phase 3 item 8 (where it runs) and phase 9 item 6 (labels). Jev is TypeSafe's typed decision model (https://docs.typesafe.ai). Olympus works the same without it: nothing here is required, and any Jev failure becomes `jev: UNAVAILABLE` while the flow continues. Mechanism: `tools/jev_steps.py`, `tools/jev_calibrate.py` and the `typesafe` adapter of `tools/olympus_call.py`.

## 1. Rules
1. Jev is advisory. It may add scrutiny and never remove it: no vote, skipped judge, cleared blocker, AGREE or reviewer slot depends on it.
2. Ladder: **OFF** (default), **SHADOW** (calls, log only), **ADVISORY** (may add one blind judgment), **BINDING** (reserved: no step qualifies, and adding one needs a new panel). The owner answers `off|shadow|advisory`. `advisory` is honoured only if `references/jev-calibration.json` has an entry for the resolved model and the question hash, otherwise it runs as SHADOW. Creating that file is a reviewed change. J1 ships at SHADOW.
3. P is never shown to judges, seats or the chair before the ruling, and never appears in `judging-results.md`, votes, `PROPOSAL-v<N>.md` or DECISION.md. Those hold only `jev: off|shadow|advisory|skipped (<reason>)|UNAVAILABLE` and flagged pair codes.

## 2. Steps and what the evidence allows
| Step | Question | Level |
|---|---|---|
| **J1 Arena verdict meta-check** | `noul` per verdict, "is this verdict correct?", frozen text, both candidate orders | SHADOW at ship; ADVISORY only after section 7 |
| J2 Jev as Arena judge | `choice` | OFF, rejected |
| J3 blocker triage, J4 dry-run cross-check | `noul`, `choice` | OFF, rejected |
| J5 stop rule, J6 reviewer over-flagging | `score`, `noul` | OFF, needs its own shadow table |
| J7 approval P(correct) | `noul` | forbidden |

- J2: first picks 19/30 (0.46-0.78), same winner in both orders 11/15 (0.48-0.89); in the live Arena order-consistent 3/6 and the three consistent picks favoured the last-ranked proposal. The 21-22/24 agreement is against mid-tier-judge labels: correlation, not independence.
- J3: 2/5 rejected blockers caught at 0.5, 0/5 at 0.3. J4: 14/27, all 5 true PARTIAL rows read CAUGHT. J7: Brier 0.227 against a constant 0.149 (mean P 0.45, base rate 0.18).
- J1: AUC 0.81 (about 0.63-0.99), Brier 0.179 against 0.207, verdict-only AUC 0.71, on n=24 with labels from mid-tier judges. The live run's 12 P values spanned 0.45-0.72 and changed no ranking. The earlier and the frozen wordings differ and both orders were never tested, so calibration restarts at n=0.
- J1 validates seeded-defect verdicts only (section 6). It says nothing about verdicts on defect-free pairs of equal quality.

## 3. Where J1 acts
- **Ruling first.** After all verdicts are saved, the President writes the ranking and base choice by blind code to `$E/jev/ruling.md` with a line `timestamp: <ISO 8601>` and a line `base: <code>` (natural-run labels need the base). Only then does `jev_steps j1` run; it refuses without that line. Every ledger row carries `ruling_sha256` and the ruling's timestamp. This is self-reported by the same process: it catches accidents, not tampering, like the file-mode note in section 6.
- **Blinding.** `judging-results.md` is written once, after the final Bradley-Terry refit, with the code-to-seat map and both rankings if they differ. P and ledger rows never appear in it.
- **Aggregate.** Per verdict, P is the mean over both candidate orders; the second order swaps the candidates and relabels the verdict's `PAIR`, `SCORES A/B` and `WINNER` lines (free text is left as written). `|P_AB - P_BA|` is logged. A TIE has no P, is never sent, is excluded from natural-run labels and is never flagged.
- **ADVISORY flag, per pair.** Both twins agree on the winner and the lower verdict P is below the action threshold, or either verdict has `|P_AB - P_BA|` above 0.20. A flagged pair gets one more blind judgment from a Sonnet or Opus non-author who has not judged it, in a random recorded order; cap 3 per Arena. Refit Bradley-Terry with and without those judgments; if the top differs, publish both rankings and rule on the base. J1 adds no comparison and no weight. In SHADOW the flags are written to `$E/jev/flags.json` and nothing is acted on. The higher-P hint on twin disagreements (6/7, n=7) is logged and not used.
- A pair with any `na` or tie verdict is never flagged.

## 4. Consent, gating, skip list
- **Phase 0** adds two lines to the topic header: `jev: off | shadow | advisory` (default off) and `jev-content: ok | blocked` (default blocked; blocked if the topic touches money, authentication, tenant isolation or a threat model). The owner sets them. The President may lower `jev-content` to `blocked` at any time and never raises it to `ok` without the owner. `jev-sent:` (file names and sha256) is appended to the DECISION.md header after phase 3.
- `jev_steps` logs `jev: skipped (<reason>)` and sends nothing when: `jev` is not shadow or advisory; `jev-content` is not `ok`; tier S (`--tier S`) or no Arena; there is no `jev` target or no key; the breaker is open; or the scan hits. Only `blind/` files and verdict blocks are sent.
- **Pre-send scan.** A tripwire for what an Arena should never contain, not a filter and not a guarantee. It logs the pattern name, never the match. The patterns below are owner-editable; the scan also refuses on the literal value (16 or more characters) of any `*_API_KEY` in the process environment or the env file (a missing env file is fine), logging the variable name only. Domain words are coarse on purpose: `RLS`, `IBAN`, `SWIFT`, `KYC` match as written, `tenant(s)` and `payout(s)` in any case. Expect many Balansas-style decisions to skip, so natural rows accumulate slowly. `jev_steps skips` counts the skips by reason and by pattern so the owner can see how often J1 runs.

```scan-patterns
sk-key :: \bsk-[A-Za-z0-9_-]{20,}
jwt :: \beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}
bearer-token :: Bearer\s+[A-Za-z0-9._~+/=-]{20,}
private-key :: -----BEGIN [A-Z ]*PRIVATE KEY-----
supabase-host :: [a-z0-9]{20}\.supabase\.co
aws-key-id :: \bAKIA[0-9A-Z]{16}\b
github-token :: \bghp_\w{30,}
github-pat :: \bgithub_pat_\w{30,}
slack-token :: \bxox[abp]-[\w-]{10,}
db-url-password :: postgres(?:ql)?://[^\s:@]+:[^\s@]+@
domain-word :: \b(?:RLS|IBAN|SWIFT|KYC|(?i:tenants?|payouts?))\b
```

- Seed text goes through the same scan at `seeded-run`; author seeds without these words.
- TypeSafe states a no-training commitment; their retention terms are unverified (https://docs.typesafe.ai/legal), so SHADOW is the owner's choice. Record in the decision record which provider saw which files.

## 5. Call contract and fallback
- **Pin.** The target `model` must match `^jev-[0-9]+\.[0-9]+\.[0-9]+$` (exit 2 otherwise; `jev-latest` and `jev-preview` are never accepted); shipped config `jev-1.13.0`. A response `model` different from the request is exit 4 with ledger note `model_mismatch`.
- Questions are frozen in `references/jev-questions.json` with sha256 (`jev_steps` refuses to run if a hash differs); ledger rows name the wording and the hash.
- Any non-zero runner exit, timeout, 429 or 5xx after retries, oversize 4xx, redirect or malformed answer is `UNAVAILABLE`: the run goes on. Breaker: 2 consecutive failed calls write `$E/jev/.open` and skip the rest of the run and later runs of that `$E`. Timeouts and 408 are not retried. `--timeout` 30 s; `jev_steps` caps each call at about 90 s of wall clock. Calls are serial and bounded (24 for a 4-candidate Arena, plus 3 canary; cap 30).
- A verdict with either order missing has `P = UNAVAILABLE` and `na` in the ledger; its pair is never flagged and it is excluded from AUC. Over 10% `na` in a calibration cycle means rerun (`j1 --supersedes <run>`).
- Cost: $0.042 per million input tokens, output free (https://docs.typesafe.ai/models); about $0.007 per Arena (1.08M tokens over 167 calls in the shadow tests).
- `jev.py` of the panel is not vendored (import-time key read, `SystemExit`, Bearer-carrying redirects, timeout retries).

## 6. Store, ledger and labels
- **Store.** One per install, outside every repo: `${OLYMPUS_JEV_DIR:-$HOME/.local/state/olympus/jev}/`. `ledger.jsonl` is append-only and text-free: no candidate, verdict or key text. Row types: `verdict` (run, kind `seeded|natural`, verdict id, pair id, candidate codes, winner, both P and their mean and difference, `na` and its reason, wording and question sha256, both state sha256, word counts, `ruling_sha256` and timestamp, resolved model, tokens, latency), `label` (run, verdict id, label, source), `skip`, `commitment`, `seed_run`, `seed_void`, `supersede`. `$E/jev/` keeps the raw per-run files (briefs, answers, `run.json`, `flags.json`).
- **Seeded-defect labels give promotion.** A seed pair is two independently authored proposals on one question, one carrying one planted, checkable defect (an invented tool, a false figure, a removed gate, including subtle ones); it is not a minimal pair. A seeded Arena is one seed pair judged twice with positions swapped, by the normal judge brief with no "synthetic" marker and a light-tier judge (light-tier judges missed planted defects in K-2036, so wrong verdicts exist). It is a calibration card outside any decision: phase 3 item 8's three-approach trigger does not apply, it uses one pair and never writes `$B` or DECISION.md. It needs an owner-approved card because it costs judge runs and Jev calls. A verdict is correct iff it picks the defect-free proposal; a TIE is wrong, has no P, is excluded from AUC and its rate is reported.
- **Commit and reveal.** The pack lives in `${OLYMPUS_JEV_DIR}/seeds/<pair-id>/a.md` and `b.md`, with neutral names that do not say which text is defective. Procedure (recipe: `references/jev-seeds.md`):
  1. The authoring seat returns the key in its final message only: `<pair-id>:a` or `<pair-id>:b` for the defective file. The key is never written to a file by Olympus and never to `$E/CHECKPOINT.md`.
  2. `printf '%s' '<key>' | python3 tools/jev_steps.py seeded-commit --pair-id <id> --author <seat>` (`--author` is required, never `unknown`, and the 2-author rule counts distinct names) commits `sha256(key bytes || nonce)` to the ledger as hex, where the nonce is 16 random bytes (128 bits) printed once as 32 hex characters. The President holds key and nonce in context only.
  3. `jev_steps seeded-run --pair-id <id>` refuses without a commitment row and copies only the two files under fresh random codes to a new neutral directory, `/var/tmp/olympus-arena-<hex>/blind/`. Nothing else goes in it: the run header (`jev: shadow`, `jev-content: ok`, `kind: seeded`, `run: <id>`) is written to `seed-runs/<run>.header.md` in the store and its path is printed, so a judge reading the directory tree finds no "seed" anywhere. The code-to-seed map also lives in the store, and no brief names the seeds path.
  4. Judges judge; the President writes `ruling.md`; `jev_steps j1 --decision <store>/seed-runs/<run>.header.md ...` runs. `j1` and `label seeded` both refuse verdicts whose codes do not belong to the run.
  5. Only after `ruling.md` and every verdict file exist, the President writes `{"key": "...", "nonce": "..."}` to `${OLYMPUS_JEV_DIR}/seeds-key/<pair-id>.json` and runs `jev_steps label seeded --run <run> --e $E --verdicts <files> --judge-outputs <files or dirs>`. It greps the judge outputs for `jev/seeds|answer.key|seeds-key|seeded` (a hit voids the cycle and retires the pair), verifies the commitment, labels every verdict row and deletes the key file.
  6. If the President's context was lost, re-request the key from the authoring seat by `SendMessage`; `label seeded` accepts it only if the hash matches the commitment. If it cannot be recovered or does not match, `seeded-void --run <run> --reason lost-key` voids the cycle and the pair returns to the pool (the key was never exposed; commit again). A void for a leak retires the pair. Each pair is used once.
- **File modes are no barrier.** An agent running as the same user can read any file the President can. The design therefore never relies on permissions: the key does not exist on disk during judging, the commitment makes it verifiable afterwards, the copy has fresh codes, and the leak grep is the backstop. The key is never written by Olympus during judging; transcripts are outside every brief path.
- **Natural-run labels are supplementary and used for demotion only.** They are narrow on purpose. `wrong` means: a later DECISION reverses to that pair's loser, or a traced defect sits on a criterion where the loser scored higher (`label l1` at the phase 9 retrospective for the defect case; `label l3` at the phase 0 intake of a later card that cites the DECISION for the reversal case). `correct` comes only from a blinded two-judge audit (`label audit`: a seeded-RNG 25% sample of the rest, drawn before P is read, by two non-author judges). A clean outcome stays unlabelled. Only verdicts that involve the base candidate (the `base:` line of `ruling.md`) can be labelled, and the labeller never sees P: `jev_steps verdicts --run <run>` lists ids, codes and winners without it, and the label commands never print it. `l1` and `l3` refuse `correct`. Report the labelled fraction with every metric.

## 7. Promotion, power, demotion, retire
- **Canary.** `references/jev-canary.json` holds 3 frozen synthetic states, re-asked at each Jev-enabled Arena start. Drift beyond the tolerance (max(0.05, 3 SD of one repeated input), a parameter), a changed pin or a changed question hash demotes to SHADOW (`demoted.json` in the store; the owner deletes it after review). Baselines are unset until the first shadow run records them.
- **Pre-registered promotion SHADOW to ADVISORY** (`python3 tools/jev_calibrate.py`): at least 30 distinct seed pairs authored by at least 2 seats, at least 20 wrong verdicts (with a P); the 2.5th percentile of a cluster-bootstrap AUC (pair id as the cluster, 10,000 draws) at least 0.70, with Hanley-McNeil as a secondary figure; an AUC above the word-count baseline; median `|P_AB - P_BA|` at most 0.10 (parameter); `na` at most 10%. AUC uses the raw mean P. Platt scaling is fitted on half the pairs; Brier on the other half is reported, not gated (about 30 rows). The action threshold is the P that flags at most 25% of the fitting half (parameter).
- **Power.** At a true AUC of 0.80 and 60 rows the Hanley-McNeil rule passes about 46% of the time (seat A's simulation); a cluster bootstrap on 30 pairs is at most that powerful. So HOLD at 60 rows is "undecided": extend to 100 rows first. Author pairs in batches of 10.
- **Demotion to SHADOW:** AUC below 0.65 over the last 30 labelled verdicts, or canary drift. **To OFF:** a leak.
- **Retire** (the owner decides): fewer than 60 labelled verdicts after 30 distinct pairs, counting only verdicts that carry a P (ties do not count); or fewer than 20 wrong verdicts after 60 pairs, which means the planted defects are too easy for the judge tier.

## 8. Commands
```
python3 tools/olympus_call.py --target jev --brief brief.json --out out.json --dry-run
python3 tools/jev_steps.py j1 --e $E --blind $B/blind --verdicts $B/judging-*.md --rubric rubric.md --decision $B/DECISION.md
python3 tools/jev_steps.py skips
python3 tools/jev_calibrate.py
python3 -m unittest discover -s tools -p 'test_*.py'
```
