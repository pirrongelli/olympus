# Command sheet for Olympus

Purpose: the exact commands, flags and exit codes the skill relies on, each read in the repository source on 2026-10-13. Serves SKILL.md section 1 (rules 7 and 8), section 5 (phases 0 and 6 to 9) and section 6. Never run `--help` on `scripts/preflight-lock.sh` (it would start a full preflight). Check a flag with `grep -n -- '<flag>' <script path>` and a path with `ls`.

## 1. Worktrees: `scripts/wt.sh`

- `scripts/wt.sh new <card-or-slug> [branch]` creates `wt/<slug>` (default branch `feature/<slug>`, base `origin/dev`); `scripts/wt.sh new --path <directory> <branch>`. Slug pattern `^[A-Za-z0-9][A-Za-z0-9._-]*$`. It installs nothing and does not fetch.
- `scripts/wt.sh done [path] [--evidence-receipt <receipt.json>]` removes a worktree and keeps its branch. It refuses (exit 2) while anything would be lost: uncommitted or unpushed work, a detached HEAD no branch contains, a live process whose cwd is inside, or fresh evidence files not yet in the root checkout. Run it from another checkout; report the refusal, never force.
- `scripts/wt.sh gc [--apply]` and `gc --artifacts [--apply]` list or remove what is safe. Unknown commands print usage to stderr and exit 64.
- Environment: `WT_MAX` (12), `WT_BASE` (`origin/dev`; `WT_BASE=origin/<main|prod>` is the hotfix form), `WT_IDLE_DAYS` (7), `WT_EVIDENCE_HOURS` (48), `DISK_WARN_GB` (60), `DISK_BLOCK_GB` (20).
- Dependencies in a new tree: check space first (`df -h .`, `docs/agents/disk.md`), then `pnpm install --frozen-lockfile`; `node --version` must be 22 (`docs/loops/RULES.md`).

## 2. The machine lock: `scripts/preflight-lock.sh`

- `bash scripts/preflight-lock.sh <label>` runs `scripts/preflight.sh`; `bash scripts/preflight-lock.sh <label> -- <command...>` runs any command under the lock. No label exits 2.
- Exit codes: the wrapped command's own code; `128+N` for a signal death; 75 when the wait timed out and nothing ran (retry, never run unlocked).
- Environment: `FLEET_LOCK_FILE` (`/tmp/<project>-preflight.lock`), `FLEET_LOCK_TIMEOUT` (3600), `FLEET_LOCK_POLL` (0.1), `FLEET_LOCK_ANNOUNCE` (30). The lock is FIFO, machine-wide and not re-entrant: a command run under it must never call the wrapper again.
- Forms used by the skill (labels `K-<n>`, `K-<n>-mut`, `K-<n>-<tag>`):
  - G0: `git fetch --quiet origin dev; export RATCHET_BASE_SHA=$(git rev-parse origin/dev); bash scripts/preflight-lock.sh K-<n> -- bash -c 'pnpm run typecheck:ratchet && pnpm run quality:deadcode && pnpm run lint'`, then `git diff origin/dev...HEAD -U0 | grep -nE '^\+.*\.at\('` prints nothing.
  - G1, one command (shell state does not persist between tool calls): `env $(env | grep -oE '^(FLEET|VOLDEMORT)_[A-Za-z0-9_]*' | sed 's/^/-u /') bash scripts/preflight-lock.sh K-<n>`.
  - UI visual proof (UI pack): `bash scripts/preflight-lock.sh K-<n> -- bash -c 'npm run test:browser && python3 scripts/ops/browser-evidence.py record'`.
  - Mutant runs and reviewer commands: `bash scripts/preflight-lock.sh K-<n>-mut -- <command>`.
- Harness-only exception (owner rule 2026-10-12): when `voldemort.py verify` prints `SUITES: none required for this diff`, targeted tests plus CI replace the full preflight.

## 3. Cards and handoff

- `python3 scripts/kcard.py --title "<line>" --body "<why, and the observation that proves it done>" [--status <name>] [--priority P0|P1|P2|P3] [--dry-run]`. Statuses: backlog, ready for development, in development, in review, shipped. `--dry-run` reads the board and creates nothing. `--number-unnumbered [--apply]` numbers cards raised elsewhere. Idempotent by open title. Never choose a K number by hand.
- `python3 scripts/agents/handoff.py write --card K-<n> --request R --goal G --proof P --state S [--decision X]... [--pending X]... [--trap X]... [--link K-<m>]... [--vault-dir D] [--no-commit]`. Other subcommands: `render`, `read`, `save`, `latest`, `path`, `exists`, `orphans`. It commits in the memory vault (the worktree on branch `memory`) and never pushes. Exit 2 means no vault (`NO_VAULT`): put the same fields in the PR body and report the blocker. Exit 1 on `read` means no handoff.

## 4. Pane executors: `.agents/skills/apollo-orchestration/fleet.py`

- `python3 .agents/skills/apollo-orchestration/fleet.py check <K-n>` refuses a card that is not on the board, has a duplicated id or has no proof-of-done.
- `... fleet.py dispatch <K-n> [--kind feature|fix|chore|docs] [--slug <slug>] [--lead <exact lead session name>] [--model fable|opus|sonnet|haiku] [--dry-run]`. `--lead` (or `FLEET_LEAD`, which the skill does not export) is required. Branch `<kind>/<k-n>-<slug>`, tree `wt-<k-n>` at the repo root, session `--name <k-n>` launched as `claude ... --permission-mode auto --settings {"crossSessionInbound":"accept"}` with no `--effort`. The worker brief holds only the card title, the first card-body paragraph matching `prueba de que est|proof|evidence|done when|criterio de cierre`, the lead line, the deadline and fixed gate text; `--dry-run` prints it.
- `... fleet.py status [--all]` lists workers (`--all`: every lead on the machine).
- `... fleet.py intake "K-<n> | VERDE|REVIEW|BLOQUEADO|FALLO|FALLO-DEADLINE | <evidence> | <next>"`. For `VERDE` whose evidence names `PR #<n>` that GitHub reports merged from the worker's own branch with the card number in its title, intake stops the worker and removes its tree; otherwise it keeps the resources. Run it from the lead whose state recorded the worker.
- Other subcommands (not used by the skill): `goal`, `sync`, `report`, `backfill`.

## 5. Evidence and R11: `voldemort.py`

- `python3 .claude/skills/voldemort/voldemort.py verify [--root <abs worktree>] [--base <branch>] [--head <40-hex>]` (the same file lives under `.agents/skills/voldemort/`). Output: a `SUITES:` line (`SUITES: none required for this diff` when nothing applies), then `R11: NONE`, or `R11: APPLY <suites>` followed by `R11: MET` or `R11: MISSING <names>`; a deferred live-api reads `R11: MET (live-api DEFERRED until dev deploy)`. Exit 1 when something is missing. When a PR exists and no review record is written yet it also prints `CODEX: MISSING` and a `HINT:` line (expected before the record exists); quote only the `R11:` lines. Without `--base` it uses the open PR's base branch, else `dev`.
- Other subcommands: `nag`, `parse`, `deadline`, `prove-live`, `verify-promotion`, `hook-prompt`, `hook-stop`, `retrospect`, `ack`, `run`.
- Evidence files: `.claude/test-evidence/<head>` written by the preflight (gitignored, per worktree).
- UI browser evidence: `python3 scripts/ops/browser-evidence.py [--failed] [--head <sha>] record` writes `.agents/browser/<head>.json` and refuses on a dirty tree; the `gh pr create` hook demands it for UI diffs.
- Type ratchet base: `RATCHET_BASE_SHA=<sha> bash scripts/typecheck-ratchet.sh` (`pnpm run typecheck:ratchet`).

## 6. The review record: `scripts/agents/review_policy.py`

- `python3 scripts/agents/review_policy.py plan [--base origin/dev] [--head <rev>]` prints `{"full_validation": ..., "required_reviews": 1|2, "risk": "routine"|"sensitive"}` for the actual diff. Two reviews for `.github/`, `.agents/`, `.claude/`, `.codex/`, `scripts/`, `supabase/`, `docs/agents/`, `docs/loops/`, `AGENTS.md`, `CLAUDE.md`, `package.json`, `package-lock.json`, `pnpm-lock.yaml`, `.npmrc`, `.coderabbit.yaml`, and any path matching `auth|payment|payout|transfer|balance|ledger|transaction|wallet|money|permission|session|secret|tenant` (case-insensitive).
- `python3 scripts/agents/review_policy.py verify --head <40-hex> --base origin/dev` prints `REVIEW: OK (...)`, or `REVIEW: BLOCK: <why>` and exits 1. `--base` is a ref, not a SHA. It reads `.agents/reviews/<head>.json` under the repo that holds the script, so run it from the merging session's cwd.
- Record skeleton (`.agents/reviews/<head>.json`, gitignored):

```json
{
  "schema_version": 1,
  "head_sha": "<40-hex head>",
  "base_sha": "<git merge-base origin/dev <head>>",
  "implementation_context": "<executor session name or agent id>",
  "implementation_contexts": ["<every other implementing session, optional>"],
  "reviews": [
    {"provider": "<actual>", "model": "<actual>", "context": "<unique reviewer session id>",
     "verdict": "SHIP", "r11": "NONE", "report": "<unaltered report text>"}
  ]
}
```

  Rules enforced by `verify`: every `context` differs from the implementation contexts and from each other; `verdict` is `SHIP` and `r11` is `NONE` or `MET`; each `report` contains the full head, a standalone `VERDICT: SHIP` line and the text `R11: <declared value>`, and no line starting `VERDICT: BLOCK` or `R11: MISSING`; the number of reviews reaches `required_reviews`. BLOCK reports stay in `$E`, never in the record.
- Kimi record fields come from `<output>.meta.json`: provider `profile.model_provider`, model `profile.model`, context `reviewer_session.session`.
- Before the merge, from the merging session's cwd: `mkdir -p .claude/test-evidence .agents/reviews`, copy the card worktree's `.claude/test-evidence/<head>*` into `.claude/test-evidence/`, write the record, run `verify`.

## 7. GitHub: `scripts/ghx`, `scripts/gh_events.py`

- `scripts/ghx <gh arguments>` is the only way to call `gh`: one call at a time machine-wide, one second between mutations, unknown shapes count as mutations. Exit 75 means the call never ran (cooldown or lock wait). Forms used: `pr view <n> --json mergeCommit` (add `--jq .mergeCommit.oid` for the SHA), `pr checks <n>`, `run list --workflow 'Deploy to Dev' --commit <sha> --json status,conclusion`, `pr create --title "K-<n>: ..."`, `pr merge <n> --squash --match-head-commit <40-hex>`.
- `python3 scripts/gh_events.py wait --pr <n> [--timeout T] [--poll P]` exits 0 green, 1 red, 2 timeout, 3 stale; follow an exit 0 with one `scripts/ghx pr checks <n>` read. On 2 or 3 re-read once per 5 minutes across turns, never a polling loop. Never `gh pr checks --watch` or `gh run watch`.
- Merge shape (`.claude/hooks/merge-gate.sh`): one single-line command `scripts/ghx pr merge <n> --squash --match-head-commit <40-hex reviewed head>`; a `cd`, subshell, chaining, env prefix or `-R` selector is denied. The gate reads `.agents/reviews/<head>.json` and `.claude/test-evidence/<head>*` from the session's cwd.
- Planning-PR presence check: `git fetch origin dev && git cat-file -e origin/dev:<decision dir>/DECISION.md` exits 0 when merged, 128 when not; on a card head, `git cat-file -e <head>:<decision dir>/DECISION.md`.

## 8. `## Edges` checks

   `cards.md` ends with a `## Edges` block, one line per `after:` edge as `K-<before> K-<card>`. Exit check, all four must hold: (1) `grep -q '^## Edges' cards.md`; (2) `sed -n '/^## Edges/,$p' cards.md | tail -n +2 | awk 'NF && !/^K-[0-9]+ K-[0-9]+$/'` prints nothing; (3) `sed -n '/^## Edges/,$p' cards.md | tail -n +2 | awk 'NF==2 && $1==$2'` prints nothing (no self-edge); (4) `sed -n '/^## Edges/,$p' cards.md | tail -n +2 | grep -E '^K-[0-9]+ K-[0-9]+$' | tsort >/dev/null` exits 0 (`tsort` exits 1 with "input contains a loop" on a cycle). Every `K-<before>` is a card in `cards.md` or satisfies `git log origin/dev --oneline | grep -cE 'K-<before>\b'` ≥ 1.

## 9. The Kimi launcher

- Full line: `python3 ~/.codex/reviewers/kimi-review.py [--check] --repo <abs worktree of the PR branch> --prompt <brief.md, 1 to 24000 bytes> --output /var/tmp/K-<n>-kimi/<head12>-<attempt> --head <40-hex> --base <40-hex merge-base> --workspace-write` (repo copy `scripts/agents/kimi/kimi-review.py`; `wc -c` the prompt first). Setup and adoption: `scripts/agents/kimi/INSTALL.md`, `bash scripts/agents/kimi/check-adoption.sh`. Procedure, prompt and exit handling: the Kimi procedure file named in SKILL.md phase 8.

## 10. Registering and checking the skill

- `node scripts/agents/sync-assets.mjs` creates the missing relative link `.claude/skills/<name>` for each `.agents/skills/<name>/SKILL.md`; `node scripts/agents/sync-assets.mjs --check` fails on a missing or drifted link. `docs/agents/assets.json` lists roles and references only: a skill needs no entry. Also `node scripts/quality/doctrine-invariants.mjs`.
- `command -v graft`; if present `graft build && graft ask "<question>"` (`docs/agents/graft.md`), else `git grep -n`. graft does not parse SQL.
