#!/bin/bash

# Log agent invocations for tracking and analytics
# Called when sub-agents are invoked

LOG_DIR=".claude/logs"
LOG_FILE="$LOG_DIR/agent-activity.log"

mkdir -p "$LOG_DIR"

TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
AGENT_NAME="${1:-unknown}"
CONTEXT="${2:-none}"

echo "$TIMESTAMP | Agent: $AGENT_NAME | Context: $CONTEXT" >> "$LOG_FILE"

# Rotate if needed
if [ -f "$LOG_FILE" ] && [ $(stat -f%z "$LOG_FILE" 2>/dev/null || stat -c%s "$LOG_FILE") -gt 10485760 ]; then
    mv "$LOG_FILE" "$LOG_FILE.old"
fi
