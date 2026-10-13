#!/usr/bin/env python3
"""Call one non-host model for an Olympus seat, judge or reviewer.

Reads a brief (a text file), sends it to the target named on the command line,
and writes the model's text to --out. Standard library only, Python >= 3.9.

    python3 tools/olympus_call.py --target kimi --brief brief.md --out out.md

Targets live in a JSON config (default: olympus.providers.json, copy it from
olympus.providers.example.json). Each target resolves to:

    {"adapter": "openai" | "anthropic" | "cli",
     "model": "...",            # required for API adapters (typesafe: a pinned jev-X.Y.Z)
     "provider": "...",         # optional label; for the openai adapter one of
                                #   openai | openrouter | moonshot selects a default base_url and key
     "base_url": "...",         # optional override (Ollama, vLLM, DeepSeek, Groq, Together, ...)
     "api_key_env": "...",      # name of the env var that holds the key (must match [A-Z0-9_]+_API_KEY)
     "command": [...],          # cli adapter only: argv list, brief goes on stdin
     "max_tokens": 8192,        # optional
     "max_tokens_param": "..."} # optional, openai adapter: "max_tokens" or "max_completion_tokens"

The "typesafe" adapter calls Jev (TypeSafe System One). The brief file is JSON with exactly the
keys "state" and "questions"; --out receives the validated response JSON. The model must be a
pinned version (jev-<major>.<minor>.<patch>); aliases such as jev-latest are rejected.

Exit codes: 0 ok, 2 config/usage error, 3 missing or malformed key, 4 provider error
after retries (or an unexpected error), 5 CLI command failed.

--config and --env default to files in the repository root (the parent of tools/), never
the current directory, so a hostile working directory cannot substitute them. Paths given
explicitly are used as given. A request that times out is not retried (the provider may
still be generating and billing); pass --retry-timeouts to opt in.

Secrets: keys are read from the process environment first, then from the --env
file. A key is never printed, logged, or written to --out or --out.meta.json,
and error messages are scrubbed of anything that looks like one.
"""

from __future__ import annotations

import argparse
import datetime
import email.utils
import hashlib
import http.client
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_MISSING_KEY = 3
EXIT_PROVIDER = 4
EXIT_CLI = 5

DEFAULT_CONFIG = "olympus.providers.json"
DEFAULT_ENV = ".env"
DEFAULT_MAX_TOKENS = 8192
DEFAULT_TIMEOUT = 600.0
TYPESAFE_TIMEOUT = 30.0
MAX_ATTEMPTS = 3
BACKOFF_BASE = 1.0  # seconds; attempt n waits BACKOFF_BASE * 2**(n-1) unless Retry-After is sent
RETRY_AFTER_CAP = 60.0

# Default endpoints and key variables for named OpenAI-compatible providers.
# Sources (checked 2026-10-13):
#   openai      https://api.openai.com/v1/chat/completions  (Bearer auth)
#               https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create
#   openrouter  https://openrouter.ai/api/v1/chat/completions (Bearer auth)
#               https://openrouter.ai/docs/quickstart
#   moonshot    https://api.moonshot.ai/v1  + /chat/completions (Bearer auth)
#               https://platform.kimi.ai/docs/guide/start-using-kimi-api
# Custom endpoints (set "base_url"): Ollama documents http://localhost:11434/v1/ and ignores
# the key (https://docs.ollama.com/api/openai-compatibility); vLLM, DeepSeek, Groq, Together
# and others expose the same /chat/completions shape under their own base URL.
OPENAI_PROVIDERS = {
    "openai": {"base_url": "https://api.openai.com/v1", "api_key_env": "OPENAI_API_KEY"},
    "openrouter": {"base_url": "https://openrouter.ai/api/v1", "api_key_env": "OPENROUTER_API_KEY"},
    "moonshot": {"base_url": "https://api.moonshot.ai/v1", "api_key_env": "MOONSHOT_API_KEY"},
}

# Anthropic Messages API: POST {base_url}/v1/messages with headers x-api-key and
# anthropic-version, body {model, max_tokens, messages}; the reply carries a "content" list of
# blocks and a "usage" object. https://platform.claude.com/docs/en/api/messages
ANTHROPIC_DEFAULT_BASE_URL = "https://api.anthropic.com"
ANTHROPIC_DEFAULT_KEY_ENV = "ANTHROPIC_API_KEY"
ANTHROPIC_VERSION = "2023-06-01"

# TypeSafe System One (Jev): POST https://api.typesafe.ai/v1/systemone, Bearer auth, body
# {model, state, questions}; the answer is {model, answers: {id: {type, noul | choice, probabilities,
# confidence | score, legend, probabilities}}, usage: {input_tokens, output_tokens}}. Question
# limits: choice up to 255 options, score 2 to 10 levels. Pricing, the 64k-token request limit and
# the jev-latest / jev-preview aliases are on https://docs.typesafe.ai/models.md; the contract is
# https://docs.typesafe.ai/api.md (checked 2026-10-13). Aliases are never accepted here: they
# move silently, and calibration is only valid for one resolved model.
TYPESAFE_BASE_URL = "https://api.typesafe.ai"
TYPESAFE_PATH = "/v1/systemone"
TYPESAFE_KEY_ENV = "TYPESAFE_API_KEY"
TYPESAFE_MODEL_RE = re.compile(r"^jev-[0-9]+\.[0-9]+\.[0-9]+$")
TYPESAFE_MAX_BRIEF_BYTES = 100000  # proxy for the 32k-token state limit
TYPESAFE_QUESTION_TYPES = ("noul", "choice", "score")
TYPESAFE_PROB_TOLERANCE = 0.05

KEY_ENV_RE = re.compile(r"^[A-Z0-9_]+_API_KEY$")
ERROR_BODY_READ_LIMIT = 65536
ERROR_TEXT_LIMIT = 500

ADAPTERS = ("openai", "anthropic", "cli", "typesafe")
LOOPBACK_HOSTS = ("localhost", "127.0.0.1", "::1")

_SECRET_HEADER_RE = re.compile(
    r"(?i)(authorization|x-api-key|api-key|proxy-authorization)(\s*[:=]\s*)(bearer\s+)?[^\s,;\"'}]+"
)
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{6,}")


class CallError(Exception):
    """A failure that maps to a specific exit code."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


# --------------------------------------------------------------------------- helpers


def _sleep(seconds):
    time.sleep(seconds)


def redact(text, secrets=()):
    """Remove secret values and secret-looking header lines from text."""
    text = str(text)
    variants = []
    for secret in secrets:
        if secret and len(secret) >= 4:
            # The bare value, the stripped value, and the repr() form that Python puts in exceptions.
            variants.extend([secret, secret.strip(), repr(secret)[1:-1]])
    for variant in sorted(set(v for v in variants if len(v) >= 4), key=len, reverse=True):
        text = text.replace(variant, "***")
    text = _SECRET_HEADER_RE.sub(lambda m: m.group(1) + m.group(2) + "***", text)
    text = _BEARER_RE.sub("Bearer ***", text)
    return text


def parse_env_file(path):
    """Parse a simple KEY=VALUE file. Ignores blank lines and # comments."""
    values = {}
    with open(path, "r", encoding="utf-8") as handle:
        for raw in handle:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export "):].lstrip()
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", key):
                continue
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            else:
                value = re.split(r"\s+#", value, maxsplit=1)[0].rstrip()
            values[key] = value
    return values


def lookup_key(name, env_values):
    """Process environment first, then the .env file. Empty counts as missing.

    Surrounding whitespace is stripped (a trailing newline from a secret manager is common).
    Anything else that is not printable ASCII is rejected, naming only the variable.
    """
    if not name:
        return None
    value = os.environ.get(name)
    if not value or not value.strip():
        value = env_values.get(name)
    if not value or not value.strip():
        return None
    value = value.strip()
    if any(ch.isspace() or not ch.isprintable() or not ch.isascii() for ch in value):
        raise CallError(
            EXIT_MISSING_KEY,
            "API key in %s contains whitespace or non-printable characters; fix the value" % name,
        )
    return value


def load_config(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        raise CallError(
            EXIT_USAGE,
            "config not found: %s (copy olympus.providers.example.json to olympus.providers.json)" % path,
        )
    except (OSError, ValueError) as exc:
        raise CallError(EXIT_USAGE, "cannot read config %s: %s" % (path, exc))
    targets = data.get("targets") if isinstance(data, dict) else None
    if not isinstance(targets, dict):
        raise CallError(EXIT_USAGE, 'config %s must be an object with a "targets" object' % path)
    return targets


def resolve_target(name, targets, max_tokens_override=None):
    """Turn a config entry into a validated, fully defaulted target dict."""
    if name not in targets:
        known = ", ".join(sorted(k for k in targets if not k.startswith("_"))) or "(none)"
        raise CallError(EXIT_USAGE, "unknown target %r; known targets: %s" % (name, known))
    raw = targets[name]
    if not isinstance(raw, dict):
        raise CallError(EXIT_USAGE, "target %r must be an object" % name)
    adapter = raw.get("adapter")
    if adapter not in ADAPTERS:
        raise CallError(EXIT_USAGE, "target %r: adapter must be one of %s" % (name, ", ".join(ADAPTERS)))

    resolved = {
        "name": name,
        "adapter": adapter,
        "provider": raw.get("provider") or {"anthropic": "anthropic", "typesafe": "typesafe"}.get(adapter, name),
        "model": raw.get("model"),
        "max_tokens": max_tokens_override or raw.get("max_tokens") or DEFAULT_MAX_TOKENS,
    }
    if adapter == "typesafe":
        if max_tokens_override is not None or raw.get("max_tokens") is not None:
            raise CallError(EXIT_USAGE, "target %r: the typesafe adapter has no max_tokens (output is typed and short)" % name)
        resolved["max_tokens"] = None
    elif not isinstance(resolved["max_tokens"], int) or resolved["max_tokens"] <= 0:
        raise CallError(EXIT_USAGE, "target %r: max_tokens must be a positive integer" % name)

    if adapter == "cli":
        command = raw.get("command")
        if (
            not isinstance(command, list)
            or not command
            or not all(isinstance(part, str) for part in command)
        ):
            raise CallError(EXIT_USAGE, 'target %r: "command" must be a non-empty list of strings' % name)
        model = resolved["model"] or ""
        resolved["command"] = [part.replace("{model}", model) for part in command]
        return resolved

    if not resolved["model"] or not isinstance(resolved["model"], str):
        raise CallError(EXIT_USAGE, 'target %r: "model" is required for the %s adapter' % (name, adapter))

    if adapter == "typesafe" and not TYPESAFE_MODEL_RE.fullmatch(resolved["model"]):
        raise CallError(
            EXIT_USAGE,
            "target %r: model must be a pinned version matching jev-<major>.<minor>.<patch>; aliases are never accepted" % name,
        )

    base_url = raw.get("base_url")
    key_env = raw.get("api_key_env")
    if adapter == "typesafe":
        base_url = base_url or TYPESAFE_BASE_URL
        key_env = key_env or TYPESAFE_KEY_ENV
        resolved["key_required"] = True
        resolved["path"] = TYPESAFE_PATH
    elif adapter == "anthropic":
        base_url = base_url or ANTHROPIC_DEFAULT_BASE_URL
        key_env = key_env or ANTHROPIC_DEFAULT_KEY_ENV
        resolved["key_required"] = True
        resolved["path"] = "/v1/messages"
    else:
        preset = OPENAI_PROVIDERS.get(resolved["provider"], {})
        base_url = base_url or preset.get("base_url")
        if not base_url:
            raise CallError(
                EXIT_USAGE,
                'target %r: set "base_url", or set "provider" to one of %s'
                % (name, ", ".join(sorted(OPENAI_PROVIDERS))),
            )
        key_env = key_env or preset.get("api_key_env")
        # A target with no key variable (for example local Ollama) sends no Authorization header.
        resolved["key_required"] = bool(key_env)
        resolved["path"] = "/chat/completions"
        param = raw.get("max_tokens_param")
        if param is None:
            host = urllib.parse.urlsplit(base_url).hostname or ""
            param = "max_completion_tokens" if host == "api.openai.com" else "max_tokens"
        if param not in ("max_tokens", "max_completion_tokens"):
            raise CallError(EXIT_USAGE, 'target %r: max_tokens_param must be "max_tokens" or "max_completion_tokens"' % name)
        resolved["max_tokens_param"] = param

    if key_env is not None and (not isinstance(key_env, str) or not KEY_ENV_RE.fullmatch(key_env)):
        raise CallError(EXIT_USAGE, "target %r: api_key_env must match [A-Z0-9_]+_API_KEY" % name)
    try:
        parts = urllib.parse.urlsplit(base_url)
        hostname = parts.hostname
    except ValueError:
        raise CallError(EXIT_USAGE, "target %r: base_url is not a valid http(s) URL" % name)
    if "@" in parts.netloc:
        # Never echo the URL: it would print the credentials.
        raise CallError(EXIT_USAGE, "target %r: base_url must not contain credentials (user:pass@); use api_key_env" % name)
    if parts.scheme not in ("http", "https") or not hostname:
        raise CallError(EXIT_USAGE, "target %r: base_url must be an http(s) URL" % name)
    if parts.scheme == "http" and hostname not in LOOPBACK_HOSTS and key_env:
        raise CallError(
            EXIT_USAGE,
            "target %r: refusing to send an API key over plain http to %s; use https" % (name, hostname),
        )
    resolved["base_url"] = base_url.rstrip("/")
    resolved["api_key_env"] = key_env
    resolved["url"] = resolved["base_url"] + resolved["path"]
    return resolved


# --------------------------------------------------------------------------- HTTP


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never follow redirects: urllib would forward the auth header to the new host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def _parse_retry_after(value):
    if not value:
        return None
    value = value.strip()
    try:
        seconds = float(value)
    except ValueError:
        try:
            when = email.utils.parsedate_to_datetime(value)
            seconds = (when - datetime.datetime.now(when.tzinfo)).total_seconds()
        except (TypeError, ValueError):
            return None
    return max(0.0, min(seconds, RETRY_AFTER_CAP))


def _is_timeout(exc):
    if isinstance(exc, urllib.error.URLError) and not isinstance(exc, urllib.error.HTTPError):
        exc = exc.reason
    return isinstance(exc, (socket.timeout, TimeoutError))


def http_post_json(url, headers, payload, timeout, secrets, retry_timeouts=False):
    """POST JSON with retries. Returns (parsed_json, attempts)."""
    body = json.dumps(payload).encode("utf-8")
    last_error = "no attempt made"
    for attempt in range(1, MAX_ATTEMPTS + 1):
        request = urllib.request.Request(url, data=body, method="POST")
        request.add_header("Content-Type", "application/json")
        request.add_header("Accept", "application/json")
        request.add_header("User-Agent", "olympus-call/1")
        for name, value in headers.items():
            request.add_header(name, value)
        retry_after = None
        try:
            with _OPENER.open(request, timeout=timeout) as response:
                raw = response.read()
            try:
                return json.loads(raw.decode("utf-8")), attempt
            except ValueError:
                raise CallError(EXIT_PROVIDER, "provider returned a non-JSON body")
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read(ERROR_BODY_READ_LIMIT).decode("utf-8", "replace")
            except Exception:  # noqa: BLE001 - best effort only
                pass
            # Redact the whole text first, then truncate, so a key cannot straddle the cut.
            last_error = redact("HTTP %s %s %s" % (exc.code, exc.reason, detail), secrets).strip()[:ERROR_TEXT_LIMIT]
            retriable = exc.code == 429 or exc.code >= 500
            if not retriable:
                raise CallError(EXIT_PROVIDER, "provider error, not retried: " + last_error)
            retry_after = _parse_retry_after(exc.headers.get("Retry-After") if exc.headers else None)
        except (urllib.error.URLError, http.client.HTTPException, OSError) as exc:
            if _is_timeout(exc):
                last_error = "request timed out after %ss" % timeout
                if not retry_timeouts:
                    raise CallError(
                        EXIT_PROVIDER,
                        "%s; not retried because the provider may still be generating (use --retry-timeouts to retry)"
                        % last_error,
                    )
            else:
                reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
                last_error = redact("connection error: %s: %s" % (type(exc).__name__, reason), secrets)[:ERROR_TEXT_LIMIT]
        if attempt < MAX_ATTEMPTS:
            delay = retry_after if retry_after is not None else BACKOFF_BASE * (2 ** (attempt - 1))
            _sleep(delay)
    raise CallError(EXIT_PROVIDER, "provider failed after %d attempts: %s" % (MAX_ATTEMPTS, last_error))


# --------------------------------------------------------------------------- adapters


def _text_from_openai(data):
    try:
        choice = data["choices"][0]
        content = choice["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise CallError(EXIT_PROVIDER, "unexpected response shape (no choices[0].message.content)")
    if isinstance(content, list):  # some servers return content parts
        content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
    if not isinstance(content, str) or not content.strip():
        reason = choice.get("finish_reason") if isinstance(choice, dict) else None
        raise CallError(EXIT_PROVIDER, "empty response (finish_reason=%s); raise --max-tokens?" % reason)
    return content


def _text_from_anthropic(data):
    blocks = data.get("content") if isinstance(data, dict) else None
    if not isinstance(blocks, list):
        raise CallError(EXIT_PROVIDER, "unexpected response shape (no content list)")
    text = "".join(b.get("text", "") for b in blocks if isinstance(b, dict) and b.get("type") == "text")
    if not text.strip():
        raise CallError(EXIT_PROVIDER, "empty response (stop_reason=%s); raise --max-tokens?" % data.get("stop_reason"))
    return text


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value == value


def _nonempty(value):
    if isinstance(value, str):
        return bool(value.strip())
    return isinstance(value, (dict, list)) and len(value) > 0


def validate_typesafe_brief(brief_text):
    """Validate a typesafe brief. Returns {"state": ..., "questions": ...}; raises exit 2."""
    if len(brief_text.encode("utf-8")) > TYPESAFE_MAX_BRIEF_BYTES:
        raise CallError(EXIT_USAGE, "brief is larger than %d bytes (the state limit is 32k tokens)" % TYPESAFE_MAX_BRIEF_BYTES)
    try:
        brief = json.loads(brief_text)
    except ValueError:
        raise CallError(EXIT_USAGE, "typesafe brief must be JSON with the keys state and questions")
    if not isinstance(brief, dict) or set(brief) != {"state", "questions"}:
        raise CallError(EXIT_USAGE, 'typesafe brief must have exactly the keys "state" and "questions"')
    if not _nonempty(brief["state"]):
        raise CallError(EXIT_USAGE, "typesafe brief: state must be a non-empty string, object or array")
    questions = brief["questions"]
    if not isinstance(questions, dict) or not questions:
        raise CallError(EXIT_USAGE, "typesafe brief: questions must be a non-empty object")
    for qid, question in questions.items():
        where = "question %r" % qid
        if not qid or not isinstance(question, dict):
            raise CallError(EXIT_USAGE, "typesafe brief: %s must be an object with a non-empty id" % where)
        if set(question) - {"type", "instructions", "criteria"}:
            raise CallError(EXIT_USAGE, "typesafe brief: %s has unknown keys" % where)
        qtype = question.get("type")
        if qtype not in TYPESAFE_QUESTION_TYPES:
            raise CallError(EXIT_USAGE, "typesafe brief: %s type must be one of %s" % (where, ", ".join(TYPESAFE_QUESTION_TYPES)))
        if not _nonempty(question.get("instructions")):
            raise CallError(EXIT_USAGE, "typesafe brief: %s needs non-empty instructions" % where)
        criteria = question.get("criteria")
        if qtype == "noul":
            if criteria is not None and (not isinstance(criteria, dict) or not set(criteria) <= {"true", "false"}):
                raise CallError(EXIT_USAGE, 'typesafe brief: %s criteria may only have "true" and "false"' % where)
        elif qtype == "choice":
            if not isinstance(criteria, dict) or not 1 <= len(criteria) <= 255:
                raise CallError(EXIT_USAGE, "typesafe brief: %s needs 1 to 255 criteria" % where)
        else:
            if not isinstance(criteria, list) or not 2 <= len(criteria) <= 10:
                raise CallError(EXIT_USAGE, "typesafe brief: %s needs a criteria list of 2 to 10 levels" % where)
    return brief


def _canonical_state(state):
    return json.dumps(state, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _check_probabilities(probs, allowed, where):
    if not isinstance(probs, dict) or not probs:
        raise CallError(EXIT_PROVIDER, "malformed answer: %s has no probabilities" % where)
    total = 0.0
    for name, value in probs.items():
        if str(name) not in allowed or not _is_number(value) or not 0 <= value <= 1:
            raise CallError(EXIT_PROVIDER, "malformed answer: %s has an invalid probability" % where)
        total += value
    if abs(total - 1.0) > TYPESAFE_PROB_TOLERANCE:
        raise CallError(EXIT_PROVIDER, "malformed answer: %s probabilities sum to %.3f" % (where, total))


def _text_from_typesafe(data, payload):
    """Validate a System One response against the request; return (json text, extra meta)."""
    if not isinstance(data, dict):
        raise CallError(EXIT_PROVIDER, "unexpected response shape (not an object)")
    if data.get("model") != payload["model"]:
        raise CallError(
            EXIT_PROVIDER,
            "model_mismatch: requested %s, response says %s" % (payload["model"], str(data.get("model"))[:60]),
        )
    answers = data.get("answers")
    questions = payload["questions"]
    if not isinstance(answers, dict) or set(answers) != set(questions):
        raise CallError(EXIT_PROVIDER, "malformed answer: answer ids differ from the requested question ids")
    for qid, question in questions.items():
        answer = answers[qid]
        where = "answer %r" % qid
        if not isinstance(answer, dict) or answer.get("type") != question["type"]:
            raise CallError(EXIT_PROVIDER, "malformed answer: %s has the wrong type" % where)
        if question["type"] == "noul":
            value = answer.get("noul")
            if not _is_number(value) or not 0 <= value <= 1:
                raise CallError(EXIT_PROVIDER, "malformed answer: %s noul is not a number in [0, 1]" % where)
        elif question["type"] == "choice":
            criteria = question["criteria"]
            if answer.get("choice") not in criteria:
                raise CallError(EXIT_PROVIDER, "malformed answer: %s choice is not one of the criteria" % where)
            _check_probabilities(answer.get("probabilities"), set(criteria), where)
        else:
            levels = question["criteria"]
            value = answer.get("score")
            if not _is_number(value) or not 0 <= value <= len(levels) - 1:
                raise CallError(EXIT_PROVIDER, "malformed answer: %s score is outside the levels" % where)
            expected = {str(i): level for i, level in enumerate(levels)}
            if answer.get("legend") != expected:
                raise CallError(EXIT_PROVIDER, "malformed answer: %s legend does not match the criteria" % where)
            _check_probabilities(answer.get("probabilities"), set(expected), where)
    state_bytes = _canonical_state(payload["state"])
    extra = {
        "resolved_model": data["model"],
        "state_bytes": len(state_bytes),
        "state_sha256": hashlib.sha256(state_bytes).hexdigest(),
        "question_ids": sorted(questions),
    }
    return json.dumps(data, indent=2, sort_keys=True) + "\n", extra


def build_request(target, brief_text, key):
    """Return (url, headers, payload) for an API adapter."""
    headers = {}
    if target["adapter"] == "typesafe":
        brief = validate_typesafe_brief(brief_text)
        headers["Authorization"] = "Bearer " + key
        payload = {"model": target["model"], "state": brief["state"], "questions": brief["questions"]}
    elif target["adapter"] == "anthropic":
        headers["x-api-key"] = key
        headers["anthropic-version"] = ANTHROPIC_VERSION
        payload = {
            "model": target["model"],
            "max_tokens": target["max_tokens"],
            "messages": [{"role": "user", "content": brief_text}],
        }
    else:
        if key:
            headers["Authorization"] = "Bearer " + key
        payload = {
            "model": target["model"],
            target["max_tokens_param"]: target["max_tokens"],
            "messages": [{"role": "user", "content": brief_text}],
        }
    return target["url"], headers, payload


def call_api(target, brief_text, key, timeout, retry_timeouts=False):
    """Returns (text, usage, attempts, extra meta)."""
    url, headers, payload = build_request(target, brief_text, key)
    data, attempts = http_post_json(url, headers, payload, timeout, [key], retry_timeouts)
    extra = {}
    if target["adapter"] == "typesafe":
        text, extra = _text_from_typesafe(data, payload)
    elif target["adapter"] == "anthropic":
        text = _text_from_anthropic(data)
    else:
        text = _text_from_openai(data)
    usage = data.get("usage") if isinstance(data, dict) and isinstance(data.get("usage"), dict) else None
    return text, usage, attempts, extra


def call_cli(target, brief_text, timeout, secrets):
    command = target["command"]
    try:
        proc = subprocess.run(
            command,
            input=brief_text.encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            shell=False,
        )
    except FileNotFoundError:
        raise CallError(EXIT_CLI, "command not found: %s" % command[0])
    except subprocess.TimeoutExpired:
        raise CallError(EXIT_CLI, "command timed out after %ss: %s" % (timeout, command[0]))
    except OSError as exc:
        raise CallError(EXIT_CLI, "cannot run %s: %s" % (command[0], exc))
    if proc.returncode != 0:
        stderr = redact(proc.stderr.decode("utf-8", "replace"), secrets).strip()[-ERROR_TEXT_LIMIT:]
        raise CallError(EXIT_CLI, "command %s exited %d: %s" % (command[0], proc.returncode, stderr))
    text = proc.stdout.decode("utf-8", "replace")
    if not text.strip():
        raise CallError(EXIT_CLI, "command %s produced no output" % command[0])
    return text


# --------------------------------------------------------------------------- CLI


def _utc_now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_text(path, text):
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)


def describe_dry_run(target, key, config_path, env_path, env_found):
    info = {
        "config_path": config_path,
        "env_path": env_path,
        "env_file_found": env_found,
        "target": target["name"],
        "adapter": target["adapter"],
        "provider": target["provider"],
        "model": target["model"],
    }
    if target["max_tokens"] is not None:
        info["max_tokens"] = target["max_tokens"]
    if target["adapter"] == "typesafe":
        info["model_pin"] = target["model"]
        info["model_pin_pattern"] = TYPESAFE_MODEL_RE.pattern
        info["timeout_seconds"] = TYPESAFE_TIMEOUT
    if target["adapter"] == "cli":
        info["command"] = target["command"]
    else:
        info["url"] = target["url"]
        if target["adapter"] == "openai":
            info["max_tokens_param"] = target["max_tokens_param"]
        elif target["adapter"] == "anthropic":
            info["max_tokens_param"] = "max_tokens"
        info["api_key_env"] = target["api_key_env"]
        if not target["key_required"]:
            info["api_key"] = "not required"
        else:
            info["api_key"] = "***" if key else "MISSING"
    return info


def build_parser():
    parser = argparse.ArgumentParser(
        prog="olympus_call.py",
        description="Send an Olympus brief to one configured model and write its answer to a file.",
    )
    parser.add_argument("--target", required=True, help="target name from the providers config")
    parser.add_argument("--brief", required=True, help="file whose text is sent as the prompt")
    parser.add_argument("--out", required=True, help="file that receives the model text (and <out>.meta.json)")
    parser.add_argument("--config", default=None, help="providers config (default: %s in the repository root, never the cwd)" % DEFAULT_CONFIG)
    parser.add_argument("--env", default=None, help="optional KEY=VALUE file with API keys (default: %s in the repository root, never the cwd)" % DEFAULT_ENV)
    parser.add_argument("--max-tokens", type=int, default=None, help="output token cap (default: config value or %d)" % DEFAULT_MAX_TOKENS)
    parser.add_argument("--timeout", type=float, default=None, help="per-request timeout in seconds (default: %d, typesafe: %d)" % (DEFAULT_TIMEOUT, TYPESAFE_TIMEOUT))
    parser.add_argument("--retry-timeouts", action="store_true", help="also retry requests that time out (default: no, a hung provider may still bill)")
    parser.add_argument("--dry-run", action="store_true", help="print the resolved target and URL, send nothing")
    return parser


def run(args, secrets):
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if args.max_tokens is not None and args.max_tokens <= 0:
        raise CallError(EXIT_USAGE, "--max-tokens must be positive")
    if args.timeout is not None and args.timeout <= 0:
        raise CallError(EXIT_USAGE, "--timeout must be positive")

    config_path = args.config if args.config else os.path.join(repo_root, DEFAULT_CONFIG)
    targets = load_config(config_path)
    target = resolve_target(args.target, targets, args.max_tokens)
    timeout = args.timeout
    if timeout is None:
        timeout = TYPESAFE_TIMEOUT if target["adapter"] == "typesafe" else DEFAULT_TIMEOUT

    env_values = {}
    env_path = args.env if args.env else os.path.join(repo_root, DEFAULT_ENV)
    env_found = os.path.isfile(env_path)
    if env_found:
        try:
            env_values = parse_env_file(env_path)
        except OSError as exc:
            raise CallError(EXIT_USAGE, "cannot read env file %s: %s" % (env_path, exc))
    elif args.env:
        raise CallError(EXIT_USAGE, "env file not found: %s" % args.env)

    key = None
    if target["adapter"] != "cli":
        key = lookup_key(target["api_key_env"], env_values)
        if key:
            secrets.append(key)

    if args.dry_run:
        print(json.dumps(describe_dry_run(target, key, config_path, env_path, env_found), indent=2))
        return EXIT_OK

    if target["adapter"] != "cli" and target["key_required"] and not key:
        raise CallError(
            EXIT_MISSING_KEY,
            "missing API key: set %s in the environment or in the env file" % target["api_key_env"],
        )

    try:
        with open(args.brief, "r", encoding="utf-8") as handle:
            brief_text = handle.read()
    except (OSError, UnicodeDecodeError) as exc:
        raise CallError(EXIT_USAGE, "cannot read brief %s: %s" % (args.brief, exc))
    if not brief_text.strip():
        raise CallError(EXIT_USAGE, "brief %s is empty" % args.brief)

    started = time.monotonic()
    usage = None
    attempts = 1
    extra = {}
    if target["adapter"] == "cli":
        text = call_cli(target, brief_text, timeout, [])
    else:
        text, usage, attempts, extra = call_api(target, brief_text, key, timeout, args.retry_timeouts)
    latency = round(time.monotonic() - started, 3)

    meta = {
        "target": target["name"],
        "provider": target["provider"],
        "adapter": target["adapter"],
        "model": target["model"],
        "latency_seconds": latency,
        "attempts": attempts,
        "usage": usage,
        "timestamp": _utc_now(),
        "brief_bytes": len(brief_text.encode("utf-8")),
        "output_bytes": len(text.encode("utf-8")),
    }
    if target["adapter"] == "cli":
        meta["command"] = target["command"][0]
    else:
        meta["base_url"] = target["base_url"]
    meta.update(extra)  # typesafe: resolved_model, state_bytes, state_sha256, question_ids (never state text)
    _write_text(args.out, text)
    _write_text(args.out + ".meta.json", json.dumps(meta, indent=2) + "\n")
    print("ok: %s via %s/%s -> %s (%.1fs)" % (target["name"], target["adapter"], target["model"] or "-", args.out, latency))
    return EXIT_OK


def main(argv=None):
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:  # argparse exits 2 on usage errors already
        return exc.code if isinstance(exc.code, int) else EXIT_USAGE
    secrets = []
    try:
        return run(args, secrets)
    except CallError as exc:
        print("olympus_call: error: %s" % exc.message, file=sys.stderr)
        return exc.code
    except Exception as exc:  # noqa: BLE001 - catch-all: never a traceback, never a key
        print(
            "olympus_call: unexpected %s: %s" % (type(exc).__name__, redact(str(exc), secrets)),
            file=sys.stderr,
        )
        return EXIT_PROVIDER


if __name__ == "__main__":
    sys.exit(main())
