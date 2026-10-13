---
title: "UI redesign domain pack"
description: "Evidence base, required proposal content, seats, proof and mutation families for UI redesign decisions."
applies_to: "olympus"
---

# UI redesign

One-line purpose: guides panel decisions on screen and modal redesigns with accessibility, data exposure rules, delta mockups, and visual proof.

## 1. Evidence base: real-screen inventory

Two counters run different queries on the pinned head and report the same unit (files that render a dialog-family surface, found by a component-tag regex and by an import query); use a `git archive` snapshot or `git grep <pinned sha>` if the checkout is stale. Commands and reconciliation: `references/inventory-template.md`.

Columns: `#`, `file:line`, side, click path (trigger file:line, or "orphan"), type (detail, form, confirm, wizard, picker), family, LOC, states covered (loading, error, empty), money-payload flag, callers. Template: `references/inventory-template.md`.

Reconcile the counts; explain any mismatch. Count callers of every shared component: an atom with many importers needs a snapshot-gated card.

Group into families; rank by traffic and money risk.

## 2. Default seats

M: money-truth skeptic; access and interaction (a11y, mobile, motion, data contract); visual-system plus product-copy. L adds product-copy as its own seat and a delivery seat (sequencing, review load, shared files).

## 3. Required proposal content

(a) a **state table before any sketch** for every family that shows status or money: state, evidence field, label, sentence, action; anything unmatched falls to "unavailable", never a success state.

(b) **Exposure rules by direction:** outbound beneficiary data complete and copyable; inbound third-party data masked (last 4, masked copy), cut at the source function, not only on screen.

(c) **Acceptance per family:** keyboard order, Escape and focus return, screen-reader announcements, 375 px and 320 px and 200% zoom without horizontal scroll, contrast measured light and dark, tokens only, `prefers-reduced-motion` honoured and no essential information carried only by motion. Axe only if the repo has it; adding it is an owner question (K-1914 handoff).

(d) **Data contract per family:** which query feeds the view, what renders while it loads or fails, and how a stale or out-of-order response is discarded (the K-2027 race).

## 4. Owner artefact: delta mockups on the Design canvas

Owner rule 2026-10-11: reproduce the real current screen completely, then show only the delta; never draw from scratch.

1. Read the real component and sub-components; copy labels, order, states, quotes, fees, helper text, footer buttons. Unknown values use realistic sample data; never omit a row.

2. Per family: today beside new at desktop, 375 px, dark, loading, error, with a "changes vs today" note (`references/mockup-canvas.md`).

3. A Haiku seat extracts the field list from the source and from each artboard into `canvas.md`; a Sonnet seat compares them and rules. A field in code and missing on the board is a defect. Mechanics: `references/mockup-canvas.md` (Artifact quickstart first, Design type, one artboard per call).

## 5. Domain proof: Visual proof

`bash scripts/preflight-lock.sh K-<n> -- bash -c 'npm run test:browser && python3 scripts/ops/browser-evidence.py record'` on a clean tree.

Before and after, light and dark, 375 px, loading, error, empty; animations disabled and volatile values masked (the Playwright screenshot options `animations: "disabled"` and `mask`, both present in the installed `playwright-core` types). The proof rule itself is in `docs/agents/rules/ui-forms.md` ("THE PROOF RULE"). Tracked screenshots: commit them and re-run the chain; if the PNGs are not byte-stable (tree dirty, `record` refuses), attach them to the PR body from an ignored directory. Baseline-diff tests are an owner decision.

### Fix-round trigger

The visual proof and browser record run only when the fix delta touches any non-test file under `src/` or the styling config (`git diff --name-only <old>..<new> -- src index.html vite.config.ts ':(exclude)src/test' ':(exclude,glob)**/*.test.*' ':(exclude,glob)**/*.spec.*' ':(exclude,glob)**/__tests__/**'` prints any path); otherwise the PR body cites the last visual proof's head.

## 6. Dev observation

A UI-only merge has no deploy run (path filter in `deploy-dev.yml`); its observation is the named click path on `dev`, by the method in `docs/loops/MEMORY.md` ("Browser-level verification of admin UI on dev"), with one screenshot (light, 375 px): the old screen must be gone (K-102). Without an authenticated session write `dev click path: NOT VERIFIED (needs login)` in the handoff `--proof`, list it as an owner action, never write "verified".

## 7. Mutation families and runner

Direction and sign (payin as Sent), fee versus principal, account-field/identifier swaps (BIC/account), status mapped twice, stale or retry state leaking (IBAN after retry), crypto address and QR (catalogue in `references/reviewer-mutation-brief.md`).

Runner: `NODE_OPTIONS='--max-old-space-size=4096 --no-experimental-webstorage' npx --no-install vitest run <file>` through the lock in the isolated copy.

## 8. Extra owner questions

Add axe as a dev dependency (default yes for M and L); any additive UI instead of replacing a named click path (K-102, default not built).

## Verified references

`docs/agents/rules/ui-forms.md` (PROOF rule), `docs/agents/rules/frontend.md`, `npm run test:browser`, `scripts/ops/browser-evidence.py record`, the Design canvas Artifact type.
