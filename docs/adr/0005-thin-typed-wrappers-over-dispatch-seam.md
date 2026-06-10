# Wrap vendored tools as thin typed functions over the upstream dispatch seam

**Status:** accepted · 2026-06-10

## Context

Stage D needs each vendored tool to become a calfkit tool node. calfkit derives
a node's LLM-facing schema from a Python function's type hints + docstring,
while hermes defines its tools as hand-written JSON-schema dicts dispatched
through `registry.dispatch(name, args, **kw)` (returning a JSON *string*).
Something has to bridge the two schema worlds, and how it bridges fixes the
contract agents see forever.

## Decision

Each node is a **typed function that mirrors the upstream tool's *advertised*
schema** — same names, types, defaults, required-ness, enums (`Literal`), and
numeric bounds (`Annotated[..., Field(...)]`) — whose body is a single
`dispatch(...)` call to the upstream seam. Specifics of the seam contract:

1. **Absent ≡ None.** `dispatch()` drops `None`-valued args before
   `registry.dispatch`. Wrappers pass every parameter explicitly; the seam
   restores "absent means upstream default". (Verified against every
   registered handler: all read args via `args.get(...)`. Without the drop,
   a present-`None` key clobbers non-None upstream defaults — this broke
   `process(action="log")`, whose pagination defaults are `0`/`200`.)
2. **Replies are parsed.** Upstream handlers return JSON strings; the seam
   `json.loads` them so agents receive structured dicts (raw passthrough if
   unparseable). Content is upstream's, framing is calfkit-native.
3. **Errors split by origin.** Upstream's `{"error": ...}` dicts pass through
   as ordinary replies (that is the tool's own vocabulary); Python exceptions
   propagate and become calfkit `FailedToolCall`s.
4. **Advertised schema wins over runtime salvage.** Where upstream's schema
   and its runtime validation disagree, the node mirrors the **schema**. The
   visible consequence is `todo`: `TODO_SCHEMA` marks `id`/`content`/`status`
   required, so the node's `TodoItem` rejects partial items that upstream's
   `_validate` would have salvaged (`id="?"`, `status="pending"`). Pinned by
   tests as deliberate, not accidental.

## Considered and rejected

- **Hand the upstream JSON-schema dicts to calfkit directly** — calfkit 0.9.0
  has no raw-schema entry point; its `Tool(func)` derivation is the contract.
- **Loosen `todo` items (`total=False`) to reproduce upstream's salvage** —
  would make the LLM-facing schema diverge from upstream's advertised one;
  calfkit's `FailedToolCall` already gives the model a structured retry, which
  is the framework-native equivalent of forgiveness.
- **Return raw JSON strings** — double-encodes the payload inside calfkit's
  JSON envelope for zero benefit.
- **Conditional kwargs instead of seam-level drop-None** — re-implements the
  same rule in every wrapper; one seam rule is testable once.

## Consequences

- Schema drift is test-enforced: every node has schema-sanity tests comparing
  param names/required/enums/bounds against the live upstream constants.
- The one knowingly non-verbatim schema field: `patch.mode` defaults to
  `"replace"` instead of being required — byte-identical behavior to
  upstream's own handler default, so the derived schema simply has no required
  params there.
