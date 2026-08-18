#!/usr/bin/env python3
"""PreToolUse(Bash) hook — automatic independent review, blocking on P0.

Runs `codex exec` headlessly (non-interactive, read-only sandbox) over the
STAGED diff and BLOCKS the commit only when it returns a P0 (priority == 0)
finding. P1-P3 print as advisory and do NOT block.

This automates the "independent review before committing" rule in CLAUDE.md —
the interactive slash command can't be fired by a hook, so we shell out to the
headless equivalent.

Design contract:
- **Fail-open, never fail-closed.** Reviewer missing / unauthenticated /
  timeout / malformed output -> ALLOW (print a notice). A review tool that
  bricks every commit when the LLM is down is worse than no gate. Only a clean
  verdict carrying a P0 blocks.
- **Blocks via stdout JSON** {"decision":"block","reason":...}.
- **Staged diff only** — keeps the review small and fast.
- **Re-checks the command itself**, because the hook `if` matcher matches on a
  substring and would otherwise fire on e.g. `cat .pre-commit-config.yaml`.

Env knobs:
- CODEX_PRECOMMIT_REVIEW=0   -> disable entirely (escape hatch)
- CODEX_REVIEW_MODEL=<model> -> override the review model
- CODEX_REVIEW_PROMPT=<text> -> override the review prompt for this project
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

PROJECT_DIR = os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd())
SCHEMA = os.path.join(PROJECT_DIR, ".claude", "hooks", "codex-review-schema.json")

DEFAULT_PROMPT = (
    "Review ONLY the staged changes in this repository (git diff --cached). "
    "Focus on real defects: incorrect or weakened assertions, missing "
    "verification of a claimed effect, concurrency/isolation hazards, wrong "
    "error semantics, incorrect expected-failure tagging, and security issues. "
    "Ignore style. Classify each finding by priority: 0=critical (must fix "
    "before commit), 1=important, 2=minor, 3=nit. Return strictly the JSON "
    "schema provided — an empty findings array if clean."
)

# Real commit invocation, not any command containing the word "commit".
# The trailing boundary must accept ; & | ) and redirects too, otherwise
# `git commit; echo done` slips past the gate entirely. The leading boundary
# must be a command position so `echo git commit` is not falsely matched.
COMMIT_RE = re.compile(
    r"(?:^|[;&|(]|&&|\|\|)\s*git(?:\s+-\S+(?:\s+\S+)?)*\s+commit(?:[\s;&|)<>]|$)"
)


def _allow(notice: str | None = None) -> None:
    if notice:
        print(notice, file=sys.stderr)
    sys.exit(0)


def _block(reason: str) -> None:
    print(json.dumps({"decision": "block", "reason": reason}))
    sys.exit(0)


def main() -> None:
    if os.environ.get("CODEX_PRECOMMIT_REVIEW") == "0":
        _allow()

    # Only act on an actual `git commit`.
    try:
        event = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        _allow()
        return
    command = (event.get("tool_input") or {}).get("command", "")
    if not COMMIT_RE.search(command):
        _allow()

    if shutil.which("codex") is None:
        _allow("[review] codex CLI not on PATH — skipping (commit allowed).")

    # Include D so deletion-only commits still get reviewed.
    diff = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMRD"],
        capture_output=True, text=True, cwd=PROJECT_DIR,
    )
    if not diff.stdout.strip():
        _allow()

    cmd = ["codex", "exec", "--sandbox", "read-only", "--skip-git-repo-check"]
    if os.path.exists(SCHEMA):
        cmd += ["--output-schema", SCHEMA]
    model = os.environ.get("CODEX_REVIEW_MODEL")
    if model:
        cmd += ["--model", model]

    with tempfile.NamedTemporaryFile("r", suffix=".json", delete=False) as tf:
        out_path = tf.name
    cmd += ["-o", out_path, os.environ.get("CODEX_REVIEW_PROMPT", DEFAULT_PROMPT)]

    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, cwd=PROJECT_DIR, timeout=540
        )
    except subprocess.TimeoutExpired:
        _allow("[review] timed out (>9m) — skipping (commit allowed).")
        return
    except Exception as e:  # noqa: BLE001 — fail-open on any launch error
        _allow(f"[review] could not run reviewer ({e}) — commit allowed.")
        return

    try:
        with open(out_path) as fh:
            raw = fh.read().strip()
    except OSError:
        raw = (proc.stdout or "").strip()
    finally:
        try:
            os.unlink(out_path)
        except OSError:
            pass

    if not raw:
        _allow(
            "[review] no parseable output "
            f"(exit {proc.returncode}; likely unauthenticated) — commit allowed."
        )

    try:
        data = json.loads(raw)
        findings = data.get("findings", []) if isinstance(data, dict) else []
    except (json.JSONDecodeError, AttributeError):
        _allow("[review] non-JSON output — commit allowed (advisory only).")
        return

    p0 = [f for f in findings if isinstance(f, dict) and f.get("priority") == 0]
    advisory = [
        f
        for f in findings
        if isinstance(f, dict) and f.get("priority") in (1, 2, 3)
    ]

    if advisory:
        lines = "\n".join(
            f"  • P{f.get('priority')} {f.get('file', '?')}: {f.get('title', '')}"
            for f in advisory
        )
        print(f"[review] advisory (non-blocking):\n{lines}", file=sys.stderr)

    if p0:
        detail = "\n".join(
            f"  • {f.get('file', '?')}:{f.get('line', '?')} — "
            f"{f.get('title', '')}: {f.get('explanation', '')}"
            for f in p0
        )
        _block(
            "Review found P0 (critical) issue(s) in the staged diff — fix before "
            f"committing (or set CODEX_PRECOMMIT_REVIEW=0 to override):\n{detail}"
        )

    _allow()


if __name__ == "__main__":
    main()
