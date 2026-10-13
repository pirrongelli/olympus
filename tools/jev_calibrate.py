#!/usr/bin/env python3
"""Read the Jev ledger and report whether J1 may move from SHADOW to ADVISORY.

Standard library only. It never writes the calibration file: creating references/jev-calibration.json
is a reviewed change, so this prints the proposed entry and a PROMOTE, HOLD or RETIRE line.

    python3 tools/jev_calibrate.py [--store DIR] [--draws 10000] [--seed 20261013] [--json]

The pre-registered rule (references/jev.md, section "Promotion"): at least 30 distinct seed pairs
authored by at least 2 seats, at least 20 wrong verdicts that carry a P, the 2.5th percentile of a
cluster-bootstrap AUC (the pair id is the cluster) at least 0.70, an AUC above the word-count
baseline, and a median |P_AB - P_BA| of at most 0.10. AUC uses the raw mean P.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import jev_steps  # noqa: E402

MIN_PAIRS = 30
MIN_AUTHORS = 2
MIN_WRONG = 20
MIN_AUC_LOWER = 0.70
MAX_MEDIAN_DELTA = 0.10
MAX_NA_FRACTION = 0.10
DRAWS = 10000
DEMOTE_AUC = 0.65
DEMOTE_WINDOW = 30
RETIRE_VERDICTS = 60
RETIRE_PAIRS_FOR_VERDICTS = 30
RETIRE_PAIRS_FOR_WRONG = 60
EXTEND_TO_ROWS = 100
ACTION_FRACTION = 0.25


# --------------------------------------------------------------------------- statistics


def wilson(successes, n, z=1.96):
    """95% Wilson score interval for a proportion."""
    if n <= 0:
        return (0.0, 1.0)
    p = successes / float(n)
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def auc(scores, labels):
    """P(score of a positive > score of a negative), ties counting half. None without both classes."""
    positives = [s for s, y in zip(scores, labels) if y]
    negatives = [s for s, y in zip(scores, labels) if not y]
    if not positives or not negatives:
        return None
    order = sorted(range(len(scores)), key=lambda i: scores[i])
    ranks = [0.0] * len(scores)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and scores[order[j + 1]] == scores[order[i]]:
            j += 1
        average = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = average
        i = j + 1
    rank_sum = sum(r for r, y in zip(ranks, labels) if y)
    n1, n2 = len(positives), len(negatives)
    return (rank_sum - n1 * (n1 + 1) / 2.0) / (n1 * n2)


def hanley_mcneil(a, n_pos, n_neg, z=1.96):
    """95% interval for an AUC (Hanley and McNeil 1982). Treats rows as independent, so it is secondary."""
    if a is None or n_pos < 1 or n_neg < 1:
        return (None, None)
    q1 = a / (2 - a)
    q2 = 2 * a * a / (1 + a)
    variance = (a * (1 - a) + (n_pos - 1) * (q1 - a * a) + (n_neg - 1) * (q2 - a * a)) / float(n_pos * n_neg)
    se = math.sqrt(max(variance, 0.0))
    return (max(0.0, a - z * se), min(1.0, a + z * se))


def cluster_bootstrap_auc(rows, draws=DRAWS, seed=0):
    """Resample whole pairs (every order and judge of a pair together); return (p2.5, p97.5, valid draws)."""
    clusters = {}
    for row in rows:
        clusters.setdefault(row["pair_id"], []).append(row)
    keys = sorted(clusters)
    rng = random.Random(seed)
    values = []
    for _ in range(draws):
        sample = [r for key in (rng.choice(keys) for _ in keys) for r in clusters[key]]
        value = auc([r["p"] for r in sample], [r["label"] == "correct" for r in sample])
        if value is not None:
            values.append(value)
    if not values:
        return (None, None, 0)
    values.sort()
    low = values[int(0.025 * (len(values) - 1))]
    high = values[int(math.ceil(0.975 * (len(values) - 1)))]
    return (low, high, len(values))


def word_baseline_auc(rows):
    """AUC of 'the longer winner is right', in the better of its two orientations (a fair, strong baseline)."""
    scores = []
    for r in rows:
        winner_words = r["words_a"] if r["winner_code"] == r["code_a"] else r["words_b"]
        loser_words = r["words_b"] if r["winner_code"] == r["code_a"] else r["words_a"]
        scores.append(winner_words - loser_words)
    value = auc(scores, [r["label"] == "correct" for r in rows])
    return None if value is None else max(value, 1 - value)


def _logit(p):
    p = min(max(p, 1e-4), 1 - 1e-4)
    return math.log(p / (1 - p))


def platt_fit(scores, labels, iterations=50):
    """Fit sigmoid(a * logit(p) + b) by Newton steps with a tiny ridge. Returns (a, b)."""
    if not scores or len(set(labels)) < 2:
        return None
    xs = [_logit(s) for s in scores]
    a, b = 1.0, 0.0
    for _ in range(iterations):
        ga = gb = 0.0
        haa = hab = hbb = 1e-6
        for x, y in zip(xs, labels):
            p = 1 / (1 + math.exp(-(a * x + b)))
            ga += (p - (1 if y else 0)) * x
            gb += (p - (1 if y else 0))
            w = p * (1 - p)
            haa += w * x * x
            hab += w * x
            hbb += w
        det = haa * hbb - hab * hab
        if abs(det) < 1e-12:
            break
        da = (hbb * ga - hab * gb) / det
        db = (-hab * ga + haa * gb) / det
        a -= da
        b -= db
        if abs(da) + abs(db) < 1e-9:
            break
    return (a, b)


def brier(probabilities, labels):
    if not probabilities:
        return None
    return sum((p - (1 if y else 0)) ** 2 for p, y in zip(probabilities, labels)) / float(len(probabilities))


def split_by_pair(rows):
    """Deterministic half split by pair id: (fit rows, hold rows)."""
    fit, hold = [], []
    for row in rows:
        bucket = hashlib.sha256(row["pair_id"].encode("utf-8")).digest()[0] & 1
        (fit if bucket == 0 else hold).append(row)
    return fit, hold


def action_threshold(fit_rows):
    """The P below which at most 25% of the fitting half falls."""
    values = sorted(r["p"] for r in fit_rows)
    if not values:
        return None
    return values[int(ACTION_FRACTION * len(values))]


# --------------------------------------------------------------------------- report


def build_report(events, draws=DRAWS, seed=0):
    verdicts = jev_steps.merged_verdicts(events)
    seeded = [r for r in verdicts if r["kind"] == "seeded"]
    natural = [r for r in verdicts if r["kind"] == "natural" and not r.get("tie")]
    authors_by_pair = {}
    for event in events:
        if event.get("type") == "commitment":
            author = (event.get("author") or "").strip().lower()
            if author and author != "unknown":
                authors_by_pair[event["pair_id"]] = author

    # calibration is valid for one (resolved model, question hash): keep the largest group
    groups = {}
    for row in seeded:
        if row.get("label") is not None and not row.get("na") and row.get("p") is not None:
            groups.setdefault((row["resolved_model"], row["question_sha256"]), []).append(row)
    key = max(groups, key=lambda k: len(groups[k])) if groups else (None, None)
    usable = groups.get(key, [])
    excluded = sum(len(v) for k, v in groups.items() if k != key)

    labelled_seeded = [r for r in seeded if r.get("label") is not None]
    ties = [r for r in labelled_seeded if r.get("tie")]
    non_tie_seeded = [r for r in seeded if not r.get("tie")]
    na_rows = [r for r in non_tie_seeded if r.get("na")]
    na_fraction = len(na_rows) / float(len(non_tie_seeded)) if non_tie_seeded else 0.0
    # pairs and authors are counted from the same (model, hash) group as the AUC and the wrong count
    pair_ids = sorted({r["pair_id"] for r in usable})
    authors = sorted({authors_by_pair[pid] for pid in pair_ids if pid in authors_by_pair})
    wrong = [r for r in usable if r["label"] == "wrong"]

    report = {
        "model": key[0], "question_sha256": key[1],
        "verdicts_with_p": len(usable), "wrong_with_p": len(wrong), "pairs": len(pair_ids), "authors": authors,
        "ties": len(ties), "tie_rate": (len(ties) / float(len(labelled_seeded))) if labelled_seeded else None,
        "na_fraction": na_fraction, "excluded_other_model_or_hash": excluded,
        "natural_labelled_fraction": (
            sum(1 for r in natural if r.get("label") is not None) / float(len(natural)) if natural else None
        ),
    }
    scores = [r["p"] for r in usable]
    labels = [r["label"] == "correct" for r in usable]
    point = auc(scores, labels)
    report["auc"] = point
    report["auc_hanley_mcneil"] = hanley_mcneil(point, sum(labels), len(labels) - sum(labels))
    low, high, valid = cluster_bootstrap_auc(usable, draws, seed) if usable else (None, None, 0)
    report["auc_bootstrap"] = {"p2_5": low, "p97_5": high, "valid_draws": valid, "draws": draws}
    report["word_baseline_auc"] = word_baseline_auc(usable) if usable else None
    deltas = [r["delta"] for r in usable if r.get("delta") is not None]
    report["median_delta"] = statistics.median(deltas) if deltas else None

    fit, hold = split_by_pair(usable)
    platt = platt_fit([r["p"] for r in fit], [r["label"] == "correct" for r in fit])
    report["platt"] = {"a": platt[0], "b": platt[1]} if platt else None
    report["action_threshold"] = action_threshold(fit)
    if platt and hold:
        calibrated = [1 / (1 + math.exp(-(platt[0] * _logit(r["p"]) + platt[1]))) for r in hold]
        report["brier_holdout"] = brier(calibrated, [r["label"] == "correct" for r in hold])
        report["brier_holdout_constant"] = brier([sum(r["label"] == "correct" for r in fit) / float(len(fit))] * len(hold),
                                                 [r["label"] == "correct" for r in hold])
    else:
        report["brier_holdout"] = report["brier_holdout_constant"] = None
    report["holdout_rows"] = len(hold)

    # demotion: AUC over the last 30 labelled verdicts (any kind) in time order
    labelled_all = sorted((r for r in verdicts if r.get("label") is not None and r.get("p") is not None), key=lambda r: r["ts"])
    recent = labelled_all[-DEMOTE_WINDOW:]
    recent_auc = auc([r["p"] for r in recent], [r["label"] == "correct" for r in recent]) if len(recent) >= DEMOTE_WINDOW else None
    report["recent_auc"] = recent_auc
    report["demote"] = recent_auc is not None and recent_auc < DEMOTE_AUC

    checks = {
        "pairs>=30": len(pair_ids) >= MIN_PAIRS,
        "authors>=2": len(authors) >= MIN_AUTHORS,
        "wrong>=20": len(wrong) >= MIN_WRONG,
        "bootstrap p2.5>=0.70": low is not None and low >= MIN_AUC_LOWER,
        "auc>word baseline": point is not None and report["word_baseline_auc"] is not None and point > report["word_baseline_auc"],
        "median delta<=0.10": report["median_delta"] is not None and report["median_delta"] <= MAX_MEDIAN_DELTA,
        "na<=10%": na_fraction <= MAX_NA_FRACTION,
    }
    report["checks"] = checks
    retire = (
        (len(pair_ids) >= RETIRE_PAIRS_FOR_VERDICTS and len(usable) < RETIRE_VERDICTS)
        or (len(pair_ids) >= RETIRE_PAIRS_FOR_WRONG and len(wrong) < MIN_WRONG)
    )
    if all(checks.values()):
        report["decision"] = "PROMOTE"
        report["note"] = "Creating references/jev-calibration.json is a reviewed change; the entry below is a proposal."
    elif na_fraction > MAX_NA_FRACTION:
        report["decision"] = "HOLD"
        report["note"] = "rerun: more than 10% of seeded verdicts are na"
    elif retire:
        report["decision"] = "RETIRE"
        report["note"] = ("owner decides: %d verdicts with a P after %d distinct pairs, %d wrong (ties carry no P and do not count; "
                          "retire at 60 verdicts after 30 pairs, or at 60 pairs with fewer than 20 wrong)"
                          % (len(usable), len(pair_ids), len(wrong)))
    else:
        report["decision"] = "HOLD"
        if len(usable) >= RETIRE_VERDICTS and len(usable) < EXTEND_TO_ROWS:
            report["note"] = "undecided at %d rows (about 46%% power at a true AUC of 0.80, and less for a clustered bootstrap): extend to 100 rows first" % len(usable)
        else:
            report["note"] = "author pairs in batches of 10 until 20 wrong verdicts accrue"
    if report["decision"] == "PROMOTE" and key[0]:
        report["proposed_entry"] = {
            "resolved_model": key[0], "question_sha256": key[1], "action_threshold": report["action_threshold"],
            "platt": report["platt"], "pairs": len(pair_ids), "wrong": len(wrong),
        }
    return report


def format_report(report):
    def fmt(x):
        return "n/a" if x is None else ("%.3f" % x if isinstance(x, float) else str(x))

    boot = report["auc_bootstrap"]
    hm = report["auc_hanley_mcneil"]
    lines = [
        "model %s, question sha256 %s" % (report["model"], (report["question_sha256"] or "n/a")[:12]),
        "seeded verdicts with a P: %d (wrong %d); distinct pairs %d; authors %s" % (
            report["verdicts_with_p"], report["wrong_with_p"], report["pairs"], ", ".join(report["authors"]) or "none"),
        "ties %d (rate %s); na fraction %s; rows excluded for another model or hash: %d" % (
            report["ties"], fmt(report["tie_rate"]), fmt(report["na_fraction"]), report["excluded_other_model_or_hash"]),
        "AUC %s; cluster bootstrap 2.5th-97.5th percentile %s - %s (%s of %s draws); Hanley-McNeil %s - %s" % (
            fmt(report["auc"]), fmt(boot["p2_5"]), fmt(boot["p97_5"]), boot["valid_draws"], boot["draws"], fmt(hm[0]), fmt(hm[1])),
        "word-count baseline AUC %s; median |P_AB - P_BA| %s" % (fmt(report["word_baseline_auc"]), fmt(report["median_delta"])),
        "Platt %s; Brier on the held-out half (%d rows) %s vs constant %s (reported, not gated); action threshold %s" % (
            report["platt"] and "a=%.3f b=%.3f" % (report["platt"]["a"], report["platt"]["b"]) or "n/a",
            report["holdout_rows"], fmt(report["brier_holdout"]), fmt(report["brier_holdout_constant"]), fmt(report["action_threshold"])),
        "natural-run labelled fraction %s (labels are supplementary: demotion only)" % fmt(report["natural_labelled_fraction"]),
        "demotion check: AUC over the last %d labelled verdicts %s%s" % (DEMOTE_WINDOW, fmt(report["recent_auc"]), " -> DEMOTE to SHADOW" if report["demote"] else ""),
    ]
    for name, ok in report["checks"].items():
        lines.append("  [%s] %s" % ("x" if ok else " ", name))
    lines.append("DECISION: %s (%s)" % (report["decision"], report["note"]))
    if report.get("proposed_entry"):
        lines.append("proposed jev-calibration.json entry: " + json.dumps(report["proposed_entry"], sort_keys=True))
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Report whether J1 may be promoted from SHADOW to ADVISORY.")
    parser.add_argument("--store", default=None)
    parser.add_argument("--draws", type=int, default=DRAWS)
    parser.add_argument("--seed", type=int, default=20261013)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    report = build_report(jev_steps.read_events(jev_steps.store_dir(args.store)), args.draws, args.seed)
    print(json.dumps(report, indent=2, sort_keys=True) if args.json else format_report(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
