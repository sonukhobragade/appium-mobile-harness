#!/usr/bin/env python3
"""SessionStart hook — inject the non-negotiables into every session.

Edit REMINDER to match the project. init.sh substitutes the branch name.
"""
from __future__ import annotations

import json
import os
import sys

# init.sh substitutes the project's protected branch here; PROTECTED_BRANCH
# still overrides at runtime.
PROTECTED = os.environ.get("PROTECTED_BRANCH", "main")

REMINDER = f"""SESSION START — MANDATORY CHECKS:
1. Verify branch: git branch --show-current (MUST NOT be {PROTECTED})
2. Sync base: git fetch origin {PROTECTED}:{PROTECTED}
3. CLAUDE.md + .claude/rules/*.md are loaded — follow them
4. Ground claims in source with file:line — never conclude from memory
5. The work exists to FIND PROBLEMS, not to look finished
6. Run `bash tools/local_gate.sh` before every commit"""


def main() -> int:
    try:
        json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        pass

    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": REMINDER,
        }
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
