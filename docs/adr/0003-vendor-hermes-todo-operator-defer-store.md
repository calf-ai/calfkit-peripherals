# Vendor the hermes todo tool as a stateless Operator; defer a Kafka-native Store

**Status:** accepted · 2026-06-08 · amended by ADR-0004 (the in-memory Store is
the *shipped* Stage-D variant by owner decision, not dev-only; the Faust table
remains deferred) · the constructor-seed seam landed as
`patches/0002-todo-store-constructor-seed.patch`

## Context

calfkit needs a per-session task-list tool (`todo`). The todos research report
(`docs/tools-research/reports/todos-report.md`) established two facts that shape the
decision:

1. **The vendorable todo logic is thin.** hermes' `tools/todo_tool.py` (277 LOC) is a
   validated, id-keyed `list[dict]` with a `merge` mode (update-by-id/append), dedup, four
   statuses, and summary counts. The genuinely rich designs — Gemini's dependency-graph
   trackerTools, hermes' distributed-queue kanban — are either TypeScript or the cut SQLite
   variant. The only other Python first-party source, OpenHands `task_tracker`, is thinner
   still (no id/merge).
2. **No vendor option ships durable, multi-tenant state.** hermes and OpenHands are
   single-tenant Operators whose isolation is the caller's job; the only options with
   built-in multi-tenancy (OpenCode SQLite-by-session, Gemini dir-per-conversation) get it
   by coupling to a local-disk store — wrong for calfkit's no-shared-filesystem model.

So the two hard parts of a production todo node — **durable state** and **per-session
isolation without a shared filesystem** — are exactly what no vendor provides, while the
part a vendor *does* provide (the list logic) is trivial. calfkit already speaks Kafka, and
Faust tables (changelog-backed, partition-keyed, recoverable) fit those two hard parts
natively. A grill confirmed: durable (cross-restart, **within** a session), Faust
acceptable, tool lives in `calfkit-peripherals`.

## Decision

**Vendor `todo_tool.py` verbatim as a stateless _Operator_ into the existing
`components/hermes-agent/` component, and make the durable _Store_ a deferred, pluggable
layer that calfkit owns — do not adopt the Operator's built-in in-memory list as the
durable store.**

Concretely:

- Vendor `tools/todo_tool.py` (1 file, 1 import rewrite, 0 new shims, 0 patches) + its
  upstream test. It auto-registers via `discover_builtin_tools()`.
- The durable state lives behind a small **Store** interface (`get`/`put`/`delete` keyed by
  `session_key`), supplied by the calfkit node adapter. The node drives the vendored
  `TodoStore` as an Operator (seed from the Store → apply `write` → persist back **on writes
  only**), so the vendor stays verbatim today. The deferred node phase will add a **1-line
  constructor seed** (`__init__(self, items=None)`, in `patches/`) to seed without
  re-validating stored data — a seam, not a behavioral change.
- Store implementations are interchangeable: an in-memory dict for dev/tests (deliberately
  leak-on-purpose — no eviction, dev-only); a **Faust table** for production durability +
  multi-tenancy.
- The Store, its implementations, and the `node/` Kafka adapter are **deferred** to a
  separate design gated on the calfkit Kafka contract (`NODE.md`). This ADR records the
  *seam and direction*, not a built store.

## Considered and rejected

- **Vendor the in-memory Operator + a node-side store registry with eviction.** Rejected as
  the *durable* answer: it isn't durable (lost on restart), and it re-introduces a
  hand-rolled per-session LRU/TTL eviction registry. Moving state to a Kafka changelog does
  not make eviction *free* — it **shifts** the work from per-process eviction bookkeeping to
  a single session-end tombstone, which still requires a session-end signal (a blocking
  `NODE.md` dependency). The in-memory dict survives only as the trivial *dev* Store.
- **Hand-roll the whole tool natively (no vendor).** Rejected: against the repo's
  vendor-first philosophy, and needless — the list logic is MIT and self-contained, so
  lifting it costs nothing and keeps the tool a clean extension of the hermes component.
- **Vendor OpenHands `task_tracker` instead.** Rejected: a new component for a *weaker*
  schema (no id/merge) — hermes strictly dominates for a fresh vendor.
- **Local-disk / SQLite persistence (hermes kanban, OpenCode, Gemini tracker).** Rejected: a
  shared-filesystem assumption calfkit cannot make; ~9k LOC + SQLite deps for kanban. Its
  CAS+heartbeat coordination is kept as a design reference only.
- **Stateless `update_plan` (Codex).** Rejected: a node has no transcript to read the list
  back from, so it cannot serve `todo` reads statelessly.

## Consequences

- The vendor lands now as a thin, self-contained, MIT-attributed addition (1 file + test),
  registerable and unit-tested, with zero new shims, zero patches, and no hand-rolled code.
  In the vendor-only phase the tool is **registered but inert** — dispatched without a store
  it returns a clean `{"error": "TodoStore not initialized"}` (pinned by a test); it must
  not be exposed to a live model until the store is wired.
- Durability and multi-tenancy are deferred behind a clean Store seam. The deferred phase
  inherits **non-negotiable dependencies**: single-writer-per-session ordering (merge is a
  read-modify-write — co-partition every request type by `session_key`); read-your-writes
  bounded by changelog ack and consistent-but-pausing across rebalance; and session
  lifecycle via a session-end tombstone *or* an idle sweeper (log compaction does not evict
  distinct session keys).
- `TodoStore.format_for_injection()` vendors verbatim but is unused by the node — it is
  agent-context machinery, not node state.
- The "vendor the Operator, own the Store" shape is reusable for future stateful tools and is
  recorded in the glossary (Operator / Store / Session) in `CONTEXT.md`.
