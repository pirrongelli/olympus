---
title: "Mockup Canvas mechanics"
description: "How to build delta-only mockups on the Design canvas for olympus UI redesigns."
applies_to: "olympus"
---

# Mockup Canvas mechanics

Purpose: real-screen-plus-delta mockups on the Design canvas, so the owner approves what changes and the field-list gate catches anything the mockup dropped. Serves SKILL.md section 5, phase 4 (owner artefact) and the UI pack's owner artefact.

## Principles (owner rule 2026-10-11)

Reproduce the real current screen completely, then show only the delta; never draw from scratch.

1. Read the real component and its sub-components; copy labels, order, states, quotes, fees, helper text and footer buttons.
2. Unknown values use realistic sample data; never omit a row or field.
3. Show today's screen beside the new one.
4. Mark the delta with a "changes vs today" note on each artboard.

## Scope per family

Today beside new at: desktop, 375 px (a phone width), dark mode, and the loading and error states (and empty where the screen has one).

## Field-list comparison gate

A Haiku seat extracts the field list from the source code and from each artboard into `canvas.md`; a Sonnet seat compares the two lists and rules. A field in the code and missing from the board is a defect. A field on the board and absent from the code is a new field and needs implementation scope.

## Using the Design canvas (the Artifact tool)

Source: the Design Artifact type's own instructions (read with the `Artifact` tool, `action: "read"` and `type_url`, on 2026-10-13, release `1791894104-3e67`); re-read them before building, because the type is versioned.

- Start with the Artifact `quickstart` (`intent: "design"`). Create the canvas once: publish with `type_url` `https://claude.ai/artifact/QKN21svewxgyPb6SYRqWnd`, a `title` and `auto_open: "after_first_write"`, no files; every later call uses the returned `url`.
- Write only under `project/`: `project/canvas.json` (the index, with `boards`, `order` and `notes`) and one `<name>.dc.html` file per artboard. One artboard per call: write one file, send it, then write the next.
- An artboard's size is set in `canvas.json` (`w` and `h` in CSS px); the type's own phone-app size is 390×844, so set `w` to 375 for the 375 px artboard the owner rule names.
- The "changes vs today" note can be a `notes` entry in `canvas.json` (`x`, `y` and `text` are required) or text inside the artboard.
- To revise an existing canvas, `read` its `project/canvas.json` first, edit the files, and publish with the canvas `url`; a publish over someone's edit is refused and names the file to re-read. An owner's existing canvas is read-only unless the owner gives edit access.
- Put the canvas link in `$B/canvas.md` and in the owner gate message (SKILL.md phase 5). The design is final when the owner approves it at the gate and the planning PR merges; the owner reviews it there, and executors build from the approved `DECISION.md` and its excerpt, not from the canvas.
