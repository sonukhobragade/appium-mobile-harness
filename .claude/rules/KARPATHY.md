# Karpathy Coding Discipline

> Source: [Andrej Karpathy on LLM coding pitfalls](https://x.com/karpathy/status/2015883857489522876), Jan 2026.
> Four failure modes LLMs hit when writing code. Behavioral rule — no tooling required.
> **Tier 1 — universal. Applies to every project regardless of stack.**

## The four principles

### 1. Think Before Coding — surface assumptions, don't guess

Before writing code, state assumptions explicitly. If two interpretations exist,
**present them and ask** — never pick silently.

Corollary: name the classification before touching code. *Is this a defect in the
system under test, or a defect in my own setup?* A wrong assumption here is how a
real bug gets papered over.

**Litmus:** if you cannot state the expected output and the way you'll verify it
before writing, you have an unsurfaced assumption. Stop and ask.

### 2. Simplicity First — minimum code, nothing speculative

No abstraction for single-use code. No "configurability" nobody asked for. No
helper that wraps one caller.

- DRY checkpoint: at the **3rd copy-paste**, STOP. Extract the helper first,
  refactor the existing instances, then continue.
- Don't add a new utility when an existing one already covers the case.

**Litmus:** would a senior engineer call this overcomplicated? A function with one
caller isn't a class. A parser handling a shape that can't occur is dead code.

### 3. Surgical Changes — touch only what the task needs

Every changed line must trace to the request. Don't reformat, re-comment,
re-annotate, or rename code orthogonal to the task. Don't delete pre-existing dead
code unless asked — mention it instead.

When work surfaces a pre-existing defect in an *adjacent* area, fix it in its
**own PR**. Don't fold unrelated repairs into a feature diff.

**Litmus:** does the diff contain a single line not explained by the stated task?
If yes, revert that line.

### 4. Goal-Driven Execution — declarative success criteria, then loop

Define concrete, verifiable checks up front; loop until they pass. Don't code
imperatively toward a vibe.

The gate loop is the success criterion: `python -m ruff check . || true` + `true` +
the target test green BEFORE staging changes.

**Litmus:** can you state the pass condition as a check a script could run? If
not, the goal is still a vibe — sharpen it.

## Red flags (STOP — you're off-leash)

| Thought | Reality |
|---|---|
| "I'll make all the edits then run the tests once" | Run the target test after each change. Isolate cause/effect. |
| "This big diff is cleaner" | Big diff = unreviewable + hides weakened checks. Split it. |
| "It probably returns X" | Verify against the source or a live call. Don't trust, check. |
| "I'll widen the assertion so it's green" | Orthogonal-edit + weaken-test failure. Document the defect instead. |
| "I'll guess the interface" | Read the source first. No guessing. |
| "Fold this legacy fix into the feature PR" | Separate PR. Every line traces to one stated task. |
| "Leave the debug print, it's harmless" | Remove it this session. Keep the diff clean. |
| "More edge-handling can't hurt" | Speculative coverage is bloat. Assert the contract, nothing impossible. |

## When to relax

| Principle | Relax when… |
|---|---|
| Think Before Coding | Request is unambiguous and self-contained ("rename this constant"). |
| Simplicity First | User explicitly asked for an abstraction / fixture / reusable helper. |
| Surgical Changes | User said "refactor this file" / "clean up this module". |
| Goal-Driven | One-liner with obvious correctness (fix a typo). |

## The 80/20

If you internalize only one: **Surgical Changes (#3)** — most measurable (diff
analysis), most commonly violated (LLMs love to "improve" things), and the most
dangerous, because the orthogonal "improvement" is usually a weakened check that
lets a real defect escape.

## Related

- `.claude/rules/VALIDATION_RULES.md` — never weaken a check; defect-vs-setup triage
- `.claude/rules/CODEBASE_EXPLORATION.md` — surface assumptions via structured audit
