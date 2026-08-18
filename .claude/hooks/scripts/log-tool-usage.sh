#!/bin/bash

# Log tool usage for analytics and debugging
# Called automatically by Claude Code on every tool invocation

LOG_DIR=".claude/logs"
LOG_FILE="$LOG_DIR/tool-usage.log"

# Create log directory if it doesn't exist
mkdir -p "$LOG_DIR"

# Log format: timestamp | tool_name | arguments
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
TOOL_NAME="${1:-unknown}"
ARGS="${2:-none}"

echo "$TIMESTAMP | $TOOL_NAME | $ARGS" >> "$LOG_FILE"

# Optional: Rotate logs if too large (>10MB)
if [ -f "$LOG_FILE" ] && [ $(stat -f%z "$LOG_FILE" 2>/dev/null || stat -c%s "$LOG_FILE") -gt 10485760 ]; then
    mv "$LOG_FILE" "$LOG_FILE.old"
fi
