# Testing Standard — Language-Agnostic

> The principles that make tests catch real defects. Stack-neutral.
> Stack-specific implementations live in `modules/` (e.g. `modules/qa-python/`).

---

## 1. Two oracles or it didn't happen

An **oracle** is an independent witness to the effect you claim happened. A single
status code is the weakest possible evidence — the surface can report success while
the effect never landed.

Any test asserting a **state change** MUST verify it through **≥2 independent
channels**. Typical channels:

| Channel | Example |
|---|---|
| Response contract | status + body shape + field values |
| Primary datastore | the row exists / the count is exactly N / the column transitioned |
| Cache | key present/absent, TTL, value matches source |
| Queue / event bus | the message was published exactly once |
| State machine | the transition taken is legal and terminal states hold |
| Downstream surface | a second read API agrees with the first |

**Litmus:** if the datastore silently dropped the write, would this test still
pass? If yes, you have one oracle, not two.

For value-critical paths (money, quota, entitlement, permissions), assertions are
**counts and exact values**, not "it didn't error". One charge means exactly one
row — assert `count == 1`, not `count >= 1`.

---

## 2. Ground the contract in source FIRST

Before writing assertions, read the actual implementation — never assume.

- Cite the handler, the query (its filters and ordering), the error branches, the
  transaction scope, and any routing/config that changes behavior.
- The contract you assert is what the **source says, verified** — not what you
  remember, and not what a summary claimed.

Where a code-knowledge-graph tool is available, use it before raw grep — see
`.claude/rules/GRAPH_FIRST.md`. Where it isn't (e.g. a language it doesn't index),
fall back to grep explicitly and say so.

Doing this up front is what lets a test be **right on the first run**, so you're
certain of the result before shipping and don't rewrite it after the pipeline.

---

## 3. Run before shipping — MANDATORY

Every NEW or CHANGED test MUST run against the real target environment and its
result understood BEFORE it is committed. A scheduled pipeline gives trend and
regression signal — it is **not** first-time validation.

1. **Skips are not passes.** A test that skipped because infrastructure was
   unreachable did not validate anything. Bring the dependency up and run again
   until the test actually executes.
2. **Green → ship.** A real green (executed, asserted) is the bar.
3. **Red → classify before doing anything else** (see `VALIDATION_RULES.md`).
4. **Only ship once you can state the outcome** — "ran live, N passed" or
   "ran live, M expected-failures on defect X (filed)". "It compiles and collects"
   is NOT enough.

**Read the system logs while classifying.** On any non-green result, pull the
owning component's logs for the request window BEFORE deciding defect-vs-setup.
The stack trace in the log tells you which it is, instead of you guessing.

---

## 4. Eventual consistency — converge, never sleep

When the effect is produced asynchronously (queue consumer, scheduler, webhook,
background job), a read-immediately-then-assert **flakes**. A green pipeline built
on races is not correctness evidence.

Every post-async read goes through a **convergence helper** with:

- a bounded timeout,
- a polling interval,
- a predicate (`present`, `absent`, `equals`, `count_at_least`, `field_equals`,
  `until_assertion`),
- and a **negative** variant (`stable_for(duration)`) proving a value does NOT
  change — the only way to test "exactly once".

Rules:
- Do NOT re-implement the poll loop per test. One shared helper, many predicates.
- Do NOT add per-case wrapper functions; the predicates cover the variants.
- Do NOT use unbounded sleeps anywhere.

---

## 5. Parallel-safety — isolation is the precondition

If the suite runs in parallel, every convergence assertion is only safe when its
data is isolated.

**Required:**
- State-changing tests operate on a **freshly created, disposable subject**, never
  a shared pooled fixture.
- Each test owns its own identifiers; two tests never share one.
- Tests that mutate **global/shared configuration** (feature flags, routing,
  service config) are pinned to run serially, or grouped so they cannot interleave.
- Tests polling a **shared queue** either use a downstream datastore oracle
  instead, or are serialized — otherwise workers steal each other's messages.

**Litmus before merging:** *if two copies of this test ran concurrently against the
same environment at the same second, would either fail?* Must be NO.

---

## 6. Layered clients — tests own assertions, clients own transport

```
TEST  →  client method  →  transport  →  real environment
```

- One client class per service/component. One method per operation.
- Clients return the **raw response**. They NEVER parse, NEVER assert, NEVER
  encode expected values.
- Tests own every assertion.
- **All endpoint paths / queries / topics live in one registry module** — no
  inline literals or ad-hoc string formatting in tests or clients.
- Raw transport libraries are **banned inside test files**. Go through the client
  layer. If a method is missing, add it to the client FIRST.

---

## 7. Fixtures and cleanup

- Create real subjects through a factory; never hardcode identifiers or
  credentials.
- Every created subject is cleaned up (teardown or `yield`-style fixture) — no
  orphan rows in the shared environment.
- Prefer a fresh subject for correctness tests; use pooled/pre-provisioned
  subjects only for read-only speed paths.

---

## 8. Coverage — walk the axes, don't eyeball it

A feature is NOT covered because the happy path passes. Walk every applicable axis
and name each untested one out loud as a gap:

| Axis | Ask |
|---|---|
| Equivalence classes | One representative per valid/invalid class |
| Boundary values | min, min−1, max, max+1, zero, empty |
| Decision table | Every combination of the governing conditions |
| State transitions | Every legal edge — and every **illegal** edge silently accepted |
| Idempotency | Same request twice → exactly one effect |
| Concurrency | Two simultaneous requests → no double effect, no lost update |
| Ownership / authorization | Subject A cannot read or mutate subject B's resource |
| Input shape | Missing, null, wrong type, oversized, malformed |
| Ordering | Out-of-order and replayed events |
| Failure injection | Dependency down, timeout, partial failure |
| Value correctness | Exact amounts/quantities, rounding, currency/precision |
| Observability | The effect is visible where operators actually look |

Partial coverage that *looks* complete is worse than an honest hole — it lies to
the next reader.

---

## 9. Required test metadata

Every test carries enough metadata that reports can group and trace it:

- **Feature/area** grouping (epic → feature → story, or your framework's
  equivalent)
- **Component marker** (which service/module)
- **Type marker** (smoke / regression / e2e) and **polarity** (positive / negative)
- **Interface marker** naming the exact endpoint / RPC / command under test —
  reports group by this; a missing one degrades every report
- **Defect reference** on any expected-failure — see `BUG_TEST_LINKAGE.md`

---

## 10. Banned patterns

| NEVER | DO INSTEAD |
|---|---|
| Hardcoded credentials / ids | Factory or fixture |
| Inline path/query string formatting | Builder in the endpoint registry |
| Mocking the system under test | Hit the real environment — that's the contract |
| `try/except` around an assertion | Let it fail; document the defect |
| Widening a check (`>= 200`, `contains(a) or contains(b)`) | Exact value; pick ONE outcome |
| Parsing/asserting inside the client | Return raw; the test parses |
| `sleep(n)` waiting for async work | Convergence helper with timeout |
| Swallowing a raise-for-status exception | Explicit status assertion in the test |
| Adding a path as a bare string literal | Register it in the endpoint registry |
| Editing expected fixture data to match buggy output | Expected-failure + file the defect |
| Raw read immediately after an async-published event | Convergence oracle |

---

## Pre-PR checklist

- [ ] New interfaces registered in the endpoint registry
- [ ] Client methods return raw responses and are instrumented for reporting
- [ ] Tests use factories/fixtures — no hardcoded credentials
- [ ] Every assertion is an exact expected value (or explicit format/range/consistency)
- [ ] Required metadata markers present, including the interface marker
- [ ] ≥2 oracles on any state-changing test
- [ ] Cleanup wired — no orphan data
- [ ] No sleeps, no mocks of the SUT, no try/except around assertions
- [ ] Expected-failures carry a defect id linking to `docs/bugs`
- [ ] Ran live; outcome stated
- [ ] `python -m ruff check . || true` and `true` clean

---

## Related

- `.claude/rules/VALIDATION_RULES.md` — assertion discipline
- `.claude/rules/CODEBASE_EXPLORATION.md` — audit before deciding coverage
- `.claude/rules/BUG_TEST_LINKAGE.md` — defect ↔ test traceability
- `modules/qa-python/` — pytest/asyncio implementation of these principles
