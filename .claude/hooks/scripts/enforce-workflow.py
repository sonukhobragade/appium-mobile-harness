#!/usr/bin/env python3
"""PreToolUse hook — block Edit/Write/MultiEdit while on the protected branch.

Universal (Tier 1). Configure the protected branch with PROTECTED_BRANCH
(default: main). Set ENFORCE_WORKFLOW=0 to disable.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

# init.sh substitutes the project's protected branch here; PROTECTED_BRANCH
# still overrides at runtime.
PROTECTED = os.environ.get("PROTECTED_BRANCH", "main")

SKIP_PATTERNS = (
    "/output/",
    ".claude/logs/",
    "/tmp/",
    ".log",
    "node_modules/",
    ".git/",
    "package-lock.json",
    "yarn.lock",
    ".claude/hooks/",
    ".claude/settings",
)


def main() -> int:
    if os.environ.get("ENFORCE_WORKFLOW") == "0":
        return 0

    try:
        event = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0

    if event.get("tool_name", "") not in ("Edit", "Write", "MultiEdit"):
        return 0

    file_path = (event.get("tool_input") or {}).get("file_path", "")
    if any(p in file_path for p in SKIP_PATTERNS):
        return 0

    # Only guard edits INSIDE this repo. Editing another checkout is not our
    # branch policy to enforce — this is the fix for the cross-repo false block.
    try:
        root = subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"],
            stderr=subprocess.DEVNULL, timeout=5,
        ).decode().strip()
    except Exception:
        return 0
    if file_path and not os.path.abspath(file_path).startswith(root):
        return 0

    try:
        branch = subprocess.check_output(
            ["git", "branch", "--show-current"],
            stderr=subprocess.DEVNULL, timeout=5,
        ).decode().strip()
    except Exception:
        return 0

    if branch == PROTECTED:
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "block",
                "reason": (
                    f"BLOCKED: cannot edit on the protected branch '{PROTECTED}'. "
                    f"Create a feature branch first: "
                    f"git checkout -b feat/your-name origin/{PROTECTED}"
                ),
            }
        }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
