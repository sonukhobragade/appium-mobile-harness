# Validation Rules — Zero Tolerance

> Tests exist to FIND DEFECTS, not to PASS.
> **Tier 3 — language-agnostic. Examples use pseudo-assertions; map them to your
> assertion library (`assert_that` / `expect` / `assert.Equal` / `should`).**

---

## BANNED: "Either/Or" validation

```
# BANNED — hides defects:
if result.status == 200:      pass
elif result.status == 201:    pass   # WHICH IS IT? escape route.

# MANDATORY:
assert result.status == 200
```

A check that accepts two outcomes cannot fail when the wrong one happens. That is
the definition of a useless check.

---

## Validation decision tree

```
Do you KNOW the exact value?
├─ YES (fresh fixture, constant from config, seeded row) → EXACT assertion
│    assert response.status == 200
│    assert body.balance == 0
│    assert body.state == "ACTIVE"
│
└─ NO (genuinely dynamic: generated id, timestamp) → Format + Range + Consistency
     assert matches(body.id, /^ord_[A-Za-z0-9]{10,}$/)
     assert body.amount > 0
     assert other_endpoint.balance == this_endpoint.balance
```

"I don't know the value" is usually false. Create the fixture in a known state and
the value becomes knowable.

---

## 4-level verification framework

| Level | Question | Catches |
|---|---|---|
| **1: Business logic** | What SHOULD the value be? | Wrong amount, wrong state, wrong type |
| **2: Consistency** | Do two independent readers agree? | Stale cache, split-brain, races |
| **3: Format** | Is the dynamic value well-formed? | Null ids, truncated fields, malformed payloads |
| **4: Integrity** | Does it make sense at all? | Negative balances, corruption, timezone bugs |

A check that only exercises Level 3 on a Level-1-knowable value is too weak.

---

## Value categories — 4 kinds

| Category | When | Validation |
|---|---|---|
| **1: Dynamic** | Unpredictable (generated id, timestamp) | Format/regex check |
| **2: Known** | Deterministic (fresh fixture balance = 0, price from config) | Exact equality |
| **3: Consistency** | Same datum exposed by two surfaces | Assert they match |
| **4: Source-driven** | Value comes from the system's own catalogue | Fetch source of truth, assert membership |

**Rules:**
- NEVER use format-only for Category 2 — it hides defects.
- NEVER hardcode Category 1 values — they're unpredictable by definition.
- NEVER "either/or" — pick ONE expected state.

### Category 4 pattern

```
# WRONG — hardcoded id breaks when the catalogue is re-seeded
assert body.plan_id == "plan_123"

# CORRECT — fetch the source of truth, then assert containment
valid_ids = { p.id for p in fetch_catalogue() }
assert body.plan_id in valid_ids
```

---

## Banned assertion patterns

| BANNED | Why | INSTEAD |
|---|---|---|
| `assert status >= 200` | Widens the check; accepts 3xx/errors | `assert status == 200` |
| `assert status in (200, 201)` | Either/or | Pick ONE — read the contract |
| `try: assert x; except: pass` | Swallows failure silently | Let it fail loudly |
| `try/except` around the CALL, then asserting anyway | Masks network/system failure | Retry the CALL, never the ASSERTION |
| Hardcoded credentials / ids in tests | Leak across runs, expire | Factory or fixture |
| Mocking the system under test's transport | Tests the mock, not the contract | Hit the real environment — that's the point |
| `log.warn("got 500 but moving on")` | Pipeline stays green, defect escapes | Assert the correct value |
| `if body.x: assert ...` | Missing field = silent pass | Assert presence FIRST, then value |
| Regex for a known constant | Too lenient for Category 2 | Exact equality |
| `sleep(5)` waiting for async work | Flaky and slow | Poll with a bounded timeout helper |
| Test method with zero assertions | Documentation, not a test | Every test needs ≥1 real check |
| Widening a check to make it pass | Defect escape | Document the defect + mark expected-failure |
| Editing expected fixture data to match observed-buggy output | Data contortion | Keep expected correct; mark expected-failure |

---

## When a test fails — triage BEFORE touching code

1. **Read the evidence.** Response body, service logs, stack trace. Don't guess.
2. **Classify: system defect or test-setup defect?**
   - Logs show a crash / wrong branch / bad query in the system → **system defect**
   - Logs show the request never reached the handler, auth rejected, our payload
     malformed → **test-setup defect**
   - Logs show an environment/gateway rewrite → **environment reality**; adjust the
     test's *assumption*, never its assertion
3. **System defect** → file it in `docs/bugs` with a source `file:line`
   citation, then mark the test expected-failure referencing that id. Do NOT
   change the assertion.
4. **Test-setup defect** → fix the setup. NEVER weaken the assertion.
5. **Genuine environment flake** → bounded retry around the CALL. Never around
   the assertion.

---

## Expected-failure markers

A failing test that documents a known defect is marked expected-failure with:

- a **strict** mode where available, so the test auto-flips to a regression guard
  the moment the defect is fixed;
- a **reason string prefixed with the defect id** — see
  `.claude/rules/BUG_TEST_LINKAGE.md` for the prefix grammar.

Non-strict expected-failure is for accepted / won't-fix behavior only.

---

## Pre-submission checklist

- [ ] Every test has ≥1 real assertion — no log-only tests
- [ ] Every branch has an assertion — no silent pass paths
- [ ] Zero either/or checks
- [ ] Zero `try/except` wrapping assertions
- [ ] Zero hardcoded credentials / ids — factories or fixtures only
- [ ] Zero mocks of the system under test
- [ ] Zero unbounded sleeps — polling helpers with timeouts
- [ ] Known values checked EXACTLY; dynamic values get format + range + consistency
- [ ] Level 1 and Level 2 both exercised where two surfaces expose the same state
- [ ] Failing tests carry a defect reference, never a weakened check

**Test litmus: "If the system is broken, will this test FAIL?" — must be YES.**
**Assertion litmus: "Can this assertion pass while the defect exists?" — must be NO.**

---

## Related

- `.claude/rules/TESTING_STANDARD.md` — oracles, eventual consistency, isolation
- `.claude/rules/ERROR_CONTRACT_POLICY.md` — what to do when the system's error
  semantics are systematically wrong and frozen
- `.claude/rules/BUG_TEST_LINKAGE.md` — defect ↔ test traceability
