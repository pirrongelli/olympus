# Reviewer brief: static reviewer 1 and mutation reviewer 2

Purpose: the briefs, mutant catalogue, runner rules, ledger and report format for the two independent reviews. Serves SKILL.md section 5, Phase 8 (items 2 and 3), and Phase 7 duty 3 (red-first tests).

## 1. Why two reviewers

The static reader and the mutating reader fail differently, so both are required. The static reader checks the diff against the rubric, the gate outputs and the lanes table. The mutation reviewer breaks the code and watches whether any test notices. Each of the K-1914, K-1915, K-2021, K-2022 and K-2023 handoffs records an Opus mutation round before SHIP: K-1914 and K-1915 killed their surviving mutants with new tests, K-2022 and K-2023 killed all of theirs, and K-2021 ended with only decoration-only survivors.

## 2. Reviewer 1 brief (static)

- Kimi first: the procedure is in the Kimi reference named by SKILL.md Phase 8 item 2. When Kimi falls back, use a Sonnet `loop-verifier`, fresh context, never a fork of the author.
- The Sonnet brief's first line is exactly: "STATIC ONLY: do not run vitest, tsc or eslint while any preflight runs; read the G0/G1 evidence instead. This overrides step 1 of your role."
- Any test, tsc, lint or build command a reviewer runs goes through `bash scripts/preflight-lock.sh K-<n>-<tag> -- <cmd>`; exit 75 means nothing ran: retry, never run unlocked.
- Checks: `.claude/codex-review-rubric.md`, the G0 outputs, files outside the card's lane row, the DECISION link in the PR body, red-first order in `git log`.
- Report: the four standalone lines in section 6. Never `R11: MISSING` unless that is the verdict.

## 3. Reviewer 2 brief (mutation, Opus)

Launch only after reviewer 1 SHIPs. Fresh context (`Agent`, `subagent_type: general-purpose`, `model: opus`), tracked background, never a fork. The President gives it:
1. Reviewer 1's SHIP report.
2. The diff command `git diff <merge-base>..<head>` (run it in the card worktree, read-only: the copy is not a git repository).
3. The R11 line from Phase 8 step 1, to copy into its report.
4. The absolute path of the isolated copy (the only place it may edit) and the lock label `K-<n>-mut`.
5. The pack's field 7 (mutation families and runner) for non-UI topics.

It writes nothing in the PR worktree, runs every command in the foreground, one test file at a time, through the lock, and returns the ledger and report in its final message. A decision-only planning PR is a docs diff: it gets a second static review (a fresh `loop-verifier`), never this procedure.

## 4. Money mutant families (UI and display paths), highest risk first

Write each mutant by hand in the copy, run the single test file that should catch it, record the result in the ledger. Size the first wave to the card's risk (see the scope rule in section 5).

| Id | Family | Mutation to try | Real gap of this kind found by mutants (board brief, "what worked" item 7) |
|---|---|---|---|
| M1 | Direction / sign | flip the direction or the sign, so a payin renders as "Sent" or an outflow as received | a payin rendered as "Sent" |
| M2 | Fee versus principal | swap fee and principal, or net and gross, in a displayed or exported amount | a fee and principal swap |
| M3 | Status mapped twice | pass a provider status through the label map two times, or map before and after a normaliser | the provider C status double-mapping (K-1915 handoff) |
| M4 | Stale or retry state leaking | keep the previous record's value after a retry, close or reopen (a pool IBAN shown after a retry, a late response overwriting a newer one) | an pooled-account IBAN leaking after a retry (K-2022 handoff: retry redaction test) |
| M5 | Account-field / identifier swaps | swap BIC and account number, IBAN and sort code, or a local id and a provider id | BIC and account swaps |

Also in the catalogue, from the K-2023 handoff: crypto address, QR payload and clipboard text on every network the screen supports. For non-UI topics use the pack's field 7 instead: architecture and security packs list their own families (tenant filter, idempotency, revocation, fail-open).

## 5. Rules, runner and isolated copy

   - Isolated copy: before launching, the President runs `rsync -a --exclude .git --exclude node_modules --exclude dist --exclude coverage --exclude .agents/reviews --exclude .claude/test-evidence <abs card worktree>/ <main checkout>/wt-mut-K-<n>-<head12>/` (a plain directory at the checkout root, not inside the PR worktree, so the card's vitest never collects it; `wt-*/**` is already excluded in `vitest.config.ts`; the main checkout's `node_modules` resolves from there only for `npx`, so the only test form in the copy is `NODE_OPTIONS='--max-old-space-size=4096 --no-experimental-webstorage' npx --no-install vitest run <file>` through the lock (`pnpm run` and `pnpm exec` fail there with "vitest not found"; if a check needs them, `ln -s <main checkout>/node_modules <copy>/node_modules` first); the copy is not a git repository, so run `git diff` in the card worktree; check `ls <main checkout>/node_modules` and free disk first). The brief names that path as the only place the reviewer may edit. The President deletes it after G3 (until then it shows as an untracked directory in the main checkout). It is not a `wt.sh` worktree.
   - Runner: vitest as above; Deno edge-function tests `bash scripts/preflight-lock.sh K-<n>-mut -- bash -c 'cd <copy>/supabase/functions && deno test --allow-all <one test file>'` per mutant, and `deno task test` (whole suite) only once, for the final confirmation run, under the same lock; pgTAP: `pnpm run test:db` runs `supabase/tests/*.test.sql`; migration specs under `supabase/migrations/__tests__/` run with `DB_URL=$(supabase status -o env | grep '^DB_URL=' | cut -d= -f2- | tr -d '"') bash .github/scripts/run-migration-specs.sh`; both only if a local stack is up (`supabase status` succeeds), otherwise the mutant is recorded `CI-only` and not counted as killed, and a money or auth card with a `CI-only` mutant on its core condition stays BLOCK until CI evidence names it.
   - It runs every command in the foreground, one test file at a time, through the lock, and returns ledger and report in its final message. Rules: first pass the whole PR diff, fix rounds the delta since the last reviewed head plus its earlier survivors; ledger of id, `file:line`, mutation, killing test or SURVIVOR plus reason, where a money-display survivor blocks and a decoration survivor needs a one-line reason and a pending item; new `.skip`, loosened assertions, updated snapshots and edited goldens each need a stated reason; every BLOCK carries a failing test, a surviving mutant or command output. Size scales with risk (handoffs: 19 to 55 mutants on large families, about 12 on fix deltas).
   - Report: four standalone lines `HEAD:` (40 hex), `BASE:`, `R11: <the value from step 1, NONE or MET>`, `VERDICT: SHIP|BLOCK`, then `MUTANTS: <killed>/<total>, survivors justified: <n>` and the ledger (the merge gate stores the report unaltered). A reviewer that ran no suites never writes `R11: MISSING`.

## 6. Ledger and report template

```
HEAD: <40 hex>
BASE: <40 hex merge-base>
R11: <NONE or MET, copied from voldemort>
VERDICT: SHIP | BLOCK
MUTANTS: <killed>/<total>, survivors justified: <n>

| id | file:line | mutation | killing test or SURVIVOR | reason (survivors) |
```

The four first lines are standalone lines, one each. The `.agents/reviews/<head>.json` record stores this report unaltered, so nothing may be added above them.

## 7. Anti-tampering checks (run on the executor's diff)

Each hit needs a stated reason or the PR is blocked:
- New skips: `git diff <merge-base>..<head> -U0 | grep -nE '^\+.*(\.skip\(|\.only\(|\.todo\(|\bxit\(|\bxdescribe\()'`
- Deleted assertions: `git diff <merge-base>..<head> -U0 -- '*.test.ts' '*.test.tsx' | grep -nE '^-.*expect\('`
- Weaker assertions: `git diff <merge-base>..<head> -U0 | grep -nE '^\+.*(toMatchObject|expect\.anything|toBeTruthy|toBeDefined)'`
- Changed snapshots or goldens: `git diff --name-only <merge-base>..<head> | grep -E '(__snapshots__/|\.snap$)'`
- A fix commit that adds a new `.at(` call: `git diff <merge-base>..<head> -U0 | grep -nE '^\+.*\.at\('` must print nothing.

## 8. Machine rules

One suite at a time. `pgrep -f "preflight|vitest"` is only a pre-dispatch read; the lock is the guard. Reviewers stay static while a preflight runs. Reviewer arenas and copies never live inside the PR worktree or under `.claude/scratch` (stray clones once made vitest collect thousands of files).
