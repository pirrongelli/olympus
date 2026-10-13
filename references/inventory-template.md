---
title: "Inventory template and method"
description: "Two-counter recount method and column reference for the UI domain evidence base."
applies_to: "olympus"
---

# Inventory template and method

Purpose: the real-screen inventory columns and the two-counter reconciliation that catches a missing screen or modal before the panel debates. Serves SKILL.md section 5, phase 1 (evidence base) and the phase 1 exit check ("two counts agree").

## Columns

| `#` | `file:line` | side | click path | type | family | LOC | states | money | callers |
|---|---|---|---|---|---|---|---|---|---|
| Row number | Source file and line of the component trigger | `admin`, `customer` or `both` | Path to reach the screen from the navigation or an entry point; "orphan" if unreachable | detail, form, confirm, wizard, picker | Named UI family (for example "payment", "kyc", "transfer") | Lines of code in this component | Comma-separated: loading, error, empty (those the screen handles) | `1` if the screen carries or shows money; `0` otherwise | Number of files that import it |

- **click path:** the route trigger or manual navigation (`Settings > Admin > Users`), or file:line if the component is unreachable from the UI.
- **family:** group by business domain or flow, for the parallel card split.
- **money:** `1` for payments, fees, balances, transfers; `0` for configuration or informational screens.
- **callers:** count of importing files, for example `git grep -lE "from ['\"]@/components/<path>/<Component>['\"]" <pinned-sha> -- 'src/**/*.tsx' | wc -l`. A shared component with two or more callers needs a snapshot-gated card.

## Two counters, one unit

Both counters report the same unit: the files that render a dialog-family surface (modal, dialog, sheet, drawer). Run both on the pinned head (the SHA in the DECISION.md header); `git grep <sha>` reads that commit, so no checkout state matters. The exclusions drop tests and the primitives themselves.

```bash
P=<pinned sha>
EX=(':(exclude)src/components/ui' ':(exclude)src/test' ':(exclude,glob)**/*.test.*' ':(exclude,glob)**/*.spec.*' ':(exclude,glob)**/__tests__/**')
# Counter 1: component-tag regex (files that open a surface)
git grep -lE '<(Dialog|AlertDialog|Sheet|Drawer)\b' "$P" -- 'src/**/*.tsx' "${EX[@]}" | sed 's/^[^:]*://' | sort > c1.txt
# Counter 2: import query (files that import the surface primitives)
git grep -lE "from ['\"]@/components/ui/(dialog|alert-dialog|sheet|drawer)['\"]" "$P" -- 'src/**/*.tsx' "${EX[@]}" | sed 's/^[^:]*://' | sort > c2.txt
wc -l c1.txt c2.txt
comm -23 c1.txt c2.txt   # only in counter 1
comm -13 c1.txt c2.txt   # only in counter 2
```

Adapt the tag names and the primitive paths to the repository under study. Optional third list for the "orphan" check: `command -v graft`, then `graft build && graft ask "<question>"` (`docs/agents/graft.md`) to find which routes reach each file; it does not parse SQL.

### Reconciliation

1. The counts agree when `comm` prints nothing in both directions. Equal totals with different lists do not count.
2. Every file in only one list gets one line in the inventory header: a file that imports a primitive but only uses a sub-part (a header or footer wrapper) is counted by counter 2 and not by counter 1; a file that renders a primitive re-exported from another path is counted by counter 1 only. Add or drop the row accordingly.
3. The `callers` column comes from the import query, not from the tag regex.

Worked example (run on this repository's head `2c9a928d` on 2026-10-13): counter 1 listed 134 files, counter 2 listed 138; the four extra files (`InvoiceReviewHeader.tsx`, `MoneynetPaymentProgress.tsx`, `SheetHeader.tsx`, `TxDetailFooter.tsx`) import the dialog primitive but use only `DialogHeader`, `DialogTitle`, `DialogDescription`, `DialogFooter` or `DialogClose`, so each got one header line and no new row.

## Stale-checkout note

If the worktree is not on the pinned head, do not count the working tree. List the pinned snapshot's file names:

```bash
git archive --format=tar <pinned sha> src/ | tar -t | grep -E '\.(tsx|ts)$' | wc -l
```

`tar -t` lists names (`tar -x --to-stdout` would print file contents and the count would be 0). `git ls-tree -r --name-only <pinned sha> -- src/ | grep -E '\.(tsx|ts)$' | wc -l` gives the same number.

## Grouping into families

1. Sort by **family**, then by **money** (money-handling first), then by **callers** (high-traffic first).
2. Identify orphaned screens (click path "orphan") for a deletion or discovery card.
3. Group screens that share state tables or form patterns into one family.
4. Document any assumption (for example "the form family includes login and password reset").

## Example rows (illustrative placeholders, not real files)

| `#` | `file:line` | side | click path | type | family | LOC | states | money | callers |
|---|---|---|---|---|---|---|---|---|---|
| 1 | `src/pages/customer/<Page>.tsx:<line>` | customer | Dashboard (entry) | detail | home | 240 | loading, error, empty | 1 | 3 |
| 2 | `src/components/<area>/<Dialog>.tsx:<line>` | customer | Dashboard > Make payment | form | payment | 580 | loading, error | 1 | 1 |
| 3 | `src/components/<area>/<Header>.tsx:<line>` | both | top navigation | detail | nav | 120 | (none) | 0 | 12 |

## Where it is saved

Save as `INVENTORY.md` in the decision directory (`$B`). It is committed in the planning PR together with the rest of `$B`, for both exits (SKILL.md section 1 rule 6 and phase 6 item 6); per-card PRs never touch it.
