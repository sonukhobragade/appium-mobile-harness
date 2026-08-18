# GEMINI.md — appium-mobile-harness

Gemini-facing entry point. The full contract lives in the shared files:

- **`CLAUDE.md`** — project facts, mandatory rules, workflow, rules index
- **`AGENTS.md`** — review contract
- **`SOUL.md`** — identity and values
- **`.claude/rules/*.md`** — the enforceable rule set

Read `CLAUDE.md` first. Everything below is additive, not a replacement.

## Non-negotiables (short form)

1. Never work on `main`. Branch from `origin/main`.
2. Never weaken a test to make it pass. A failing test is a discovery — document
   the defect, don't soften the assertion.
3. Ground every claim in source with a `file:line` citation. No conclusions from
   memory.
4. Run `bash tools/local_gate.sh` before every commit.
5. Verify before claiming done. Run the command, read the output, then report.

<!-- init.sh appends the code-review-graph MCP section here when that module is enabled -->
