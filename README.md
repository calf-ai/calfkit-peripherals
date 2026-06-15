# 🐮 calfkit-tools

[![PyPI version](https://img.shields.io/pypi/v/calfkit-tools.svg)](https://pypi.org/project/calfkit-tools/)
[![Tests](https://github.com/calf-ai/calfkit-peripherals/actions/workflows/test.yml/badge.svg)](https://github.com/calf-ai/calfkit-peripherals/actions/workflows/test.yml)
[![Coverage](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/calf-ai/calfkit-peripherals/python-coverage-comment-action-data/endpoint.json)](https://github.com/calf-ai/calfkit-peripherals/tree/python-coverage-comment-action-data)
[![License: Apache-2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](./LICENSE)

A single distribution where **calfkit** vendors agent tools ported from many upstream
open-source codebases into one place.

calfkit's tool interface is **Kafka-based** (language-agnostic): a "node tool" is a process
that consumes a request topic and produces a reply. This repo packages those tools as one
installable distribution, **`calfkit-tools`** — organised internally by the upstream source
each tool was vendored from, each a subpackage exposing its tools over the calfkit Kafka
contract.

## Layout

- `src/calfkit_tools/<source>/` — one subpackage per upstream source (e.g. `hermes`,
  `web_fetch`); its tool nodes live under `<source>/node/`.
- `vendor/<source>/` — provenance only (`LICENSE`, `METADATA.yaml`, `NODE.md`, `README.md`,
  `patches/`). Never on `sys.path`, never in the wheel.
- `vendor/_template/` — copy this to start a new source.
- `tests/<source>/` — per-source test suites.
- `pyproject.toml` — the single `calfkit-tools` distribution (deps + extras).
- `docs/project-structure.md` — **start here** to add a source.
- `docs/reference/tool-contracts.md` — roll-up of every tool node's interface
  contract (params, deps/resource wiring, env config, reply shapes); each
  source's `vendor/<source>/NODE.md` stays authoritative.
- `docs/design/` — per-tool design docs (e.g. the hermes-agent shell+file port).
- `docs/tools-research/` — the upstream survey that informed which tools to vendor.
- `THIRD_PARTY_NOTICES.md` — aggregated attribution index (per-source `vendor/<source>/LICENSE`
  + `METADATA.yaml` are authoritative).

## Install

One distribution covers every default tool path:

```bash
pip install calfkit-tools          # or: uv add calfkit-tools
```

The base install runs all tools with their default backends. Three **opt-in** extras enable
remote shell-execution backends only (the default local backend needs none of them):

```bash
pip install "calfkit-tools[shell-docker]"   # docker backend
pip install "calfkit-tools[shell-modal]"    # modal backend
pip install "calfkit-tools[shell-daytona]"  # daytona backend
pip install "calfkit-tools[all]"            # all three
```

## Adding a source

Add code under `src/calfkit_tools/<source>/`, provenance under `vendor/<source>/`, tests under
`tests/<source>/`, and any new deps/extras to the root `pyproject.toml`. Follow
[`docs/project-structure.md`](docs/project-structure.md). Vendor **by source** (license-first,
per the `open-source-vendoring-best-practices` conventions), expose **by tool**, and keep
upstream code import-rewritten under `_vendor/`.

## Status

**Stage D landed:** all eleven vendored tools are deployable calfkit tool nodes
(calfkit ≥ 0.9.0, 1:1 tool→node, in-memory multitenancy keyed per calling agent
— ADR-0004). Import-and-deploy:

```python
from calfkit_tools.hermes.node import HERMES_NODES          # terminal, process, files, todo, web, execute_code
from calfkit_tools.web_fetch.node import web_fetch
```

See [`docs/design/node-port.md`](docs/design/node-port.md) and each source's
`vendor/<source>/NODE.md` for contracts, wiring, and the trust model. Durable state and
security hardening remain deferred.
