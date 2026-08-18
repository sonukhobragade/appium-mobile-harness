# Codebase Exploration — Mandatory Before Coverage Decisions

> This rule exists because shallow surface scans miss the real defects.
> Trusting recent-commit summaries alone produces generic, low-yield work.
> Defects live in code paths, state machines, async handlers, and cross-component
> flows — not in `git log --oneline`.

## Read the doc index FIRST

If the repo carries generated `index.md` files under its docs tree (progressive
disclosure: one row per doc — id · type · one-line description), **read the index,
pick the 1–2 relevant docs by description, open only those.** Never bulk-scan a
docs tree; that's the token waste the index exists to kill.

## When this rule fires

Mandatory **before**:
- Building any plan / coverage matrix / defect-hunt hit list
- Writing the first test for a component you haven't touched before
- Re-prioritizing after upstream changes
- Filing or rejecting a defect hypothesis
- Producing tickets for team consumption
- Answering "what should we work on next?"

Not required for: fixing one assertion, adding one fixture row, renaming a helper,
doc-only edits.

## The Rule

You may NOT propose coverage or a plan from:
- `git log --oneline` summaries alone
- Surface/route scans alone
- Filename or directory listings alone
- Memory of "what we did last time"
- A single file read in isolation

You MUST first run the **8-axis exploration** below and produce a written audit
(`docs/audit/<topic>-<date>.md`) with `file:line` citations before any plan or
ticket is published.

## The 8 axes (cover all of them)

For each component in scope:

1. **Synchronous surface** — every externally callable entry point: method, path
   or signature, request/response shape, auth gate, tenancy/locale handling,
   idempotency-key acceptance. `file:line` each.

2. **Asynchronous surface** — every queue consumer, scheduled job, event listener,
   webhook handler. Topic/queue name, retry policy, idempotency mechanism,
   dead-letter behavior, ordering guarantee. `file:line` each. **This is the most
   under-tested layer — mandatory pass.**

3. **State machines** — every status/state enum plus every transition implemented
   in the logic layer. Draw the legal graph; list the **illegal transitions
   silently accepted** — those are defects.

4. **Mutation surfaces** — every function that writes value-bearing state
   (balance, ledger, credit, quota, entitlement, permission, lock). Identify the
   transaction scope and the idempotency mechanism (lock key, dedup table, unique
   constraint). `file:line` each.

5. **Authorization surfaces** — the gate on each entry point. Where does identity
   come from? How is a caller-supplied subject id cross-checked against the
   authenticated identity? List every entry point that accepts a subject id as a
   parameter.

6. **Outbound calls** — for each call this component makes: target, header/context
   propagation (auth? locale? trace id?), timeout, retry, fallback. These are
   split-brain origins.

7. **Persistence surfaces** — every repository, query, cache write, config write.
   Unique constraints, partial indexes, document-shape validation, cache key TTL.
   Stale-cache risk per key.

   **MANDATORY — full inventory, not a per-entry-point scan.** Enumerate every
   table/collection/key-space in every reachable store, then:
   - **Map every one → the entry point(s) that read/write it.** An unmapped table
     is an unexplored surface — go find its consumer.
   - **Inspect newly-added tables first** — highest signal, most easily missed.
   - **Chase every unknown dependency to ground.** Never defer it, never write it
     off as out of scope.
   - **Verify config/feature-flag behavior on the ORCHESTRATOR**, not just leaf
     entry points. "No-op here" does NOT mean "no-op everywhere".

8. **Recent change diff** — last 60 days. For each change touching a mutation,
   auth, or state-machine surface, read the diff. Note "comment fix" vs real
   behavior change. A commit message that contradicts its diff is a tell.

## Required output format

```markdown
## <component-name>

### Synchronous surface
- `POST /foo/bar` — Handler.ext:42 — auth: role=USER — in: FooReq — out: FooRes
- `POST /foo/admin/baz` — Handler.ext:88 — auth: NONE [⚠ admin-by-convention]

### Asynchronous surface
- consumer `payments.completed` — Listener.ext:23 — idempotency: unique(payment_id) — retry 3 — DLQ yes

### State machines
- `Status`: PRE_INIT → ACTIVE → CANCELLED|EXPIRED → RENEWED
- ⚠ Service.ext:120 allows ACTIVE → PRE_INIT (illegal back-transition)

### Mutation surfaces
- `Wallet.credit()` — Wallet.ext:55 — lock `lock:wallet:{id}` — txn REQUIRED
- ⚠ `Override.apply()` — Override.ext:200 — NO LOCK, NO AUDIT

### Authorization surfaces
- 14 entry points accept a `{subjectId}` param; only 3 cross-check it (list)

### Outbound calls
- payments → accounts (PaymentService.ext:300). Auth context NOT forwarded.

### Persistence surfaces
- cache `entitlement:{id}` TTL=3600s, written by A, read by B. ⚠ stale window after cancel.

### Recent change diff (60d)
- `a465192` "Cap X to 100" — Utils.ext:106 — was `> 30`, now `> 100`; log text still says 30 ⚠

### Defect hypotheses (cited)
1. Stale entitlement cache post-cancel (Persistence #1)
2. Override has no idempotency (Mutation #2)
```

**Every bullet cites `file:line`. No citation = not in the audit.**

## Cost-aware gathering

| Surface | Tool order |
|---|---|
| Entry points | knowledge-graph search first; fall back to grep for route/handler annotations |
| Async handlers | graph search for listener/scheduler annotations; fall back to grep |
| State machines | semantic search for the status enum, then read the enum file fully |
| Mutation methods | graph/grep for balance / ledger / credit / debit / quota / lock |
| Authorization | grep the auth annotations; read the filter/middleware chain config |
| Outbound calls | grep the HTTP/RPC client constructs |
| Persistence | grep repository/query/cache-client constructs + enumerate the store |
| Recent diff | `git log --since="60 days ago"` then read each relevant diff |

If the knowledge graph does not index a language or repo, say so explicitly and
fall back to grep. **Don't pretend the graph covered something it didn't.**

## Parallelism

If scope ≥ 3 components, dispatch exploration agents in parallel (one per cluster).
Each prompt MUST include this 8-axis template and require `file:line` citations.
Merge reports into a single audit doc and **cross-reference** findings (e.g.
"A writes cache key X, B reads it stale" → one combined hypothesis).

## Anti-patterns

| Don't | Do |
|---|---|
| "I scanned the routes and these look untested" | Run the 8 axes; cite `file:line` per hypothesis |
| "Recent commits show X changed" | Read the diff; check message-vs-change consistency |
| "Coverage looks thin here" | Quantify: N surfaces / M tested = K% per axis |
| "Probably an authorization bug" | Cite the param + the missing cross-check `file:line` |
| Locking a plan from a path grep | Insert the audit step; produce the doc; THEN ticket |
| Trusting an empty graph result | The repo may not be indexed; fall back explicitly |
| Skipping the async surface | Most under-tested layer; mandatory |
| Skipping the state-machine graph | Silent illegal transitions are the highest-yield class |

## Workflow integration

```
1. Someone asks for a plan / coverage gaps / "what next?"
2. STOP. Run the 8-axis exploration on every in-scope component.
3. Write the audit doc with file:line citations.
4. Derive the coverage matrix from the audit.
5. Derive the hit list from the audit hypotheses (each cited).
6. Open tickets referencing the audit doc + hypothesis ids.
7. Implement only after audit + matrix + tickets ship.
```

## Litmus

> "If a teammate asks 'why this hypothesis?', can you cite a `file:line` for each one?"
> - **YES** → ship. **NO** → back to step 2.

## Related

- `.claude/rules/GRAPH_FIRST.md` — use the knowledge graph before grep
- `.claude/rules/VALIDATION_RULES.md` — prevents weakening once tests exist
- `.claude/rules/ERROR_CONTRACT_POLICY.md` — what to do when the audit reveals
  systematically wrong error semantics
