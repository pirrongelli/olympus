"""Offline tests for jev_steps.py. The runner is a stub script, except one end-to-end test that drives the
real tools/olympus_call.py against a loopback mock of the System One API.

Run from the repository root:  python3 -m unittest discover -s tools -p 'test_*.py'
"""

import contextlib
import hashlib
import io
import json
import os
import re
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import jev_steps  # noqa: E402

FAKE_KEY = "typesafe-test-key-0123456789"

FAKE_RUNNER = r'''
import json, os, sys
argv = sys.argv[1:]
def arg(name):
    return argv[argv.index(name) + 1]
brief = json.load(open(arg("--brief")))
out = arg("--out")
log = os.environ.get("JEV_FAKE_LOG")
if log:
    with open(log, "a") as handle:
        handle.write(json.dumps(brief["state"]) + "\n")
item = 0.6
script = os.environ.get("JEV_FAKE_SCRIPT")
if script and os.path.exists(script):
    seq = json.load(open(script))
    if seq:
        item = seq.pop(0)
        json.dump(seq, open(script, "w"))
if isinstance(item, dict):
    if item.get("model_mismatch"):
        sys.stderr.write("olympus_call: error: model_mismatch: requested jev-1.13.0")
        sys.exit(4)
    sys.exit(item.get("exit", 4))
qid = list(brief["questions"])[0]
json.dump({"model": "jev-1.13.0", "answers": {qid: {"type": "noul", "noul": item}},
           "usage": {"input_tokens": 100, "output_tokens": 5}}, open(out, "w"))
json.dump({"resolved_model": "jev-1.13.0", "usage": {"input_tokens": 100, "output_tokens": 5},
           "latency_seconds": 0.1}, open(out + ".meta.json", "w"))
'''

CODES = ("AAA", "BBB", "CCC", "DDD")


def load_json(path):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def verdict_block(a, b, winner, sa=5, sb=3):
    return (
        "PAIR: P-%s (A) vs P-%s (B)\n"
        "SCORES A: e=%d f=%d c=%d $=%d s=%d p=%d | weighted=%d\n"
        "SCORES B: e=%d f=%d c=%d $=%d s=%d p=%d | weighted=%d\n"
        "STEELMAN-AGAINST-LEAN: the other one is shorter\n"
        "WINNER: %s\n"
        "STEAL: nothing\n\n" % (a, b, sa, sa, sa, sa, sa, sa, sa * 20, sb, sb, sb, sb, sb, sb, sb * 20, winner)
    )


def run_main(argv, stdin=None):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        if stdin is not None:
            with mock.patch("sys.stdin", io.StringIO(stdin)):
                code = jev_steps.main(argv)
        else:
            code = jev_steps.main(argv)
    return code, out.getvalue(), err.getvalue()


class JevCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.saved_env = dict(os.environ)
        self.addCleanup(self._restore_env)
        for name in list(os.environ):
            if name.endswith("_API_KEY"):
                del os.environ[name]
        os.environ["TYPESAFE_API_KEY"] = FAKE_KEY
        os.environ["JEV_FAKE_LOG"] = self.p("calls.log")
        os.environ["JEV_FAKE_SCRIPT"] = self.p("script.json")
        self.E = self.mkdir("E")
        self.edir = self.mkdir("E/jev")
        self.blind = self.mkdir("blind")
        self.store = self.mkdir("store")
        for code in CODES:
            self.write("blind/P-%s.md" % code, "Candidate %s says: use the documented command, check the figure. " % code * 5)
        self.write("runner.py", FAKE_RUNNER)
        self.write("rubric.md", "Correctness [100]: score 1 to 5 per criterion.\n")
        self.write("env.env", "")
        self.write("decision.md", "# DECISION\njev: shadow\njev-content: ok\n")
        self.write("config.json", json.dumps({"targets": {"jev": {"adapter": "typesafe", "model": "jev-1.13.0"}}}))

    def _restore_env(self):
        os.environ.clear()
        os.environ.update(self.saved_env)

    def p(self, *parts):
        return os.path.join(self.tmp.name, *parts)

    def mkdir(self, name):
        os.makedirs(self.p(name), exist_ok=True)
        return self.p(name)

    def write(self, name, text):
        path = self.p(name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        return path

    def read(self, path):
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read()

    def write_ruling(self, text="ranking by code: AAA > BBB\nbase: AAA\ntimestamp: 2026-10-13T20:00:00Z\n"):
        self.write("E/jev/ruling.md", text)

    def arena(self, verdicts):
        """verdicts: list of (judge, a, b, winner)."""
        by_judge = {}
        for judge, a, b, winner in verdicts:
            by_judge.setdefault(judge, []).append(verdict_block(a, b, winner))
        files = []
        for judge, blocks in by_judge.items():
            files.append(self.write("judging-%s.md" % judge, "".join(blocks)))
        return files

    def j1_argv(self, files, extra=()):
        return ["j1", "--e", self.E, "--blind", self.blind, "--verdicts"] + files + [
            "--rubric", self.p("rubric.md"), "--decision", self.p("decision.md"), "--config", self.p("config.json"),
            "--env", self.p("env.env"), "--store", self.store, "--runner", self.p("runner.py"),
            "--canary", self.p("no-canary.json"), "--calibration", self.p("no-calibration.json"), "--run-id", "run-test",
        ] + list(extra)

    def run_j1(self, files, extra=()):
        return run_main(self.j1_argv(files, extra))

    def script(self, items):
        self.write("script.json", json.dumps(items))

    def calls(self):
        if not os.path.exists(self.p("calls.log")):
            return []
        return [json.loads(line) for line in self.read(self.p("calls.log")).splitlines()]

    def events(self):
        return jev_steps.read_events(self.store)

    def verdict_rows(self):
        return [e for e in self.events() if e["type"] == "verdict"]


class SwapTest(unittest.TestCase):
    def test_swap_relabels_structured_lines_only(self):
        block = verdict_block("AAA", "BBB", "A", 5, 3).strip()
        swapped = jev_steps.swap_verdict(block)
        self.assertIn("PAIR: P-BBB (A) vs P-AAA (B)", swapped)
        self.assertIn("WINNER: B", swapped)
        self.assertIn("SCORES B: e=5", swapped)  # the first judged candidate's scores now sit under B
        self.assertIn("SCORES A: e=3", swapped)
        self.assertIn("STEELMAN-AGAINST-LEAN: the other one is shorter", swapped)

    def test_swap_twice_restores_the_verdict(self):
        block = verdict_block("AAA", "BBB", "B", 2, 4).strip()
        twice = jev_steps.swap_verdict(jev_steps.swap_verdict(block))
        self.assertEqual(sorted(twice.splitlines()), sorted(block.splitlines()))

    def test_swap_handles_bold_markup_and_tie(self):
        block = "**PAIR:** P-AAA (A) vs P-BBB (B)\n**WINNER:** TIE"
        swapped = jev_steps.swap_verdict(block)
        self.assertIn("P-BBB (A) vs P-AAA (B)", swapped)
        self.assertIn("TIE", swapped)

    def test_parse_verdicts(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "judging-C.md")
            with open(path, "w") as handle:
                handle.write(verdict_block("AAA", "BBB", "A") + verdict_block("CCC", "DDD", "TIE")
                             + verdict_block("AAA", "CCC", "ABSTAIN") + "**PAIR:** P-BBB (A) vs P-DDD (B)\n**WINNER:** B\n")
            verdicts = jev_steps.parse_verdicts([path])
        self.assertEqual([(v["code_a"], v["code_b"], v["winner"]) for v in verdicts],
                         [("AAA", "BBB", "A"), ("CCC", "DDD", "TIE"), ("BBB", "DDD", "B")])
        self.assertEqual(verdicts[0]["judge"], "C")


class ScanTest(unittest.TestCase):
    def hits(self, text, secrets=None):
        return jev_steps.presend_scan([text], secrets, jev_steps.load_scan_patterns())

    def test_hits(self):
        cases = {
            "sk-key": "token sk-abcdefghijklmnopqrstuvwxyz1234 here",
            "jwt": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0",
            "bearer-token": "Authorization: Bearer abcdefghijklmnopqrstuvwxyz",
            "private-key": "-----BEGIN RSA PRIVATE KEY-----",
            "supabase-host": "https://abcdefghijklmnopqrst.supabase.co/rest",
            "aws-key-id": "AKIAABCDEFGHIJKLMNOP",
            "github-token": "ghp_" + "a" * 36,
            "github-pat": "github_pat_" + "b" * 40,
            "slack-token": "xoxb-1234567890-abcdef",
            "db-url-password": "postgres://user:hunter2@db.example.com/app",
            "domain-word": "the multi-tenant RLS policy",
        }
        for name, text in cases.items():
            self.assertIn(name, self.hits(text), name)

    def test_domain_words(self):
        for text in ("Tenants are isolated", "a payout run", "the KYC flow", "SWIFT code", "IBAN list"):
            self.assertIn("domain-word", self.hits(text), text)

    def test_prose_passes(self):
        for text in ("a risk-based approach", "the Bearer token is rotated", "postgresql://host/db has no credentials",
                     "task-force of reviewers", "a skeleton key", "sk-short"):
            self.assertEqual(self.hits(text), [], text)

    def test_literal_secret_logs_the_variable_name_only(self):
        secret = "literal-secret-value-0123456789"
        hits = self.hits("the value is " + secret, {"MOONSHOT_API_KEY": secret})
        self.assertEqual(hits, ["literal:MOONSHOT_API_KEY"])
        self.assertNotIn(secret, " ".join(hits))
        self.assertEqual(self.hits("the value is " + secret, {"MOONSHOT_API_KEY": "short"}), [])

    def test_collect_secrets_reads_environment_and_env_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "k.env")
            with open(path, "w") as handle:
                handle.write("FILE_ONLY_API_KEY=file-secret-value-0123456789\nSHORT_API_KEY=abc\nNOT_A_KEY=long-value-long-value-long\n")
            with mock.patch.dict(os.environ, {"ENV_ONLY_API_KEY": "env-secret-value-0123456789", "EMPTY_API_KEY": ""}):
                found = jev_steps.collect_secrets(path)
                missing = jev_steps.collect_secrets(os.path.join(tmp, "absent.env"))
        self.assertEqual(found["FILE_ONLY_API_KEY"], "file-secret-value-0123456789")
        self.assertEqual(found["ENV_ONLY_API_KEY"], "env-secret-value-0123456789")
        self.assertNotIn("SHORT_API_KEY", found)
        self.assertNotIn("NOT_A_KEY", found)
        self.assertNotIn("EMPTY_API_KEY", found)
        self.assertIn("ENV_ONLY_API_KEY", missing)

    def test_jev_md_block_matches_the_built_in_patterns(self):
        self.assertEqual(jev_steps.load_scan_patterns(jev_steps.JEV_DOC_PATH), list(jev_steps.DEFAULT_SCAN_PATTERNS))

    def test_scan_doc_is_owner_editable(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "jev.md")
            with open(path, "w") as handle:
                handle.write("```scan-patterns\nonly-this :: forbidden-word\n```\n")
            self.assertEqual(jev_steps.load_scan_patterns(path), [("only-this", "forbidden-word")])
            self.assertEqual(jev_steps.presend_scan(["a forbidden-word here"], {}, jev_steps.load_scan_patterns(path)), ["only-this"])


class QuestionsTest(unittest.TestCase):
    def test_shipped_questions_pass_the_hash_check(self):
        wording = jev_steps.load_questions()
        self.assertEqual(wording["name"], "j1-v2")
        self.assertEqual(wording["question_id"], "p")
        self.assertEqual(list(wording["questions"]), ["p"])

    def test_edited_wording_is_refused(self):
        data = load_json(jev_steps.QUESTIONS_PATH)
        data["wordings"]["j1-v2"]["questions"]["p"]["instructions"] += " (edited)"
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "q.json")
            with open(path, "w") as handle:
                json.dump(data, handle)
            with self.assertRaises(jev_steps.Refused):
                jev_steps.load_questions(path)

    def test_j1_refuses_a_changed_question_hash(self):
        data = load_json(jev_steps.QUESTIONS_PATH)
        data["wordings"]["j1-v1"]["sha256"] = "0" * 64
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "q.json")
            with open(path, "w") as handle:
                json.dump(data, handle)
            code, _, err = run_main(["j1", "--e", tmp, "--blind", tmp, "--verdicts", path, "--rubric", path,
                                     "--decision", path, "--questions", path, "--store", tmp])
        self.assertEqual(code, 2)
        self.assertIn("sha256", err)

    def test_canary_file_is_consistent(self):
        canary = load_json(jev_steps.CANARY_PATH)
        self.assertEqual(len(canary["states"]), 3)
        for item in canary["states"]:
            self.assertEqual(item["state_sha256"], hashlib.sha256(jev_steps.canonical(item["state"])).hexdigest())
            self.assertEqual(sorted(item["state"]), ["candidate_A", "candidate_B", "rubric", "verdict"])


class J1Test(JevCase):
    def test_both_orders_are_asked_with_the_verdict_relabelled(self):
        files = self.arena([("A", "AAA", "BBB", "A")])
        self.write_ruling()
        self.script([0.8, 0.6])
        code, out, err = self.run_j1(files)
        self.assertEqual(code, 0, err)
        first, second = self.calls()
        self.assertEqual(first["candidate_A"], self.read(self.p("blind/P-AAA.md")))
        self.assertEqual(first["candidate_B"], self.read(self.p("blind/P-BBB.md")))
        self.assertIn("PAIR: P-AAA (A) vs P-BBB (B)", first["verdict"])
        self.assertIn("WINNER: A", first["verdict"])
        self.assertEqual(second["candidate_A"], self.read(self.p("blind/P-BBB.md")))
        self.assertEqual(second["candidate_B"], self.read(self.p("blind/P-AAA.md")))
        self.assertIn("PAIR: P-BBB (A) vs P-AAA (B)", second["verdict"])
        self.assertIn("WINNER: B", second["verdict"])
        self.assertEqual(first["rubric"], second["rubric"])

    def test_row_aggregates_both_orders_and_stays_text_free(self):
        files = self.arena([("A", "AAA", "BBB", "A")])
        self.write_ruling()
        self.script([0.8, 0.6])
        self.assertEqual(self.run_j1(files)[0], 0)
        (row,) = self.verdict_rows()
        self.assertAlmostEqual(row["p"], 0.7)
        self.assertAlmostEqual(row["delta"], 0.2)
        self.assertEqual((row["p_ab"], row["p_ba"]), (0.8, 0.6))
        self.assertEqual(row["winner_code"], "AAA")
        self.assertFalse(row["na"])
        self.assertEqual(row["resolved_model"], "jev-1.13.0")
        self.assertEqual((row["tokens_in"], row["tokens_out"]), (200, 10))
        self.assertEqual(row["kind"], "natural")
        self.assertEqual(row["ruling_sha256"], hashlib.sha256(self.read(self.p("E/jev/ruling.md")).encode()).hexdigest())
        self.assertEqual(row["ruling_ts"], "2026-10-13T20:00:00Z")
        self.assertEqual(row["wording"], "j1-v2")
        self.assertEqual(len(row["state_sha256_ab"]), 64)
        ledger = self.read(os.path.join(self.store, "ledger.jsonl"))
        for fragment in ("Candidate AAA says", "STEELMAN", "Correctness [100]", FAKE_KEY):
            self.assertNotIn(fragment, ledger)

    def test_stdout_never_shows_p(self):
        files = self.arena([("A", "AAA", "BBB", "A")])
        self.write_ruling()
        self.script([0.8123, 0.6123])
        code, out, err = self.run_j1(files)
        self.assertNotIn("0.81", out + err)
        self.assertNotIn("0.71", out + err)
        self.assertIn("jev: shadow", out)

    def test_refuses_without_ruling(self):
        files = self.arena([("A", "AAA", "BBB", "A")])
        code, _, err = self.run_j1(files)
        self.assertEqual(code, 2)
        self.assertIn("ruling.md", err)
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.verdict_rows(), [])

    def test_refuses_ruling_without_timestamp_line(self):
        files = self.arena([("A", "AAA", "BBB", "A")])
        self.write_ruling("ranking: AAA first\n")
        self.assertEqual(self.run_j1(files)[0], 2)
        self.write_ruling("ranking: AAA first\ntimestamp: not-a-time\n")
        self.assertEqual(self.run_j1(files)[0], 2)
        self.assertEqual(self.calls(), [])

    def skip_case(self, decision_text, expected, extra=()):
        self.write("decision.md", decision_text)
        files = self.arena([("A", "AAA", "BBB", "A")])
        self.write_ruling()
        code, out, _ = self.run_j1(files, extra)
        self.assertEqual(code, 0)
        self.assertIn("jev: skipped (%s)" % expected, out)
        self.assertEqual(self.calls(), [])
        skips = [e for e in self.events() if e["type"] == "skip"]
        self.assertEqual(skips[-1]["reason"], expected)

    def test_skip_when_jev_off_or_absent(self):
        self.skip_case("jev: off\njev-content: ok\n", "jev off")
        self.skip_case("# no jev lines\n", "jev off")

    def test_skip_when_content_not_ok(self):
        self.skip_case("jev: shadow\njev-content: blocked\n", "jev-content not ok")
        self.skip_case("jev: advisory\n", "jev-content not ok")  # default is blocked

    def test_skip_for_tier_s(self):
        self.skip_case("jev: shadow\njev-content: ok\n", "tier S", ["--tier", "S"])

    def test_skip_without_key(self):
        del os.environ["TYPESAFE_API_KEY"]
        self.skip_case("jev: shadow\njev-content: ok\n", "no key")

    def test_skip_when_breaker_open(self):
        self.write("E/jev/.open", "open\n")
        self.skip_case("jev: shadow\njev-content: ok\n", "breaker open")

    def test_skip_without_jev_target(self):
        self.write("config.json", json.dumps({"targets": {"other": {"adapter": "typesafe", "model": "jev-1.13.0"}}}))
        self.skip_case("jev: shadow\njev-content: ok\n", "no jev target")

    def test_scan_hit_skips_the_whole_run_and_logs_names_only(self):
        self.write("blind/P-BBB.md", "The RLS policy and the key sk-abcdefghijklmnopqrstuvwxyz12 apply.")
        files = self.arena([("A", "AAA", "BBB", "A")])
        self.write_ruling()
        code, out, _ = self.run_j1(files)
        self.assertEqual(code, 0)
        self.assertIn("jev: skipped (scan)", out)
        self.assertEqual(self.calls(), [])
        (skip,) = [e for e in self.events() if e["type"] == "skip"]
        self.assertEqual(skip["reason"], "scan")
        self.assertEqual(sorted(skip["patterns"]), ["domain-word", "sk-key"])
        self.assertNotIn("sk-abcdefghijklmn", json.dumps(skip))

    def scan_skip(self, candidate_text, expected):
        self.write("blind/P-BBB.md", candidate_text)
        files = self.arena([("A", "AAA", "BBB", "A")])
        self.write_ruling()
        code, out, _ = self.run_j1(files)
        self.assertEqual(code, 0)
        self.assertIn("jev: skipped (scan)", out, candidate_text)
        self.assertEqual(self.calls(), [])
        skips = [e for e in self.events() if e["type"] == "skip"]
        self.assertEqual(skips[-1]["patterns"], expected)

    def test_scan_sees_through_json_escapes_at_line_starts(self):
        # json.dumps would turn these newlines and tabs into backslash sequences and hide the token from \b
        self.scan_skip("Intro line.\nAKIAABCDEFGHIJKLMNOP\nmore text", ["aws-key-id"])
        self.scan_skip("Intro line.\n\tAKIAABCDEFGHIJKLMNOP", ["aws-key-id"])
        self.scan_skip("First paragraph.\n\nTenant isolation is described next.", ["domain-word"])
        self.scan_skip("Intro.\r\nTenants differ.", ["domain-word"])

    def test_scan_covers_the_rubric_and_the_verdict_text_too(self):
        self.write("rubric.md", "Rubric.\nKYC checks [10]\n")
        files = self.arena([("A", "AAA", "BBB", "A")])
        self.write_ruling()
        code, out, _ = self.run_j1(files)
        self.assertIn("jev: skipped (scan)", out)
        self.write("rubric.md", "Plain rubric.\n")
        self.write("judging-A.md", verdict_block("AAA", "BBB", "A").replace("STEAL: nothing", "STEAL: the payout\nline"))
        self.write_ruling()
        code, out, _ = self.run_j1([self.p("judging-A.md")], ["--run-id", "run-test-2"])
        self.assertIn("jev: skipped (scan)", out)

    def test_scan_covers_the_canary_states(self):
        canary = load_json(jev_steps.CANARY_PATH)
        canary["states"][1]["state"]["candidate_A"] += "\nAKIAABCDEFGHIJKLMNOP"
        self.write("canary.json", json.dumps(canary))
        files = self.arena([("A", "AAA", "BBB", "A")])
        self.write_ruling()
        code, out, _ = self.run_j1(files, ["--canary", self.p("canary.json")])
        self.assertIn("jev: skipped (scan)", out)
        self.assertEqual(self.calls(), [])

    def test_iter_strings_recurses_into_nested_values(self):
        nested = {"a": ["x\ny", {"k\tz": ("deep\nvalue", 3)}], "b": None, "c": 4}
        self.assertEqual(sorted(jev_steps.iter_strings(nested)), sorted(["a", "x\ny", "k\tz", "deep\nvalue", "b", "c"]))

    def test_scan_skip_counts(self):
        self.write("blind/P-BBB.md", "mentions the IBAN field")
        files = self.arena([("A", "AAA", "BBB", "A")])
        self.write_ruling()
        self.run_j1(files)
        self.run_j1(files, ["--run-id", "run-test-2"])
        code, out, _ = run_main(["skips", "--store", self.store])
        self.assertEqual(code, 0)
        self.assertIn("scan: 2", out)
        self.assertIn("scan pattern domain-word: 2", out)

    def test_literal_api_key_value_in_text_skips(self):
        other = "another-provider-key-0123456789"
        os.environ["MOONSHOT_API_KEY"] = other
        self.write("blind/P-AAA.md", "oops pasted " + other)
        files = self.arena([("A", "AAA", "BBB", "A")])
        self.write_ruling()
        code, out, _ = self.run_j1(files)
        self.assertIn("jev: skipped (scan)", out)
        (skip,) = [e for e in self.events() if e["type"] == "skip"]
        self.assertEqual(skip["patterns"], ["literal:MOONSHOT_API_KEY"])
        self.assertNotIn(other, json.dumps(skip))

    def test_breaker_opens_after_two_failures_and_skips_the_rest(self):
        files = self.arena([("A", "AAA", "BBB", "A"), ("B", "CCC", "DDD", "B"), ("C", "AAA", "CCC", "A")])
        self.write_ruling()
        self.script([{"exit": 4}, {"exit": 4}])
        code, out, _ = self.run_j1(files)
        self.assertEqual(code, 0)
        self.assertTrue(os.path.exists(self.p("E/jev/.open")))
        self.assertEqual(len(self.calls()), 2)
        rows = self.verdict_rows()
        self.assertTrue(all(r["na"] for r in rows))
        self.assertEqual([r["na_reason"] for r in rows], ["exit4", "exit4", "breaker"])
        self.assertIn("breaker=open", out)
        # the next run in the same $E is skipped
        code, out, _ = self.run_j1(files, ["--run-id", "run-test-2"])
        self.assertIn("jev: skipped (breaker open)", out)

    def test_a_success_resets_the_failure_count(self):
        files = self.arena([("A", "AAA", "BBB", "A"), ("B", "CCC", "DDD", "B")])
        self.write_ruling()
        self.script([{"exit": 4}, 0.5, 0.5, {"exit": 4}, 0.5])  # fail, ok, ok, fail, ok: never two in a row
        self.run_j1(files)
        self.assertFalse(os.path.exists(self.p("E/jev/.open")))

    def test_call_cap(self):
        files = self.arena([("A", "AAA", "BBB", "A"), ("B", "CCC", "DDD", "B"), ("C", "AAA", "CCC", "A")])
        self.write_ruling()
        code, out, _ = self.run_j1(files, ["--max-calls", "3"])
        self.assertEqual(len(self.calls()), 3)
        rows = self.verdict_rows()
        self.assertFalse(rows[0]["na"])
        self.assertEqual([r["na_reason"] for r in rows[1:]], ["call_cap", "call_cap"])

    def test_either_order_missing_makes_the_verdict_na(self):
        files = self.arena([("A", "AAA", "BBB", "A"), ("B", "CCC", "DDD", "B")])
        self.write_ruling()
        self.script([0.9, {"exit": 4}, 0.5, 0.5])
        code, out, _ = self.run_j1(files)
        rows = self.verdict_rows()
        self.assertTrue(rows[0]["na"])
        self.assertIsNone(rows[0]["p"])
        self.assertEqual(rows[0]["p_ab"], None)
        self.assertFalse(rows[1]["na"])
        self.assertEqual(len(self.calls()), 4)
        self.assertIn("more than 10% of verdicts are na", out)

    def test_model_mismatch_is_recorded(self):
        files = self.arena([("A", "AAA", "BBB", "A")])
        self.write_ruling()
        self.script([{"model_mismatch": True}])
        self.run_j1(files)
        (row,) = self.verdict_rows()
        self.assertEqual(row["na_reason"], "model_mismatch")

    def test_natural_ties_are_never_sent_and_leave_no_row(self):
        files = self.arena([("A", "AAA", "BBB", "TIE"), ("B", "CCC", "DDD", "A")])
        self.write_ruling()
        code, out, _ = self.run_j1(files)
        self.assertEqual(len(self.calls()), 2)
        rows = self.verdict_rows()
        self.assertEqual([r["verdict_id"] for r in rows], ["B:CCC-DDD"])
        self.assertIn("ties=1", out)

    def test_abstain_blocks_are_ignored(self):
        files = self.arena([("A", "AAA", "BBB", "ABSTAIN"), ("B", "CCC", "DDD", "A")])
        self.write_ruling()
        self.run_j1(files)
        self.assertEqual(len(self.verdict_rows()), 1)

    def test_instability_flags_a_pair_and_is_logged_not_acted_in_shadow(self):
        files = self.arena([("A", "AAA", "BBB", "A"), ("B", "BBB", "AAA", "B")])
        self.write_ruling()
        self.script([0.9, 0.5, 0.7, 0.7])  # first verdict |delta| 0.4, second 0.0
        code, out, _ = self.run_j1(files)
        flags = json.loads(self.read(self.p("E/jev/flags.json")))
        self.assertEqual(flags["level"], "shadow")
        self.assertFalse(flags["acted"])
        self.assertEqual([f["pair"] for f in flags["flagged"]], ["AAA|BBB"])
        self.assertNotIn("flagged pairs", out)

    def test_flag_cap_is_three_per_arena(self):
        pairs = [("AAA", "BBB"), ("AAA", "CCC"), ("AAA", "DDD"), ("BBB", "CCC"), ("BBB", "DDD")]
        files = self.arena([("A", a, b, "A") for a, b in pairs])
        self.write_ruling()
        self.script([0.9, 0.1] * 5)
        self.run_j1(files)
        flags = json.loads(self.read(self.p("E/jev/flags.json")))
        self.assertEqual(len(flags["flagged"]), 3)

    def test_a_pair_with_an_na_verdict_is_never_flagged(self):
        files = self.arena([("A", "AAA", "BBB", "A"), ("B", "BBB", "AAA", "B")])
        self.write_ruling()
        self.script([0.9, 0.1, 0.5, {"exit": 4}])  # first verdict unstable, twin is na
        self.run_j1(files)
        self.assertEqual(json.loads(self.read(self.p("E/jev/flags.json")))["flagged"], [])

    def test_advisory_needs_a_calibration_entry_and_then_flags_low_p(self):
        self.write("decision.md", "jev: advisory\njev-content: ok\n")
        files = self.arena([("A", "AAA", "BBB", "A"), ("B", "BBB", "AAA", "B")])
        self.write_ruling()
        self.script([0.3, 0.3, 0.35, 0.35])
        code, out, _ = self.run_j1(files)
        self.assertIn("jev: shadow", out)  # no calibration file: advisory is not honoured
        self.assertEqual(json.loads(self.read(self.p("E/jev/flags.json")))["flagged"], [])
        qhash = jev_steps.load_questions()["sha256"]
        entry = {"entries": [{"resolved_model": "jev-1.13.0", "question_sha256": qhash, "action_threshold": 0.5}]}
        self.write("calibration.json", json.dumps(entry))
        self.script([0.3, 0.3, 0.35, 0.35])
        code, out, _ = self.run_j1(files, ["--calibration", self.p("calibration.json"), "--run-id", "run-test-2"])
        self.assertIn("jev: advisory", out)
        self.assertIn("flagged pairs for one extra blind judgment: AAA|BBB", out)
        flags = json.loads(self.read(self.p("E/jev/flags.json")))
        self.assertTrue(flags["acted"])
        self.assertEqual(flags["flagged"][0]["reason"], "low_p")
        # a calibration entry for another model or hash is ignored
        self.write("calibration.json", json.dumps({"entries": [{"resolved_model": "jev-9.9.9", "question_sha256": qhash}]}))
        self.script([0.3, 0.3, 0.35, 0.35])
        code, out, _ = self.run_j1(files, ["--calibration", self.p("calibration.json"), "--run-id", "run-test-3"])
        self.assertIn("jev: shadow", out)

    def test_canary_runs_and_drift_demotes(self):
        canary = load_json(jev_steps.CANARY_PATH)
        for item, baseline in zip(canary["states"], (0.9, 0.1, 0.5)):
            item["baseline"] = baseline
        self.write("canary.json", json.dumps(canary))
        self.write("decision.md", "jev: advisory\njev-content: ok\n")
        files = self.arena([("A", "AAA", "BBB", "A")])
        self.write_ruling()
        self.script([0.9, 0.1, 0.5, 0.6, 0.6])  # canary on baseline
        code, out, _ = self.run_j1(files, ["--canary", self.p("canary.json")])
        self.assertEqual(len(self.calls()), 5)
        self.assertFalse(os.path.exists(os.path.join(self.store, "demoted.json")))
        self.script([0.9, 0.4, 0.5, 0.6, 0.6])  # the second canary state drifted by 0.3
        code, out, _ = self.run_j1(files, ["--canary", self.p("canary.json"), "--run-id", "run-test-2"])
        self.assertTrue(os.path.exists(os.path.join(self.store, "demoted.json")))
        self.assertIn("jev: shadow", out)
        summary = json.loads(self.read(self.p("E/jev/run.json")))
        self.assertTrue(summary["canary"]["drift"])

    def calibrated(self):
        qhash = jev_steps.load_questions()["sha256"]
        self.write("calibration.json", json.dumps({"entries": [
            {"resolved_model": "jev-1.13.0", "question_sha256": qhash, "action_threshold": 0.5}]}))
        self.write("decision.md", "jev: advisory\njev-content: ok\n")
        return ["--calibration", self.p("calibration.json")]

    def test_demoted_json_blocks_advisory_even_with_a_valid_calibration(self):
        extra = self.calibrated()
        files = self.arena([("A", "AAA", "BBB", "A"), ("B", "BBB", "AAA", "B")])
        self.write_ruling()
        self.script([0.3, 0.3, 0.35, 0.35])
        code, out, _ = self.run_j1(files, extra)
        self.assertIn("jev: advisory", out)  # control: the same setup is ADVISORY without the flag
        with open(os.path.join(self.store, "demoted.json"), "w") as handle:
            json.dump({"level": "shadow", "reasons": ["drift:canary-wrong"]}, handle)
        self.script([0.3, 0.3, 0.35, 0.35])
        code, out, _ = self.run_j1(files, extra + ["--run-id", "run-test-2"])
        self.assertIn("jev: shadow", out)
        self.assertNotIn("flagged pairs", out)
        flags = json.loads(self.read(self.p("E/jev/flags.json")))
        self.assertEqual((flags["level"], flags["acted"]), ("shadow", False))

    def test_canary_drift_blocks_advisory_even_with_a_valid_calibration(self):
        extra = self.calibrated()
        canary = load_json(jev_steps.CANARY_PATH)
        for item, baseline in zip(canary["states"], (0.9, 0.1, 0.5)):
            item["baseline"] = baseline
        self.write("canary.json", json.dumps(canary))
        files = self.arena([("A", "AAA", "BBB", "A"), ("B", "BBB", "AAA", "B")])
        self.write_ruling()
        self.script([0.9, 0.1, 0.5, 0.3, 0.3, 0.35, 0.35])  # canary on baseline
        code, out, _ = self.run_j1(files, extra + ["--canary", self.p("canary.json")])
        self.assertIn("jev: advisory", out)
        self.assertFalse(os.path.exists(os.path.join(self.store, "demoted.json")))
        self.script([0.9, 0.1, 0.9, 0.3, 0.3, 0.35, 0.35])  # third canary state drifted by 0.4
        code, out, _ = self.run_j1(files, extra + ["--canary", self.p("canary.json"), "--run-id", "run-test-2"])
        self.assertIn("jev: shadow", out)
        self.assertNotIn("flagged pairs", out)
        self.assertTrue(os.path.exists(os.path.join(self.store, "demoted.json")))
        # the demotion sticks: the next run is SHADOW although its canary is back on baseline
        self.script([0.9, 0.1, 0.5, 0.3, 0.3, 0.35, 0.35])
        code, out, _ = self.run_j1(files, extra + ["--canary", self.p("canary.json"), "--run-id", "run-test-3"])
        self.assertIn("jev: shadow", out)

    def test_a_changed_pin_or_question_hash_in_the_canary_baseline_demotes(self):
        extra = self.calibrated()
        canary = load_json(jev_steps.CANARY_PATH)
        for item, baseline in zip(canary["states"], (0.6, 0.6, 0.6)):
            item["baseline"] = baseline
        canary["baseline_recorded_for"] = {"resolved_model": "jev-1.12.0", "question_sha256": None}
        self.write("canary.json", json.dumps(canary))
        files = self.arena([("A", "AAA", "BBB", "A")])
        self.write_ruling()
        code, out, _ = self.run_j1(files, extra + ["--canary", self.p("canary.json")])
        self.assertIn("jev: shadow", out)
        summary = json.loads(self.read(self.p("E/jev/run.json")))
        self.assertIn("pin changed", summary["canary"]["reasons"])

    def test_canary_without_baselines_only_records(self):
        self.write_ruling()
        files = self.arena([("A", "AAA", "BBB", "A")])
        code, out, _ = self.run_j1(files, ["--canary", jev_steps.CANARY_PATH])
        self.assertEqual(len(self.calls()), 5)
        self.assertFalse(json.loads(self.read(self.p("E/jev/run.json")))["canary"]["drift"])

    def test_supersede_drops_the_replaced_run_from_the_merged_view(self):
        files = self.arena([("A", "AAA", "BBB", "A")])
        self.write_ruling()
        self.run_j1(files)
        self.run_j1(files, ["--run-id", "run-test-2", "--supersedes", "run-test"])
        merged = jev_steps.merged_verdicts(self.events())
        self.assertEqual([r["run"] for r in merged], ["run-test-2"])

    def test_unexpected_error_is_unavailable_with_exit_zero(self):
        files = self.arena([("A", "AAA", "BBB", "A")])
        self.write_ruling()
        with mock.patch.object(jev_steps, "build_states", side_effect=RuntimeError("boom " + FAKE_KEY)):
            code, out, err = self.run_j1(files)
        self.assertEqual(code, 0)
        self.assertIn("UNAVAILABLE", err)
        self.assertNotIn("Traceback", err)


class SeededTest(JevCase):
    def seed_pack(self, pair="sp-1", a="Proposal A about a documented command.", b="Proposal B about another approach."):
        self.write("store/seeds/%s/a.md" % pair, a)
        self.write("store/seeds/%s/b.md" % pair, b)

    def commit(self, pair="sp-1", key=None, author="seatX"):
        code, out, err = run_main(["seeded-commit", "--pair-id", pair, "--author", author, "--store", self.store],
                                  stdin=key or "%s:b" % pair)
        nonce = re.search(r"nonce: ([0-9a-f]{32})", out)
        return code, (nonce.group(1) if nonce else None), err

    def seeded_run(self, pair="sp-1"):
        code, out, err = run_main(["seeded-run", "--pair-id", pair, "--store", self.store, "--base", self.p("var"), "--env", self.p("env.env")])
        if code != 0:
            return code, None, None, err
        self.last_header = re.search(r"header: (\S+)", out).group(1)
        return code, re.search(r"directory: (\S+)", out).group(1), re.search(r"seeded run: (\S+)", out).group(1), err

    def test_commitment_format(self):
        nonce = "00112233445566778899aabbccddeeff"
        expected = hashlib.sha256(b"sp-1:b" + bytes.fromhex(nonce)).hexdigest()
        self.assertEqual(jev_steps.make_commitment("sp-1:b", nonce), expected)
        with self.assertRaises(jev_steps.Refused):
            jev_steps.make_commitment("sp-1:b", "00" * 15)  # the nonce is exactly 16 bytes
        with self.assertRaises(ValueError):
            jev_steps.make_commitment("sp-1:b", "zz" * 16)

    def test_commit_records_the_hash_and_prints_a_16_byte_nonce(self):
        self.seed_pack()
        code, nonce, err = self.commit()
        self.assertEqual(code, 0, err)
        self.assertEqual(len(nonce), 32)
        (event,) = [e for e in self.events() if e["type"] == "commitment"]
        self.assertEqual(event["commitment"], jev_steps.make_commitment("sp-1:b", nonce))
        self.assertEqual(event["author"], "seatX")
        self.assertNotIn(nonce, self.read(os.path.join(self.store, "ledger.jsonl")))
        self.assertNotIn("sp-1:b", self.read(os.path.join(self.store, "ledger.jsonl")))

    def test_author_is_required_and_cannot_be_unknown(self):
        self.seed_pack()
        code, _, err = run_main(["seeded-commit", "--pair-id", "sp-1", "--store", self.store], stdin="sp-1:b")
        self.assertEqual(code, 2)
        for author in ("unknown", "UNKNOWN", "", "bad name", "x" * 40):
            code, _, err = run_main(["seeded-commit", "--pair-id", "sp-1", "--author", author, "--store", self.store], stdin="sp-1:b")
            self.assertEqual(code, 2, repr(author))
        self.assertEqual([e for e in self.events() if e["type"] == "commitment"], [])

    def test_commit_checks_the_key_and_the_pack(self):
        self.seed_pack()
        self.assertEqual(self.commit(key="other:a")[0], 2)  # names another pair
        self.assertEqual(self.commit(key="sp-1:c")[0], 2)
        self.assertEqual(self.commit(pair="sp-9", key="sp-9:a")[0], 2)  # no pack in the store
        self.assertEqual(self.commit(pair="../x", key="../x:a")[0], 2)
        self.assertEqual([e for e in self.events() if e["type"] == "commitment"], [])

    def test_seeded_run_refuses_without_a_commitment(self):
        self.seed_pack()
        code, directory, run, err = self.seeded_run()
        self.assertEqual(code, 2)
        self.assertIn("commitment", err)
        self.assertFalse(os.path.exists(self.p("var")))  # nothing was copied

    def test_seeded_run_copies_under_fresh_codes_and_keeps_the_map_elsewhere(self):
        self.seed_pack()
        self.commit()
        code, directory, run, err = self.seeded_run()
        self.assertEqual(code, 0, err)
        self.assertTrue(directory.startswith(self.p("var")))
        self.assertRegex(os.path.basename(directory), r"^olympus-arena-[0-9a-f]{12}$")
        files = sorted(os.listdir(os.path.join(directory, "blind")))
        self.assertEqual(len(files), 2)
        self.assertTrue(all(re.match(r"^P-[A-Z]{3}\.md$", f) for f in files))
        # the run header lives in the store, never in the judge directory
        header = self.read(self.last_header)
        self.assertEqual(self.last_header, os.path.join(self.store, "seed-runs", run + ".header.md"))
        self.assertIn("jev: shadow", header)
        self.assertIn("jev-content: ok", header)
        self.assertIn("kind: seeded", header)
        self.assertIn("run: %s" % run, header)
        self.assertEqual(os.listdir(directory), ["blind"])
        record = json.loads(self.read(os.path.join(self.store, "seed-runs", run + ".json")))
        self.assertEqual(sorted(record["codes"].values()), ["a", "b"])
        self.assertEqual(record["pair_id"], "sp-1")
        self.assertFalse(os.path.abspath(os.path.join(self.store, "seed-runs")).startswith(os.path.abspath(directory)))
        body = self.read(os.path.join(directory, "blind", files[0]))
        self.assertRegex(body.splitlines()[0], r"^<!-- blind P-[A-Z]{3}; \d+ words -->$")

    def test_the_judge_directory_tree_never_says_seed(self):
        self.seed_pack()
        self.commit()
        code, directory, run, err = self.seeded_run()
        self.assertEqual(code, 0, err)
        for root, dirs, names in os.walk(directory):
            for name in dirs + names:
                relative = os.path.relpath(os.path.join(root, name), os.path.dirname(directory)).lower()
                self.assertNotIn("seed", relative)
            for name in names:
                text = self.read(os.path.join(root, name)).lower()
                for word in ("seed", "sp-1", "defect", "synthetic", "kind:", "jev"):
                    self.assertNotIn(word, text, os.path.join(root, name))

    def test_each_pair_is_used_once(self):
        self.seed_pack()
        self.commit()
        self.assertEqual(self.seeded_run()[0], 0)
        self.assertEqual(self.seeded_run()[0], 2)
        self.assertEqual(self.commit()[0], 2)

    def test_seeded_run_scans_the_seed_text(self):
        self.seed_pack(a="This one mentions the tenant isolation policy.")
        self.commit()
        code, _, _, err = self.seeded_run()
        self.assertEqual(code, 2)
        self.assertIn("domain-word", err)
        self.assertNotIn("tenant isolation", err)

    def full_cycle(self, picks, key="sp-1:b", pair="sp-1", nonce_override=None, key_override=None):
        """Commit, run, judge (picks: winner letters per verdict), j1, ruling. Returns (run, codes map, nonce, directory)."""
        self.seed_pack(pair=pair)
        code, nonce, _ = self.commit(pair=pair, key=key)
        code, directory, run, err = self.seeded_run(pair=pair)
        self.assertEqual(code, 0, err)
        record = json.loads(self.read(os.path.join(self.store, "seed-runs", run + ".json")))
        codes = sorted(record["codes"])
        verdicts = []
        for index, winner in enumerate(picks):
            first, second = (codes[0], codes[1]) if index % 2 == 0 else (codes[1], codes[0])
            verdicts.append(("J%d" % index, first, second, winner))
        files = self.arena(verdicts)
        self.verdict_files = files
        self.judge_output_dir = self.p("judge-outputs")
        self.write("judge-outputs/notes.md", "The judge compared the two texts and chose.\n")
        self.write_ruling()
        self.header = self.last_header
        argv = ["j1", "--e", self.E, "--blind", os.path.join(directory, "blind"), "--verdicts"] + files + [
            "--rubric", self.p("rubric.md"), "--decision", self.header, "--config", self.p("config.json"),
            "--env", self.p("env.env"), "--store", self.store, "--runner", self.p("runner.py"),
            "--canary", self.p("no-canary.json"), "--calibration", self.p("no-calibration.json")]
        code, out, err = run_main(argv)
        self.assertEqual(code, 0, err)
        return run, record["codes"], nonce, directory

    def reveal(self, key, nonce, pair="sp-1"):
        self.write("store/seeds-key/%s.json" % pair, json.dumps({"key": key, "nonce": nonce}))

    def label(self, run, outputs=None):
        return run_main(["label", "seeded", "--run", run, "--e", self.E, "--verdicts"] + self.verdict_files + [
            "--judge-outputs"] + (outputs or [self.judge_output_dir]) + ["--store", self.store])

    def test_seeded_rows_use_the_seed_pair_as_the_cluster_and_the_run_header(self):
        run, codes, nonce, directory = self.full_cycle(["A", "B"])
        rows = self.verdict_rows()
        self.assertEqual({r["kind"] for r in rows}, {"seeded"})
        self.assertEqual({r["pair_id"] for r in rows}, {"sp-1"})
        self.assertEqual({r["run"] for r in rows}, {run})

    def test_label_seeded_after_a_correct_reveal(self):
        run, codes, nonce, directory = self.full_cycle(["A", "A"])  # picks the first listed candidate each time
        defective = next(c for c, name in codes.items() if name == "b")
        self.reveal("sp-1:b", nonce)
        code, out, err = self.label(run)
        self.assertEqual(code, 0, err)
        labels = {e["verdict_id"]: e["label"] for e in self.events() if e["type"] == "label"}
        for row in self.verdict_rows():
            expected = "correct" if row["winner_code"] != defective else "wrong"
            self.assertEqual(labels[row["verdict_id"]], expected)
            self.assertEqual(jev_steps.merged_verdicts(self.events())[0]["label_source"], "seeded")
        self.assertFalse(os.path.exists(self.p("store/seeds-key/sp-1.json")))  # the key file is removed
        self.assertEqual(self.label(run)[0], 2)  # and cannot be replayed

    def test_a_tie_is_labelled_wrong_and_has_no_p(self):
        run, codes, nonce, directory = self.full_cycle(["TIE", "A"])
        tie_row = next(r for r in self.verdict_rows() if r["tie"])
        self.assertIsNone(tie_row["p"])
        self.assertEqual(tie_row["na_reason"], "tie")
        self.assertEqual(len(self.calls()), 2)  # only the non-tie verdict was sent
        self.reveal("sp-1:b", nonce)
        self.assertEqual(self.label(run)[0], 0)
        merged = {r["verdict_id"]: r for r in jev_steps.merged_verdicts(self.events())}
        self.assertEqual(merged[tie_row["verdict_id"]]["label"], "wrong")

    def test_commitment_mismatches_are_refused(self):
        run, codes, nonce, directory = self.full_cycle(["A", "A"])
        wrong_nonce = "ab" * 16
        for key, nn in (("sp-1:a", nonce), ("sp-1:b", wrong_nonce), (nonce, "sp-1:b"), ("sp-1:b", nonce[:-2] + "00")):
            self.reveal(key, nn)
            code, _, err = self.label(run)
            self.assertEqual(code, 2, (key, nn))
            self.assertTrue(os.path.exists(self.p("store/seeds-key/sp-1.json")))  # nothing consumed
        self.assertEqual([e for e in self.events() if e["type"] == "label"], [])
        self.reveal("sp-1:b", nonce)  # the right pair of values still works afterwards
        self.assertEqual(self.label(run)[0], 0)

    def test_label_needs_ruling_and_every_verdict_file(self):
        run, codes, nonce, directory = self.full_cycle(["A", "B"])
        self.reveal("sp-1:b", nonce)
        os.remove(self.p("E/jev/ruling.md"))
        self.assertEqual(self.label(run)[0], 2)
        self.write_ruling()
        os.remove(self.verdict_files[0])
        code, _, err = self.label(run)
        self.assertEqual(code, 2)
        self.assertIn("verdict file", err)

    def test_leak_in_judge_outputs_voids_and_retires_the_pair(self):
        run, codes, nonce, directory = self.full_cycle(["A", "B"])
        self.write("judge-outputs/bad.md", "I looked at the seeds-key folder.\n")
        self.reveal("sp-1:b", nonce)
        code, _, err = self.label(run)
        self.assertEqual(code, 2)
        self.assertIn("void", err)
        self.assertEqual(jev_steps.pair_state(self.events(), "sp-1"), "retired")
        self.assertEqual(jev_steps.merged_verdicts(self.events()), [])  # the voided run's rows are dropped
        self.seed_pack()
        self.assertEqual(self.commit()[0], 2)

    def test_label_seeded_refuses_verdict_codes_that_do_not_belong_to_the_run(self):
        run, codes, nonce, directory = self.full_cycle(["A", "B"])
        self.reveal("sp-1:b", nonce)
        forged = dict(self.verdict_rows()[0], verdict_id="J9:XXX-YYY", code_a="XXX", code_b="YYY", winner_code="XXX")
        jev_steps.append_rows(self.store, [forged])
        code, _, err = self.label(run)
        self.assertEqual(code, 2)
        self.assertIn("do not belong", err)
        self.assertEqual([e for e in self.events() if e["type"] == "label"], [])
        self.assertTrue(os.path.exists(self.p("store/seeds-key/sp-1.json")))

    def test_j1_refuses_a_seeded_run_whose_codes_are_not_the_runs(self):
        self.seed_pack()
        self.commit()
        code, directory, run, err = self.seeded_run()
        files = self.arena([("A", "AAA", "BBB", "A")])  # AAA and BBB are not this run's fresh codes
        self.write_ruling()
        code, out, err = run_main(["j1", "--e", self.E, "--blind", self.blind, "--verdicts"] + files + [
            "--rubric", self.p("rubric.md"), "--decision", self.last_header, "--config", self.p("config.json"),
            "--env", self.p("env.env"), "--store", self.store, "--runner", self.p("runner.py"),
            "--canary", self.p("no-canary.json"), "--calibration", self.p("no-calibration.json")])
        self.assertEqual(code, 2)
        self.assertIn("do not belong", err)
        self.assertEqual(self.calls(), [])

    def test_a_judge_output_that_says_seeded_voids_the_cycle(self):
        run, codes, nonce, directory = self.full_cycle(["A", "B"])
        self.write("judge-outputs/bad.md", "This looks like a seeded Arena to me.\n")
        self.reveal("sp-1:b", nonce)
        code, _, err = self.label(run)
        self.assertEqual(code, 2)
        self.assertEqual(jev_steps.pair_state(self.events(), "sp-1"), "retired")

    def test_leak_patterns(self):
        for line in ("see jev/seeds/x", "the answer.key file", "answer-key", "seeds-key", "this looks like a seeded Arena"):
            self.assertTrue(jev_steps.LEAK_PATTERN.search(line), line)
        self.assertFalse(jev_steps.LEAK_PATTERN.search("a plain judgment"))

    def test_lost_key_void_returns_the_pair_to_the_pool(self):
        run, codes, nonce, directory = self.full_cycle(["A", "B"])
        code, out, _ = run_main(["seeded-void", "--run", run, "--reason", "lost-key", "--store", self.store])
        self.assertEqual(code, 0)
        self.assertEqual(jev_steps.pair_state(self.events(), "sp-1"), "none")
        self.assertEqual(self.seeded_run()[0], 2)  # needs a fresh commitment first
        code, nonce2, _ = self.commit()
        self.assertEqual(code, 0)
        self.assertNotEqual(nonce, nonce2)
        self.assertEqual(self.seeded_run()[0], 0)

    def test_the_old_commitment_does_not_verify_after_a_lost_key_void(self):
        run, codes, nonce, directory = self.full_cycle(["A", "B"])
        run_main(["seeded-void", "--run", run, "--reason", "lost-key", "--store", self.store])
        self.assertIsNone(jev_steps.latest_commitment(self.events(), "sp-1"))

    def natural_run(self, ruling=None):
        files = self.arena([("A", "AAA", "BBB", "A"), ("B", "CCC", "DDD", "A"), ("C", "BBB", "AAA", "TIE")])
        if ruling is None:
            self.write_ruling()
        else:
            self.write_ruling(ruling)
        self.script([0.61, 0.62, 0.63, 0.64])
        self.run_j1(files)
        return ["--run", "run-test", "--store", self.store]

    def test_natural_labels_l1_and_l3_only_say_wrong(self):
        base = self.natural_run()
        self.assertEqual(run_main(["label", "l1", "--verdict-id", "A:AAA-BBB", "--label", "wrong"] + base)[0], 0)
        merged = {r["verdict_id"]: r for r in jev_steps.merged_verdicts(self.events())}
        self.assertEqual((merged["A:AAA-BBB"]["label"], merged["A:AAA-BBB"]["label_source"]), ("wrong", "L1"))
        self.assertEqual(run_main(["label", "l3", "--verdict-id", "A:AAA-BBB", "--label", "wrong"] + base)[0], 0)
        self.assertEqual(jev_steps.merged_verdicts(self.events())[0]["label_source"], "L3")
        for source in ("l1", "l3"):
            code, _, err = run_main(["label", source, "--verdict-id", "A:AAA-BBB", "--label", "correct"] + base)
            self.assertEqual(code, 2)
            self.assertIn("audit", err)

    def test_correct_comes_only_from_the_audit(self):
        base = self.natural_run()
        self.assertEqual(run_main(["label", "audit", "--verdict-id", "A:AAA-BBB", "--label", "correct"] + base)[0], 0)
        merged = {r["verdict_id"]: r for r in jev_steps.merged_verdicts(self.events())}
        self.assertEqual((merged["A:AAA-BBB"]["label"], merged["A:AAA-BBB"]["label_source"]), ("correct", "audit"))

    def test_clean_verdicts_stay_unlabelled(self):
        self.natural_run()
        merged = jev_steps.merged_verdicts(self.events())
        self.assertTrue(all(r["label"] is None for r in merged))

    def test_only_verdicts_involving_the_base_are_labelled(self):
        base = self.natural_run()  # base: AAA; the second verdict is CCC vs DDD
        code, _, err = run_main(["label", "l1", "--verdict-id", "B:CCC-DDD", "--label", "wrong"] + base)
        self.assertEqual(code, 2)
        self.assertIn("base candidate", err)
        self.assertEqual(run_main(["label", "audit", "--verdict-id", "B:CCC-DDD", "--label", "correct"] + base)[0], 2)

    def test_no_base_line_means_no_natural_labels(self):
        base = self.natural_run(ruling="ranking: AAA first\ntimestamp: 2026-10-13T20:00:00Z\n")
        code, _, err = run_main(["label", "l1", "--verdict-id", "A:AAA-BBB", "--label", "wrong"] + base)
        self.assertEqual(code, 2)
        self.assertIn("base:", err)

    def test_ties_and_unknown_ids_are_refused(self):
        base = self.natural_run()
        self.assertEqual(run_main(["label", "l1", "--verdict-id", "nope", "--label", "wrong"] + base)[0], 2)
        self.assertEqual(run_main(["label", "l1", "--verdict-id", "C:BBB-AAA", "--label", "wrong"] + base)[0], 2)

    def test_the_labeller_listing_never_shows_p(self):
        base = self.natural_run()
        code, out, _ = run_main(["verdicts", "--run", "run-test", "--store", self.store])
        self.assertEqual(code, 0)
        self.assertIn("base AAA", out)
        self.assertIn("A:AAA-BBB AAA|BBB winner=AAA base_involved=yes labelled=no", out)
        self.assertIn("B:CCC-DDD CCC|DDD winner=CCC base_involved=no", out)
        for fragment in ("0.6", "p_ab", "p="):
            self.assertNotIn(fragment, out)
        self.assertNotIn("C:BBB-AAA", out)  # the tie is not labellable

    def test_the_ledger_row_records_the_base_code(self):
        self.natural_run()
        self.assertEqual({r["base_code"] for r in self.verdict_rows()}, {"AAA"})

    def test_natural_labels_refuse_seeded_rows(self):
        run, codes, nonce, directory = self.full_cycle(["A", "B"])
        row = self.verdict_rows()[0]
        code, _, err = run_main(["label", "l1", "--run", run, "--verdict-id", row["verdict_id"], "--label", "wrong", "--store", self.store])
        self.assertEqual(code, 2)
        self.assertIn("seeded", err)


class DocsTest(unittest.TestCase):
    ROOT = os.path.dirname(HERE)

    def read(self, *parts):
        with open(os.path.join(self.ROOT, *parts), "r", encoding="utf-8") as handle:
            return handle.read()

    def test_skill_md_stays_within_500_lines(self):
        self.assertLessEqual(len(self.read("SKILL.md").splitlines()), 500)

    def test_every_reference_named_in_skill_md_exists(self):
        for path in set(re.findall(r"`(references/[A-Za-z0-9_./-]+\.(?:md|json))`", self.read("SKILL.md"))):
            self.assertTrue(os.path.isfile(os.path.join(self.ROOT, path)), path)

    def test_skill_md_reference_count_matches(self):
        text = self.read("SKILL.md")
        declared = int(re.search(r"## References \((\d+) files", text).group(1))
        section = text.split("## References")[1]
        listed = [line for line in section.splitlines() if line.startswith("- `references/")]
        self.assertEqual(declared, len(listed))

    def test_no_seed_pack_is_shipped(self):
        for root, dirs, _ in os.walk(self.ROOT):
            dirs[:] = [d for d in dirs if d != ".git"]
            self.assertNotIn("seeds", dirs, root)
        self.assertFalse(os.path.exists(os.path.join(self.ROOT, "references", "jev-calibration.json")))

    def test_example_config_has_a_valid_pinned_jev_target(self):
        import olympus_call
        targets = olympus_call.load_config(os.path.join(self.ROOT, "olympus.providers.example.json"))
        target = olympus_call.resolve_target("jev", targets)
        self.assertEqual(target["adapter"], "typesafe")
        self.assertEqual(target["model"], "jev-1.13.0")
        self.assertEqual(target["url"], "https://api.typesafe.ai/v1/systemone")

    def test_gitignore_keeps_pycache_and_local_config_out(self):
        text = self.read(".gitignore")
        self.assertIn("__pycache__/", text)
        self.assertIn("olympus.providers.json", text)


class HeaderTest(unittest.TestCase):
    def test_header_parsing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "DECISION.md")
            with open(path, "w") as handle:
                handle.write("# T\n- jev: `Advisory`\n- jev-content: OK\njev: off\nkind: seeded\nrun: run-abc\n")
            header = jev_steps.parse_header(path)
        self.assertEqual(header, {"jev": "advisory", "jev_content": "ok", "kind": "seeded", "run": "run-abc"})
        self.assertEqual(jev_steps.parse_header("/nonexistent/x.md")["jev"], "off")
        self.assertEqual(jev_steps.parse_header("/nonexistent/x.md")["jev_content"], "blocked")


class _SystemOneHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])).decode("utf-8"))
        self.server.requests.append({"path": self.path, "auth": self.headers.get("Authorization"), "body": body})
        qid = list(body["questions"])[0]
        raw = json.dumps({"model": body["model"], "answers": {qid: {"type": "noul", "noul": 0.77}},
                          "usage": {"input_tokens": 50, "output_tokens": 3}}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


class EndToEndTest(JevCase):
    def test_j1_through_the_real_runner_and_adapter(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), _SystemOneHandler)
        server.requests = []
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.write("config.json", json.dumps({"targets": {"jev": {
            "adapter": "typesafe", "model": "jev-1.13.0", "base_url": "http://127.0.0.1:%d" % server.server_address[1]}}}))
        files = self.arena([("A", "AAA", "BBB", "A")])
        self.write_ruling()
        argv = self.j1_argv(files)
        argv[argv.index("--runner") + 1] = jev_steps.DEFAULT_RUNNER
        code, out, err = run_main(argv)
        self.assertEqual(code, 0, err)
        self.assertEqual(len(server.requests), 2)
        for request in server.requests:
            self.assertEqual(request["path"], "/v1/systemone")
            self.assertEqual(request["auth"], "Bearer " + FAKE_KEY)
            self.assertEqual(sorted(request["body"]["state"]), ["candidate_A", "candidate_B", "rubric", "verdict"])
            self.assertEqual(request["body"]["questions"], jev_steps.load_questions()["questions"])
        (row,) = self.verdict_rows()
        self.assertAlmostEqual(row["p"], 0.77)
        self.assertEqual(row["resolved_model"], "jev-1.13.0")
        self.assertEqual(row["tokens_in"], 100)
        self.assertNotIn(FAKE_KEY, self.read(os.path.join(self.store, "ledger.jsonl")))


if __name__ == "__main__":
    unittest.main()
