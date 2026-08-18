#!/usr/bin/env bash
# init.sh — turn this template into a real project.
#
#   bash scripts/init.sh                        # interactive
#   bash scripts/init.sh --set PROJECT_NAME=foo --set STACK="Go 1.22" ...
#   bash scripts/init.sh --enable qa-python --enable notion-tracker
#   bash scripts/init.sh --enable qa-python    # later, on an already-init'd repo
#   bash scripts/init.sh --dry-run
#
# What it does:
#   1. Collects placeholder values (prompt, or --set / .template.env)
#   2. Substitutes {{PLACEHOLDER}} across every tier-2 file
#   3. Installs the chosen modules from modules/<name>/ into the repo
#   4. Deletes disabled modules and the template's own scaffolding
#
# Idempotent: re-running with --enable only installs the new module.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

DRY_RUN=0
declare -a ENABLE=()
declare -A VALUES=()
STATE=".template.env"

# Keys in manifest order. Keep in sync with template.manifest.yaml.
KEYS=(
  PROJECT_NAME PROJECT_TAGLINE AGENT_NAME AGENT_DOMAIN AGENT_STAKES
  STACK CORE_PATTERN ENDPOINT_REGISTRY BUG_DOC_DIR TRACKER DEFAULT_BRANCH
  INSTALL_CMD TEST_CMD TEST_SINGLE_CMD LINT_CMD TYPECHECK_CMD
  UNIT_TEST_CMD COLLECT_CMD
)

prompt_for() {
  case "$1" in
    PROJECT_NAME)      echo "Project name" ;;
    PROJECT_TAGLINE)   echo "One-line description" ;;
    AGENT_NAME)        echo "Agent identity name for SOUL.md" ;;
    AGENT_DOMAIN)      echo "What the agent works on (e.g. 'on payment systems')" ;;
    AGENT_STAKES)      echo "What is at stake when this system is wrong" ;;
    STACK)             echo "Stack (e.g. 'Python 3.11, pytest')" ;;
    CORE_PATTERN)      echo "Core pattern, one line" ;;
    ENDPOINT_REGISTRY) echo "Path to the interface registry module" ;;
    BUG_DOC_DIR)       echo "Defect docs directory" ;;
    TRACKER)           echo "External tracker name (or 'none')" ;;
    DEFAULT_BRANCH)    echo "Protected branch" ;;
    INSTALL_CMD)       echo "Install command" ;;
    TEST_CMD)          echo "Run the whole suite" ;;
    TEST_SINGLE_CMD)   echo "Run a single test file" ;;
    LINT_CMD)          echo "Lint command" ;;
    TYPECHECK_CMD)     echo "Type-check command (or 'true')" ;;
    UNIT_TEST_CMD)     echo "Unit tests needing no external deps" ;;
    COLLECT_CMD)       echo "Collect-only smoke (or 'true')" ;;
  esac
}

default_for() {
  case "$1" in
    AGENT_NAME)        echo "Agent" ;;
    AGENT_DOMAIN)      echo "on this codebase" ;;
    AGENT_STAKES)      echo "Correctness here is what users depend on." ;;
    ENDPOINT_REGISTRY) echo "src/config/endpoints" ;;
    BUG_DOC_DIR)       echo "docs/bugs/" ;;
    TRACKER)           echo "none" ;;
    DEFAULT_BRANCH)    echo "main" ;;
    TYPECHECK_CMD)     echo "true" ;;
    COLLECT_CMD)       echo "true" ;;
    *)                 echo "" ;;
  esac
}

is_known_key() {
  local needle="$1" k
  for k in "${KEYS[@]}"; do [ "$k" = "$needle" ] && return 0; done
  return 1
}

while [ $# -gt 0 ]; do
  case "$1" in
    --set)
      # Validate KEY=VALUE and that KEY is real. A silently-dropped typo
      # (PROJET_NAME=x) leaves the correct key on its empty default and
      # produces a permanently misconfigured scaffold.
      case "${2-}" in
        *=*) : ;;
        *) echo "--set expects KEY=VALUE, got: ${2-<missing>}" >&2; exit 2 ;;
      esac
      if ! is_known_key "${2%%=*}"; then
        echo "unknown --set key: ${2%%=*}" >&2
        echo "valid keys: ${KEYS[*]}" >&2
        exit 2
      fi
      VALUES["${2%%=*}"]="${2#*=}"; shift 2 ;;
    --enable)  ENABLE+=("$2"); shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

# Reload previously saved values so --enable later doesn't re-prompt.
if [ -f "$STATE" ]; then
  # shellcheck disable=SC1090
  while IFS='=' read -r k v; do
    [ -n "${k:-}" ] || continue
    case "$k" in \#*) continue ;; esac
    [ -n "${VALUES[$k]+x}" ] || VALUES["$k"]="$v"
  done < "$STATE"
fi

# Treat the repo as initialized only when the state file actually carries every
# key. Existence alone is not enough: a truncated, hand-edited, or older state
# file would skip collection and then get rewritten with blanks for the missing
# keys, silently substituting them away.
INITIALIZED=0
if [ -f "$STATE" ]; then
  INITIALIZED=1
  missing=""
  for k in "${KEYS[@]}"; do
    [ -n "${VALUES[$k]+x}" ] || missing="$missing $k"
  done
  if [ -n "$missing" ]; then
    INITIALIZED=0
    echo "!! $STATE is missing keys:$missing"
    echo "   Re-collecting those values (existing ones are kept)."
  fi
fi

# --------------------------------------------------------------------------
# 1. Collect values (skip if already initialized and no new ones requested)
# --------------------------------------------------------------------------
if [ "$INITIALIZED" -eq 0 ]; then
  echo "== Configuring template =="
  for k in "${KEYS[@]}"; do
    if [ -z "${VALUES[$k]+x}" ]; then
      def="$(default_for "$k")"
      if [ -t 0 ]; then
        read -r -p "$(prompt_for "$k")${def:+ [$def]}: " ans || ans=""
      else
        ans=""
      fi
      VALUES["$k"]="${ans:-$def}"
    fi
  done
fi

# --------------------------------------------------------------------------
# 2. Substitute placeholders across tier-2 files
# --------------------------------------------------------------------------
substitute() {
  local file="$1"
  [ -f "$file" ] || return 0
  local tmp; tmp="$(mktemp)"
  cp "$file" "$tmp"
  # tools/local_gate.sh embeds command values inside SHELL SINGLE QUOTES, so a
  # value containing an apostrophe would terminate the quote early. Escape it
  # there and only there — everywhere else the value is prose and must stay raw.
  local shell_quote=0
  case "$file" in
    */tools/local_gate.sh|tools/local_gate.sh|./tools/local_gate.sh) shell_quote=1 ;;
  esac
  for k in "${KEYS[@]}"; do
    local v="${VALUES[$k]:-}"
    # Use python for safe replacement — sed chokes on / and & in values.
    python3 - "$tmp" "$k" "$v" "$shell_quote" <<'PY'
import sys, pathlib
p, key, val, shq = pathlib.Path(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4]
if shq == "1":
    val = val.replace("'", "'\\''")
p.write_text(p.read_text().replace("{{%s}}" % key, val))
PY
  done
  if [ "$DRY_RUN" -eq 1 ]; then
    if ! diff -q "$file" "$tmp" >/dev/null; then echo "would rewrite: $file"; fi
    rm -f "$tmp"
  else
    mv "$tmp" "$file"
  fi
}

# List every file still carrying a {{TOKEN}}, minus the ones that must keep
# theirs (the template's own machinery and uninstalled module payloads).
#
# NOTE: do NOT use `grep --exclude-dir=scripts` for this. That matches a
# directory of that name at ANY depth, so it silently skipped
# .claude/hooks/scripts/ — the hook files were never substituted, and a
# verification grep using the same flag agreed with itself. Filter on the
# path PREFIX instead.
discover_placeholder_files() {
  grep -rl '{{[A-Z_]\+}}' --exclude-dir=.git . 2>/dev/null \
    | grep -v '^\./scripts/' \
    | grep -v '^\./modules/' \
    | grep -v '^\./template\.manifest\.yaml$' \
    | grep -v '^\./README\.md$' \
    || true
}

if [ "$INITIALIZED" -eq 0 ]; then
  echo "== Substituting placeholders =="
  while IFS= read -r f; do
    [ -n "$f" ] && substitute "$f"
  done < <(discover_placeholder_files)
fi

# --------------------------------------------------------------------------
# 3. Install enabled modules
# --------------------------------------------------------------------------
install_module() {
  # NOTE: two statements, not `local name=$1 dir=modules/$name` — a `local`
  # builtin expands all its words BEFORE assigning any, so the second would
  # dereference an unbound $name and abort under `set -u`.
  local name="$1"
  local dir="modules/$name"
  if [ ! -d "$dir" ]; then
    echo "!! no such module: $name" >&2; return 1
  fi
  echo "== Installing module: $name =="
  if [ "$DRY_RUN" -eq 1 ]; then
    echo "would run: $dir/install.sh"
    return 0
  fi
  if [ -x "$dir/install.sh" ] || [ -f "$dir/install.sh" ]; then
    ( cd "$ROOT" && bash "$dir/install.sh" )
  fi
  # Re-substitute in case the module dropped templated files in.
  while IFS= read -r f; do
    [ -n "$f" ] && substitute "$f"
  done < <(discover_placeholder_files)
  echo "$name" >> .template.modules
}

for m in ${ENABLE[@]+"${ENABLE[@]}"}; do
  [ -n "$m" ] && install_module "$m"
done

# Refresh the generated module table in CLAUDE.md.
refresh_module_table() {
  [ -f CLAUDE.md ] || return 0
  python3 - <<'PY'
import pathlib, re
p = pathlib.Path("CLAUDE.md")
mods = pathlib.Path(".template.modules")
names = []
if mods.exists():
    for line in mods.read_text().splitlines():
        line = line.strip()
        if line and line not in names:
            names.append(line)

if names:
    rows = ["| Module | Adds |", "|---|---|"]
    for n in names:
        desc = ""
        readme = pathlib.Path("modules") / n / "README.md"
        if readme.exists():
            for line in readme.read_text().splitlines():
                if line.startswith("# Module:"):
                    continue
                if line.strip():
                    desc = line.strip()
                    break
        rows.append(f"| `{n}` | {desc} |")
    body = "\n".join(rows)
else:
    body = "_none yet — run `bash scripts/init.sh --enable <module>`_"

text = p.read_text()
new = re.sub(
    r"(<!-- MODULES:BEGIN[^>]*-->\n).*?(\n<!-- MODULES:END -->)",
    lambda m: m.group(1) + body + m.group(2),
    text,
    flags=re.S,
)
if new != text:
    p.write_text(new)
PY
}

if [ "$DRY_RUN" -eq 0 ]; then
  refresh_module_table
fi

# NOTE: GRAPH_FIRST.md is NOT shipped in the base tree and deleted here — it is
# shipped BY the code-review-graph module. Ship-then-delete meant enabling the
# module later reinstated the MCP config but not the rule, leaving the agent
# with a graph and no instruction to use it.

# --------------------------------------------------------------------------
# 4. Persist state
# --------------------------------------------------------------------------
if [ "$DRY_RUN" -eq 0 ]; then
  {
    echo "# written by scripts/init.sh — values used for placeholder substitution"
    for k in "${KEYS[@]}"; do printf '%s=%s\n' "$k" "${VALUES[$k]:-}"; done
  } > "$STATE"
  chmod +x tools/local_gate.sh .claude/hooks/scripts/*.sh 2>/dev/null || true
fi

echo
echo "Done."
if [ "$INITIALIZED" -eq 0 ]; then
  cat <<'NEXT'
Next:
  1. Read CLAUDE.md and fill in any project-specific sections.
  2. Rewrite SOUL.md's voice for your project (keep the creed).
  3. Set the info-disclosure decision in .claude/rules/ERROR_CONTRACT_POLICY.md.
  4. Run: bash tools/local_gate.sh
  5. Delete the modules/ directory once you're done enabling what you need,
     or keep it to enable more later.
NEXT
fi
