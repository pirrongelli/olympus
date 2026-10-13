# Executor brief and standing bans

Purpose: the paste-ready brief for one executor per card, and the standing bans that every brief carries. Serves SKILL.md section 5, Phase 7 (Execute) and section 2 (pane executors).

## 1. How the brief reaches the executor

- `Agent` executors (`subagent_type: loop-executor`, mechanical cards): paste this brief verbatim.
- Pane executors (`fleet.py dispatch`, Sonnet): dispatch builds its own brief from the card title, one proof-of-done paragraph in the card body, the lead name and fixed gate text. Send this brief as the first `SendMessage` to session `k-<n>` right after dispatch. The card body keeps exactly one paragraph containing "proof", "evidence" or "done when". `dispatch --dry-run` prints the generated brief: check that the proof paragraph is the stop condition.
- Every dispatch cites `TASK K-<n>`.

## 2. Brief template (fill every field)

```
TASK K-<n>: <card title>
Work at effort HIGH.                       (Agent briefs only; panes cannot pin effort)
Tier and reason: <Haiku|Sonnet|Opus>, <one line>
Worktree (absolute): <path>
Card base: <planning merge commit; tier S: the origin/dev head at dispatch>
Decision: <decision dir>/DECISION.md (present at the card base; read only, never copy it into your tree)
Lanes row: <card, family, files owned, shared files touched, helpers created, after:, strict>
DECISION excerpt (pasted): <the card's required proposal content, acceptance, owner rulings, artefact link>
Proof of done: <one paragraph: the observation that proves it>
PR, commits and docs in English. No AI or tool attribution anywhere.
Standing bans: section 3 below, pasted in full.
```

## 3. Standing bans

1. No `.at()`. The TS lib has no es2022, so `Array.prototype.at` fails the CI typecheck (TS2550) after reviews already passed (K-1921 and K-2022). Use index access.
2. Ratchet with an explicit base: `git fetch --quiet origin dev; export RATCHET_BASE_SHA=$(git rev-parse origin/dev); bash scripts/preflight-lock.sh K-<n> -- bash -c 'pnpm run typecheck:ratchet && pnpm run quality:deadcode && pnpm run lint'`, and `git diff origin/dev...HEAD -U0 | grep -nE '^\+.*\.at\('` prints nothing. Vitest and the build do not typecheck, so a green preflight is not evidence for this.
3. Environment: run the preflight as ONE command, because shell state does not persist between tool calls: `env $(env | grep -oE '^(FLEET|VOLDEMORT)_[A-Za-z0-9_]*' | sed 's/^/-u /') bash scripts/preflight-lock.sh K-<n>`. Inherited `FLEET_*` variables make the harness suite refuse to run when a diff touches `docs/agents`.
4. One suite at a time on the machine: every suite, build and test run goes through `bash scripts/preflight-lock.sh K-<n> -- <cmd>`; exit 75 means nothing ran, so retry. Run commands in the foreground. Never run `--help` on `scripts/preflight-lock.sh` (it starts a full preflight).
5. Harness-only diffs: when `voldemort.py verify` prints "SUITES: none required", targeted tests plus CI replace the full preflight (owner rule 2026-10-12). This overrides the generated preflight line in a pane brief.
6. Shared files and helpers: touch only the files in your lanes row. Do not edit a shared registry file (for example the UI harness case list) unless the row says you own it. Before writing a helper run `command -v graft`; if present `graft build && graft ask "<question>"`, else `git grep -n`; import from the owner card or wait (sibling cards once duplicated `groupSortCode`).
7. Export only what is imported: knip fails the dead-code gate on an unused export (K-2023 fix cycle).
8. Install first: check free space (`df -h .`, `docs/agents/disk.md`), then `pnpm install --frozen-lockfile` in your worktree; `node --version` must be 22. Run `git fetch origin dev` before every read of `origin/dev`.
9. Drift check before coding: compare `git rev-parse origin/dev` with the card base; if `git diff --stat <card base>..origin/dev -- <your files and inventory rows>` lists anything, re-run `python3 .claude/skills/voldemort/voldemort.py verify --root <abs worktree> --head <40-hex head> --base dev`, re-check lane owners, then re-observe the named path on current dev. If it differs from the owner artefact, stop and report.
10. Red first: commit the failing tests, paste the failing run in the PR body, then the implementation commit. Money paths get tests for the mutant families in the reviewer brief reference named by SKILL.md Phase 7 duty 3 and the pack's field 7.
11. Domain proof from the pack, recorded where the President says (`$E/<card>/<head12>/`): UI visual proof and browser record, architecture fitness checks and rollback rehearsal, security probe or failing-first regression test. Evidence recording needs a clean tree.
12. Open the PR to `dev`, title starts `K-<n>:`. Never merge it, never review your own work. Three failed cycles on one failure: stop and report. After the final review nothing is committed to the branch except a fix commit that restarts both reviews.

## 4. Fix rounds

Reuse the same executor. One commit batches both reviewers' findings. Re-run G0 and G1 (or the harness-only R11 rule) on the new head, and the domain proof when the fix delta touches what it observes. Both reviewers re-check only the delta.

## 5. Trap log (append from each handoff before the next card)

A new trap in a handoff `--trap` is appended here as a numbered ban before the next brief is sent. A handoff that finds none writes "checked: none". Current entries are bans 1 to 7 above, taken from the K-1914, K-1921, K-2022 and K-2023 handoffs.
