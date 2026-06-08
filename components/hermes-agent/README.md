# calfkit-hermes

calfkit node component vendoring the **shell + file toolset** from
[NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent) (MIT).

Exposes (over the calfkit Kafka contract — see `NODE.md`, Stage D):
- **shell:** `terminal`, `process`, `execute_code` (Programmatic Tool Calling)
- **files:** `read_file`, `write_file`, `patch`, `search_files`

## Provenance & license

- Upstream: `NousResearch/hermes-agent` @ `5a36f76` — see `METADATA.yaml`.
- License: MIT © Nous Research (`LICENSE`, verbatim). Secondary origins (opencode,
  nearai/ironclaw, free-code) recorded in `METADATA.yaml` and the repo
  `THIRD_PARTY_NOTICES.md`.

## Structure

- `src/calfkit_hermes/_vendor/` — the 42-file vendored upstream tree (import-rewritten).
- `src/calfkit_hermes/_shims/` — thin replacements for hermes' app-runtime edges.
- `src/calfkit_hermes/node/` — calfkit Kafka adapter + tool wrappers (Stage D — deferred).
- `tests/` — vendored upstream tests (regression) + adapter tests.
- `patches/` — re-appliable functional changes to vendored code.

## Status

Vendored verbatim; import-rewrite + shims in progress on `vendor/hermes-shell-file`.
**Stage D (Kafka adapter + security + multi-tenancy) is deferred** — see
[`../../docs/design/shell-file-tool-port.md`](../../docs/design/shell-file-tool-port.md) §6, §12.
Not yet runnable as a node.
