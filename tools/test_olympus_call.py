"""Offline tests for olympus_call.py. A local HTTP server on 127.0.0.1 mocks both protocols.

Run from the repository root:  python3 -m unittest tools/test_olympus_call.py
"""

import contextlib
import hashlib
import http.client
import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# Loopback must bypass any proxy configured in the environment; set before the module builds its opener.
os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import olympus_call  # noqa: E402

FAKE_KEY = "sk-test-SECRET-1234567890"


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # keep test output quiet
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8")
        server = self.server
        server.requests.append(
            {"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}, "body": json.loads(body)}
        )
        status, headers, payload = server.responses.pop(0) if server.responses else (500, {}, {"error": "no response queued"})
        headers = dict(headers)
        delay = float(headers.pop("X-Test-Delay", 0))
        raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
        try:
            if delay:
                time.sleep(delay)
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            for name, value in headers.items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(raw)
        except OSError:  # the client gave up (timeout test)
            pass


def openai_ok(text="hello from openai", usage=None):
    return (
        200,
        {},
        {
            "choices": [{"message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
            "usage": usage or {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
        },
    )


def anthropic_ok(blocks=None):
    return (
        200,
        {},
        {
            "content": blocks or [{"type": "text", "text": "hello from anthropic"}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 13, "output_tokens": 5},
        },
    )


class OlympusCallTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        cls.port = cls.server.server_address[1]
        cls.base = "http://127.0.0.1:%d" % cls.port
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        self.server.requests = []
        self.server.responses = []
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.sleeps = []
        self._orig_sleep = olympus_call._sleep
        olympus_call._sleep = self.sleeps.append
        self.addCleanup(setattr, olympus_call, "_sleep", self._orig_sleep)
        # Keys must come only from what each test sets.
        self._saved_env = dict(os.environ)
        for name in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "MOONSHOT_API_KEY", "OPENROUTER_API_KEY", "TEST_API_KEY"):
            os.environ.pop(name, None)
        self.addCleanup(self._restore_env)
        self.brief = self.path("brief.md")
        with open(self.brief, "w", encoding="utf-8") as handle:
            handle.write("Review this plan.")
        self.out = self.path("out.md")

    def _restore_env(self):
        os.environ.clear()
        os.environ.update(self._saved_env)

    def path(self, name):
        return os.path.join(self.tmp.name, name)

    def write_config(self, targets):
        path = self.path("providers.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"targets": targets}, handle)
        return path

    def run_tool(self, target, config, extra=()):
        argv = ["--target", target, "--brief", self.brief, "--out", self.out, "--config", config,
                "--env", self.path("none.env")] + list(extra)
        if not os.path.exists(self.path("none.env")):
            open(self.path("none.env"), "w").close()
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = olympus_call.main(argv)
        return code, out.getvalue(), err.getvalue()

    def read(self, path):
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read()

    def openai_target(self, **extra):
        target = {"adapter": "openai", "model": "m-1", "base_url": self.base + "/v1", "api_key_env": "TEST_API_KEY"}
        target.update(extra)
        return target

    # ----------------------------------------------------------------- adapters

    def test_openai_success(self):
        os.environ["TEST_API_KEY"] = FAKE_KEY
        self.server.responses = [openai_ok()]
        config = self.write_config({"t": self.openai_target()})
        code, stdout, stderr = self.run_tool("t", config, ["--max-tokens", "123"])
        self.assertEqual(code, 0, stderr)
        self.assertEqual(self.read(self.out), "hello from openai")
        req = self.server.requests[0]
        self.assertEqual(req["path"], "/v1/chat/completions")
        self.assertEqual(req["headers"]["authorization"], "Bearer " + FAKE_KEY)
        self.assertEqual(req["body"]["model"], "m-1")
        self.assertEqual(req["body"]["max_tokens"], 123)
        self.assertEqual(req["body"]["messages"], [{"role": "user", "content": "Review this plan."}])
        self.assertNotIn(FAKE_KEY, stdout + stderr)

    def test_openai_max_completion_tokens_option(self):
        os.environ["TEST_API_KEY"] = FAKE_KEY
        self.server.responses = [openai_ok()]
        config = self.write_config({"t": self.openai_target(max_tokens_param="max_completion_tokens")})
        self.assertEqual(self.run_tool("t", config)[0], 0)
        body = self.server.requests[0]["body"]
        self.assertIn("max_completion_tokens", body)
        self.assertNotIn("max_tokens", body)

    def test_openai_without_key_variable_sends_no_auth(self):
        self.server.responses = [openai_ok()]
        target = self.openai_target()
        del target["api_key_env"]
        config = self.write_config({"local": target})
        code, _, stderr = self.run_tool("local", config)
        self.assertEqual(code, 0, stderr)
        self.assertNotIn("authorization", self.server.requests[0]["headers"])

    def test_anthropic_success(self):
        os.environ["TEST_API_KEY"] = FAKE_KEY
        self.server.responses = [
            anthropic_ok([{"type": "text", "text": "part one. "}, {"type": "thinking", "thinking": "x"},
                          {"type": "text", "text": "part two."}])
        ]
        config = self.write_config({
            "a": {"adapter": "anthropic", "model": "claude-test", "base_url": self.base, "api_key_env": "TEST_API_KEY"}
        })
        code, _, stderr = self.run_tool("a", config)
        self.assertEqual(code, 0, stderr)
        self.assertEqual(self.read(self.out), "part one. part two.")
        req = self.server.requests[0]
        self.assertEqual(req["path"], "/v1/messages")
        self.assertEqual(req["headers"]["x-api-key"], FAKE_KEY)
        self.assertEqual(req["headers"]["anthropic-version"], "2023-06-01")
        self.assertNotIn("authorization", req["headers"])
        self.assertEqual(req["body"]["model"], "claude-test")
        self.assertEqual(req["body"]["max_tokens"], olympus_call.DEFAULT_MAX_TOKENS)

    def test_cli_success(self):
        script = "import sys; sys.stdout.write('echo:' + sys.stdin.read().upper())"
        config = self.write_config({"c": {"adapter": "cli", "command": [sys.executable, "-c", script]}})
        code, _, stderr = self.run_tool("c", config)
        self.assertEqual(code, 0, stderr)
        self.assertEqual(self.read(self.out), "echo:REVIEW THIS PLAN.")
        meta = json.loads(self.read(self.out + ".meta.json"))
        self.assertEqual(meta["adapter"], "cli")
        self.assertEqual(self.server.requests, [])

    def test_cli_model_placeholder(self):
        script = "import sys; sys.stdout.write(sys.argv[1])"
        config = self.write_config({
            "c": {"adapter": "cli", "model": "the-model", "command": [sys.executable, "-c", script, "{model}"]}
        })
        self.assertEqual(self.run_tool("c", config)[0], 0)
        self.assertEqual(self.read(self.out), "the-model")

    def test_cli_failure_exit_5_and_no_output_file(self):
        script = "import sys; sys.stderr.write('boom'); sys.exit(7)"
        config = self.write_config({"c": {"adapter": "cli", "command": [sys.executable, "-c", script]}})
        code, _, stderr = self.run_tool("c", config)
        self.assertEqual(code, 5)
        self.assertIn("exited 7", stderr)
        self.assertFalse(os.path.exists(self.out))

    def test_cli_missing_binary_exit_5(self):
        config = self.write_config({"c": {"adapter": "cli", "command": ["definitely-not-a-real-binary-xyz"]}})
        self.assertEqual(self.run_tool("c", config)[0], 5)

    def test_cli_timeout_exit_5(self):
        script = "import time; time.sleep(5)"
        config = self.write_config({"c": {"adapter": "cli", "command": [sys.executable, "-c", script]}})
        self.assertEqual(self.run_tool("c", config, ["--timeout", "0.5"])[0], 5)

    # ----------------------------------------------------------------- retries

    def test_429_then_success_honours_retry_after(self):
        os.environ["TEST_API_KEY"] = FAKE_KEY
        self.server.responses = [(429, {"Retry-After": "2"}, {"error": "slow down"}), openai_ok()]
        config = self.write_config({"t": self.openai_target()})
        code, _, stderr = self.run_tool("t", config)
        self.assertEqual(code, 0, stderr)
        self.assertEqual(len(self.server.requests), 2)
        self.assertEqual(self.sleeps, [2.0])
        self.assertEqual(json.loads(self.read(self.out + ".meta.json"))["attempts"], 2)

    def test_5xx_uses_exponential_backoff_then_gives_up(self):
        os.environ["TEST_API_KEY"] = FAKE_KEY
        self.server.responses = [(503, {}, {"error": "down"})] * 3
        config = self.write_config({"t": self.openai_target()})
        code, _, stderr = self.run_tool("t", config)
        self.assertEqual(code, 4)
        self.assertEqual(len(self.server.requests), 3)
        self.assertEqual(self.sleeps, [olympus_call.BACKOFF_BASE, olympus_call.BACKOFF_BASE * 2])
        self.assertIn("3 attempts", stderr)
        self.assertFalse(os.path.exists(self.out))

    def test_401_fails_fast(self):
        os.environ["TEST_API_KEY"] = FAKE_KEY
        self.server.responses = [(401, {}, {"error": "bad key"}), openai_ok()]
        config = self.write_config({"t": self.openai_target()})
        code, _, stderr = self.run_tool("t", config)
        self.assertEqual(code, 4)
        self.assertEqual(len(self.server.requests), 1)
        self.assertEqual(self.sleeps, [])
        self.assertIn("401", stderr)

    def test_redirect_is_not_followed(self):
        os.environ["TEST_API_KEY"] = FAKE_KEY
        self.server.responses = [(302, {"Location": self.base + "/elsewhere"}, {}), openai_ok()]
        config = self.write_config({"t": self.openai_target()})
        code, _, _ = self.run_tool("t", config)
        self.assertEqual(code, 4)
        self.assertEqual(len(self.server.requests), 1)

    def test_empty_model_text_is_a_provider_error(self):
        os.environ["TEST_API_KEY"] = FAKE_KEY
        self.server.responses = [openai_ok(text="")]
        config = self.write_config({"t": self.openai_target()})
        self.assertEqual(self.run_tool("t", config)[0], 4)

    # ----------------------------------------------------------------- keys

    def test_missing_key_exits_3_and_sends_nothing(self):
        config = self.write_config({"t": self.openai_target()})
        code, _, stderr = self.run_tool("t", config)
        self.assertEqual(code, 3)
        self.assertIn("TEST_API_KEY", stderr)
        self.assertEqual(self.server.requests, [])

    def test_key_from_env_file(self):
        env_path = self.path("keys.env")
        with open(env_path, "w") as handle:
            handle.write("TEST_API_KEY=%s\n" % FAKE_KEY)
        self.server.responses = [openai_ok()]
        config = self.write_config({"t": self.openai_target()})
        argv = ["--target", "t", "--brief", self.brief, "--out", self.out, "--config", config, "--env", env_path]
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(olympus_call.main(argv), 0)
        self.assertEqual(self.server.requests[0]["headers"]["authorization"], "Bearer " + FAKE_KEY)

    def test_process_env_wins_over_env_file(self):
        env_path = self.path("keys.env")
        with open(env_path, "w") as handle:
            handle.write("TEST_API_KEY=from-file-1234\n")
        os.environ["TEST_API_KEY"] = "from-process-1234"
        self.server.responses = [openai_ok()]
        config = self.write_config({"t": self.openai_target()})
        argv = ["--target", "t", "--brief", self.brief, "--out", self.out, "--config", config, "--env", env_path]
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(olympus_call.main(argv), 0)
        self.assertEqual(self.server.requests[0]["headers"]["authorization"], "Bearer from-process-1234")

    def test_env_parser(self):
        path = self.path("parse.env")
        with open(path, "w") as handle:
            handle.write(
                "# a comment\n"
                "\n"
                "PLAIN=abc123\n"
                "export EXPORTED=def456\n"
                'DOUBLE="quoted value # not a comment"\n'
                "SINGLE='single quoted'\n"
                "INLINE=value # trailing comment\n"
                "EMPTY=\n"
                "SPACED = padded \n"
                "no equals here\n"
                "1BAD=skipped\n"
                "URL=https://example.com/a?b=c\n"
            )
        values = olympus_call.parse_env_file(path)
        self.assertEqual(values["PLAIN"], "abc123")
        self.assertEqual(values["EXPORTED"], "def456")
        self.assertEqual(values["DOUBLE"], "quoted value # not a comment")
        self.assertEqual(values["SINGLE"], "single quoted")
        self.assertEqual(values["INLINE"], "value")
        self.assertEqual(values["EMPTY"], "")
        self.assertEqual(values["SPACED"], "padded")
        self.assertEqual(values["URL"], "https://example.com/a?b=c")
        self.assertNotIn("1BAD", values)
        self.assertNotIn("no equals here", values)

    def test_key_redacted_in_provider_error(self):
        os.environ["TEST_API_KEY"] = FAKE_KEY
        echo = {"error": "Incorrect API key provided: %s. Authorization: Bearer %s" % (FAKE_KEY, FAKE_KEY)}
        self.server.responses = [(401, {}, echo)]
        config = self.write_config({"t": self.openai_target()})
        code, stdout, stderr = self.run_tool("t", config)
        self.assertEqual(code, 4)
        self.assertNotIn(FAKE_KEY, stdout + stderr)
        self.assertIn("***", stderr)

    def test_redact_function(self):
        text = "x-api-key: abcdef123456 and Authorization: Bearer tok_123456789 and raw SECRETVALUE9"
        cleaned = olympus_call.redact(text, ["SECRETVALUE9"])
        for leaked in ("abcdef123456", "tok_123456789", "SECRETVALUE9"):
            self.assertNotIn(leaked, cleaned)

    def test_no_key_in_output_or_meta(self):
        os.environ["TEST_API_KEY"] = FAKE_KEY
        self.server.responses = [openai_ok()]
        config = self.write_config({"t": self.openai_target()})
        self.assertEqual(self.run_tool("t", config)[0], 0)
        self.assertNotIn(FAKE_KEY, self.read(self.out))
        self.assertNotIn(FAKE_KEY, self.read(self.out + ".meta.json"))

    # ----------------------------------------------------------------- meta, dry run, config

    def test_meta_json_content(self):
        os.environ["TEST_API_KEY"] = FAKE_KEY
        self.server.responses = [openai_ok(usage={"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3})]
        config = self.write_config({"t": self.openai_target(provider="custom-x")})
        self.assertEqual(self.run_tool("t", config)[0], 0)
        meta = json.loads(self.read(self.out + ".meta.json"))
        self.assertEqual(meta["target"], "t")
        self.assertEqual(meta["provider"], "custom-x")
        self.assertEqual(meta["adapter"], "openai")
        self.assertEqual(meta["model"], "m-1")
        self.assertEqual(meta["usage"], {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3})
        self.assertIsInstance(meta["latency_seconds"], float)
        self.assertRegex(meta["timestamp"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

    def test_anthropic_meta_usage(self):
        os.environ["TEST_API_KEY"] = FAKE_KEY
        self.server.responses = [anthropic_ok()]
        config = self.write_config({
            "a": {"adapter": "anthropic", "model": "claude-test", "base_url": self.base, "api_key_env": "TEST_API_KEY"}
        })
        self.assertEqual(self.run_tool("a", config)[0], 0)
        meta = json.loads(self.read(self.out + ".meta.json"))
        self.assertEqual(meta["usage"], {"input_tokens": 13, "output_tokens": 5})

    def test_dry_run_with_and_without_key(self):
        config = self.write_config({"t": self.openai_target()})
        code, stdout, _ = self.run_tool("t", config, ["--dry-run"])
        self.assertEqual(code, 0)
        info = json.loads(stdout)
        self.assertEqual(info["api_key"], "MISSING")
        self.assertEqual(info["url"], self.base + "/v1/chat/completions")
        os.environ["TEST_API_KEY"] = FAKE_KEY
        code, stdout, _ = self.run_tool("t", config, ["--dry-run"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout)["api_key"], "***")
        self.assertNotIn(FAKE_KEY, stdout)
        self.assertEqual(self.server.requests, [])
        self.assertFalse(os.path.exists(self.out))

    def test_dry_run_cli_target(self):
        config = self.write_config({"c": {"adapter": "cli", "model": "mm", "command": ["tool", "--model", "{model}"]}})
        code, stdout, _ = self.run_tool("c", config, ["--dry-run"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout)["command"], ["tool", "--model", "mm"])

    def test_provider_defaults(self):
        config = self.write_config({
            "o": {"adapter": "openai", "provider": "openai", "model": "x"},
            "r": {"adapter": "openai", "provider": "openrouter", "model": "x"},
            "m": {"adapter": "openai", "provider": "moonshot", "model": "x"},
            "a": {"adapter": "anthropic", "model": "x"},
        })
        expected = {
            "o": ("https://api.openai.com/v1/chat/completions", "OPENAI_API_KEY"),
            "r": ("https://openrouter.ai/api/v1/chat/completions", "OPENROUTER_API_KEY"),
            "m": ("https://api.moonshot.ai/v1/chat/completions", "MOONSHOT_API_KEY"),
            "a": ("https://api.anthropic.com/v1/messages", "ANTHROPIC_API_KEY"),
        }
        for name, (url, key_env) in expected.items():
            code, stdout, stderr = self.run_tool(name, config, ["--dry-run"])
            self.assertEqual(code, 0, stderr)
            info = json.loads(stdout)
            self.assertEqual(info["url"], url)
            self.assertEqual(info["api_key_env"], key_env)
            # api.openai.com takes max_completion_tokens; every other endpoint here takes max_tokens.
            self.assertEqual(info["max_tokens_param"], "max_completion_tokens" if name == "o" else "max_tokens")

    def test_config_errors_exit_2(self):
        config = self.write_config({
            "noadapter": {"model": "x"},
            "nomodel": {"adapter": "openai", "base_url": self.base},
            "nobase": {"adapter": "openai", "model": "x"},
            "badcli": {"adapter": "cli", "command": "not-a-list"},
            "plainhttp": {"adapter": "openai", "model": "x", "base_url": "http://example.com/v1", "api_key_env": "K_API_KEY"},
            "lowerkey": {"adapter": "openai", "model": "x", "base_url": self.base, "api_key_env": "my_api_key"},
            "nosuffix": {"adapter": "openai", "model": "x", "base_url": self.base, "api_key_env": "TEST_KEY"},
            "pathy": {"adapter": "anthropic", "model": "x", "api_key_env": "PATH"},
        })
        for name in ("noadapter", "nomodel", "nobase", "badcli", "plainhttp", "lowerkey", "nosuffix", "pathy", "unknown"):
            self.assertEqual(self.run_tool(name, config)[0], 2, name)
        self.assertEqual(self.run_tool("x", self.path("missing.json"))[0], 2)

    def test_usage_errors_exit_2(self):
        config = self.write_config({"t": self.openai_target()})
        os.environ["TEST_API_KEY"] = FAKE_KEY
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(olympus_call.main(["--target", "t"]), 2)
        os.remove(self.brief)
        self.assertEqual(self.run_tool("t", config)[0], 2)

    # ----------------------------------------------------------------- fix round

    def test_api_key_env_pattern_accepts_valid_names(self):
        for name in ("TEST_API_KEY", "A1_B2_API_KEY", "X_API_KEY"):
            target = olympus_call.resolve_target(
                "t", {"t": {"adapter": "openai", "model": "x", "base_url": self.base, "api_key_env": name}}
            )
            self.assertEqual(target["api_key_env"], name)

    def test_defaults_resolve_to_repo_root_never_cwd(self):
        hostile = tempfile.mkdtemp(dir=self.tmp.name)
        with open(os.path.join(hostile, "olympus.providers.json"), "w") as handle:
            json.dump({"targets": {"t": {"adapter": "openai", "model": "x", "provider": "hostile",
                                         "base_url": "https://hostile.example/v1", "api_key_env": "HOSTILE_ONLY_API_KEY"}}}, handle)
        with open(os.path.join(hostile, ".env"), "w") as handle:
            handle.write("HOSTILE_ONLY_API_KEY=hostile-secret-123456\n")
        script = os.path.join(HERE, "olympus_call.py")
        repo_root = os.path.dirname(HERE)
        env = {k: v for k, v in os.environ.items() if k != "HOSTILE_ONLY_API_KEY"}

        # 1. Default config: the hostile ./olympus.providers.json must not be read.
        proc = subprocess.run(
            [sys.executable, script, "--target", "t", "--brief", self.brief, "--out", self.out, "--dry-run"],
            cwd=hostile, capture_output=True, text=True, env=env, timeout=30,
        )
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertNotIn("hostile", proc.stdout + proc.stderr)
        self.assertIn(os.path.join(repo_root, "olympus.providers.json"), proc.stderr)

        # 2. Explicit config, default env: the hostile ./.env must not be read.
        good = self.write_config({"t": self.openai_target(api_key_env="HOSTILE_ONLY_API_KEY")})
        proc = subprocess.run(
            [sys.executable, script, "--target", "t", "--brief", self.brief, "--out", self.out, "--config", good, "--dry-run"],
            cwd=hostile, capture_output=True, text=True, env=env, timeout=30,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        info = json.loads(proc.stdout)
        self.assertEqual(info["api_key"], "MISSING")
        self.assertEqual(info["env_path"], os.path.join(repo_root, ".env"))
        self.assertEqual(info["config_path"], good)
        self.assertNotIn("hostile-secret", proc.stdout + proc.stderr)

    def test_dry_run_prints_explicit_paths(self):
        config = self.write_config({"t": self.openai_target()})
        code, stdout, _ = self.run_tool("t", config, ["--dry-run"])
        info = json.loads(stdout)
        self.assertEqual(info["config_path"], config)
        self.assertEqual(info["env_path"], self.path("none.env"))
        self.assertTrue(info["env_file_found"])
        self.assertEqual(info["max_tokens_param"], "max_tokens")

    def test_explicit_missing_env_file_is_usage_error(self):
        config = self.write_config({"t": self.openai_target()})
        argv = ["--target", "t", "--brief", self.brief, "--out", self.out, "--config", config,
                "--env", self.path("absent.env")]
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(olympus_call.main(argv), 2)

    def _target_for(self, adapter):
        if adapter == "anthropic":
            self.server.responses = [anthropic_ok()]
            return {"adapter": "anthropic", "model": "claude-test", "base_url": self.base, "api_key_env": "TEST_API_KEY"}
        self.server.responses = [openai_ok()]
        return self.openai_target()

    def test_key_with_trailing_newline_is_stripped(self):
        for adapter in ("openai", "anthropic"):
            self.server.requests = []
            os.environ["TEST_API_KEY"] = FAKE_KEY + "\n"
            config = self.write_config({"t": self._target_for(adapter)})
            code, stdout, stderr = self.run_tool("t", config)
            self.assertEqual(code, 0, adapter + stderr)
            headers = self.server.requests[0]["headers"]
            sent = headers["x-api-key"] if adapter == "anthropic" else headers["authorization"]
            self.assertTrue(sent.endswith(FAKE_KEY), sent)
            self.assertNotIn(FAKE_KEY, stdout + stderr)

    def test_key_with_inner_whitespace_or_control_chars_exits_3(self):
        for bad in ("abc def-1234", "abc\tdef-1234", "abc\x07def-1234", "abc\x7fdef-1234", "abc\r\ndef-1234", "caf\u00e9-key-1234"):
            for adapter in ("openai", "anthropic"):
                self.server.requests = []
                os.environ["TEST_API_KEY"] = bad
                config = self.write_config({"t": self._target_for(adapter)})
                code, stdout, stderr = self.run_tool("t", config)
                self.assertEqual(code, 3, (adapter, bad))
                self.assertIn("TEST_API_KEY", stderr)
                self.assertNotIn("def-1234", stdout + stderr)
                self.assertEqual(self.server.requests, [])

    def test_catch_all_redacts_key_when_a_header_is_rejected(self):
        # Bypass the key check to simulate an unstripped value reaching urllib, which raises
        # ValueError("Invalid header value ...") with the key inside the message.
        bad_key = FAKE_KEY + "\n"
        for adapter in ("openai", "anthropic"):
            self.server.requests = []
            config = self.write_config({"t": self._target_for(adapter)})
            with mock.patch.object(olympus_call, "lookup_key", return_value=bad_key):
                code, stdout, stderr = self.run_tool("t", config)
            self.assertEqual(code, 4, adapter)
            self.assertNotIn(FAKE_KEY, stdout + stderr)
            self.assertNotIn("Traceback", stdout + stderr)
            self.assertIn("unexpected", stderr)

    def test_catch_all_on_arbitrary_exception(self):
        os.environ["TEST_API_KEY"] = FAKE_KEY
        config = self.write_config({"t": self.openai_target()})
        with mock.patch.object(olympus_call, "http_post_json", side_effect=RuntimeError("boom " + FAKE_KEY)):
            code, stdout, stderr = self.run_tool("t", config)
        self.assertEqual(code, 4)
        self.assertNotIn(FAKE_KEY, stdout + stderr)
        self.assertNotIn("Traceback", stdout + stderr)

    def test_key_straddling_truncation_point_is_redacted(self):
        key = "zq9-straddle-KEY-0123456789"
        os.environ["TEST_API_KEY"] = key
        prefix = "HTTP 401 Unauthorized "
        pad = olympus_call.ERROR_TEXT_LIMIT - len(prefix) - 10  # key starts 10 chars before the cut
        body = ("x" * pad + key + "y" * 600).encode("utf-8")
        for adapter in ("openai", "anthropic"):
            self.server.requests = []
            self.server.responses = [(401, {}, body)]
            target = self.openai_target() if adapter == "openai" else {
                "adapter": "anthropic", "model": "m", "base_url": self.base, "api_key_env": "TEST_API_KEY"}
            config = self.write_config({"t": target})
            code, stdout, stderr = self.run_tool("t", config)
            self.assertEqual(code, 4)
            self.assertNotIn("zq9-str", stdout + stderr)
            self.assertNotIn("0123456789", stdout + stderr)
            self.assertIn("***", stderr)

    def test_cli_stderr_redaction_before_truncation(self):
        script = "import sys; sys.stderr.write('Authorization: Bearer abcdef0123456789 ' + 'z' * 800); sys.exit(1)"
        config = self.write_config({"c": {"adapter": "cli", "command": [sys.executable, "-c", script]}})
        code, _, stderr = self.run_tool("c", config)
        self.assertEqual(code, 5)
        self.assertNotIn("abcdef0123456789", stderr)

    def test_http_exceptions_are_retried_then_exit_4(self):
        os.environ["TEST_API_KEY"] = FAKE_KEY
        config = self.write_config({"t": self.openai_target()})
        for exc in (http.client.IncompleteRead(b"par"), http.client.BadStatusLine("garbage")):
            self.sleeps.clear()
            with mock.patch.object(olympus_call._OPENER, "open", side_effect=exc) as opener:
                code, _, stderr = self.run_tool("t", config)
            self.assertEqual(code, 4, repr(exc))
            self.assertEqual(opener.call_count, 3)
            self.assertEqual(self.sleeps, [olympus_call.BACKOFF_BASE, olympus_call.BACKOFF_BASE * 2])
            self.assertNotIn("Traceback", stderr)

    def test_http_exception_then_success(self):
        os.environ["TEST_API_KEY"] = FAKE_KEY
        self.server.responses = [openai_ok()]
        config = self.write_config({"t": self.openai_target()})
        real_open = olympus_call._OPENER.open
        calls = []

        def flaky(request, timeout=None):
            calls.append(1)
            if len(calls) == 1:
                raise http.client.IncompleteRead(b"")
            return real_open(request, timeout=timeout)

        with mock.patch.object(olympus_call._OPENER, "open", side_effect=flaky):
            code, _, stderr = self.run_tool("t", config)
        self.assertEqual(code, 0, stderr)
        self.assertEqual(len(calls), 2)

    def test_timeout_is_not_retried_by_default(self):
        os.environ["TEST_API_KEY"] = FAKE_KEY
        config = self.write_config({"t": self.openai_target()})
        with mock.patch.object(olympus_call._OPENER, "open", side_effect=socket.timeout("timed out")) as opener:
            code, _, stderr = self.run_tool("t", config)
        self.assertEqual(code, 4)
        self.assertEqual(opener.call_count, 1)
        self.assertEqual(self.sleeps, [])
        self.assertIn("--retry-timeouts", stderr)

    def test_retry_timeouts_opt_in(self):
        os.environ["TEST_API_KEY"] = FAKE_KEY
        config = self.write_config({"t": self.openai_target()})
        with mock.patch.object(olympus_call._OPENER, "open", side_effect=socket.timeout("timed out")) as opener:
            code, _, _ = self.run_tool("t", config, ["--retry-timeouts"])
        self.assertEqual(code, 4)
        self.assertEqual(opener.call_count, 3)

    def test_real_timeout_sends_one_request(self):
        os.environ["TEST_API_KEY"] = FAKE_KEY
        self.server.responses = [(200, {"X-Test-Delay": "1.5"}, {"choices": []})]
        config = self.write_config({"t": self.openai_target()})
        code, _, stderr = self.run_tool("t", config, ["--timeout", "0.3"])
        self.assertEqual(code, 4, stderr)
        self.assertEqual(len(self.server.requests), 1)
        self.assertIn("timed out", stderr)

    def test_base_url_with_credentials_is_rejected_and_never_printed(self):
        os.environ["TEST_API_KEY"] = FAKE_KEY
        for url in ("https://user:hunter2pw@api.example.com/v1", "https://tokenonly@api.example.com/v1"):
            config = self.write_config({
                "t": {"adapter": "openai", "model": "x", "base_url": url, "api_key_env": "TEST_API_KEY"},
                "a": {"adapter": "anthropic", "model": "x", "base_url": url, "api_key_env": "TEST_API_KEY"},
            })
            for name in ("t", "a"):
                for extra in ([], ["--dry-run"]):
                    code, stdout, stderr = self.run_tool(name, config, extra)
                    self.assertEqual(code, 2)
                    self.assertNotIn("hunter2pw", stdout + stderr)
                    self.assertNotIn("tokenonly", stdout + stderr)
        self.assertEqual(self.server.requests, [])

    def test_redact_handles_repr_and_stripped_forms(self):
        cleaned = olympus_call.redact("bad header b'Bearer sk-abc-123456\\n'", ["sk-abc-123456\n"])
        self.assertNotIn("sk-abc-123456", cleaned)

    # ----------------------------------------------------------------- typesafe adapter

    TS_STATE = {"rubric": "SENTINEL-RUBRIC-TEXT", "verdict": "SENTINEL-VERDICT-TEXT"}

    def ts_target(self, **extra):
        target = {"adapter": "typesafe", "model": "jev-1.13.0", "base_url": self.base, "api_key_env": "TEST_API_KEY"}
        target.update(extra)
        return target

    def ts_brief(self, questions=None, state=None, raw=None):
        questions = questions or {"p": {"type": "noul", "instructions": "Is the verdict correct?"}}
        text = raw if raw is not None else json.dumps({"state": state or self.TS_STATE, "questions": questions})
        with open(self.brief, "w", encoding="utf-8") as handle:
            handle.write(text)
        return questions

    def ts_ok(self, answers, model="jev-1.13.0"):
        return (200, {}, {"model": model, "answers": answers, "usage": {"input_tokens": 296, "output_tokens": 20}})

    def ts_run(self, targets=None, extra=()):
        os.environ["TEST_API_KEY"] = FAKE_KEY
        config = self.write_config(targets or {"j": self.ts_target()})
        return self.run_tool("j", config, extra)

    def test_typesafe_noul_success(self):
        self.ts_brief()
        self.server.responses = [self.ts_ok({"p": {"type": "noul", "noul": 0.62}})]
        code, stdout, stderr = self.ts_run()
        self.assertEqual(code, 0, stderr)
        req = self.server.requests[0]
        self.assertEqual(req["path"], "/v1/systemone")
        self.assertEqual(req["headers"]["authorization"], "Bearer " + FAKE_KEY)
        self.assertEqual(sorted(req["body"]), ["model", "questions", "state"])
        self.assertEqual(req["body"]["model"], "jev-1.13.0")
        self.assertEqual(json.loads(self.read(self.out))["answers"]["p"]["noul"], 0.62)

    def test_typesafe_choice_success(self):
        crit = {"first": "first is better", "second": "second is better", "tie": "equal"}
        self.ts_brief({"w": {"type": "choice", "instructions": "Which?", "criteria": crit}})
        self.server.responses = [self.ts_ok({"w": {"type": "choice", "choice": "second", "confidence": 0.4,
                                                    "probabilities": {"first": 0.3, "second": 0.5, "tie": 0.2}}})]
        code, _, stderr = self.ts_run()
        self.assertEqual(code, 0, stderr)
        self.assertEqual(json.loads(self.read(self.out))["answers"]["w"]["choice"], "second")

    def test_typesafe_score_success(self):
        levels = ["low", "mid", "high"]
        self.ts_brief({"s": {"type": "score", "instructions": "How big?", "criteria": levels}})
        self.server.responses = [self.ts_ok({"s": {"type": "score", "score": 1.43, "confidence": 0.35,
                                                    "legend": {"0": "low", "1": "mid", "2": "high"},
                                                    "probabilities": {"0": 0.0, "1": 0.57, "2": 0.43}}})]
        code, _, stderr = self.ts_run()
        self.assertEqual(code, 0, stderr)

    def test_typesafe_alias_rejected(self):
        self.ts_brief()
        for model in ("jev-latest", "jev-preview", "jev-1.13", "jev-1.13.0-beta", "jev-1.13.0\n", "other-1.0.0", ""):
            code, _, stderr = self.ts_run({"j": self.ts_target(model=model)})
            self.assertEqual(code, 2, repr(model))
        self.assertEqual(self.server.requests, [])

    def test_typesafe_response_model_mismatch_exit4(self):
        self.ts_brief()
        self.server.responses = [self.ts_ok({"p": {"type": "noul", "noul": 0.5}}, model="jev-1.14.0")]
        code, _, stderr = self.ts_run()
        self.assertEqual(code, 4)
        self.assertIn("model_mismatch", stderr)
        self.assertFalse(os.path.exists(self.out))

    def test_typesafe_invalid_brief_exit2_sends_nothing(self):
        big = {"state": "x" * (olympus_call.TYPESAFE_MAX_BRIEF_BYTES + 1), "questions": {"p": {"type": "noul", "instructions": "q"}}}
        q = {"type": "noul", "instructions": "q"}
        bad = [
            "not json",
            json.dumps([]),
            json.dumps({"state": "s"}),
            json.dumps({"state": "s", "questions": {"p": q}, "extra": 1}),
            json.dumps({"state": "", "questions": {"p": q}}),
            json.dumps({"state": "s", "questions": {}}),
            json.dumps({"state": "s", "questions": {"p": {"type": "rank", "instructions": "q"}}}),
            json.dumps({"state": "s", "questions": {"p": {"type": "noul", "instructions": "  "}}}),
            json.dumps({"state": "s", "questions": {"p": {"type": "noul", "instructions": "q", "extra": 1}}}),
            json.dumps({"state": "s", "questions": {"p": {"type": "noul", "instructions": "q", "criteria": {"maybe": "x"}}}}),
            json.dumps({"state": "s", "questions": {"p": {"type": "choice", "instructions": "q", "criteria": {}}}}),
            json.dumps({"state": "s", "questions": {"p": {"type": "choice", "instructions": "q",
                                                          "criteria": {"o%d" % i: "d" for i in range(256)}}}}),
            json.dumps({"state": "s", "questions": {"p": {"type": "score", "instructions": "q", "criteria": ["only"]}}}),
            json.dumps({"state": "s", "questions": {"p": {"type": "score", "instructions": "q",
                                                          "criteria": ["l%d" % i for i in range(11)]}}}),
            json.dumps(big),
        ]
        for raw in bad:
            self.ts_brief(raw=raw)
            code, _, stderr = self.ts_run()
            self.assertEqual(code, 2, raw[:80])
        self.assertEqual(self.server.requests, [])

    def test_typesafe_choice_limit_255_is_accepted(self):
        crit = {"o%d" % i: "d" for i in range(255)}
        self.ts_brief({"w": {"type": "choice", "instructions": "q", "criteria": crit}})
        probs = {k: 0.0 for k in crit}
        probs["o0"] = 1.0
        self.server.responses = [self.ts_ok({"w": {"type": "choice", "choice": "o0", "confidence": 1.0, "probabilities": probs}})]
        self.assertEqual(self.ts_run()[0], 0)

    def test_typesafe_malformed_answer_exit4(self):
        crit = {"a": "A", "b": "B"}
        levels = ["lo", "hi"]
        questions = {
            "n": {"type": "noul", "instructions": "q"},
            "c": {"type": "choice", "instructions": "q", "criteria": crit},
            "s": {"type": "score", "instructions": "q", "criteria": levels},
        }
        good = {
            "n": {"type": "noul", "noul": 0.5},
            "c": {"type": "choice", "choice": "a", "confidence": 0.5, "probabilities": {"a": 0.6, "b": 0.4}},
            "s": {"type": "score", "score": 0.5, "confidence": 0.5, "legend": {"0": "lo", "1": "hi"},
                  "probabilities": {"0": 0.5, "1": 0.5}},
        }

        def mutated(**changes):
            answers = json.loads(json.dumps(good))
            for key, fn in changes.items():
                fn(answers)
            return answers

        cases = {
            "noul above 1": mutated(a=lambda a: a["n"].__setitem__("noul", 1.2)),
            "noul negative": mutated(a=lambda a: a["n"].__setitem__("noul", -0.1)),
            "noul bool": mutated(a=lambda a: a["n"].__setitem__("noul", True)),
            "noul missing": mutated(a=lambda a: a["n"].pop("noul")),
            "choice outside criteria": mutated(a=lambda a: a["c"].__setitem__("choice", "zzz")),
            "choice probabilities off": mutated(a=lambda a: a["c"].__setitem__("probabilities", {"a": 0.2, "b": 0.2})),
            "choice unknown option": mutated(a=lambda a: a["c"].__setitem__("probabilities", {"a": 0.5, "q": 0.5})),
            "score out of range": mutated(a=lambda a: a["s"].__setitem__("score", 1.5)),
            "score legend wrong": mutated(a=lambda a: a["s"].__setitem__("legend", {"0": "lo", "1": "XX"})),
            "score probabilities off": mutated(a=lambda a: a["s"].__setitem__("probabilities", {"0": 0.9, "1": 0.9})),
            "wrong type": mutated(a=lambda a: a["n"].__setitem__("type", "choice")),
            "missing id": mutated(a=lambda a: a.pop("s")),
            "extra id": mutated(a=lambda a: a.__setitem__("zzz", {"type": "noul", "noul": 0.5})),
        }
        for label, answers in cases.items():
            self.server.requests = []
            self.ts_brief(questions)
            self.server.responses = [self.ts_ok(answers)]
            code, _, stderr = self.ts_run()
            self.assertEqual(code, 4, label)
        # the unmutated answer passes, so the cases above fail for their own reason
        self.server.responses = [self.ts_ok(good)]
        self.assertEqual(self.ts_run()[0], 0)
        # non-object and missing answers keys
        for body in ([], {"model": "jev-1.13.0"}, {"model": "jev-1.13.0", "answers": []}):
            self.server.responses = [(200, {}, body)]
            self.assertEqual(self.ts_run()[0], 4, body)

    def test_typesafe_probability_tolerance(self):
        crit = {"a": "A", "b": "B"}
        self.ts_brief({"c": {"type": "choice", "instructions": "q", "criteria": crit}})
        ok = {"c": {"type": "choice", "choice": "a", "probabilities": {"a": 0.52, "b": 0.5}}}  # sum 1.02
        self.server.responses = [self.ts_ok(ok)]
        self.assertEqual(self.ts_run()[0], 0)

    def test_typesafe_401_and_422_fail_fast(self):
        self.ts_brief()
        for status in (401, 422):
            self.server.requests = []
            self.server.responses = [(status, {}, {"error": "nope"}), self.ts_ok({"p": {"type": "noul", "noul": 0.5}})]
            code, _, stderr = self.ts_run()
            self.assertEqual(code, 4)
            self.assertEqual(len(self.server.requests), 1)
            self.assertIn(str(status), stderr)
        self.assertEqual(self.sleeps, [])

    def test_typesafe_429_then_success(self):
        self.ts_brief()
        self.server.responses = [(429, {"Retry-After": "1"}, {"error": "rate"}), self.ts_ok({"p": {"type": "noul", "noul": 0.5}})]
        code, _, stderr = self.ts_run()
        self.assertEqual(code, 0, stderr)
        self.assertEqual(len(self.server.requests), 2)
        self.assertEqual(self.sleeps, [1.0])

    def test_typesafe_timeout_not_retried(self):
        self.ts_brief()
        with mock.patch.object(olympus_call._OPENER, "open", side_effect=socket.timeout("timed out")) as opener:
            code, _, stderr = self.ts_run()
        self.assertEqual(code, 4)
        self.assertEqual(opener.call_count, 1)
        self.assertIn("timed out", stderr)

    def test_typesafe_default_timeout_is_30_seconds(self):
        self.ts_brief()
        self.server.responses = [self.ts_ok({"p": {"type": "noul", "noul": 0.5}})]
        seen = []
        real_open = olympus_call._OPENER.open

        def spy(request, timeout=None):
            seen.append(timeout)
            return real_open(request, timeout=timeout)

        with mock.patch.object(olympus_call._OPENER, "open", side_effect=spy):
            self.assertEqual(self.ts_run()[0], 0)
        self.assertEqual(seen, [30.0])
        # other adapters keep 600
        os.environ["TEST_API_KEY"] = FAKE_KEY
        self.server.responses = [openai_ok()]
        seen.clear()
        config = self.write_config({"t": self.openai_target()})
        with mock.patch.object(olympus_call._OPENER, "open", side_effect=spy):
            self.assertEqual(self.run_tool("t", config)[0], 0)
        self.assertEqual(seen, [600.0])

    def test_typesafe_timeout_flag_must_be_positive(self):
        self.ts_brief()
        self.assertEqual(self.ts_run(extra=["--timeout", "0"])[0], 2)
        self.assertEqual(self.ts_run(extra=["--timeout", "-3"])[0], 2)

    def test_typesafe_redirect_not_followed(self):
        self.ts_brief()
        self.server.responses = [(302, {"Location": self.base + "/elsewhere"}, {}), self.ts_ok({"p": {"type": "noul", "noul": 0.5}})]
        code, _, _ = self.ts_run()
        self.assertEqual(code, 4)
        self.assertEqual(len(self.server.requests), 1)

    def test_typesafe_missing_key_exit3_zero_requests(self):
        self.ts_brief()
        config = self.write_config({"j": self.ts_target()})
        code, _, stderr = self.run_tool("j", config)
        self.assertEqual(code, 3)
        self.assertIn("TEST_API_KEY", stderr)
        self.assertEqual(self.server.requests, [])

    def test_typesafe_default_key_variable_is_typesafe_api_key(self):
        self.ts_brief()
        target = self.ts_target()
        del target["api_key_env"]
        os.environ.pop("TYPESAFE_API_KEY", None)
        config = self.write_config({"j": target})
        code, stdout, _ = self.run_tool("j", config, ["--dry-run"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout)["api_key_env"], "TYPESAFE_API_KEY")

    def test_typesafe_no_key_or_state_in_out_or_meta(self):
        self.ts_brief()
        self.server.responses = [self.ts_ok({"p": {"type": "noul", "noul": 0.5}})]
        code, stdout, stderr = self.ts_run()
        self.assertEqual(code, 0, stderr)
        for path in (self.out, self.out + ".meta.json"):
            content = self.read(path)
            self.assertNotIn(FAKE_KEY, content)
            self.assertNotIn("SENTINEL", content)
        self.assertNotIn("SENTINEL", stdout + stderr)

    def test_typesafe_meta_has_resolved_model_and_hashes(self):
        self.ts_brief({"b": {"type": "noul", "instructions": "q"}, "a": {"type": "noul", "instructions": "q2"}})
        self.server.responses = [self.ts_ok({"a": {"type": "noul", "noul": 0.1}, "b": {"type": "noul", "noul": 0.9}})]
        self.assertEqual(self.ts_run()[0], 0)
        meta = json.loads(self.read(self.out + ".meta.json"))
        canonical = json.dumps(self.TS_STATE, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        self.assertEqual(meta["resolved_model"], "jev-1.13.0")
        self.assertEqual(meta["provider"], "typesafe")
        self.assertEqual(meta["state_bytes"], len(canonical))
        self.assertEqual(meta["state_sha256"], hashlib.sha256(canonical).hexdigest())
        self.assertEqual(meta["question_ids"], ["a", "b"])
        self.assertEqual(meta["usage"], {"input_tokens": 296, "output_tokens": 20})

    def test_typesafe_dry_run_shows_pin_without_brief(self):
        if os.path.exists(self.brief):
            os.remove(self.brief)
        os.environ["TEST_API_KEY"] = FAKE_KEY
        config = self.write_config({"j": self.ts_target()})
        code, stdout, stderr = self.run_tool("j", config, ["--dry-run"])
        self.assertEqual(code, 0, stderr)
        info = json.loads(stdout)
        self.assertEqual(info["model_pin"], "jev-1.13.0")
        self.assertEqual(info["url"], self.base + "/v1/systemone")
        self.assertEqual(info["api_key"], "***")
        self.assertNotIn("max_tokens", info)
        self.assertEqual(info["timeout_seconds"], 30.0)
        self.assertEqual(self.server.requests, [])

    def test_api_key_env_with_a_trailing_newline_is_rejected(self):
        for name in ("TEST_API_KEY\n", "TEST_API_KEY ", "test_api_key"):
            with self.assertRaises(olympus_call.CallError):
                olympus_call.resolve_target("j", {"j": self.ts_target(api_key_env=name)})

    def test_typesafe_default_url(self):
        target = olympus_call.resolve_target("j", {"j": {"adapter": "typesafe", "model": "jev-1.13.0"}})
        self.assertEqual(target["url"], "https://api.typesafe.ai/v1/systemone")
        self.assertEqual(target["api_key_env"], "TYPESAFE_API_KEY")

    def test_typesafe_max_tokens_rejected(self):
        self.ts_brief()
        self.assertEqual(self.ts_run(extra=["--max-tokens", "100"])[0], 2)
        code, _, _ = self.ts_run({"j": self.ts_target(max_tokens=100)})
        self.assertEqual(code, 2)
        self.assertEqual(self.server.requests, [])

    def test_entrypoint_dry_run_subprocess(self):
        config = self.write_config({"t": self.openai_target()})
        env = dict(os.environ, TEST_API_KEY=FAKE_KEY)
        open(self.path("none.env"), "w").close()
        proc = subprocess.run(
            [sys.executable, os.path.join(HERE, "olympus_call.py"), "--target", "t", "--brief", self.brief,
             "--out", self.out, "--config", config, "--env", self.path("none.env"), "--dry-run"],
            capture_output=True, text=True, env=env, timeout=30,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["api_key"], "***")
        self.assertNotIn(FAKE_KEY, proc.stdout + proc.stderr)


if __name__ == "__main__":
    unittest.main()
