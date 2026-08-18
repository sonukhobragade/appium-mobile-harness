#!/usr/bin/env python3
"""PostToolUse hook — edit-time syntax + lint gate.

Surfaces syntax/lint errors immediately instead of at commit time, so the
pre-commit loop never discovers them late.

Universal (Tier 1) but stack-aware: the linter per extension is configured in
LINTERS below. Non-blocking — prints findings as feedback, never hard-fails.

Knobs: EDIT_TIME_LINT=0 to disable.
"""
from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess
import sys

# ext -> (argv template, stdout_means_failure)
#
# `stdout_means_failure` exists because some tools report problems on stdout
# while still EXITING 0 — `gofmt -l` lists unformatted files and returns
# success. Keying only on the exit code silently swallows those findings.
LINTERS: dict[str, tuple[list[str], bool]] = {
    ".py": (["ruff", "check", "--fix", "{path}"], False),
    ".ts": (["npx", "--no-install", "eslint", "--fix", "{path}"], False),
    ".tsx": (["npx", "--no-install", "eslint", "--fix", "{path}"], False),
    ".js": (["npx", "--no-install", "eslint", "--fix", "{path}"], False),
    ".go": (["gofmt", "-l", "{path}"], True),
    ".rs": (["rustfmt", "--check", "{path}"], False),
}


def main() -> int:
    if os.environ.get("EDIT_TIME_LINT") == "0":
        return 0

    try:
        event = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0

    path = (event.get("tool_input") or {}).get("file_path", "")
    ext = os.path.splitext(path)[1]
    if ext not in LINTERS:
        return 0

    msgs: list[str] = []

    # 1. Syntax gate (Python only — other stacks get it from their linter).
    if ext == ".py":
        try:
            with open(path, encoding="utf-8") as fh:
                ast.parse(fh.read())
        except SyntaxError as exc:
            msgs.append(f"SyntaxError in {path}: {exc}")
        except OSError:
            return 0

    # 2. Lint + autofix, respecting the project's own linter config.
    template, stdout_means_failure = LINTERS[ext]
    argv = [a.format(path=path) for a in template]
    if shutil.which(argv[0]):
        try:
            proc = subprocess.run(argv, capture_output=True, text=True, timeout=20)
            out = (proc.stdout + proc.stderr).strip()
            failed = proc.returncode != 0 or (
                stdout_means_failure and proc.stdout.strip() != ""
            )
            if failed and out:
                msgs.append(out)
        except subprocess.TimeoutExpired:
            msgs.append(f"{argv[0]} timed out on {path}")

    if msgs:
        print(
            "Edit-time lint found issues (fix before committing):\n" + "\n".join(msgs),
            file=sys.stderr,
        )
        return 2  # surface as feedback to the model

    return 0


if __name__ == "__main__":
    sys.exit(main())
