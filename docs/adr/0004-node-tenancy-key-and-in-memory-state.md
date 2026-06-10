# Scope node-layer tool state by agent identity + client-wired session id, in memory, through hermes' own `task_id` seam

**Status:** accepted · 2026-06-10

## Context

Stage D wraps each vendored tool as a calfkit tool node. calfkit's `ToolContext`
carries no built-in session/tenant field — only `agent_name` (the calling agent's
node id, stamped from the unspoofable `x-calf-emitter` Kafka header), `run_id`
(one invocation/turn), and `deps` (a client-supplied JSON dict). Meanwhile the
stateful hermes tools (shell, files, processes, todo) key all their internal
registries by a `task_id` that upstream collapses to `"default"` — i.e. zero
isolation out of the box. calfkit requires multitenancy: one agent's shell must
not affect another's.

## Decision

1. **Tenancy key:** every stateful tool call derives
   `session_key = f"{ctx.agent_name}:{ctx.deps.get('session_id', 'default')}"`.
   The default scope is **agent-lifetime** — state persists across invocations by
   the same agent. When one agent deployment serves many end-users, the invoking
   client partitions state by wiring `deps={"session_id": ...}`. Neither half is
   agent-authored: the prefix comes from transport, the refinement from the
   client — so a forged `session_id` can never reach another agent's state.
2. **Isolation mechanism:** the node passes `session_key` into the vendored code
   as its `task_id` (via `registry.dispatch(name, args, task_id=session_key)`).
   hermes' own per-`task_id` registries (terminal environments, file trackers,
   process registry) then provide the isolation — no surgery on vendor internals.
   **Amendment (same day, found by TDD):** for the shell/process registries the
   premise holds only for *registered* task_ids — `_resolve_container_task_id`
   deliberately collapses unregistered ids to `"default"` (upstream wants
   subagents to share the parent's container). Upstream's documented escape
   hatch for "rollouts [that] need their own isolated sandbox" is
   `register_task_env_overrides(task_id, {...})`, called by infrastructure
   code — which is what our node is. So `dispatch()` registers each
   `session_key` (with empty overrides — a no-op at the only read site) before
   dispatching. Still no vendor patch; the seam is upstream's own.
3. **State is in-memory only.** Lost on node restart; the durable/Kafka-backed
   Store variants (ADR-0003's Faust table) stay deferred. This *amends*
   ADR-0003's "in-memory is dev-only" stance: the in-memory Store is the shipped
   variant for now, by explicit owner decision. Consequence: a stateful node is
   correct at **one process per node** (calfkit partitions messages by
   correlation id, not session, so horizontal scaling would scatter sessions).

## Considered and rejected

- **Per-run default scope (`run_id`)** — glossary-pure ("session = one agent
  run") but resets shell cwd/todos every turn for callers that wire nothing.
  Owner ruled the glossary wrong instead; CONTEXT.md's *Session* entry was
  rewritten to agent-lifetime-by-default.
- **Re-keying hermes' global registries on a new `session_key` contextvar**
  (shell-file design doc §12's original sketch) — rejected as deep vendor
  surgery; feeding `session_key` through the existing `task_id` parameter
  achieves the same re-keying at the public seam.
- **Requiring `deps["session_id"]` on every call** — most explicit, but breaks
  plug-and-play; rejected by owner.
