#!/usr/bin/env python3
"""Jev steps for Olympus: the J1 Arena verdict meta-check, the ledger and the seeded-defect labels.

Standard library only, Python >= 3.9. Rules and the ladder (OFF, SHADOW, ADVISORY) are in
references/jev.md; this file is the mechanism. Jev is advisory: it may add scrutiny and never
removes any, so every failure here ends in `jev: skipped (...)` or `P = UNAVAILABLE` and exit 0.
Exit 2 is reserved for refusals the President must fix (no ruling file, a changed question hash,
a bad commitment).

Subcommands
  j1             ask "is this verdict correct?" for every non-tie verdict, in both candidate orders
  seeded-commit  record sha256(key bytes || 16-byte nonce) for a seed pair (key on stdin)
  seeded-run     copy one seed pair under fresh random codes to a new /var/tmp directory
  seeded-void    void a seeded cycle (lost-key returns the pair to the pool, leak retires it)
  label          seeded | l1 | l3 | audit : attach correctness labels to ledger rows
  verdicts       list a natural run's labellable verdicts (never shows P)
  skips          count the logged skips by reason and by scan pattern

The store is one per install, outside every repo: ${OLYMPUS_JEV_DIR:-~/.local/state/olympus/jev}.
It holds ledger.jsonl (text-free rows), seeds/ (the seed pack), seeds-key/ (written by the
President after the ruling) and seed-runs/ (the code-to-seed map, never inside a judge directory).
"""

from __future__ import annotations

import argparse
import datetime
import glob
import hashlib
import hmac
import json
import os
import re
import secrets as secrets_module
import string
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import olympus_call  # noqa: E402

EXIT_OK = 0
EXIT_REFUSED = 2

DEFAULT_RUNNER = os.path.join(HERE, "olympus_call.py")
QUESTIONS_PATH = os.path.join(REPO_ROOT, "references", "jev-questions.json")
CANARY_PATH = os.path.join(REPO_ROOT, "references", "jev-canary.json")
CALIBRATION_PATH = os.path.join(REPO_ROOT, "references", "jev-calibration.json")
JEV_DOC_PATH = os.path.join(REPO_ROOT, "references", "jev.md")

DEFAULT_TARGET = "jev"
DEFAULT_MAX_CALLS = 30  # 24 for a 4-candidate Arena (6 pairs x 2 verdicts x 2 orders) + 3 canary + slack (param)
DEFAULT_CALL_WALL = 90.0  # seconds of wall clock per call (param)
RUNNER_TIMEOUT = 30.0
BREAKER_FAILURES = 2
INSTABILITY_THRESHOLD = 0.20  # |P_AB - P_BA| above this flags the pair (param)
EXTRA_JUDGMENT_CAP = 3  # flagged pairs per Arena that get an extra blind judgment (param)
MIN_SECRET_LENGTH = 16
LEAK_PATTERN = re.compile(r"jev/seeds|answer.key|seeds-key|seeded")
AUTHOR_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$")
PAIR_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
KEY_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]{0,63}):([ab])$")

# Pre-send scan: a tripwire, not a filter. The owner-editable copy is the `scan-patterns` block in
# references/jev.md (a test keeps the two identical). Each pattern is logged by NAME, never by match.
DEFAULT_SCAN_PATTERNS = (
    ("sk-key", r"\bsk-[A-Za-z0-9_-]{20,}"),
    ("jwt", r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
    ("bearer-token", r"Bearer\s+[A-Za-z0-9._~+/=-]{20,}"),
    ("private-key", r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    ("supabase-host", r"[a-z0-9]{20}\.supabase\.co"),
    ("aws-key-id", r"\bAKIA[0-9A-Z]{16}\b"),
    ("github-token", r"\bghp_\w{30,}"),
    ("github-pat", r"\bgithub_pat_\w{30,}"),
    ("slack-token", r"\bxox[abp]-[\w-]{10,}"),
    ("db-url-password", r"postgres(?:ql)?://[^\s:@]+:[^\s@]+@"),
    ("domain-word", r"\b(?:RLS|IBAN|SWIFT|KYC|(?i:tenants?|payouts?))\b"),
)


class Refused(Exception):
    """A refusal the President must fix; exit 2."""


# --------------------------------------------------------------------------- small helpers


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sha256_hex(data):
    return hashlib.sha256(data).hexdigest()


def word_count(text):
    return len(text.split())


def store_dir(arg=None):
    path = arg or os.environ.get("OLYMPUS_JEV_DIR") or os.path.join(os.path.expanduser("~"), ".local", "state", "olympus", "jev")
    return path


def ensure_dir(path, mode=0o700):
    os.makedirs(path, mode=mode, exist_ok=True)
    return path


def read_text(path):
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def write_private(path, text):
    ensure_dir(os.path.dirname(path))
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)


def fresh_codes(count, rng=None):
    rng = rng or secrets_module.SystemRandom()
    codes = set()
    while len(codes) < count:
        codes.add("".join(rng.choice(string.ascii_uppercase) for _ in range(3)))
    codes = list(codes)
    rng.shuffle(codes)
    return codes


# --------------------------------------------------------------------------- questions and header


def load_questions(path=QUESTIONS_PATH):
    """Return the active wording after verifying every frozen hash."""
    try:
        data = json.loads(read_text(path))
    except (OSError, ValueError) as exc:
        raise Refused("cannot read %s: %s" % (path, exc))
    wordings = data.get("wordings")
    if not isinstance(wordings, dict) or not wordings:
        raise Refused("%s has no wordings" % path)
    for name, wording in wordings.items():
        if sha256_hex(canonical(wording.get("questions"))) != wording.get("sha256"):
            raise Refused("question wording %s no longer matches its sha256: wordings are frozen, add a new one" % name)
    active = data.get("active")
    if active not in wordings:
        raise Refused("%s: active wording %r is not defined" % (path, active))
    return {
        "name": active,
        "questions": wordings[active]["questions"],
        "sha256": wordings[active]["sha256"],
        "question_id": data.get("question_id", "p"),
    }


def parse_header(path):
    """Read `jev:`, `jev-content:`, `kind:` and `run:` from the first lines of a DECISION header."""
    header = {"jev": "off", "jev_content": "blocked", "kind": "natural", "run": None}
    try:
        lines = read_text(path).splitlines()[:80]
    except OSError:
        return header
    seen = set()
    for line in lines:
        match = re.match(r"^\s*(?:[-*]\s*)?(jev-content|jev|kind|run)\s*:\s*(\S+)", line, re.IGNORECASE)
        if match:
            key = match.group(1).lower().replace("-", "_")
            if key not in seen:  # the first occurrence wins
                seen.add(key)
                header[key] = match.group(2).strip().strip("`").lower() if key != "run" else match.group(2).strip()
    return header


# --------------------------------------------------------------------------- pre-send scan


def load_scan_patterns(doc_path=JEV_DOC_PATH):
    """Patterns from the `scan-patterns` block of references/jev.md; built-ins if the file is absent."""
    try:
        text = read_text(doc_path)
    except OSError:
        return list(DEFAULT_SCAN_PATTERNS)
    match = re.search(r"```scan-patterns\n(.*?)```", text, re.DOTALL)
    if not match:
        return list(DEFAULT_SCAN_PATTERNS)
    patterns = []
    for line in match.group(1).splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, sep, regex = line.partition("::")
        if not sep:
            raise Refused("%s: bad scan pattern line %r (use name :: regex)" % (doc_path, name[:30]))
        patterns.append((name.strip(), regex.strip()))
    return patterns or list(DEFAULT_SCAN_PATTERNS)


def collect_secrets(env_path=None):
    """Every *_API_KEY value in the process environment and the env file (a missing file is fine)."""
    found = {}
    if env_path and os.path.isfile(env_path):
        try:
            for name, value in olympus_call.parse_env_file(env_path).items():
                if name.endswith("_API_KEY"):
                    found[name] = value
        except OSError:
            pass
    for name, value in os.environ.items():
        if name.endswith("_API_KEY"):
            found[name] = value
    return {name: value.strip() for name, value in found.items() if len(value.strip()) >= MIN_SECRET_LENGTH}


def iter_strings(value):
    """Every string inside a nested value (keys included), raw, so a JSON escape cannot hide a line start."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            for text in iter_strings(key):
                yield text
            for text in iter_strings(item):
                yield text
    elif isinstance(value, (list, tuple)):
        for item in value:
            for text in iter_strings(item):
                yield text


def presend_scan(texts, secret_values=None, patterns=None):
    """Return the names of the patterns that hit. Never returns or logs the matched text."""
    patterns = patterns if patterns is not None else load_scan_patterns()
    hits = []
    for name, regex in patterns:
        compiled = re.compile(regex)
        if any(compiled.search(text) for text in texts):
            hits.append(name)
    for name, value in sorted((secret_values or {}).items()):
        if len(value) >= MIN_SECRET_LENGTH and any(value in text for text in texts):
            hits.append("literal:" + name)
    return hits


# --------------------------------------------------------------------------- verdicts and states


def parse_verdicts(paths):
    """Parse `PAIR: P-xxx (A) vs P-yyy (B)` blocks with their WINNER from judging-<judge>.md files."""
    verdicts = []
    for path in paths:
        judge = re.sub(r"^judging-", "", os.path.splitext(os.path.basename(path))[0])
        for block in re.split(r"(?=^\W*PAIR:)", read_text(path), flags=re.MULTILINE)[1:]:
            pair = re.search(r"PAIR:\W*(?:[A-Za-z]-)?(\w{3})\W*\(A\)\s*vs\s*(?:[A-Za-z]-)?(\w{3})\W*\(B\)", block)
            winner = re.search(r"WINNER:\W*(A|B|TIE|ABSTAIN)\b", block)
            if not pair or not winner or winner.group(1) == "ABSTAIN":
                continue
            code_a, code_b = pair.groups()
            verdicts.append({
                "judge": judge,
                "code_a": code_a,
                "code_b": code_b,
                "winner": winner.group(1),
                "text": block.strip(),
            })
    return verdicts


def swap_verdict(text):
    """Relabel a verdict block as if the candidates had been shown in the other order.

    Only the structured lines move (PAIR, SCORES A/B, WINNER); free text is left as written.
    """
    out = []
    for line in text.splitlines():
        pair = re.match(r"^(\W*PAIR:\W*)((?:[A-Za-z]-)?\w{3})(\W*)\(A\)(\s*vs\s*)((?:[A-Za-z]-)?\w{3})(\W*)\(B\)(.*)$", line)
        if pair:
            g = pair.groups()
            line = "%s%s%s(A)%s%s%s(B)%s" % (g[0], g[4], g[2], g[3], g[1], g[5], g[6])
        else:
            scores = re.match(r"^(\W*SCORES )([AB])(:.*)$", line)
            if scores:
                line = scores.group(1) + ("B" if scores.group(2) == "A" else "A") + scores.group(3)
            else:
                winner = re.match(r"^(\W*WINNER:\W*)(A|B)\b(.*)$", line)
                if winner:
                    line = winner.group(1) + ("B" if winner.group(2) == "A" else "A") + winner.group(3)
        out.append(line)
    return "\n".join(out)


def build_states(rubric, verdict_text, candidate_a, candidate_b):
    """The two states for one verdict: as judged, and with the candidates (and the verdict) swapped."""
    as_judged = {"rubric": rubric, "verdict": verdict_text, "candidate_A": candidate_a, "candidate_B": candidate_b}
    swapped = {"rubric": rubric, "verdict": swap_verdict(verdict_text), "candidate_A": candidate_b, "candidate_B": candidate_a}
    return as_judged, swapped


def find_candidate(blind_dir, code):
    matches = sorted(glob.glob(os.path.join(blind_dir, "*-%s.md" % code)))
    if len(matches) != 1:
        raise Refused("expected one blind file for code %s in %s, found %d" % (code, blind_dir, len(matches)))
    return read_text(matches[0])


# --------------------------------------------------------------------------- ledger


def ledger_path(store):
    return os.path.join(store, "ledger.jsonl")


def append_rows(store, rows):
    ensure_dir(store)
    path = ledger_path(store)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")


def read_events(store):
    path = ledger_path(store)
    if not os.path.isfile(path):
        return []
    events = []
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                try:
                    events.append(json.loads(line))
                except ValueError:
                    continue
    return events


def merged_verdicts(events):
    """Verdict rows with the latest label event merged in; superseded and void runs dropped."""
    superseded = {e["run"] for e in events if e.get("type") == "supersede"}
    rows = {}
    for event in events:
        if event.get("type") == "verdict" and event["run"] not in superseded:
            row = dict(event)
            row.setdefault("label", None)
            row.setdefault("label_source", None)
            rows[(row["run"], row["verdict_id"])] = row
    for event in events:
        if event.get("type") == "label":
            row = rows.get((event["run"], event["verdict_id"]))
            if row is not None:
                row["label"] = event["label"]
                row["label_source"] = event["source"]
    return list(rows.values())


def pair_state(events, pair_id):
    """none | committed | used | retired (lost-key voids return a pair to `none`)."""
    state = "none"
    for event in events:
        if event.get("pair_id") != pair_id:
            continue
        kind = event.get("type")
        if kind == "commitment" and state in ("none", "committed"):
            state = "committed"
        elif kind == "seed_run" and state == "committed":
            state = "used"
        elif kind == "seed_void":
            state = "none" if event.get("reason") == "lost-key" else "retired"
    return state


def latest_commitment(events, pair_id):
    found = None
    for event in events:
        if event.get("type") == "commitment" and event.get("pair_id") == pair_id:
            found = event
        elif event.get("type") == "seed_void" and event.get("pair_id") == pair_id:
            found = None
    return found


# --------------------------------------------------------------------------- calling the runner


class Caller:
    """Serial calls to tools/olympus_call.py with a breaker and a call cap."""

    def __init__(self, workdir, runner, target, config, env_file, max_calls, wall, breaker_file):
        self.workdir = workdir
        self.runner = runner
        self.target = target
        self.config = config
        self.env_file = env_file
        self.max_calls = max_calls
        self.wall = wall
        self.breaker_file = breaker_file
        self.calls = 0
        self.consecutive_failures = 0
        self.resolved_model = None
        ensure_dir(os.path.join(workdir, "calls"))

    @property
    def breaker_open(self):
        return os.path.exists(self.breaker_file)

    def ask(self, label, state, questions, question_id):
        """Returns {"ok": bool, "p": float|None, "note": str|None, "tokens_in", "tokens_out", "latency"}."""
        if self.breaker_open:
            return {"ok": False, "note": "breaker"}
        if self.calls >= self.max_calls:
            return {"ok": False, "note": "call_cap"}
        self.calls += 1
        brief = os.path.join(self.workdir, "calls", label + ".brief.json")
        out = os.path.join(self.workdir, "calls", label + ".out.json")
        write_private(brief, json.dumps({"state": state, "questions": questions}))
        argv = [sys.executable, self.runner, "--target", self.target, "--brief", brief, "--out", out,
                "--timeout", str(RUNNER_TIMEOUT)]
        if self.config:
            argv += ["--config", self.config]
        if self.env_file:
            argv += ["--env", self.env_file]
        note = None
        try:
            proc = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=self.wall, shell=False)
            if proc.returncode != 0:
                stderr = proc.stderr.decode("utf-8", "replace")
                note = "model_mismatch" if "model_mismatch" in stderr else "exit%d" % proc.returncode
        except subprocess.TimeoutExpired:
            note = "wall_timeout"
        except OSError:
            note = "runner_missing"
        result = {"ok": False, "p": None, "note": note, "tokens_in": 0, "tokens_out": 0, "latency": None}
        if note is None:
            try:
                answer = json.loads(read_text(out))["answers"][question_id]["noul"]
                if isinstance(answer, bool) or not isinstance(answer, (int, float)) or not 0 <= answer <= 1:
                    raise ValueError("bad noul")
                result.update(ok=True, p=float(answer))
                meta = json.loads(read_text(out + ".meta.json"))
                self.resolved_model = meta.get("resolved_model") or self.resolved_model
                usage = meta.get("usage") or {}
                result["tokens_in"] = int(usage.get("input_tokens", 0) or 0)
                result["tokens_out"] = int(usage.get("output_tokens", 0) or 0)
                result["latency"] = meta.get("latency_seconds")
            except (OSError, ValueError, KeyError, TypeError):
                result["note"] = "bad_output"
        if result["ok"]:
            self.consecutive_failures = 0
        else:
            self.consecutive_failures += 1
            if self.consecutive_failures >= BREAKER_FAILURES and not self.breaker_open:
                write_private(self.breaker_file, "open %s after %d consecutive failures\n" % (utc_now(), self.consecutive_failures))
        return result


# --------------------------------------------------------------------------- J1


def parse_ruling(path):
    """The ruling file must exist and carry a `timestamp:` line; returns (sha256, timestamp, base code or None).

    The ruling should also carry a `base: <code>` line (the base choice, by blind code); natural-run labels
    need it to know which verdicts involve the base candidate.
    """
    try:
        data = open(path, "rb").read()
    except OSError:
        raise Refused("refused: %s does not exist. Write the ruling (ranking and base choice, by blind code) with a `timestamp:` line first." % path)
    text = data.decode("utf-8", "replace")
    match = re.search(r"^\s*timestamp\s*:\s*(\S+)\s*$", text, re.MULTILINE | re.IGNORECASE)
    if not match:
        raise Refused("refused: %s has no `timestamp: <ISO 8601>` line" % path)
    stamp = match.group(1)
    try:
        datetime.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        raise Refused("refused: the timestamp in %s is not ISO 8601" % path)
    base = re.search(r"^\s*(?:[-*]\s*)?base\s*:\s*(?:[A-Za-z]-)?(\w{3})\s*$", text, re.MULTILINE | re.IGNORECASE)
    return sha256_hex(data), stamp, (base.group(1) if base else None)


def calibration_entry(path, resolved_model, question_sha256):
    try:
        data = json.loads(read_text(path))
    except (OSError, ValueError):
        return None
    for entry in data.get("entries", []):
        if entry.get("resolved_model") == resolved_model and entry.get("question_sha256") == question_sha256:
            return entry
    return None


def _p(row):
    return row.get("p")


def flag_pairs(rows, action_threshold=None, cap=EXTRA_JUDGMENT_CAP):
    """Per-pair ADVISORY flags. A pair with any `na` or tie verdict is never flagged."""
    by_pair = {}
    for row in rows:
        by_pair.setdefault(frozenset((row["code_a"], row["code_b"])), []).append(row)
    flagged = []
    for codes, group in by_pair.items():
        if any(r.get("na") or r.get("tie") for r in group):
            continue
        deltas = [r["delta"] for r in group if r.get("delta") is not None]
        unstable = any(d > INSTABILITY_THRESHOLD for d in deltas)
        agree = len(group) >= 2 and len({r["winner_code"] for r in group}) == 1
        low = (
            action_threshold is not None and agree and min(r["p"] for r in group) < action_threshold
        )
        if unstable or low:
            flagged.append({
                "pair": "|".join(sorted(codes)),
                "reason": "+".join(x for x, on in (("unstable", unstable), ("low_p", low)) if on),
                "max_delta": max(deltas) if deltas else 0.0,
                "min_p": min(r["p"] for r in group),
            })
    flagged.sort(key=lambda f: (-f["max_delta"], f["min_p"], f["pair"]))
    return flagged[:cap]


def log_skip(store, run, reason, patterns=None, kind="natural"):
    append_rows(store, [{"type": "skip", "run": run, "kind": kind, "reason": reason, "patterns": patterns or [], "ts": utc_now()}])
    print("jev: skipped (%s)" % reason)


def new_run_id():
    return "run-%s-%s" % (datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S"), secrets_module.token_hex(2))


def cmd_j1(args):
    store = store_dir(args.store)
    header = parse_header(args.decision)
    kind = args.kind or header["kind"]
    if kind not in ("natural", "seeded"):
        raise Refused("kind must be natural or seeded")
    run_id = args.run_id or header["run"] or new_run_id()
    edir = os.path.join(args.e, "jev")
    ensure_dir(edir)
    wording = load_questions(args.questions)
    qid = wording["question_id"]

    # ---- skip list (log and continue; Jev never blocks the flow)
    if header["jev"] not in ("shadow", "advisory"):
        return log_skip(store, run_id, "jev off", kind=kind) or EXIT_OK
    if header["jev_content"] != "ok":
        return log_skip(store, run_id, "jev-content not ok", kind=kind) or EXIT_OK
    if (args.tier or "").upper() == "S":
        return log_skip(store, run_id, "tier S", kind=kind) or EXIT_OK
    config_path = args.config or os.path.join(REPO_ROOT, olympus_call.DEFAULT_CONFIG)
    env_path = args.env or os.path.join(REPO_ROOT, olympus_call.DEFAULT_ENV)
    try:
        targets = olympus_call.load_config(config_path)
        target = olympus_call.resolve_target(args.target, targets)
    except olympus_call.CallError:
        return log_skip(store, run_id, "no jev target", kind=kind) or EXIT_OK
    env_values = {}
    if os.path.isfile(env_path):
        try:
            env_values = olympus_call.parse_env_file(env_path)
        except OSError:
            env_values = {}
    try:
        key = olympus_call.lookup_key(target["api_key_env"], env_values)
    except olympus_call.CallError:
        key = None
    if not key:
        return log_skip(store, run_id, "no key", kind=kind) or EXIT_OK
    breaker_file = os.path.join(edir, ".open")
    if os.path.exists(breaker_file):
        return log_skip(store, run_id, "breaker open", kind=kind) or EXIT_OK

    # ---- ruling first: P is never computed before the ruling is on record
    ruling_sha, ruling_ts, base_code = parse_ruling(os.path.join(edir, "ruling.md"))

    rubric = read_text(args.rubric)
    verdicts = parse_verdicts(args.verdicts)
    if not verdicts:
        raise Refused("no verdict blocks found in the given files")
    prepared = []
    ties = 0
    for v in verdicts:
        v["id"] = "%s:%s-%s" % (v["judge"], v["code_a"], v["code_b"])
        cand_a = find_candidate(args.blind, v["code_a"])
        cand_b = find_candidate(args.blind, v["code_b"])
        v["words_a"], v["words_b"] = word_count(cand_a), word_count(cand_b)
        v["tie"] = v["winner"] == "TIE"
        if v["tie"]:
            ties += 1
            if kind == "natural":
                continue  # ties are excluded from J1 and natural-run labels
            v["states"] = None
        else:
            v["states"] = build_states(rubric, v["text"], cand_a, cand_b)
        prepared.append(v)

    # ---- pre-send scan over everything that would leave the machine
    # Raw strings, not json.dumps output: a JSON "\n" or "\t" would hide a token at a line start from `\b`.
    texts = list(iter_strings(wording["questions"]))
    for v in prepared:
        if v["states"]:
            for state in v["states"]:
                texts.extend(iter_strings(state))
    canary = None
    if args.canary and os.path.isfile(args.canary):
        canary = json.loads(read_text(args.canary))
        for item in canary.get("states", []):
            texts.extend(iter_strings(item["state"]))
    hits = presend_scan(texts, collect_secrets(env_path), load_scan_patterns(args.scan_doc))
    if hits:
        return log_skip(store, run_id, "scan", patterns=hits, kind=kind) or EXIT_OK

    # ---- seeded runs carry their pair id from the seed-run record
    seed_pair = None
    if kind == "seeded":
        record_path = os.path.join(store, "seed-runs", run_id + ".json")
        if not os.path.isfile(record_path):
            raise Refused("refused: no seed-run record for %s (run `seeded-run` first)" % run_id)
        record = json.loads(read_text(record_path))
        seed_pair = record["pair_id"]
        for v in prepared:
            if v["code_a"] not in record["codes"] or v["code_b"] not in record["codes"]:
                raise Refused("refused: verdict %s names codes that do not belong to seed run %s" % (v["id"], run_id))

    caller = Caller(edir, args.runner or DEFAULT_RUNNER, args.target, args.config, args.env, args.max_calls, args.call_wall, breaker_file)
    questions = wording["questions"]

    # ---- canary: frozen synthetic states, re-asked at every Jev-enabled Arena start
    canary_report = run_canary(canary, caller, questions, qid, wording["sha256"], store) if canary else None

    level = header["jev"]
    demoted_path = os.path.join(store, "demoted.json")
    demoted = os.path.isfile(demoted_path)
    if canary_report and canary_report.get("drift"):
        demoted = True

    # ---- both orders for every non-tie verdict
    rows = []
    for v in prepared:
        row = {
            "type": "verdict", "run": run_id, "kind": kind, "verdict_id": v["id"],
            "pair_id": seed_pair or "%s:%s" % (run_id, "-".join(sorted((v["code_a"], v["code_b"])))),
            "code_a": v["code_a"], "code_b": v["code_b"], "winner": v["winner"],
            "winner_code": {"A": v["code_a"], "B": v["code_b"]}.get(v["winner"]),
            "tie": v["tie"], "p_ab": None, "p_ba": None, "p": None, "delta": None,
            "na": False, "na_reason": None, "wording": wording["name"], "question_sha256": wording["sha256"],
            "state_sha256_ab": None, "state_sha256_ba": None, "words_a": v["words_a"], "words_b": v["words_b"],
            "ruling_sha256": ruling_sha, "ruling_ts": ruling_ts, "base_code": base_code, "resolved_model": None,
            "tokens_in": 0, "tokens_out": 0, "latency_s": 0.0, "ts": utc_now(), "label": None, "label_source": None,
        }
        if v["tie"]:
            row["na"], row["na_reason"] = True, "tie"
            rows.append(row)
            continue
        results = []
        for order, state in zip(("ab", "ba"), v["states"]):
            row["state_sha256_" + order] = sha256_hex(canonical(state))
            result = caller.ask("%s-%s" % (re.sub(r"[^A-Za-z0-9_.-]", "_", v["id"]), order), state, questions, qid)
            results.append(result)
            row["tokens_in"] += result.get("tokens_in", 0)
            row["tokens_out"] += result.get("tokens_out", 0)
            row["latency_s"] = round(row["latency_s"] + (result.get("latency") or 0.0), 3)
            if not result["ok"]:
                break  # either order missing makes the verdict `na`; do not spend the second call
        if len(results) == 2 and all(r["ok"] for r in results):
            row["p_ab"], row["p_ba"] = results[0]["p"], results[1]["p"]
            row["p"] = round((row["p_ab"] + row["p_ba"]) / 2.0, 6)
            row["delta"] = round(abs(row["p_ab"] - row["p_ba"]), 6)
        else:
            failed = next(r for r in results if not r["ok"])
            row["na"], row["na_reason"] = True, failed.get("note") or "unavailable"
        row["resolved_model"] = caller.resolved_model
        rows.append(row)

    # ---- effective level, flags
    entry = calibration_entry(args.calibration, caller.resolved_model, wording["sha256"]) if caller.resolved_model else None
    if level == "advisory" and (entry is None or demoted):
        level = "shadow"
    threshold = entry.get("action_threshold") if entry else None
    flagged = flag_pairs([r for r in rows if r["type"] == "verdict"], threshold)
    na_count = sum(1 for r in rows if r["na"] and not r["tie"])
    sent = sum(1 for r in rows if not r["tie"])
    summary = {
        "run": run_id, "kind": kind, "level": level, "wording": wording["name"], "question_sha256": wording["sha256"],
        "resolved_model": caller.resolved_model, "calls": caller.calls, "verdicts": len(prepared), "ties": ties,
        "na": na_count, "breaker_open": caller.breaker_open, "canary": canary_report,
        "flagged": flagged, "acted": level == "advisory", "ruling_sha256": ruling_sha, "ts": utc_now(),
    }
    write_private(os.path.join(edir, "run.json"), json.dumps(summary, indent=2) + "\n")
    write_private(os.path.join(edir, "flags.json"), json.dumps({"level": level, "acted": level == "advisory", "cap": EXTRA_JUDGMENT_CAP, "flagged": flagged}, indent=2) + "\n")
    events = list(rows)
    if args.supersedes:
        events.insert(0, {"type": "supersede", "run": args.supersedes, "by": run_id, "ts": utc_now()})
    append_rows(store, events)

    # stdout never shows P. SHADOW logs flags only; ADVISORY lists the pair codes that get an extra judgment.
    print("jev: %s run=%s calls=%d verdicts=%d ties=%d na=%d%s" % (level, run_id, caller.calls, len(prepared), ties, na_count,
                                                                  " breaker=open" if caller.breaker_open else ""))
    if level == "advisory" and flagged:
        print("jev: flagged pairs for one extra blind judgment: " + ", ".join(f["pair"] for f in flagged))
    if sent and na_count / float(sent) > 0.10:
        print("jev: more than 10%% of verdicts are na; rerun before using this run for calibration (--supersedes %s)" % run_id)
    return EXIT_OK


def run_canary(canary, caller, questions, qid, question_sha, store):
    """Ask the frozen synthetic states; compare with baselines when they are set."""
    baseline_for = canary.get("baseline_recorded_for") or {}
    tolerance = float(canary.get("tolerance", 0.05))
    report = {"asked": 0, "unavailable": 0, "drift": False, "baseline": False, "answers": {}}
    drift_reasons = []
    for item in canary.get("states", []):
        result = caller.ask("canary-" + item["id"], item["state"], questions, qid)
        report["asked"] += 1
        if not result["ok"]:
            report["unavailable"] += 1
            continue
        report["answers"][item["id"]] = result["p"]
        if item.get("baseline") is not None:
            report["baseline"] = True
            if abs(result["p"] - float(item["baseline"])) > tolerance:
                drift_reasons.append("drift:%s" % item["id"])
    if report["baseline"]:
        if baseline_for.get("resolved_model") and caller.resolved_model and baseline_for["resolved_model"] != caller.resolved_model:
            drift_reasons.append("pin changed")
        if baseline_for.get("question_sha256") and baseline_for["question_sha256"] != question_sha:
            drift_reasons.append("question hash changed")
    if drift_reasons:
        report["drift"] = True
        report["reasons"] = drift_reasons
        write_private(os.path.join(store, "demoted.json"), json.dumps({"level": "shadow", "reasons": drift_reasons, "ts": utc_now()}) + "\n")
    return report


# --------------------------------------------------------------------------- seeded workflow


def read_key_from_stdin():
    return sys.stdin.read().strip()


def make_commitment(key, nonce_hex):
    """sha256 over the key's UTF-8 bytes followed by the 16 raw nonce bytes, as lowercase hex."""
    nonce = bytes.fromhex(nonce_hex)
    if len(nonce) != 16:
        raise Refused("the nonce must be 16 bytes (32 hex characters)")
    return sha256_hex(key.encode("utf-8") + nonce)


def cmd_seeded_commit(args):
    store = store_dir(args.store)
    if not PAIR_ID_RE.match(args.pair_id):
        raise Refused("bad pair id")
    if not AUTHOR_RE.match(args.author) or args.author.lower() == "unknown":
        raise Refused("--author must name the authoring seat (letters, digits, . _ -), not `unknown`")
    key = read_key_from_stdin()
    match = KEY_RE.match(key)
    if not match or match.group(1) != args.pair_id:
        raise Refused("the key must read `<pair-id>:a` or `<pair-id>:b` and name the same pair")
    seed_dir = os.path.join(store, "seeds", args.pair_id)
    for name in ("a.md", "b.md"):
        if not os.path.isfile(os.path.join(seed_dir, name)):
            raise Refused("seed pair %s is missing %s in the store" % (args.pair_id, name))
    events = read_events(store)
    state = pair_state(events, args.pair_id)
    if state in ("used", "retired"):
        raise Refused("pair %s is %s; each pair is used once" % (args.pair_id, state))
    nonce = secrets_module.token_hex(16)  # 128 random bits
    append_rows(store, [{"type": "commitment", "pair_id": args.pair_id, "author": args.author,
                         "commitment": make_commitment(key, nonce), "ts": utc_now()}])
    print("nonce: " + nonce)
    print("Keep the key and this nonce in context only. Never write them to a file before the ruling.")
    return EXIT_OK


def cmd_seeded_run(args):
    store = store_dir(args.store)
    if not PAIR_ID_RE.match(args.pair_id):
        raise Refused("bad pair id")
    events = read_events(store)
    if pair_state(events, args.pair_id) != "committed":
        raise Refused("refused: pair %s has no open commitment in the ledger (commit first; a used or retired pair stays out)" % args.pair_id)
    seed_dir = os.path.join(store, "seeds", args.pair_id)
    texts = {}
    for name in ("a", "b"):
        try:
            texts[name] = read_text(os.path.join(seed_dir, name + ".md"))
        except OSError:
            raise Refused("seed pair %s is missing %s.md" % (args.pair_id, name))
    hits = presend_scan(list(texts.values()), collect_secrets(args.env), load_scan_patterns(args.scan_doc))
    if hits:
        raise Refused("seed text hits the pre-send scan (%s); rewrite the seed without those terms" % ", ".join(hits))
    run_id = new_run_id()
    base = args.base or "/var/tmp"
    # The judge directory name and tree must not reveal the Arena is seeded: only blind/ goes in it.
    out_dir = os.path.join(base, "olympus-arena-" + secrets_module.token_hex(6))
    ensure_dir(os.path.join(out_dir, "blind"))
    codes = fresh_codes(2)
    mapping = {}
    for code, name in zip(codes, ("a", "b")):
        text = texts[name]
        write_private(os.path.join(out_dir, "blind", "P-%s.md" % code),
                      "<!-- blind P-%s; %d words -->\n%s" % (code, word_count(text), text))
        mapping[code] = name
    # The run header: seeded Arenas have no DECISION.md, so the run carries its own consent lines. It lives
    # in the store, never next to blind/, so a judge reading the directory cannot see `kind: seeded`.
    header_path = os.path.join(store, "seed-runs", run_id + ".header.md")
    write_private(header_path, "jev: shadow\njev-content: ok\nkind: seeded\nrun: %s\n" % run_id)
    write_private(os.path.join(store, "seed-runs", run_id + ".json"),
                  json.dumps({"run": run_id, "pair_id": args.pair_id, "codes": mapping, "ts": utc_now()}, indent=2) + "\n")
    append_rows(store, [{"type": "seed_run", "pair_id": args.pair_id, "run": run_id, "ts": utc_now()}])
    print("seeded run: %s" % run_id)
    print("directory: %s" % out_dir)
    print("header: %s" % header_path)
    print("codes: %s" % ", ".join(sorted(mapping)))
    return EXIT_OK


def cmd_seeded_void(args):
    store = store_dir(args.store)
    record = os.path.join(store, "seed-runs", args.run + ".json")
    if not os.path.isfile(record):
        raise Refused("no such seed run")
    pair_id = json.loads(read_text(record))["pair_id"]
    append_rows(store, [{"type": "seed_void", "pair_id": pair_id, "run": args.run, "reason": args.reason, "ts": utc_now()},
                        {"type": "supersede", "run": args.run, "by": "void:" + args.reason, "ts": utc_now()}])
    print("void: run %s (%s); pair %s %s" % (args.run, args.reason, pair_id,
                                            "returns to the pool (commit again)" if args.reason == "lost-key" else "is retired"))
    return EXIT_OK


def leak_hits(paths):
    hits = []
    for path in paths:
        files = []
        if os.path.isdir(path):
            for root, _, names in os.walk(path):
                files.extend(os.path.join(root, n) for n in names)
        else:
            files.append(path)
        for name in files:
            try:
                for number, line in enumerate(read_text(name).splitlines(), 1):
                    if LEAK_PATTERN.search(line):
                        hits.append("%s:%d" % (name, number))
            except (OSError, UnicodeDecodeError):
                continue
    return hits


def cmd_label_seeded(args):
    store = store_dir(args.store)
    events = read_events(store)
    record_path = os.path.join(store, "seed-runs", args.run + ".json")
    if not os.path.isfile(record_path):
        raise Refused("no such seed run")
    record = json.loads(read_text(record_path))
    pair_id = record["pair_id"]
    if pair_state(events, pair_id) != "used":
        raise Refused("pair %s is not in the used state (void or retired)" % pair_id)
    edir = os.path.join(args.e, "jev")
    parse_ruling(os.path.join(edir, "ruling.md"))
    for path in args.verdicts:
        if not os.path.isfile(path):
            raise Refused("refused: verdict file %s does not exist yet; the key is revealed only after every verdict is saved" % path)
    leaks = leak_hits(args.judge_outputs)
    if leaks:
        append_rows(store, [{"type": "seed_void", "pair_id": pair_id, "run": args.run, "reason": "leak", "ts": utc_now()},
                            {"type": "supersede", "run": args.run, "by": "void:leak", "ts": utc_now()}])
        raise Refused("cycle void: the judge outputs mention the seed or key paths (%s); pair %s is retired" % (", ".join(leaks[:5]), pair_id))
    key_path = os.path.join(store, "seeds-key", pair_id + ".json")
    try:
        revealed = json.loads(read_text(key_path))
        key, nonce = revealed["key"], revealed["nonce"]
        digest = make_commitment(key, nonce)
    except (OSError, ValueError, KeyError, TypeError):
        raise Refused("refused: no readable key file at seeds-key/%s.json ({key, nonce}); re-request the key from the authoring seat" % pair_id)
    commitment = latest_commitment(events, pair_id)
    if commitment is None or not hmac.compare_digest(digest, commitment["commitment"]):
        raise Refused("commitment mismatch: the key and nonce do not match the ledger. Re-request them from the authoring seat, or void the cycle as lost-key")
    match = KEY_RE.match(key)
    if not match or match.group(1) != pair_id:
        raise Refused("the key does not name this pair")
    defective_code = next(code for code, name in record["codes"].items() if name == match.group(2))
    labels = []
    wrong = 0
    rows = [r for r in merged_verdicts(events) if r["run"] == args.run]
    if not rows:
        raise Refused("no verdict rows for run %s (J1 was skipped or has not run)" % args.run)
    for row in rows:
        if row["code_a"] not in record["codes"] or row["code_b"] not in record["codes"]:
            raise Refused("refused: verdict %s names codes that do not belong to seed run %s" % (row["verdict_id"], args.run))
    for row in rows:
        # correct iff the verdict picked the defect-free proposal; a TIE is wrong
        label = "correct" if row["winner_code"] and row["winner_code"] != defective_code else "wrong"
        wrong += label == "wrong"
        labels.append({"type": "label", "run": args.run, "verdict_id": row["verdict_id"], "label": label,
                       "source": "seeded", "ts": utc_now()})
    append_rows(store, labels)
    os.remove(key_path)
    print("labelled %d verdicts for run %s (%d wrong); key file removed" % (len(labels), args.run, wrong))
    return EXIT_OK


def cmd_label_natural(args, source):
    """Natural-run labels are for demotion only and are deliberately narrow.

    L1 and L3 can only say `wrong`; `correct` comes only from a blinded two-judge audit (`audit`); a clean
    outcome is simply unlabelled. Only verdicts that involve the base candidate can be labelled, and this
    command never reads or prints P.
    """
    store = store_dir(args.store)
    rows = [r for r in merged_verdicts(read_events(store)) if r["run"] == args.run and r["verdict_id"] == args.verdict_id]
    if not rows:
        raise Refused("no such verdict row")
    row = rows[0]
    if row["kind"] != "natural":
        raise Refused("seeded runs are labelled only by `label seeded`")
    if row.get("tie"):
        raise Refused("ties are excluded from natural-run labels")
    if source in ("L1", "L3") and args.label != "wrong":
        raise Refused("%s can only record `wrong`; `correct` comes only from a blinded two-judge audit (label audit), and a clean outcome stays unlabelled" % source)
    base = row.get("base_code")
    if not base:
        raise Refused("this run's ruling.md recorded no `base: <code>` line, so no verdict can be tied to the base candidate")
    if base not in (row["code_a"], row["code_b"]):
        raise Refused("only verdicts involving the base candidate are labelled; this one does not")
    append_rows(store, [{"type": "label", "run": args.run, "verdict_id": args.verdict_id, "label": args.label,
                         "source": source, "ts": utc_now()}])
    print("labelled %s as %s (%s)" % (args.verdict_id, args.label, source))
    return EXIT_OK


def cmd_verdicts(args):
    """List a natural run's labellable verdicts (ids, codes, winner, base involvement), never P."""
    store = store_dir(args.store)
    rows = [r for r in merged_verdicts(read_events(store)) if r["run"] == args.run and r["kind"] == "natural" and not r.get("tie")]
    base = rows[0].get("base_code") if rows else None
    print("run %s base %s" % (args.run, base or "none recorded"))
    for row in rows:
        print("%s %s|%s winner=%s base_involved=%s labelled=%s" % (
            row["verdict_id"], row["code_a"], row["code_b"], row["winner_code"],
            "yes" if base and base in (row["code_a"], row["code_b"]) else "no", row.get("label") or "no"))
    return EXIT_OK


def cmd_skips(args):
    events = read_events(store_dir(args.store))
    skips = [e for e in events if e.get("type") == "skip"]
    by_reason = {}
    by_pattern = {}
    for skip in skips:
        by_reason[skip["reason"]] = by_reason.get(skip["reason"], 0) + 1
        for name in skip.get("patterns", []):
            by_pattern[name] = by_pattern.get(name, 0) + 1
    runs = len({e["run"] for e in events if e.get("type") == "verdict"})
    print("skips: %d; runs with verdict rows: %d" % (len(skips), runs))
    for reason, count in sorted(by_reason.items()):
        print("  %s: %d" % (reason, count))
    for name, count in sorted(by_pattern.items()):
        print("  scan pattern %s: %d" % (name, count))
    return EXIT_OK


# --------------------------------------------------------------------------- CLI


def build_parser():
    parser = argparse.ArgumentParser(prog="jev_steps.py", description="Jev steps for Olympus (advisory, optional).")
    sub = parser.add_subparsers(dest="command", required=True)

    j1 = sub.add_parser("j1", help="meta-check every Arena verdict in both candidate orders")
    j1.add_argument("--e", required=True, help="the panel's $E directory (ruling.md must be in <e>/jev/)")
    j1.add_argument("--blind", required=True, help="directory with the blind candidate files <prefix>-<code>.md")
    j1.add_argument("--verdicts", nargs="+", required=True, help="judging-<judge>.md files")
    j1.add_argument("--rubric", required=True, help="file with the rubric text")
    j1.add_argument("--decision", required=True, help="DECISION.md (or a seeded run header.md) with the jev: and jev-content: lines")
    j1.add_argument("--kind", choices=("natural", "seeded"), default=None)
    j1.add_argument("--run-id", default=None)
    j1.add_argument("--supersedes", default=None, help="run id this run replaces (rerun after over 10%% na)")
    j1.add_argument("--tier", default=None, help="S skips Jev")
    j1.add_argument("--target", default=DEFAULT_TARGET)
    j1.add_argument("--config", default=None)
    j1.add_argument("--env", default=None)
    j1.add_argument("--store", default=None)
    j1.add_argument("--runner", default=None)
    j1.add_argument("--questions", default=QUESTIONS_PATH)
    j1.add_argument("--canary", default=CANARY_PATH)
    j1.add_argument("--calibration", default=CALIBRATION_PATH)
    j1.add_argument("--scan-doc", default=JEV_DOC_PATH)
    j1.add_argument("--max-calls", type=int, default=DEFAULT_MAX_CALLS)
    j1.add_argument("--call-wall", type=float, default=DEFAULT_CALL_WALL)

    commit = sub.add_parser("seeded-commit", help="commit sha256(key || nonce) for a seed pair; the key is read from stdin")
    commit.add_argument("--pair-id", required=True)
    commit.add_argument("--author", required=True, help="authoring seat (the promotion rule needs 2 distinct authors)")
    commit.add_argument("--store", default=None)

    run = sub.add_parser("seeded-run", help="copy one committed seed pair under fresh codes to a new /var/tmp directory")
    run.add_argument("--pair-id", required=True)
    run.add_argument("--store", default=None)
    run.add_argument("--base", default=None, help="parent directory for the new run directory (default /var/tmp)")
    run.add_argument("--env", default=None)
    run.add_argument("--scan-doc", default=JEV_DOC_PATH)

    void = sub.add_parser("seeded-void", help="void a seeded cycle")
    void.add_argument("--run", required=True)
    void.add_argument("--reason", choices=("lost-key", "leak"), required=True)
    void.add_argument("--store", default=None)

    label = sub.add_parser("label", help="attach labels")
    label_sub = label.add_subparsers(dest="source", required=True)
    seeded = label_sub.add_parser("seeded", help="verify the commitment, then label a seeded run")
    seeded.add_argument("--run", required=True)
    seeded.add_argument("--e", required=True)
    seeded.add_argument("--verdicts", nargs="+", required=True, help="the verdict files that must exist before the reveal")
    seeded.add_argument("--judge-outputs", nargs="+", required=True, help="files or directories the judges wrote (grepped for leaks)")
    seeded.add_argument("--store", default=None)
    for name in ("l1", "l3", "audit"):
        natural = label_sub.add_parser(name, help="natural-run label (%s; l1 and l3 only record `wrong`, audit may record either)" % name)
        natural.add_argument("--run", required=True)
        natural.add_argument("--verdict-id", required=True)
        natural.add_argument("--label", choices=("correct", "wrong"), required=True)
        natural.add_argument("--store", default=None)

    listing = sub.add_parser("verdicts", help="list a natural run's labellable verdicts (never shows P)")
    listing.add_argument("--run", required=True)
    listing.add_argument("--store", default=None)

    skips = sub.add_parser("skips", help="count logged skips by reason and scan pattern")
    skips.add_argument("--store", default=None)
    return parser


def main(argv=None):
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else EXIT_REFUSED
    try:
        if args.command == "j1":
            return cmd_j1(args)
        if args.command == "seeded-commit":
            return cmd_seeded_commit(args)
        if args.command == "seeded-run":
            return cmd_seeded_run(args)
        if args.command == "seeded-void":
            return cmd_seeded_void(args)
        if args.command == "label":
            if args.source == "seeded":
                return cmd_label_seeded(args)
            return cmd_label_natural(args, {"l1": "L1", "l3": "L3", "audit": "audit"}[args.source])
        if args.command == "skips":
            return cmd_skips(args)
        if args.command == "verdicts":
            return cmd_verdicts(args)
    except Refused as exc:
        print("jev_steps: %s" % exc, file=sys.stderr)
        return EXIT_REFUSED
    except Exception as exc:  # noqa: BLE001 - Jev is optional: never a traceback, never a secret
        message = olympus_call.redact(str(exc), list(collect_secrets(None).values()))
        print("jev: UNAVAILABLE (%s: %s)" % (type(exc).__name__, message), file=sys.stderr)
        return EXIT_OK if args.command == "j1" else EXIT_REFUSED
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
