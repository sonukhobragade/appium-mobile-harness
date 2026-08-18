#!/bin/bash

# Log errors for debugging
# Called when errors occur during command execution

LOG_DIR=".claude/logs"
LOG_FILE="$LOG_DIR/errors.log"

mkdir -p "$LOG_DIR"

TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
ERROR_MSG="${1:-unknown error}"
COMMAND="${2:-unknown}"

echo "$TIMESTAMP | Command: $COMMAND | Error: $ERROR_MSG" >> "$LOG_FILE"

# Rotate if needed
if [ -f "$LOG_FILE" ] && [ $(stat -f%z "$LOG_FILE" 2>/dev/null || stat -c%s "$LOG_FILE") -gt 10485760 ]; then
    mv "$LOG_FILE" "$LOG_FILE.old"
fi
