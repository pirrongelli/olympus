# Seed pairs for Jev calibration (recipe only)

Purpose: how to author the seeded-defect pairs that give J1 its promotion labels (`references/jev.md` section 6). This file is the recipe. **No seed pack is shipped with Olympus**: the pack lives in `${OLYMPUS_JEV_DIR:-$HOME/.local/state/olympus/jev}/seeds/`, outside every repo, and writing it is a separate, owner-approved calibration run (it costs authoring, judge runs and Jev calls). Until at least 30 pairs exist and `tools/jev_calibrate.py` says PROMOTE, J1 stays at SHADOW.

## What a pair is
- Two proposals that answer the same question, **authored independently** (two seats, no sight of each other's text), each a normal-length proposal of the kind an Arena judges. One carries exactly one planted, checkable defect. It is not a minimal pair: the texts differ in structure and wording, so the verdict cannot be won by spotting the diff.
- The planted defect is checkable by a command or a lookup that a careful judge could run: an invented tool or flag (`ls` the path, `grep` the flag), a false figure (the source has the real number), a removed gate (the process step that makes a known failure mechanical), a wrong order of steps. Mix obvious and subtle ones; subtle ones (a plausible but wrong flag, a figure off by a unit) are where a light-tier judge fails, and wrong verdicts are what the AUC needs.
- Neither text says "seed", "synthetic", "planted" or names a defect. The judges get the normal judge brief.

## Authoring steps (the authoring seat, one pair at a time)
1. Pick a question with a verifiable answer in the repository or the docs. Vary topics across pairs; do not reuse a defect type more than a third of the time.
2. Seat X writes proposal 1 and seat Y writes proposal 2, both clean, each at most 900 words. Keep the word counts within 20% of each other; length is a cue the word-count baseline will measure anyway.
3. Draw which one is defective with `python3 -c "import secrets; print(secrets.choice(['a','b']))"` and plant exactly one defect in it, in the author's own style. Write the clean text to one file and the defective text to the other, with the draw deciding which is `a.md` and which is `b.md`.
4. Avoid the scan words (`references/jev.md` section 4: `RLS`, `IBAN`, `SWIFT`, `KYC`, tenant, payout, key-like strings). `seeded-run` refuses a seed that hits the scan.
5. Pack layout, with a neutral pair id such as `sp-3f9a1c`: `seeds/<pair-id>/a.md` and `seeds/<pair-id>/b.md`. No other file in the directory: nothing may name the defect, the author or the draw. The code-to-pair map is created later by `seeded-run` and stays in the store's `seed-runs/`.
6. **Key hygiene.** The authoring seat returns the key only in its final message, as `<pair-id>:a` or `<pair-id>:b` (the defective file) plus one line naming the defect for the reviewer. It never writes the key or the defect note to a file. The President commits it (`seeded-commit`) and keeps key and nonce in context. If the context is lost, the President asks the seat again by `SendMessage`; the commitment proves the answer.

## Cohort rules
- At least 30 distinct pairs from at least 2 authoring seats (`seeded-commit --author <seat>` is required, never `unknown`, and only distinct names count). Author in batches of 10 and run `tools/jev_calibrate.py` after each batch.
- Stop when 20 wrong verdicts have accrued and the promotion checks are met. If 60 pairs give fewer than 20 wrong verdicts, the planted defects are too easy for the judge tier: retire the experiment (the owner decides) or harden the defects.
- A pair is used once. A lost-key void returns it to the pool (commit again with a fresh nonce); a leak retires it.
- A seeded Arena is a calibration card outside any decision: one pair, two verdicts (positions swapped), by a light-tier judge, never writing `$B` or DECISION.md, and not triggered by phase 3 item 8.

## Running one
```
printf '%s' '<pair-id>:<a|b>' | python3 tools/jev_steps.py seeded-commit --pair-id <pair-id> --author <seat>
python3 tools/jev_steps.py seeded-run --pair-id <pair-id>        # prints the run id, a neutral /var/tmp/olympus-arena-<hex> directory and the header path (in the store)
# judges read only <dir>/blind/; the President writes $E/jev/ruling.md (with a timestamp line)
python3 tools/jev_steps.py j1 --e $E --blind <dir>/blind --verdicts <judging files> --rubric <rubric> --decision <header path printed by seeded-run>
# after ruling.md and every verdict file exist, write seeds-key/<pair-id>.json {"key","nonce"}
python3 tools/jev_steps.py label seeded --run <run> --e $E --verdicts <judging files> --judge-outputs <judge files or dirs>
```
