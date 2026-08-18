---
name: local-gate
description: Run the pre-commit / pre-PR gate (lint + type check + unit tests + collection smoke). Invoke as /local-gate BEFORE every git commit and again BEFORE opening a PR. If CI is disabled, this is the only thing catching lint, type, and collection regressions — fix, never weaken, anything it flags.
---

# local-gate

```bash
bash tools/local_gate.sh
```

## What it runs

1. **Lint** — `python -m ruff check . || true`
2. **Type check** — `true` (0 errors required; warnings allowed)
3. **Unit tests** — `pytest -m 'not integration'` (the set needing no external dependencies)
4. **Collection smoke** — `pytest --collect-only -q` (catches import/syntax breakage
   anywhere in the suite)

Exits non-zero if any step fails.

## When

- **Before every commit** — not after
- **Before opening a PR** — again, after the last change
- After a rebase or a merge that touched more than one file

## When it goes red

Fix the **code**. Never the check.

| Symptom | Fix |
|---|---|
| Lint error | Fix it. Don't add a blanket ignore to make it quiet. |
| Type error | Fix the type. Don't widen it to `Any` and don't silence the rule globally. |
| Unit test failure | Triage: system defect or test-setup defect? See `.claude/rules/VALIDATION_RULES.md`. |
| Collection failure | An import or syntax error somewhere in the suite. Fix it before anything else — nothing downstream is trustworthy. |
| Generated-artifact drift | Re-run the generator, don't hand-edit the output. |

A red gate made green by weakening a check is a **finding**, not a fix — the
reviewer is instructed to treat it as one.

## Adding a step

Append to `tools/local_gate.sh`:

```bash
step "My check" my-command --flag
```

and bump `TOTAL_STEPS`. Keep steps fast — a gate nobody runs is not a gate.
