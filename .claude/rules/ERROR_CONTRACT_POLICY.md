# Error Contract Policy — When the System's Error Semantics Are Wrong

> Applies to any system whose handlers wrap their body in a blanket
> `catch(Exception) → 5xx`, so every input error surfaces as a server error.
> The pattern holds wherever the error contract is systematically wrong and the
> owning team has frozen it.
>
> Adapt the concrete status codes to your protocol (HTTP, gRPC, CLI exit codes).

## The triage rule — read this first

For **invalid input** on a surface, the observed behavior decides the verdict:

| Observed | Verdict | Action |
|---|---|---|
| Correct client-error code (400 / 404 / 409 / equivalent) | ✅ correct | assert it; done |
| **Server-error code** produced by a blanket catch-all | ⚠️ frozen anti-pattern, **NOT a new defect** | assert the observed behavior, tag `[ANTI-PATTERN]`, **no new defect id**, add the surface to Known Offenders |
| **Silent success** that accepts the bad input or mutates state | ❌ **real defect** | file a defect id + strict expected-failure |

**Litmus: a wrong-but-erroring response = leave it. Success-when-it-should-reject = defect.**

**The one carve-out:** a server error that is a *concrete logic crash on a
value-bearing, state-changing, or auth path* (a specific null-dereference, a
missing validator, an index-out-of-bounds) is still a real defect — file it. Plain
input-validation errors funnelled through the shared catch-all are not.

## The anti-pattern

```
handler(req) {
    try {
        return ok(process(req))
    } catch (DomainException e) {
        return status(504).body(e)      // wrong code + serializes the exception
    } catch (Exception e) {
        return status(503).body("Unable to fetch the thing")
    }
}
```

Any failure — validation, null deref, missing row, duplicate key, downstream
timeout — collapses into an arbitrary server error. Serializing the exception into
the response body is also an information-disclosure issue.

## Observed mappings (do NOT trust the code)

| Sent | Returned | Correct |
|---|---|---|
| Missing required field | 5xx | 400 |
| Unknown enum value | 5xx + trace | 400 |
| Unknown id | 5xx | 404 |
| Repeat delete | 5xx | 200 / 404 / 410 |
| Malformed body | **silent success** OR 5xx | 400 |
| Duplicate create | 5xx | 409 |
| Business-rule violation | 5xx | 400 / 422 |

## The decision litmus

> **"If the team shipped one global exception handler tomorrow, would this be fixed?"**
>
> - **YES** → not a new defect. Add the surface to **Known Offenders** below.
>   Tag the test `[ANTI-PATTERN]`.
> - **NO** → a real defect. File an id.

File a **new defect id** only when the failure is NOT the shared catch-all:

- Silent success on a business-rule violation (wrong state machine, not a throw)
- Data leak / cross-tenant visibility
- Idempotency / double-execute / race
- Value correctness — wrong amount, missing debit, no-op refund
- A concrete logic bug with a specific line as the cause

## How to write tests against a frozen contract

1. **When the correct behavior is genuinely a future fix** (a specific crash, a
   missing validator), assert the **correct** value and mark it
   **strict expected-failure**. Strict means it auto-flips to a regression guard
   the moment the fix lands.

2. **When the wrong behavior is FROZEN and owner-rejected**, a status-code
   expected-failure produces **zero regression signal** — it will never flip back.
   For those paths:
   - **Drop the status assertion.** It's permanent noise.
   - **Keep a functional-invariant test instead.** Idempotency? assert no double
     effect. Bad input? assert the legitimate downstream effect didn't happen.
   - Encode the observed contract in fixture data with an `[ANTI-PATTERN]` tag so
     triage can see "frozen, not your bug to fix".

3. **Never retry around an assertion** hoping the error goes away. A server error
   on bad input is a defect, not flakiness. Retry only the CALL, and only for
   genuine environment flake.

4. **Never edit expected fixture data to match buggy output** unless the surface is
   on the Known Offenders list AND the team has explicitly refused to fix it. Then
   the observed value goes in the fixture with an `[ANTI-PATTERN]` tag, and the
   correct value is documented in the defect tracker.

## Information disclosure — decide the policy once, write it down

Serializing exceptions into responses leaks internals. Two defensible positions:

- **Enforce it** — assert the response body does not contain stack frames,
  exception class names, internal paths, or PII. This is a real regression guard
  and it does fire when handler plumbing changes.
- **Retire it** — if the owning team has explicitly rejected the fix and the leaks
  are frozen, these assertions become permanent noise that never drives a change.
  Then **ban them outright** so they don't get re-added, and record the decision.

**Pick one, record it here, and enforce that choice consistently.** The failure
mode is a half-enforced policy where some tests assert leaks and others don't.

> **This project's decision:** _(set during init — enforce | retire)_

## Known Offenders

One row per surface confirmed to be on the frozen anti-pattern. This list — not a
growing pile of defect ids — is the paper trail.

| Surface | Source `file:line` | Observed | Correct | Notes |
|---|---|---|---|---|
| _(populate as discovered)_ | | | | |

When you observe a new instance:
1. Read the handler source. If it's the shared catch-all → **STOP, no new id.**
2. Add one row above.
3. Tag the test `[ANTI-PATTERN]` pointing at this file — not a defect id.
4. If the surface is **load-bearing** (value, auth, deletion), escalate as a
   severity override on the existing meta-entry, not as a new id.

## Related

- `.claude/rules/VALIDATION_RULES.md` — assertion discipline, defect-vs-setup triage
- `.claude/rules/BUG_TEST_LINKAGE.md` — id prefixes, tracker sync, what never gets a test
