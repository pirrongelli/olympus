# Kimi reviewer procedure (reviewer 1)

Purpose: how the President launches the Kimi launcher as the static reviewer, what the prompt says, how to read its exit, and when to fall back to Sonnet. Serves SKILL.md section 5, phase 8 item 2. Facts below were read in `scripts/agents/kimi/kimi-review.py`, `scripts/agents/kimi/INSTALL.md` and `docs/agents/kimi-reviewer.md` on 2026-10-13.

## 1. What the launcher does (clone facts)

- It clones `--repo` with `git clone --no-hardlinks` into `<--output>.arena` (the `--workspace-write` form), checks out `--head` detached, verifies the clone against that head afterwards and removes the clone and its per-session Codex home on a clean run. A fresh clone has no `node_modules`; the sandbox has no network; the reviewer may write only inside the clone and temp directories.
- It refuses to start when `--output`, its `.jsonl` log, `.meta.json`, `.stderr.log`, the arena or the session home already exist ("Output already exists; choose a new path"), when the output directory is missing, when `--head` or `--base` is not a canonical lowercase 40-hex SHA, when `--workspace-write` lacks them, when the brief plus its injected contract exceeds 24000 bytes, when `BALANSAS_KIMI_ROLE` is set to anything but `author` or `coordinator`, or when `BALANSAS_KIMI_SESSION` is set (no nested reviews).
- It injects the output contract: four standalone fields, each exactly once, `HEAD: <40-hex>`, `BASE: <40-hex>`, `VERDICT: SHIP|BLOCK`, `R11: NONE|MET|MISSING`, followed by findings. It validates them locally, rejects `VERDICT: SHIP` with `R11: MISSING` (so does `review_policy.py`, for any line starting `R11: MISSING`), rejects a report above 64000 bytes, and never retries a model call. Its role contract defines `R11` as the reviewer's own verification: `MET` only if the checks ran and passed, `MISSING` if they could not run, `NONE` if the brief requires none.
- A clean run keeps four evidence files next to `--output`: the report, `<output>.jsonl`, `<output>.meta.json` and `<output>.stderr.log`. A failed run keeps everything for debugging.
- The key comes from `KIMI_API_KEY`, or from a key file `kimi-api-key` beside the launcher or in `~/.codex/reviewers/` (owned by the user, mode 600, not a symlink); `python3 ~/.codex/reviewers/configure-kimi-key.py` sets it. Adoption check: `bash scripts/agents/kimi/check-adoption.sh` (ADOPTED, or NOT ADOPTED with the missing step; go to the Sonnet fallback when it is not adopted).

## 2. Steps

1. `bash scripts/agents/kimi/check-adoption.sh` once per machine.
2. Step 1 of phase 8 first: `python3 .claude/skills/voldemort/voldemort.py verify --root <abs card worktree> --head <40-hex head> --base dev` must show `R11: NONE` or `R11: MET`.
3. `mkdir -p /var/tmp/K-<n>-kimi`; choose `--output /var/tmp/K-<n>-kimi/<head12>-<attempt>` (a new `<attempt>` for every call, retries included).
4. The clone must have no dependencies. The check must print nothing: `p=/var/tmp/K-<n>-kimi; while [ "$p" != / ]; do [ -d "$p/node_modules" ] && echo "$p"; p=$(dirname "$p"); done; [ -d /node_modules ] && echo /`.
5. Write the brief to a file under `/var/tmp/K-<n>-kimi/`, then `wc -c <brief>` must be under 24000 (the contract adds bytes; keep a margin).
6. `--check` first: `python3 ~/.codex/reviewers/kimi-review.py --check --repo <abs worktree of the PR branch> --prompt <brief.md> --output <output> --head <40-hex head> --base <40-hex merge-base> --workspace-write`. It prints "Local check passed; no provider request; no review performed." and exits 0; it proves the install, not the quota.
7. The real call is the same line without `--check`, run as a tracked background command. The merge base is `git merge-base origin/dev <head>`.
8. Save `<output>` verbatim to `$E/<card>/<head12>/`; take provider, model and context from `<output>.meta.json` (`profile.model_provider`, `profile.model`, `reviewer_session.session`).

## 3. The prompt (fill in; keep it under 24000 bytes)

```
Review statically. You are the independent reviewer of PR #<n> (card K-<n>), head <40-hex head>, base <40-hex base>. Apply .claude/codex-review-rubric.md.
Read the diff and the DECISION excerpt below. Run only read-only commands. Never call scripts/preflight-lock.sh: it would wait for a lock your caller holds. Your clone has no dependencies and no network, so do not try to run vitest, tsc or eslint.
Executor gate results (exit code and the last 20 output lines of each): G0 <...>; G1 <...>.
For R11, run: python3 .claude/skills/voldemort/voldemort.py verify --root <abs card worktree> --head <40-hex head> --base dev
Put exactly one standalone line in your report, R11: NONE or R11: MET, using the value its last R11 line ends with (the value only, never a trailing note). Quote any of its output only with a "> " prefix. List any other check you could not run in the body, never on a line starting R11:.
Check: files outside the lane (lanes row below), the DECISION link in the PR body, red-first order in git log, and the pack's proof for this card.
DECISION excerpt: <lanes row, required proposal content, acceptance, owner rulings>
```

The rubric text asks reviewers to quote the `R11:` lines; the single-line rule above overrides it for Kimi because the launcher rejects a second line-start `R11:` field.

## 4. Reading the exit

- Exit 0 and the line "Review format valid": the report is a valid review (a format check, not an approval). A `VERDICT: BLOCK` report is a completed review and is handled as a BLOCK.
- Exit 2 before the review: an argument or environment refusal (section 1); the message names it; fix it and retry with a new `<attempt>`.
- Exit 2 after the review: "Invalid or missing standalone <field>", or "Clone integrity not verified ... `r11_trusted=false`": no valid review; the report is preserved; no provider retry happened.
- Any other non-zero exit is the model client's own exit status: no valid review.

## 5. Fallback

On a failed `--check`, a quota error, a 403, a timeout or an unreadable worktree, retry once with a narrower brief, then use a Sonnet `Agent` (`subagent_type: loop-verifier`, `model: sonnet`, fresh context) without asking the owner (owner OK 2026-09-29, recorded in the owner's memory note `feedback-sonnet-reviewer-when-kimi-out`, not in a repo doc). `AGENTS.md` and `CLAUDE.md` say there is no automatic fallback when Kimi is unavailable and `docs/agents/kimi-reviewer.md` repeats it: the fallback here is therefore not automatic, it is that standing owner authorization, and `docs/agents/review-policy.md` accepts any capable independent context. If the authorization cannot be cited, ask the owner once. Its brief starts: "STATIC ONLY: do not run vitest, tsc or eslint while any preflight runs; read the G0/G1 evidence instead. This overrides step 1 of your role." Any test, tsc, lint or build command a reviewer runs goes through `bash scripts/preflight-lock.sh K-<n>-<tag> -- <cmd>`; exit 75 means nothing ran: retry, never run unlocked. A reviewer never launches another reviewer.
