#!/usr/bin/env bash
# PreToolUse hook — block `git commit` on the protected branch.
#
# WHY THIS IS A SCRIPT AND NOT AN INLINE COMMAND:
# The obvious inline form wired on `if: "Bash(git commit:*)"` matches on the
# SUBSTRING "commit", so completely unrelated commands get blocked:
#   cat .pre-commit-config.yaml       <- blocked
#   cp codex-precommit-review.py x/   <- blocked
# This script re-checks the actual command and only acts on a real commit
# invocation.
#
# Knobs: PROTECTED_BRANCH (default main), ENFORCE_WORKFLOW=0 to disable.
set -uo pipefail

[ "${ENFORCE_WORKFLOW:-1}" = "0" ] && exit 0

PROTECTED="${PROTECTED_BRANCH:-main}"

EVENT="$(cat)"
CMD="$(printf '%s' "$EVENT" | python3 -c \
  'import json,sys; print((json.load(sys.stdin).get("tool_input") or {}).get("command",""))' \
  2>/dev/null || true)"

# Real commit invocation only, not a path that merely contains the word.
#
# Boundaries matter in BOTH directions:
#   - trailing: must also accept ; & | ) and redirects, or `git commit; echo x`
#     and `git commit|tee log` silently BYPASS the guard.
#   - leading: must be a command position (start, or after ; & | ( or a
#     newline), so `echo git commit` is not falsely blocked.
printf '%s' "$CMD" | grep -Eq \
  '(^|[;&|(]|&&|\|\|)[[:space:]]*git([[:space:]]+-[^[:space:]]+([[:space:]]+[^[:space:]]+)?)*[[:space:]]+commit([[:space:];&|)<>]|$)' \
  || exit 0

BRANCH="$(git branch --show-current 2>/dev/null || true)"
if [ "$BRANCH" = "$PROTECTED" ]; then
  printf '{"decision":"block","reason":"BLOCKED: cannot commit on the protected branch %s. Create a feature branch first: git checkout -b feat/your-name origin/%s"}\n' \
    "$PROTECTED" "$PROTECTED"
fi
exit 0
