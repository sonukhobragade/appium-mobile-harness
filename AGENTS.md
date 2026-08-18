# AGENTS.md — appium-mobile-harness

> Appium page-object harness with generation, oracles and Allure reporting

This file is the **review contract** for this repo. Treat the primary coding agent
as the author and the review agent (Codex, or whichever reviewer is wired) as the
reviewer. Review what the author changed against the same standard the author is
expected to follow, with emphasis on real defects, regressions, weakened checks,
missing oracles, wrong error semantics, and drift from documented defect status.

## Project

- **Stack**: Python, pytest, Appium, Selenium, Allure
- **Interface registry**: supplied by the consuming project
- **Defect tracker**: `docs/bugs` + GitHub Issues

## Review mission

Optimize for:

- Finding **genuine defects** in the changed diff, not speculative style nits
- Checking whether tests still enforce the **correct contract**, especially error
  semantics and value-bearing behavior
- Catching any attempt to **weaken a check to make it pass**
- Verifying defect-doc / marker / status coherence
- Asking whether coverage is still missing on a meaningful axis, **without
  inventing unrelated work**

Default review scope is the **uncommitted or staged diff** unless the user
explicitly asks for broader exploration.

## Ground the review in source, not memory

Do not conclude a review from training data alone.

1. Read the changed files and the code they exercise.
2. Where a knowledge graph is wired, use it for blast radius before declaring a
   diff safe (`.claude/rules/GRAPH_FIRST.md`).
3. For upstream/dependency behavior, check the actual source or the current docs —
   not recollection.

## Review rules

### Never normalize a default-branch violation
Authoring belongs on a feature branch via PR. Don't wave it through.

### The gate is not optional
`bash tools/local_gate.sh` must be green. A red gate fixed by weakening a check is
itself a finding.

### Assertion weakening is high-signal
Tests here exist to expose defects; they are not success theater. Treat a widened,
skipped, muted, or de-oracled check as a primary finding, not a nit.

## Reviewer output rules

- Anchor every finding to a concrete `file:line`
- Report **real findings only**
- Prioritize defects, regressions, incorrect contracts, missing validation, and
  incoherent defect-state changes
- Do **not** demand unrelated refactors or speculative coverage outside scope
- If no real issues exist, say so plainly
- After the findings, explicitly answer:
  - "anything left / missed?"
  - "should these cases be extended?"

## Reviewer checklist

1. **Contract** — is the asserted behavior the correct product / protocol /
   accounting contract?
2. **Weakening** — did the change widen, skip, mute, or de-oracle a previously
   stronger check?
3. **Marker semantics** — is the expected-failure / skip marker correct for the
   documented defect state, and is it strict where it should be?
4. **State coherence** — do code markers, defect docs, and tracker intent align?
5. **Value rigor** — if value-bearing behavior changed, are the required oracles
   still present, and are they counts/exact values rather than "didn't error"?
6. **Blast radius** — what else does this touch? Check before declaring it safe.
7. **Diff hygiene** — does every changed line trace to the stated task
   (`.claude/rules/KARPATHY.md` #3)?

## Rules index

Same auto-loaded rule set as the author — see the table in `CLAUDE.md`. The
reviewer enforces them; the author follows them.

## Restricted actions

| Level | Actions |
|---|---|
| REQUIRES user approval | Push to the default branch, force-push, close/merge PRs, delete branches, schema changes, dependency updates, disable hooks, weaken a check |
| ALLOWED freely | Read files, search code, run tests, read logs, lint |
