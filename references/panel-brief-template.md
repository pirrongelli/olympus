# Panel brief template

Purpose: the fill-in brief the President sends to each panel seat, in its three variants (round 0 research, independent proposal, vote). Serves SKILL.md phase 2 (panel setup and research) and phase 3 (debate to consensus), and ground rules 1, 3 and 7.

## Dispatch rules (SKILL.md ground rules and section 2)
- Every dispatch cites `TASK K-<n>`; `.claude/hooks/task-gate.sh` denies a dispatch that does not.
- The first line of the brief is `Work at effort HIGH` (the `Agent` tool has no effort parameter).
- Spawn with `Agent` (`subagent_type: general-purpose`, `model`) and reuse the seat in later rounds with `SendMessage`. Only the President spawns; a seat never delegates.
- Tier by task: Haiku for counting, field-list extraction and copy tables; Sonnet for seats with blocking authority. A fact check (does this tool or flag exist, is this claim sourced) is never given to a light-tier seat.

## Brief skeleton (fill every field)
```
TASK K-<n> — <one-line topic>
Work at effort HIGH
SEAT: <seat name>   FAILURE CLASS OWNED: <the way a wrong decision would fail that this seat is responsible for catching>
ROUND: <0 research | 1 independent proposal | N vote on version vN>
OBJECTIVE: <one sentence: what this round must produce>
READ FIRST: <paths, with the line ranges that matter; the evidence base INVENTORY.md; for round N the PROPOSAL-vN.md and the previous votes>
CONSTRAINTS: <hard constraints of the topic, including the DECISION.md out-of-scope list>
OUTPUT: write only <$B/<file>.md>, at most <N> words
REPLY: at most <N> lines: the path and the key points
```

## Boundaries every brief states
- Research and writing only. Never run test suites, preflight, vitest, pnpm, or anything heavy. No git commits, no pushes, no branch changes.
- Write only the file the President names for the round, inside `$B`. Never edit another seat's file.
- Web: load WebSearch and WebFetch with ToolSearch (`select:WebSearch,WebFetch`) and cite URLs.
- Do not invent tools, scripts or flags: verify that a script or flag exists in the worktree before naming it.
- Token economy: the file holds the long content; the chat reply stays short. English only. No AI attribution in any file.

## Variant 0: research (`r0-<seat>.md`, 900 words at most)
Sources with URLs, what past runs in this repo show (cite files), and 5 to 8 recommendations from the seat's failure class.

## Variant 1: independent proposal (`r1-proposal-<seat>.md`)
Label it "proposal, not vote". Work alone: do not read another seat's round-1 file. Include the pack's required proposal content (SKILL.md phase 3 item 2).

## Variant N: vote (`r<N>-<seat>.md`)
Vote on the exact version named in the brief, in this format:
```
ROLE / VERSION / VOTE (AGREE|DISAGREE)
RESOLVED: each of my earlier blockers and how the text resolves it
AMENDMENTS: n. BLOCKER|MINOR, location, the false or lost claim, the fix
RESPONSE TO OTHERS: ACCEPT | ACCEPT WITH CHANGE | REJECT, why
```
A BLOCKER needs location, the claim the data does not prove or the information lost, and a concrete fix; anything else is MINOR. A bare flip to AGREE is invalid; a vote changes only on evidence.

Compact variant used in the K-2036 rounds 3 to 20 (same rules, same fields, one file per seat per round):
```
VOTE: AGREE | BLOCK
BLOCKERS: (only if BLOCK; each one falsifiable)
- B1: <file:section/line> | <what is false, missing or unsafe> | <exact replacement text or diff>
MINORS: (unvoted; go to the annex)
- M1: <location> | <suggestion>
RESOLVED: (only if AGREE; one line per blocker you raised in earlier rounds, saying how the draft resolved it)
RESPONSE-TO-OTHERS: (at most 3 lines; endorse or contest another seat's blocker with evidence)
```
A blocker without a location, a falsifiable claim and a concrete replacement is a minor. Check every tool or flag you mention against the worktree.
