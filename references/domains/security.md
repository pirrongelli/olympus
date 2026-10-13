# Security decisions pack

Purpose: the domain pack for a security decision (a threat model, a security posture, an authorization or session-handling change, a probe or control choice). Serves SKILL.md section 5: phase 1 (evidence base), phase 2 (default seats), phase 3 (required proposal content), phase 4 (owner artefact), phase 7 duty 6 and phase 9 item 3 (domain proof and dev observation), phase 8 item 3 (mutation families), and section 7 (owner questions). Sections 1 to 8 are the pack fields, in this order (the dev observation is its own field, 6); the numbers are the ones SKILL.md cites ("the pack's field 7"). Section 9 lists references.

## 1. Evidence base: the threat surface, from the code and the live configuration

- Assets (customer money and PII, credentials, provider keys), actors (customer member, platform admin, anonymous, service role, provider webhook), entry points (edge functions in `supabase/functions/`, RPCs, RLS tables, UI routes) and trust boundaries, each row with `file:line`. Two counters with different queries, reconciled (SKILL.md phase 1).
- Read first: `docs/SECURITY.md`, `docs/RLS_AUDIT_GUIDE.md`, `docs/ADMIN_SECURITY.md`, `docs/MFA_GUIDE.md`, and the last audit state in `docs/audits/.findings-ledger.json`.
- Runtime configuration the code does not show: cron schedules (`select jobid, jobname, schedule, active from cron.job` (no `command` column: commands can carry bearer tokens; each job's target comes from `git grep -n cron.schedule supabase/migrations/`, and any command text that is needed is redacted first with `regexp_replace(command, 'Bearer [A-Za-z0-9._-]+', 'Bearer <redacted>', 'g')`)) and extensions (`select extname from pg_extension`) are read with the Supabase MCP `execute_sql`, SELECT only (dev; on prod only through the read-only role and never a function-calling SELECT, per `security-audit`'s hard rules). The `regexp_replace` covers only the `Bearer <token>` form, which is why the default omits `command`. Auth settings, Auth hooks and JWT and refresh-token lifetimes are not exposed by the MCP tools granted here and are not in `supabase/config.toml`: ask the owner for each value, one row per setting marked `DASHBOARD-ONLY`, source `owner-reported <date>`; any change to one is an owner action.

## 2. Default seats

- Attacker (red-team) seat, which is the skeptic; tenant-isolation and RLS seat (the rule is the tenant-membership helper, never the raw role table); data-protection and compliance seat; delivery seat (sequencing, review load, shared files).
- Tier M: the attacker seat plus the tenant-isolation seat and the data-protection seat. Tier L adds the delivery seat.

## 3. Required proposal content

A proposal that omits any item is a BLOCKER (SKILL.md phase 3 item 2).
1. A threat model: asset, actor, entry point, STRIDE-like category, existing control with `file:line`, gap, severity, proposed control, residual risk.
2. Every control placed at an enforcement point the client cannot bypass (RLS, edge function gate, database constraint); a UI check is never a control.
3. Fail-closed behaviour, and what the user sees when the control denies.
4. Tenant-isolation and privilege-change cases: revocation, role change, session reuse.
5. Logging and an audit trail without secrets or PII.
6. Negative tests for each control, written to fail before the fix.
7. The data contract: what is the source of truth, and how a stale or out-of-order claim or response is discarded.

## 4. Owner artefact

- The threat model table plus a residual-risk table with a recommended accept or fix per row and a one-line reason.
- No raw project refs, no `*.supabase.co` URLs, no secrets or tokens in any artefact; custom domains only (rules carried from the `security-audit` skill). Exploit steps and proof-of-concept payloads stay in `$E` (never committed); `$B` carries the threat model rows and the proposed controls.

## 5. Domain proof

- First `test -f .agents/skills/security-audit/SKILL.md` and record the result (a repo skill has no quota; if the file is missing, record the sweep as NOT VERIFIED). Then invoke the `security-audit` skill for sweeps and probes, never duplicating its checks. Its phases: 1 recon, 2 passive sweep (dev, sandbox and prod advisors and read-only queries), 3 active probes on dev and sandbox only, 4 prod passive and read-only. Run its phase 5 only to triage findings into DECISION.md, with no report file or ledger write; its phase 6 (email and draft PRs) and its closing checklist only when the owner asks.
- Probes: `<your probe runner> --env dev [--suite <names>] --out <file>` (and the sandbox environment). Read the `RESULT:` line: exit 0 and `RESULT: CLEAN` is the only clean pass; exit 250 and `RESULT: INCONCLUSIVE` means probes were skipped and is not a clean pass; 1 to 249 is the findings count. Its coverage matrix applies. If the probe fixtures file (gitignored) is absent on this machine, record the probe as NOT VERIFIED (no fixtures), never as clean. The step-up (AAL2) check is not automated: do it by hand and mark the gap.
- Each control also needs the failing-first adversarial test (SKILL.md phase 7 duty 3). Two reviews are required for auth, RLS or money paths (SKILL.md section 3 floor).

## 6. Dev observation

- The probe run or the regression test against dev or sandbox. The probe runner must refuse prod through its own environment guard; send no hand-made mutating request to prod. If it cannot be observed, write `NOT VERIFIED (<reason>)` in the handoff `--proof`.
- A merge touching `supabase/migrations/`, `supabase/functions/` or `supabase/config.toml` also needs the `Deploy to Dev` run green for the merge commit (SKILL.md phase 9 item 3).

## 7. Mutation families and runner

- Families for reviewer 2: role or tenant check inverted or removed, the tenant-membership helper replaced by a raw role-table read, revocation applied to the current session only or before the role write commits, fail-open on error, stale claim reused.
- Runner: `NODE_OPTIONS='--max-old-space-size=4096 --no-experimental-webstorage' npx --no-install vitest run <file>` through the lock in the isolated copy; Deno edge-function tests `bash scripts/preflight-lock.sh K-<n>-mut -- bash -c 'cd <copy>/supabase/functions && deno test --allow-all <one test file>'` per mutant, and `deno task test` (whole suite) only once, for the final confirmation run, under the same lock.
- pgTAP: `pnpm run test:db` runs `supabase/tests/*.test.sql`; migration specs under `supabase/migrations/__tests__/` run with `DB_URL=$(supabase status -o env | grep '^DB_URL=' | cut -d= -f2- | tr -d '"') bash .github/scripts/run-migration-specs.sh`; both only if a local stack is up (`supabase status` succeeds), otherwise the mutant is recorded `CI-only` and not counted as killed, and a money or auth card with a `CI-only` mutant on its core condition stays BLOCK until CI evidence names it.

## 8. Extra owner questions (each with a default)

- Which residual risks are accepted? Default: none above medium.
- Revoke sessions immediately or at next refresh? Default: immediately.
- Who is notified? Default: the owner.
- Moving real money against prod or any irreversible action stays an owner hard stop (AGENTS.md).

## 9. Verified references

`.agents/skills/security-audit/SKILL.md` and `report-template.md`, your probe runner and its environment guard, `docs/agents/rules/edge-functions.md`, `docs/agents/rules/realtime.md`, and the `supabase-expert` and `webhook-system` (SSRF) skills.
