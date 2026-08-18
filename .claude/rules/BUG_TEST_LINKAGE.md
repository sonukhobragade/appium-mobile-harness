# Defect ↔ Test Linkage & Triage

> `docs/bugs` is the source of truth. `GitHub Issues` is the team mirror.
> These rules govern what gets automated, what gets skipped, how reports bucket
> defects, and how every test traces back to one.

## Rule 1 — "Not a Defect" / "Invalid" → NEVER automate

If a tracker row is **`Withdrawn`**, **`Not a Bug`**, or **`Invalid`** (or the doc
marks it WITHDRAWN / RETIRED / TEST BUG):

> **`Withdrawn` is the preferred label** for "we're dropping this — don't test it",
> whether it was real-but-deprioritized, intended behavior, or a mis-file.

- **Do NOT write a new test asserting the "defect".** No expected-failure, no skip
  marker, no guard. There is nothing to catch.
- If a test already exists → **stop running it now, delete in a dedicated cleanup
  pass.** A withdrawn defect's guard never flips and never signals.
  **EXCEPTION:** if the verdict is *intended AND load-bearing* behavior (value,
  auth, state), keep a **positive** guard asserting the intended contract so
  silent drift is caught.
- **Do NOT re-file.** Check the withdrawn set before proposing any hunt.

### Litmus
> "Is this behavior in the Withdrawn/Invalid set, or does an owner call or source
> proof say it's intended?"
> - YES → skip outright. No test. Cite the prior verdict.
> - NO → proceed (file an id, or route to `ERROR_CONTRACT_POLICY.md`).

## Rule 1a — Learn from invalid defects

Every withdrawn defect carries a **reusable lesson**. Record it, because these are
the most common ways a non-defect gets filed:

| Failure mode | Lesson |
|---|---|
| Filing on a mechanism the system ignores | Verify the input actually reaches the decision. An optional parameter the system overrides is a non-risk. |
| Investigator error (compared against the wrong subject) | Get the real identifiers from the logs BEFORE judging output correctness. |
| Filing on an intentionally out-of-scope surface | Confirm intent before filing. Admin/internal surfaces may be exempt by design. |
| Assuming cascade behavior that was never specified | Read the spec or ask the owner. Minimal-by-design ≠ broken. |
| Filing a grace window as a staleness defect | A TTL grace period is intended grace, not a stale cache. |
| Filing an "unlimited" defect where a different guard caps it | Trace the full path — a missing cap upstream may be neutralized downstream. |
| Filing a permissive response with no consumer | A silent success no consumer acts on is harmless. Trace the effect before assigning severity. |
| Filing a wrong-code-only issue | Right effect, wrong status → anti-pattern list, not a defect id. |
| Filing our own setup error as a system defect | Classify **test-defect vs system-defect FIRST**. Fix the setup. |

**Meta-lessons:**
1. **Owner or source before severity.** Most invalids were "looks wrong from
   outside, intended inside."
2. **Reachability before severity.** An input no real client can produce is Low at
   most.
3. **Verify the exploit end-to-end.** Fetch the supposedly-leaked resource before
   claiming a bypass. Reproduce the double-charge before filing it.
4. **Anti-pattern ≠ new defect** (see `ERROR_CONTRACT_POLICY.md`).

Keep a running rejection log so a killed hypothesis is never re-proposed.

## Rule 2 — Report buckets

The report separates two tiers so a real regression never hides:

- **`expected-failure` (open defects)** — `[BUG-<ID>]`, **strict**. Genuine, open,
  actionable. Auto-flips red when fixed.
- **`known issues`** — `[ANTI-PATTERN]` / `[KNOWN-ISSUE]` / `[PENDING]`,
  non-strict. Won't-fix / accepted / backlog. Not the on-call's problem today.

`Withdrawn` / `Invalid` appear in **neither** — Rule 1 means no test exists.

## Rule 3 — Every test links to its defect, bidirectionally

- **Test → defect:** the expected-failure reason MUST start with the category
  prefix + id:
  `expected_failure(reason="[BUG-B62] POST /x returns 503 on unknown id", strict=True)`
  Prefix grammar: `[BUG-<ID>]` · `[ANTI-PATTERN]` · `[KNOWN-ISSUE]` · `[PENDING]`.
- **Defect → test:** the tracker row's **Test path** field holds the test locator
  (`path/to/test::Class::method`). A defect with a guard but no Test path is a gap.
- The sync command keeps both in step — run it on every new id or status change.
- A `[BUG-<ID>]` in code with no tracker row (or vice versa) is a **linkage defect**
  — fix it before merge.

## Rule 4 — Track the automated test inventory in one place

Don't invent a new store. Use the existing metrics tables and make sure they're
**populated and joined**:

| Need | Table.column |
|---|---|
| Per-test inventory | `test_result(nodeid, class, method, interface_marker, component, outcome, is_flaky)` |
| Test → defect link | `test_result.bug_ref` (from the expected-failure prefix) |
| Test → external case id | `test_case_link(case_id)` |
| Defect provenance | `bug_detection(bug_id, found_by, status)` |
| Coverage denominator + risk | `interface_coverage(interface, risk_weight, tested)` |
| Time-saved baseline | `manual_baseline(scope, manual_exec_min)` |

Population rules:
- Every collected test → one `test_result` row, idempotent on `(run_id, nodeid)`.
- `outcome` carries `expected-failure` / `unexpected-pass` / `skipped` so buckets
  and the flaky verdict derive **by query, not by hand**.
- `bug_ref` is the join key → detection rate and linkage are one join.
- `outcome = expected-failure` with a NULL `bug_ref` is a **Rule 3 violation**.
- Withdrawn/Invalid never produce a guard → never a `bug_ref` row (Rule 1).

## Rule 5 — Definition of done for a defect

A defect is not "done" when its tracker properties are set. Done means:

1. Doc entry in `docs/bugs` with source `file:line` citation and
   reproduction steps
2. Tracker page with the **full body**, not just properties — including
   reproduction steps
3. Test guard with the correct prefix and strictness
4. Tracker row's Test path pointing at the guard
5. Test-case catalogue row updated to Automated with the test locator

Do all of it in the **initial** sync, not a follow-up pass.

## Related

- `.claude/rules/ERROR_CONTRACT_POLICY.md` — frozen anti-patterns, no new ids
- `.claude/rules/VALIDATION_RULES.md` — never weaken; test-defect vs system-defect
- `.claude/rules/TESTING_STANDARD.md` — prefix + Test-path conventions
- `modules/notion-tracker/` — tracker sync implementation (if enabled)
