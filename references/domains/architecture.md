# Architecture pack

Purpose: the domain pack for architecture decisions (service boundaries, data flows, migrations, provider paths). Serves SKILL.md phase 1 (evidence base), phase 2 (seats), phase 3 item 2 (required proposal content), phase 4 (owner artefact), phase 7 duty 6 (domain proof), phase 8 item 3 (mutation families and runner) and phase 9 item 3 (dev observation). The core owns every gate, command and lock; this pack only names the domain's content.

## 1. Evidence base
Current-state map from the code, never from memory. Rows with `file:line`, owning module, callers, data stores touched, external provider calls, tests that pin it. Method: `command -v graft`, then `graft build && graft ask "<question>"`; graft does not parse SQL, so grep `supabase/migrations/` separately (`docs/agents/graft.md`); fall back to `git grep -n` and the import graph. Two counters with different queries, reconciled (core phase 1). The map explicitly covers `supabase/functions/**` (including `_shared` imports and the Deno config in `supabase/functions/deno.json`); when graft is absent, `git grep -n` on those paths (Deno URL and relative imports included) lists the callers. Runtime configuration the code does not show: cron schedules (`select jobid, jobname, schedule, active from cron.job` (no `command` column: commands can carry bearer tokens; each job's target comes from `git grep -n cron.schedule supabase/migrations/`, and any command text that is needed is redacted first with `regexp_replace(command, 'Bearer [A-Za-z0-9._-]+', 'Bearer <redacted>', 'g')`)) and extensions (`select extname from pg_extension`) are read with the Supabase MCP `execute_sql`, SELECT only (dev; on prod only through the read-only role and never a function-calling SELECT, per `security-audit`'s hard rules). Auth settings, Auth hooks and JWT and refresh-token lifetimes are not exposed by the MCP tools granted here and are not in `supabase/config.toml`: ask the owner for each value, one row per setting marked `DASHBOARD-ONLY`, source `owner-reported <date>`; any change to one is an owner action. Operating constraints from `docs/ENVIRONMENTS.md` and `docs/DISASTER_RECOVERY.md`.

## 2. Default seats
correctness and money-truth skeptic (`docs/agents/rules/money-moves.md`); data and migration seat (`supabase-expert` skill, `create-migration` skill); operations and rollback seat; delivery seat (sequencing and shared files); L adds a seat for the external-provider contract.

## 3. Required proposal content
(a) the current-state map rows the change touches; (b) at least two options plus "do nothing", each as an ADR section: context, decision, trade-offs, cost, risks; (c) reversibility (one-way or two-way door), migration path and rollback path (the `create-migration` skill's description mentions rollback support but its body has no rollback section, so this pack requires the ADR to state the rollback path itself and, for a migration, to name the pgTAP spec under `supabase/migrations/__tests__/` that the skill describes); (d) blast radius: edge functions (`docs/agents/rules/edge-functions.md`), realtime and RLS (`docs/agents/rules/realtime.md`), callers listed from the map; (e) fitness checks written as commands that exist (`pnpm run typecheck:ratchet`, `pnpm run quality:deadcode`, `pnpm run quality:complexity`, `pnpm run quality:crap`, scoped vitest); (f) the data contract (what is the source of truth, how stale or out-of-order data is discarded).

## 4. Owner artefact
the ADR set with a recommended option and a one-line reason per option, delta-only against the current-state map (show what changes, copy what stays); a migration and rollback timeline; the questions in item 8.

## 5. Domain proof
the fitness checks under `preflight-lock.sh`; a rollback rehearsal on dev (precedent: `docs/audits/2026-05-19-prod-deploy-rollback-rehearsal.md`); for money paths the adversarial two-requests-in-parallel test (AGENTS.md quality bar) and the mutant families of the reviewer mutation brief (SKILL.md phase 8 item 3); any enum, field name or payload sent to provider B or provider A validated against the live sandbox first (AGENTS.md hard stop).

## 6. Dev observation
the migration or function deploy run for the merge commit (core phase 9 item 3) plus the changed behaviour observed on `dev`; otherwise `NOT VERIFIED (<reason>)`.

## 7. Mutation families and runner
tenant filter dropped, idempotency key ignored so a retry creates a duplicate, provider payload field mapped to the wrong source, v1/v2 branch chosen by the wrong flag, error swallowed into success. Runner: `bash scripts/preflight-lock.sh K-<n>-mut -- bash -c 'cd <copy>/supabase/functions && deno test --allow-all <one test file>'` per mutant; `deno task test` (whole suite) only once, for the final confirmation run, under the same lock. pgTAP: `pnpm run test:db` runs `supabase/tests/*.test.sql`; migration specs under `supabase/migrations/__tests__/` run with `DB_URL=$(supabase status -o env | grep '^DB_URL=' | cut -d= -f2- | tr -d '"') bash .github/scripts/run-migration-specs.sh`; both only if a local stack is up (`supabase status` succeeds), otherwise the mutant is `CI-only` and not counted as killed; a money or auth card with a `CI-only` mutant on its core condition stays BLOCK until CI evidence names it.

## 8. Extra owner questions
Each with a default: accept a one-way door? default no unless the rollback rehearsal passed; phase the migration over several cards? default yes; any change to global client defaults (`QueryClient` options, `src/App.tsx`, the Supabase client) is an AGENTS.md hard stop and is always asked.

## 9. Exit type
Decision-only exit is the common case: the planning PR plus follow-up cards (core section 3 exit types).
