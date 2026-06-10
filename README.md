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

**Stage D landed:** all eleven vendored tools are deployable calfkit tool nodes
(calfkit ≥ 0.9.0, 1:1 tool→node, in-memory multitenancy keyed per calling agent
— ADR-0004). Import-and-deploy:

```python
from calfkit_hermes.node import HERMES_NODES          # terminal, process, files, todo, web, execute_code
from calfkit_pydantic_web_fetch.node import web_fetch
```

See [`docs/design/node-port.md`](docs/design/node-port.md) and each component's
`NODE.md` for contracts, wiring, and the trust model. Durable state and security
hardening remain deferred.
