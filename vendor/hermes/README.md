# hermes (calfkit-tools source)

The `hermes` source of **calfkit-tools** — vendoring the **shell, file, web, and todo
toolsets** from
[NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent) (MIT).

Exposes (over the calfkit Kafka contract — see `NODE.md`, Stage D):
- **shell:** `terminal`, `process`, `execute_code` (Programmatic Tool Calling)
- **files:** `read_file`, `write_file`, `patch`, `search_files`
- **web_search / web_extract:** query → ranked links / URLs → content (provider system: ddgs / brave-free / searxng / tavily)
- **todo:** `todo` — per-session task list (id-keyed; merge/replace; durable Store deferred — Faust table)

## Provenance & license

- Upstream: `NousResearch/hermes-agent` @ `5a36f76` — see `METADATA.yaml`.
- License: MIT © Nous Research (`LICENSE`, verbatim). Secondary origins (opencode,
  nearai/ironclaw, free-code) recorded in `METADATA.yaml` and the repo
  `THIRD_PARTY_NOTICES.md`.

## Structure

- `src/calfkit_tools/hermes/_vendor/` — the vendored upstream tree (import-rewritten).
- `src/calfkit_tools/hermes/_shims/` — thin replacements for hermes' app-runtime edges.
- `src/calfkit_tools/hermes/node/` — the calfkit tool nodes (Stage D): one deployable
  `ToolNodeDef` per tool, thin wrappers over the upstream registry seam.
- `tests/hermes/` — vendored upstream tests (regression) + node-layer tests.
- `vendor/hermes/patches/` — re-appliable functional changes to vendored code.
- `vendor/hermes/` (this dir) — provenance only (`LICENSE`, `METADATA.yaml`, `NODE.md`).

## Status

**Stage D — complete.** All ten tools are deployable calfkit nodes
(`from calfkit_tools.hermes.node import terminal, ..., HERMES_NODES`) with in-memory
multitenancy keyed per calling agent (ADR-0004). See `NODE.md` for the node
contract, deps/resource wiring, configuration, and trust model;
[`../../docs/design/node-port.md`](../../docs/design/node-port.md) for the design.
Durable state and security hardening (env allowlist, sandbox enforcement) remain
deferred — [`../../docs/design/shell-file-tool-port.md`](../../docs/design/shell-file-tool-port.md) §6.1.
