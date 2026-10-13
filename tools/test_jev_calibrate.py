"""Offline tests for jev_calibrate.py on small synthetic ledgers."""

import contextlib
import io
import json
import os
import random
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import jev_calibrate as cal  # noqa: E402
import jev_steps  # noqa: E402

MODEL, QHASH = "jev-1.13.0", "q" * 64


def row(pair, index, label, p, delta=0.02, kind="seeded", tie=False, na=False, model=MODEL, words=(100, 100),
        run=None, ts=None):
    return {
        "type": "verdict", "run": run or "run-%s" % pair, "kind": kind, "verdict_id": "J%d:%s" % (index, pair),
        "pair_id": pair, "code_a": "AAA", "code_b": "BBB", "winner": "TIE" if tie else "A",
        "winner_code": None if tie else "AAA", "tie": tie, "p": None if (tie or na) else p,
        "delta": None if (tie or na) else delta, "na": na or tie, "na_reason": "tie" if tie else ("exit4" if na else None),
        "resolved_model": model, "question_sha256": QHASH, "words_a": words[0], "words_b": words[1],
        "ts": ts or "2026-10-13T%02d:%02d:00Z" % (index % 24, sum(map(ord, pair)) % 60), "label": label, "label_source": "seeded",
    }


def commitments(pairs, authors=("seatX", "seatY")):
    return [{"type": "commitment", "pair_id": pair, "author": authors[i % len(authors)], "commitment": "0" * 64,
             "ts": "2026-10-13T00:00:00Z"} for i, pair in enumerate(pairs)]


def good_ledger(pairs=30, authors=("seatX", "seatY"), seed=1):
    """Per pair: one correct verdict with a high P and one wrong with a low P, small jitter."""
    rng = random.Random(seed)
    events, ids = [], []
    for n in range(pairs):
        pair = "sp-%02d" % n
        ids.append(pair)
        events.append(row(pair, 0, "correct", 0.7 + 0.2 * rng.random()))
        events.append(row(pair, 1, "wrong", 0.2 + 0.2 * rng.random()))
    return events + commitments(ids, authors)


class StatsTest(unittest.TestCase):
    def test_wilson_matches_the_panel_figure(self):
        low, high = cal.wilson(21, 24)
        self.assertEqual((round(low, 2), round(high, 2)), (0.69, 0.96))
        self.assertEqual(cal.wilson(0, 0), (0.0, 1.0))

    def test_wilson_edges_stay_inside_0_1(self):
        low, high = cal.wilson(24, 24)
        self.assertLessEqual(high, 1.0)
        self.assertGreater(low, 0.8)
        self.assertEqual(cal.wilson(0, 10)[0], 0.0)

    def test_auc(self):
        self.assertEqual(cal.auc([0.9, 0.8, 0.3, 0.2], [1, 1, 0, 0]), 1.0)
        self.assertEqual(cal.auc([0.1, 0.2, 0.8, 0.9], [1, 1, 0, 0]), 0.0)
        self.assertEqual(cal.auc([0.5, 0.5, 0.5, 0.5], [1, 1, 0, 0]), 0.5)
        self.assertAlmostEqual(cal.auc([0.9, 0.4, 0.5, 0.2], [1, 1, 0, 0]), 0.75)
        self.assertIsNone(cal.auc([0.9, 0.4], [1, 1]))
        self.assertIsNone(cal.auc([], []))

    def test_hanley_mcneil_hand_computed(self):
        low, high = cal.hanley_mcneil(0.8, 2, 2)
        q1, q2 = 0.8 / 1.2, 2 * 0.64 / 1.8
        se = ((0.16 + (q1 - 0.64) + (q2 - 0.64)) / 4) ** 0.5
        self.assertAlmostEqual(low, max(0.0, 0.8 - 1.96 * se))
        self.assertAlmostEqual(high, min(1.0, 0.8 + 1.96 * se))
        narrow = cal.hanley_mcneil(0.8, 40, 40)
        self.assertLess(narrow[1] - narrow[0], high - low)
        self.assertEqual(cal.hanley_mcneil(None, 5, 5), (None, None))

    def test_cluster_bootstrap_on_separable_and_random_ledgers(self):
        rows = [r for r in good_ledger(30) if r["type"] == "verdict"]
        low, high, valid = cal.cluster_bootstrap_auc(rows, draws=300, seed=3)
        self.assertEqual((low, high), (1.0, 1.0))
        self.assertGreater(valid, 250)
        rng = random.Random(5)
        noise = []
        for n in range(30):
            noise.append(row("np-%d" % n, 0, "correct", rng.random()))
            noise.append(row("np-%d" % n, 1, "wrong", rng.random()))
        low, high, _ = cal.cluster_bootstrap_auc(noise, draws=300, seed=3)
        self.assertLess(low, 0.70)

    def test_cluster_bootstrap_resamples_whole_pairs(self):
        # Two clusters only: every draw contains complete pairs, so a draw with one cluster has both classes
        # and an AUC of 1.0; resampling rows independently could produce a one-class sample.
        rows = [row("p1", 0, "correct", 0.9), row("p1", 1, "wrong", 0.1), row("p2", 0, "correct", 0.8), row("p2", 1, "wrong", 0.2)]
        low, high, valid = cal.cluster_bootstrap_auc(rows, draws=200, seed=1)
        self.assertEqual(valid, 200)
        self.assertEqual((low, high), (1.0, 1.0))

    def test_word_baseline_takes_the_better_orientation(self):
        longer_wins = [row("a", 0, "correct", 0.5, words=(300, 100)), row("b", 0, "wrong", 0.5, words=(100, 300))]
        self.assertEqual(cal.word_baseline_auc(longer_wins), 1.0)
        shorter_wins = [row("a", 0, "correct", 0.5, words=(100, 300)), row("b", 0, "wrong", 0.5, words=(300, 100))]
        self.assertEqual(cal.word_baseline_auc(shorter_wins), 1.0)

    def test_platt_and_brier(self):
        scores = [0.9, 0.8, 0.85, 0.2, 0.3, 0.25]
        labels = [True, True, True, False, False, False]
        a, b = cal.platt_fit(scores, labels)
        self.assertGreater(a, 0)
        self.assertIsNone(cal.platt_fit([0.5, 0.6], [True, True]))
        self.assertAlmostEqual(cal.brier([1.0, 0.0], [True, False]), 0.0)
        self.assertAlmostEqual(cal.brier([0.5, 0.5], [True, False]), 0.25)

    def test_split_by_pair_keeps_pairs_together_and_is_deterministic(self):
        rows = [r for r in good_ledger(30) if r["type"] == "verdict"]
        fit, hold = cal.split_by_pair(rows)
        self.assertEqual(len(fit) + len(hold), len(rows))
        self.assertFalse({r["pair_id"] for r in fit} & {r["pair_id"] for r in hold})
        self.assertEqual(cal.split_by_pair(rows)[0], fit)

    def test_action_threshold_flags_at_most_a_quarter(self):
        rows = [{"p": i / 100.0} for i in range(1, 41)]
        t = cal.action_threshold(rows)
        self.assertLessEqual(sum(1 for r in rows if r["p"] < t), 10)
        self.assertIsNone(cal.action_threshold([]))


class DecisionTest(unittest.TestCase):
    def report(self, events, draws=300):
        return cal.build_report(events, draws=draws, seed=7)

    def test_promote_on_a_clean_ledger(self):
        report = self.report(good_ledger(30))
        self.assertEqual(report["decision"], "PROMOTE", report["checks"])
        self.assertEqual(report["pairs"], 30)
        self.assertEqual(report["wrong_with_p"], 30)
        self.assertEqual(report["authors"], ["seatx", "seaty"])
        self.assertEqual(report["proposed_entry"]["resolved_model"], MODEL)
        self.assertIsNotNone(report["action_threshold"])
        text = cal.format_report(report)
        self.assertIn("DECISION: PROMOTE", text)

    def test_one_author_blocks_promotion(self):
        report = self.report(good_ledger(30, authors=("seatX",)))
        self.assertEqual(report["decision"], "HOLD")
        self.assertFalse(report["checks"]["authors>=2"])

    def test_pairs_are_counted_from_the_same_model_group_as_the_auc(self):
        events = good_ledger(30)
        # 10 more pairs under another model: they feed neither the AUC nor the pair count
        other = [row("xp-%02d" % n, i, "correct" if i == 0 else "wrong", 0.8 if i == 0 else 0.2, model="jev-2.0.0")
                 for n in range(10) for i in (0, 1)]
        report = self.report(events + other + commitments(["xp-%02d" % n for n in range(10)]))
        self.assertEqual(report["pairs"], 30)
        self.assertEqual(report["verdicts_with_p"], 60)
        self.assertEqual(report["excluded_other_model_or_hash"], 20)

    def test_pairs_under_a_minority_model_do_not_reach_the_minimum(self):
        events = good_ledger(20) + [row("xp-%02d" % n, i, "correct" if i == 0 else "wrong", 0.8 if i == 0 else 0.2, model="jev-2.0.0")
                                    for n in range(15) for i in (0, 1)]
        report = self.report(events + commitments(["xp-%02d" % n for n in range(15)]))
        self.assertEqual(report["pairs"], 20)  # 35 labelled pairs overall, but only 20 in the model group
        self.assertEqual(report["decision"], "HOLD")

    def test_authors_must_be_distinct_names_and_unknown_does_not_count(self):
        for authors in (("seatX", "SEATX"), ("seatX", "unknown"), ("seatX", " seatx ")):
            report = self.report(good_ledger(30, authors=authors))
            self.assertEqual(report["decision"], "HOLD", authors)
            self.assertFalse(report["checks"]["authors>=2"], authors)
        events = good_ledger(30)
        report = self.report([e for e in events if e["type"] != "commitment"])  # no commitments: no authors at all
        self.assertEqual(report["authors"], [])
        self.assertEqual(report["decision"], "HOLD")

    def test_too_few_pairs_hold(self):
        report = self.report(good_ledger(20))
        self.assertEqual(report["decision"], "HOLD")
        self.assertFalse(report["checks"]["pairs>=30"])

    def test_few_wrong_verdicts_hold_below_the_retire_point(self):
        # 40 pairs: 90 correct verdicts and 15 wrong ones, so 105 carry a P (retire needs under 60) and fewer than 20 are wrong
        events = good_ledger(40)
        rng = random.Random(4)
        events = [e for e in events if not (e["type"] == "verdict" and e["label"] == "wrong" and int(e["pair_id"][3:]) >= 15)]
        events += [row("sp-%02d" % n, 2, "correct", 0.7 + 0.2 * rng.random()) for n in range(40)]
        events += [row("sp-%02d" % n, 3, "correct", 0.7 + 0.2 * rng.random()) for n in range(10)]
        report = self.report(events)
        self.assertEqual(report["wrong_with_p"], 15)
        self.assertEqual(report["verdicts_with_p"], 105)
        self.assertEqual(report["decision"], "HOLD")
        self.assertIn("batches of 10", report["note"])

    def test_weak_judge_does_not_pass(self):
        rng = random.Random(11)
        events, ids = [], []
        for n in range(30):
            pair = "wp-%02d" % n
            ids.append(pair)
            events += [row(pair, 0, "correct", rng.random()), row(pair, 1, "wrong", rng.random())]
        report = self.report(events + commitments(ids))
        self.assertEqual(report["decision"], "HOLD")
        self.assertFalse(report["checks"]["bootstrap p2.5>=0.70"])

    def test_unstable_p_blocks_promotion(self):
        events = good_ledger(30)
        for e in events:
            if e["type"] == "verdict":
                e["delta"] = 0.3
        report = self.report(events)
        self.assertEqual(report["decision"], "HOLD")
        self.assertFalse(report["checks"]["median delta<=0.10"])

    def test_length_cue_is_not_a_win(self):
        events = good_ledger(30)
        for e in events:
            if e["type"] == "verdict":
                e["words_a"], e["words_b"] = (300, 100) if e["label"] == "correct" else (100, 300)
        report = self.report(events)
        self.assertEqual(report["word_baseline_auc"], 1.0)
        self.assertFalse(report["checks"]["auc>word baseline"])
        self.assertEqual(report["decision"], "HOLD")

    def test_more_than_ten_percent_na_means_rerun(self):
        events = good_ledger(30)
        extra = [row("sp-%02d" % n, 5, "wrong", 0.5, na=True) for n in range(10)]
        report = self.report(events + extra)
        self.assertGreater(report["na_fraction"], 0.10)
        self.assertEqual(report["decision"], "HOLD")
        self.assertIn("rerun", report["note"])

    def test_ties_have_no_p_and_are_reported(self):
        events = good_ledger(30)
        events += [row("sp-%02d" % n, 7, "wrong", None, tie=True) for n in range(6)]
        report = self.report(events)
        self.assertEqual(report["ties"], 6)
        self.assertAlmostEqual(report["tie_rate"], 6 / 66.0)
        self.assertEqual(report["verdicts_with_p"], 60)
        self.assertEqual(report["decision"], "PROMOTE")

    def test_retire_after_30_pairs_with_fewer_than_60_verdicts_with_a_p(self):
        # 30 pairs, 58 verdicts carry a P and 2 are ties: ties do not count toward the 60
        events = good_ledger(30, authors=("seatX",))  # one author: promotion is not on the table either
        dropped = 0
        for e in events:
            if e["type"] == "verdict" and e["label"] == "correct" and dropped < 2:
                e["tie"], e["p"], e["delta"], e["na"], e["na_reason"], e["winner_code"] = True, None, None, True, "tie", None
                e["label"] = "wrong"
                dropped += 1
        report = self.report(events)
        self.assertEqual(report["verdicts_with_p"], 58)
        self.assertEqual(report["decision"], "RETIRE")
        self.assertIn("ties carry no P", report["note"])

    def test_retire_after_60_pairs_with_fewer_than_20_wrong(self):
        events, ids = [], []
        rng = random.Random(2)
        for n in range(60):
            pair = "ep-%02d" % n
            ids.append(pair)
            events.append(row(pair, 0, "correct", 0.6 + 0.3 * rng.random()))
            events.append(row(pair, 1, "correct" if n >= 12 else "wrong", 0.6 + 0.3 * rng.random()))
        report = self.report(events + commitments(ids))
        self.assertEqual(report["wrong_with_p"], 12)
        self.assertEqual(report["decision"], "RETIRE")
        self.assertIn("fewer than 20 wrong", report["note"])

    def test_sixty_rows_that_do_not_pass_are_undecided(self):
        events = good_ledger(30)
        for e in events:
            if e["type"] == "verdict":
                e["delta"] = 0.3
        report = self.report(events)
        self.assertEqual(report["verdicts_with_p"], 60)
        self.assertEqual(report["decision"], "HOLD")

    def test_undecided_note_between_60_and_100_rows(self):
        events = good_ledger(30, authors=("seatX",))
        events += [row("sp-%02d" % n, 9, "correct", 0.8) for n in range(5)]
        report = self.report(events)
        self.assertEqual(report["verdicts_with_p"], 65)
        self.assertIn("extend to 100 rows", report["note"])

    def test_rows_from_another_model_or_hash_are_excluded(self):
        events = good_ledger(30)
        events += [row("sp-%02d" % n, 3, "wrong", 0.9, model="jev-2.0.0") for n in range(5)]
        report = self.report(events)
        self.assertEqual(report["model"], MODEL)
        self.assertEqual(report["excluded_other_model_or_hash"], 5)

    def test_natural_rows_are_supplementary_and_reported_as_a_fraction(self):
        events = good_ledger(30)
        events += [row("n1", 0, "wrong", 0.5, kind="natural"), row("n2", 0, None, 0.5, kind="natural", run="run-n2")]
        events[-2]["label_source"] = "L1"
        events[-1]["label"] = None
        report = self.report(events)
        self.assertEqual(report["natural_labelled_fraction"], 0.5)
        self.assertEqual(report["verdicts_with_p"], 60)  # natural rows never feed the promotion statistics

    def test_demotion_check_uses_the_last_30_labelled_verdicts(self):
        events = []
        for i in range(30):
            events.append(row("d%d" % i, 0, "correct" if i % 2 == 0 else "wrong", 0.5 + (0.01 if i % 2 else -0.01) * 5,
                              ts="2026-10-13T10:%02d:00Z" % i))
        events += commitments(["d%d" % i for i in range(30)])
        report = self.report(events)
        self.assertLess(report["recent_auc"], 0.65)
        self.assertTrue(report["demote"])
        self.assertIsNone(self.report(good_ledger(10))["recent_auc"])  # only 20 rows: not enough

    def test_empty_ledger(self):
        report = self.report([])
        self.assertEqual(report["decision"], "HOLD")
        self.assertIn("DECISION: HOLD", cal.format_report(report))

    def test_main_reads_the_store(self):
        with tempfile.TemporaryDirectory() as tmp:
            jev_steps.append_rows(tmp, good_ledger(30))
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = cal.main(["--store", tmp, "--draws", "200"])
            self.assertEqual(code, 0)
            self.assertIn("DECISION: PROMOTE", out.getvalue())
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                cal.main(["--store", tmp, "--draws", "200", "--json"])
            self.assertEqual(json.loads(out.getvalue())["decision"], "PROMOTE")


if __name__ == "__main__":
    unittest.main()
