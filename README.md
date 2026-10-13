# Olympus

Olympus is an agent skill for making software decisions with a panel of agents, then delivering the result with verified, reviewed changes.

Use it for any decision worth arguing before you build: a UI redesign across many screens, an architecture choice, a security posture, and similar.

## How it works

1. **Evidence base.** The panel works from facts read in the code (`file:line`), never from memory.
2. **Panel.** Several agents, each owning a failure class, write independent proposals.
   - An optional blind Arena judges them in pairs, with positions swapped.
   - The panel then debates with falsifiable blockers until it is unanimous, or until a supermajority at the round budget.
3. **Dry run and owner gate.** The decision is replayed against known failures. The owner then answers 1 to 3 questions, each with a recommended default.
4. **Two exits.**
   - *Decision-only*: an approved `DECISION.md` plus follow-up cards.
   - *Decision + build*: card split with a dependency check, test-first executors, deterministic gates under a machine-wide test lock, two independent reviews (static, then mutation testing), exact-head evidence and a pinned merge.

Domain packs in `references/domains/` adapt the evidence, the artefact shown to the owner and the verification gates:
- `ui-redesign.md`
- `architecture.md`
- `security.md`

To add a domain, add a pack. `SKILL.md` does not change.

## Layout

```
SKILL.md                  the skill (frontmatter + process)
references/               templates, briefs, commands, evals
references/domains/       domain packs
```

## Install

Copy this directory into your agent's skills folder: for example `.claude/skills/olympus/` for Claude Code, or `.agents/skills/olympus/`.

## Adapting it

Olympus was written for one repository's agent harness. Its commands name that harness's scripts: a worktree manager, a machine-wide test lock, a review-record verifier, a GitHub CLI wrapper, an evidence checker and a fleet dispatcher. `references/commands.md` lists every one of them with its flags.

Before you use Olympus in another project, map each command to your own equivalent or remove it. Placeholders such as `<your probe runner>` mark the places to adapt.
