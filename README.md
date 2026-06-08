# 🐮 calfkit-peripherals

Shared monorepo where **calfkit** vendors agent tools ported from many upstream open-source codebases into one place.

calfkit's tool interface is **Kafka-based** (language-agnostic): a "node tool" is a process
that consumes a request topic and produces a reply. So this repo is a **polyglot
monorepo of node components** — each component vendors one upstream source and exposes
its tools over the calfkit Kafka contract.

## Layout

- `components/<source>/` — one self-contained node component per upstream source.
- `components/_template/` — copy this to start a new port.
- `docs/project-structure.md` — **start here** to add a port.
- `docs/design/` — per-tool design docs (e.g. the hermes-agent shell+file port).
- `docs/tools-research/` — the upstream survey that informed which tools to vendor.
- `THIRD_PARTY_NOTICES.md` — aggregated attribution index (per-component `LICENSE` +
  `METADATA.yaml` are authoritative).

## Adding a port

Copy `components/_template/` → `components/<source>/` and follow
[`docs/project-structure.md`](docs/project-structure.md). Vendor **by source**
(license-first, per the `open-source-vendoring-best-practices` conventions), expose **by tool**, and
keep upstream code import-rewritten under `_vendor/`.

## Status

Foundation + conventions established on `main`. The first port (hermes-agent shell+file)
is in progress on a branch — see [`docs/design/shell-file-tool-port.md`](docs/design/shell-file-tool-port.md).
