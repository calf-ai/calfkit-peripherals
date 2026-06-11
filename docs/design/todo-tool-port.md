# Design: Todo tool (`todo`) — vendoring & calfkit integration

> **Note (2026-06-10):** Pre-unification port-design record. Packaging has since been unified into a single `calfkit-tools` distribution — see [`../project-structure.md`](../project-structure.md) for the current layout (`src/calfkit_tools/<source>/`; provenance in `vendor/<source>/`). `components/<source>/` paths and per-component package names (`calfkit-hermes`, `calfkit-pydantic-web-fetch`) below describe the original design and are kept as history.

**Status:** Implemented (vendor phase). Deep-review round 1 + convergence complete;
the durable Store + node adapter are deferred (gated on the calfkit Kafka contract).
**Date:** 2026-06-08
**Owners:** Ryan
**Decisions:** [ADR-0003](../adr/0003-vendor-hermes-todo-operator-defer-store.md) (vendor the Operator, defer a Kafka-native Store).

This doc captures the design for the third tool vendored into the existing
`components/hermes-agent/` component (vendor-by-source: hermes → shell + files +
web_search + **todo**), following the todos research report
(`docs/tools-research/reports/todos-report.md`) and a grilling session that fixed the
domain language (Operator / Store / Session — now in `CONTEXT.md`).

---

## 1. Purpose & scope

Expose a per-session **task list** tool (`todo`) by vendoring hermes-agent's todo
implementation. This phase scopes the **vendor only**. Two things are deliberately
**deferred** to a later, separately-designed phase (both gated on the calfkit Kafka
contract, `NODE.md`, which is undefined repo-wide — the shell+file port's Stage D):

- The calfkit **node adapter** (`node/`) that maps `todo` onto the Kafka contract.
- The durable, multi-tenant **Store** (the pluggable state layer; a **Faust table** is
  the confirmed eventual implementation). See §6 — which records real, **blocking**
  dependencies that phase inherits.

The vendor lands the upstream Operator + its regression test, import-rewritten and
attributed, so the tool registers and unit-tests today; everything stateful and
calfkit-specific is built later behind the Store seam defined in §6.

## 2. Source & provenance

- **Source:** `NousResearch/hermes-agent` @ `5a36f76a00cc448948856a5c1b52710aafec264e`,
  MIT © 2025 Nous Research. **Same pin** as the existing component — no version bump
  (`METADATA.yaml` `revision`/`retrieved` unchanged; the new files share the pin).
- **Upstream paths:** `tools/todo_tool.py` (277 LOC) and `tests/tools/test_todo_tool.py`.
- **License:** MIT — classified before copying; component `LICENSE` is the verbatim
  upstream MIT. `todo_tool.py` carries no "Ported from" header → no secondary-origin
  attribution. The component's `THIRD_PARTY_NOTICES.md` entry already exists (web-tools
  landed on `main`); `todo` was added to its `Tools:` line.

## 3. Vendor set

| Upstream file | Lands at | Transform |
|---|---|---|
| `tools/todo_tool.py` | `src/calfkit_hermes/_vendor/tools/todo_tool.py` | **verbatim, no calfkit header** (provenance in `METADATA.yaml`); 1 internal import rewritten |
| `tests/tools/test_todo_tool.py` | `tests/test_vendored_todo_tool.py` | verbatim + the 2-line `# Vendored from …` banner; 1 import hand-edited |

- **NEW vendored files: 1** (`todo_tool.py`). **NEW shims: 0. No `patches/`** this phase.
- **Header convention** (matches existing `_vendor/` files): impl files are byte-for-byte
  upstream with **no** calfkit header — provenance lives in `METADATA.yaml`. Only the
  **test** file gets the 2-line banner used by the existing `test_vendored_*` tests.
- **Import rewrite (impl):** `from tools.registry import registry, tool_error`
  → `from calfkit_hermes._vendor.tools.registry import registry, tool_error`
  (`todo_tool.py:267`; this import sits mid-file, just above the `register(...)` call —
  intentional, do not move). It is the module's only internal import — no `agent.*`,
  `hermes_cli.*`, `utils`, i18n, redact, or interrupt edges. `registry` (the singleton at
  `_vendor/tools/registry.py:549`) and `tool_error` (`:568`) are already vendored.
- **Import rewrite (test):** `from tools.todo_tool import …`
  → `from calfkit_hermes._vendor.tools.todo_tool import …` (hand-edited; the test is pure
  stdlib `import json`, no fixtures/env).

**Excluded (reaffirms the existing cut):** `tools/kanban_tools.py` + `hermes_cli/kanban_db.py`
— the persistent SQLite variant (~9k LOC; the `METADATA.yaml` `cut:` note already records
the kanban cut). Its single-shared-file SQLite assumption is exactly what calfkit's
no-shared-filesystem model cannot make; its CAS+heartbeat coordination is a design
reference only.

## 4. Registration & import-rewrite mechanics

Registration is **automatic** (proven by execution): `discover_builtin_tools()`
(`_vendor/tools/registry.py:57`) globs `*.py`, AST-checks each for a top-level
`registry.register(...)` (`_module_registers_tools`, defined `:42`), and imports the
survivors under `__package__` (the calfkit local-mod at `:60-64`). `todo_tool.py` has its
`registry.register(name="todo", toolset="todo", …)` at module level (`:269`) and is not in
the exclusion set (`:68`), so the tool registers on import; `toolset="todo"` is new (no
shadow/alias conflict). `todo_tool.py` has no `__file__`-relative or dynamic imports, so
the shell+file port's 4 manual fix-ups do not apply.

## 5. Terminology (now in `CONTEXT.md`)

The grill resolved three terms, added to the glossary: **Operator** (a vendored tool's
stateless logic; the vendored `TodoStore` is an Operator in our terms despite its name),
**Store** (a pluggable, calfkit-owned state layer behind an Operator, keyed by
`session_key`; the Faust table is one implementation; parallels Provider but for *state*),
and **Session** (the scope a stateful tool's state belongs to — one agent run; starts
empty per session; "durable" = survives node restart *within* the session).

## 6. Statefulness model & the deferred Store (design intent + blocking dependencies)

Upstream, `TodoStore` holds `self._items: list[dict]` and is instantiated once per
`AIAgent` (one per session) and passed into the handler; lifetime is GC of the agent
object. A calfkit node is long-lived with no `AIAgent`, serving many sessions, so the node
must own that lifecycle. The deferred design — and the dependencies that phase **must not
treat as free**:

- **Store interface:** `get(session_key) -> list[dict]`, `put(session_key, list)`,
  `delete(session_key)`. Value = the whole list for the session (todos are tiny).
- **Operator usage + seed seam.** Per request the node seeds a fresh `TodoStore` from
  `store.get(session_key)`, applies `write(todos, merge)` (or a read), and persists with
  `store.put(...)` **only for a write**. Two caveats the deferred node must handle:
  - **The vendor lands unpatched today, but the deferred node will want a 1-line seed seam.**
    Seeding by replaying `write(stored_list)` re-runs `_validate`/`_dedupe_by_id` every
    call, which can collapse two items with empty/`"?"` ids into one (silent task loss).
    The clean seam is a constructor overload — `__init__(self, items=None)` — in `patches/`
    *in the node phase*; not a behavioral change to the Operator.
  - **Reads must skip `put()`** — the seed itself is a `write()`; persisting on reads would
    cause changelog write-amplification and re-trigger the id-collapse.
- **Concurrency invariant (load-bearing).** `merge=True` is a read-modify-write; correctness
  **depends on single-writer-per-session ordering**. Co-partitioning the request stream and
  table by `session_key` is therefore the **concurrency-control mechanism**, not just a
  read-consistency nicety: every `todo` request type (read *and* write) must be
  `group_by(session_key)`-repartitioned onto the same co-partitioned agent.
- **Read-your-writes — conditional.** Holds *provided* the above co-partitioning of every
  request type, bounded by changelog acknowledgement; across a rebalance it is consistent
  but pausing (the table shard is rebuilt from the changelog before the partition resumes).
- **Lifecycle / eviction is a BLOCKING `NODE.md` dependency, not a free property.** Log
  compaction does **not** evict distinct `session_key`s (only collapses duplicates of one
  key), so without a delete trigger the changelog grows unbounded across sessions. A
  session-end tombstone presupposes a session-end signal calfkit may not emit (confirm
  against the Kafka contract); the fallback is a periodic sweeper that tombstones idle keys
  via a last-touch timestamp. (A windowed table is **not** viable — Faust windowed tables
  are time-bucketed aggregations, not per-key idle eviction.)
- **Out of scope for the node:** `TodoStore.format_for_injection()` (post-compaction
  re-injection) is agent-context machinery the node does not own; it vendors verbatim but is
  never called by the node and does not deliver a compaction-survival capability.

## 7. What we hand-roll (net, this phase)

**Nothing.** The vendor is the upstream Operator + its test, import-rewritten. All
first-party code (Store interface, in-memory + Faust implementations, the node adapter, the
1-line seed seam, eviction) belongs to the **deferred** phase, gated on `NODE.md`.

## 8. Test gate

- **Vendored regression:** `tests/test_vendored_todo_tool.py` (upstream's own tests:
  write/read/dedupe/merge/`format_for_injection`/the `todo_tool()` function incl. the
  no-store→error path).
- **Registration smoke:** `"todo"` added to `EXPECTED_TOOLS` in `tests/test_import_smoke.py`
  (matches the existing `get_entry(name) is not None` assertion shape).
- **Vendor-only dispatch contract (new test):** `json.loads(registry.dispatch("todo", {}))
  ["error"] == "TodoStore not initialized"` — pins "registered ⇒ clean error, never a crash"
  in the store-less vendor phase. **Dispatch is intentionally an error path** until the
  node/store phase.
- **Ghost-capability caveat:** registering `todo` advertises an available-but-inert tool;
  harmless **only while the component is build/test-only** (not wired to a live model). The
  node phase wires the store before exposing `todo` to a model.
- **Gate:** `uv run pytest` green in the component; build→import→discover registers `todo`.

## 9. Provenance & notices (done this phase)

- **`METADATA.yaml`:** added `tools/todo_tool.py` to the file list; `provides` →
  `[shell, files, web_search, web_extract, todo]` (+ comment-block line); a
  `local_modifications` note (single import rewrite; vendored Operator, Store deferred);
  reaffirmed the kanban `cut:` entry; `revision`/`retrieved` unchanged (same pin).
- **`THIRD_PARTY_NOTICES.md`:** added `todo` to the existing hermes-agent component entry's
  `Tools:` line.
- **Component `README.md`:** added the `todo` bullet (and the previously-missing web bullet)
  under "Exposes".
- **`CONTEXT.md`:** added the Operator / Store / Session glossary terms.

## 10. Commit structure & next steps

- **Commit 1 (verbatim):** `todo_tool.py` + `test_vendored_todo_tool.py` byte-for-byte.
- **Commit 2 (adapt):** import rewrites + test banner + `METADATA.yaml` + `README.md` +
  `THIRD_PARTY_NOTICES.md` + `CONTEXT.md` + the two smokes + this doc + ADR-0003.
- **Gate:** `uv run pytest` (§8).
- **Deferred (next design):** the Store interface + in-memory/Faust implementations + the
  `node/` Kafka adapter — gated on `NODE.md`. That design **must** resolve the §6 blocking
  dependencies (the 1-line seed seam, read-skips-`put()`, single-writer-per-session
  ordering, and a session-end signal *or* an idle sweeper).
