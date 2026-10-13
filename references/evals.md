# Evals: replays and dry runs that must pass

Purpose: pass/fail checks to run after any edit to this skill. Serves SKILL.md section 8 (scaling down: "after editing this skill, replay the dry run") and section 6 (known failure to catching step). A replay passes only when the named step fires and its command gives the stated result; a missing step, or a step that exists only as a reminder, is a fail. Run the replays with a fresh agent on the Haiku, Sonnet and Opus tiers when the edit touches wording that a cheaper model must follow.

## 0. Gate check (run first)

| Check | Command | Pass |
|---|---|---|
| The mutation brief lists the five money families | `grep -cE '^\| M[1-5] \|' .agents/skills/olympus/references/reviewer-mutation-brief.md` | prints `5` |
| Each family name is present | `for p in 'Direction / sign' 'Fee versus principal' 'Status mapped twice' 'Stale or retry state' 'Account-field / identifier'; do grep -c "$p" .agents/skills/olympus/references/reviewer-mutation-brief.md; done` | five lines, each at least `1` |
| The runner rules are present | `grep -oE 'npx --no-install vitest\|deno test --allow-all\|CI-only' .agents/skills/olympus/references/reviewer-mutation-brief.md \| sort -u \| wc -l` | prints `3` |
| Packs stay small | `wc -l .agents/skills/olympus/references/domains/*.md` | each file at most 120 lines |
| Registration | `node scripts/agents/sync-assets.mjs --check` | exits 0 |

## 1. Three replays

| Replay | Scenario | Step that must fire | Pass when |
|---|---|---|---|
| R1 Debate skipped | A lead drafts the proposal alone (the K-2020 modal proposal) and asks for executors | SKILL.md Phase 0 tier rule, Phase 3 exit, brief field `DECISION:`, reviewer 1 check | `ls $B/r1-proposal-*.md \| wc -l` differs from the header seat count or `test -s $B/DECISION.md` fails, so no brief is sent; a PR body without the DECISION path fails `git cat-file -e <head>:<decision dir>/DECISION.md` and reviewer 1 BLOCKs |
| R2 `.at()` slip | An executor adds `list.at(-1)` in a test and reports "typecheck OK" without a base | Phase 7 duty 4 (G0), standing ban 1 and 2 | `git diff origin/dev...HEAD -U0 \| grep -nE '^\+.*\.at\('` prints the line, and `RATCHET_BASE_SHA=$(git rev-parse origin/dev) pnpm run typecheck:ratchet` reports TS2550 under the lock |
| R3 Parallel-suite freeze | A preflight, Opus mutants, a Sonnet reviewer and a Kimi run start together | Rules 5 and 6; G0, G1, visual proof and mutants under `preflight-lock.sh`; Kimi runs statically in a `/var/tmp` clone | the second suite waits in the lock queue (exit 75 only when its timeout expires, and then nothing ran); `pgrep -f "preflight\|vitest"` is used only as a pre-dispatch read; `$E/CHECKPOINT.md` survives a reboot |

Environment micro-check (known failure `FLEET_*`): `( export FLEET_X=1; env $(env | grep -oE '^(FLEET|VOLDEMORT)_[A-Za-z0-9_]*' | sed 's/^/-u /') bash -c 'env | grep -cE "^(FLEET|VOLDEMORT)_"' )` must print `0` (the variable must be exported before the command substitution runs; a prefix assignment on the `env` call would not be seen by it).

## 2. Dry run 1: the UI modal rollout (K-2020 to K-2024) through the `ui-redesign` pack

Walk the phases; every row must name its catching step and be CAUGHT.

| Item | Catching step and check | Pass |
|---|---|---|
| F1 Board skipped | R1 above | CAUGHT |
| F2 Parallel suites, scratchpad wiped | R3 above | CAUGHT |
| F3 `.at()` | R2 above | CAUGHT |
| F4 `FLEET_*` env | Phase 7 duty 5 as one `env -u` command; environment micro-check above | CAUGHT |
| F5 Shared-file churn | Phase 3 item 9 lanes table, Phase 6 `## Edges` checks (`tsort` exits 0, no self-edge), reviewer 1 lane BLOCK, update by merge | CAUGHT |
| F6 Duplicated helper (`groupSortCode`) | Phase 3 item 9 helper owner, Phase 7 duty 2 (`graft ask` or `git grep -n`), reviewer greps `git diff origin/dev...HEAD -U0 \| grep -E '^\+export'` | CAUGHT |
| M1 to M5 | One mutant family each in `reviewer-mutation-brief.md` (payin as Sent, fee/principal, status twice, stale or retry state, BIC/account) plus Phase 7 duty 3 red-first tests | CAUGHT |

## 3. Dry run 2: two non-UI decisions

### (a) Architecture: "move the payee flows to the v2 server-side path" (decision + build, tier L)

| Phase | Must happen | Pass |
|---|---|---|
| 1 | Current-state map with `file:line` from `graft ask` and `git grep`, plus `supabase/migrations/` grepped separately, plus runtime-configuration rows read through the Supabase MCP or marked `DASHBOARD-ONLY` | CAUGHT |
| 3 | At least two options plus "do nothing" as ADRs, reversibility, migration and rollback path, fitness commands that exist | CAUGHT |
| 6 | Items 2 and 3 are domain-neutral: characterisation tests pin the old path first and run unchanged against the new one | CAUGHT |
| 8 | Reviewer 2 uses the pack's mutation families; Deno tests run as `deno test --allow-all <one file>` under the lock; a pgTAP-only mutant is `CI-only` and not counted as killed | CAUGHT |
| 9 | `Deploy to Dev` run for the merge commit when `supabase/` paths changed; otherwise `dev proof: NOT VERIFIED (<reason>)` | CAUGHT |

### (b) Security: "session revocation on role change" (decision + build, tier L; also run once as decision-only)

| Phase | Must happen | Pass |
|---|---|---|
| 1 | Assets, actors, entry points with `file:line`; Auth settings and hooks listed as `DASHBOARD-ONLY` with the owner as source | CAUGHT |
| 3 | Threat model with enforcement points the client cannot bypass, fail-closed behaviour, privilege-change cases, failing-first negative tests | CAUGHT |
| 5 | Owner questions each carry a default (revoke immediately or at refresh, accepted residual risks) | CAUGHT |
| 7 | `security-audit` probes on dev and sandbox only; `test -f .agents/skills/security-audit/SKILL.md` first; no mutating probe at prod | CAUGHT |
| 8 | Mutation families for authorization (check inverted or removed, raw role-table read instead of the tenant-membership helper, revocation of the current session only, fail-open); decision-only exit: a second static review in a fresh context, never the mutation procedure | CAUGHT |

## 4. Fail handling

A fail is fixed in SKILL.md or the named reference, never by relaxing the check. Re-run this file after the fix. A new real incident becomes a new row here and a new ban in `executor-brief.md` before the next card.
