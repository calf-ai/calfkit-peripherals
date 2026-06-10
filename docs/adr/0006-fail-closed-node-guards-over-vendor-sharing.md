# Fail closed at the node where upstream's single-tenant sharing model conflicts with multitenancy

**Status:** accepted · 2026-06-10

## Context

hermes was built single-tenant: several of its behaviors deliberately *share*
state or *widen* access in ways that are correct for one user on one box and
wrong for a multi-tenant node. Stage D's deep reviews surfaced three such
spots. The repo's vendoring philosophy forbids casual vendor surgery, so the
question was: patch the vendor, document the hazard, or guard at the node?

## Decision

**Guard at the node, fail closed, never patch the vendor for this.** Three
guards shipped:

1. **Process handle ownership.** Upstream scopes only `process(action="list")`
   by task_id; poll/log/wait/kill/write/submit/close resolve the globally
   unique `proc_*` handle directly — deliberate upstream sharing that lets one
   tenant control another's process if a handle leaks. The `process` wrapper
   verifies ownership before any handle action by running a `list` scoped to
   the caller's `session_key` (still the public seam; upstream's list includes
   finished processes, so owners can inspect exited ones) and replies with
   upstream's own not-found shape on failure — **deny-as-not-found**, never
   revealing that a foreign handle exists. `submit` is guarded alongside
   `write` (both push raw stdin).
2. **`execute_code` tool-set fallback.** Upstream expands an *empty*
   `enabled_tools ∩ SANDBOX_ALLOWED_TOOLS` intersection back to the FULL tool
   set — so a typo'd restrictive `EXECUTE_CODE_ENABLED_TOOLS` would silently
   grant everything. The node refuses (error reply) instead of dispatching.
3. **Tenancy key derivation.** `session_key()` raises on a missing/empty
   `agent_name` rather than minting a shared `"None:default"` bucket for all
   unstamped callers.

## Considered and rejected

- **Vendor patches** (scope handle lookups by task_id; remove the full-set
  fallback) — against the thin-vendor philosophy, and every re-sync would
  carry a security-load-bearing patch. Node guards live in first-party code.
- **Document-only** (treat `proc_*` handles as unguessable capability tokens)
  — handles appear in tool replies that flow through model contexts and logs;
  "unguessable" is not "unleakable". The node *advertises* per-agent isolation
  (ADR-0004); shipping a documented hole in the advertised property fails the
  least-surprise test.
- **Falling back to the default tool set on an empty intersection** — quieter,
  but a *restrictive* config silently granting more than it names is exactly
  the failure mode being removed; an explicit error is observable and
  diagnosable.

## Consequences

- Handle actions cost one extra `list` dispatch per call (negligible; kept at
  the public seam by design — flagged and accepted in review).
- The remaining shared-by-design vendor behavior — the global 64-entry
  `MAX_PROCESSES` LRU — is **accepted and documented** (NODE.md) rather than
  guarded: bounding it per-tenant has no public seam and the in-memory,
  one-process deployment model bounds the blast radius.
- Pattern for future ports: when a vendored behavior assumes single-tenant
  trust, prefer a first-party fail-closed guard at the node boundary over
  vendor surgery or documentation alone.
