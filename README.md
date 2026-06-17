# 🐮 calfkit-tools

[![PyPI version](https://img.shields.io/pypi/v/calfkit-tools.svg)](https://pypi.org/project/calfkit-tools/)
[![Python versions](https://img.shields.io/pypi/pyversions/calfkit-tools.svg)](https://pypi.org/project/calfkit-tools/)
[![Tests](https://github.com/calf-ai/calfkit-peripherals/actions/workflows/test.yml/badge.svg)](https://github.com/calf-ai/calfkit-peripherals/actions/workflows/test.yml)
[![Coverage](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/calf-ai/calfkit-peripherals/python-coverage-comment-action-data/endpoint.json)](https://github.com/calf-ai/calfkit-peripherals/tree/python-coverage-comment-action-data)
[![License: Apache-2.0 AND MIT](https://img.shields.io/badge/License-Apache--2.0%20AND%20MIT-blue.svg)](./LICENSE)

> A curated, pip-installable bundle of AI-agent tools — shell, files, code execution, web, and
> todo — each deployable as a [calfkit](https://github.com/calf-ai) tool node over Kafka.

`calfkit-tools` packages battle-tested agent tools, ported from upstream open-source projects
(license and provenance preserved), into one distribution. Every tool ships as a calfkit
`ToolNodeDef`: import it, run it as a node, and it serves over the Kafka mesh that calfkit
agents call.

- **One install, eleven tools** — shell, file ops, code execution, web search/fetch, and task tracking.
- **Vendor by source, expose by tool** — each tool is ported from a named upstream (e.g.
  NousResearch/hermes-agent, pydantic-ai) and kept self-contained with its own provenance.
- **Deploy as nodes** — 1:1 tool→node; runs locally out of the box, with no remote backend required.

## Contents

- [Install](#install)
- [Quickstart](#quickstart)
- [Available tools](#available-tools)
- [Importing a specific tool](#importing-a-specific-tool)
- [Security](#security)
- [Deploying tools as nodes](#deploying-tools-as-nodes)
- [Contributing](#contributing)
- [License](#license)

## Install

```bash
pip install calfkit-tools          # or: uv add calfkit-tools
```

Requires Python 3.11+. The base install runs every tool with its default backend (shell and
code execution run locally).

Optional extras add **remote shell-execution backends** for the `terminal`, `process`, and
`execute_code` tools — the default local backend needs none of them:

```bash
pip install "calfkit-tools[shell-docker]"    # run shell commands in Docker
pip install "calfkit-tools[shell-modal]"     # ... in Modal sandboxes
pip install "calfkit-tools[shell-daytona]"   # ... in Daytona sandboxes
pip install "calfkit-tools[all]"             # all three
```

## Quickstart

Each tool is a calfkit node. Serve one on the Kafka mesh with the `calfkit run` dev command:

```bash
calfkit run calfkit_tools.hermes.node:terminal
```

The node consumes `tool.terminal.input` and replies on `tool.terminal.output` — any calfkit
agent on the mesh can now call `terminal`. Host several tools in one worker by pointing at the
bundle:

```bash
calfkit run calfkit_tools.hermes.node:HERMES_NODES   # all ten hermes tools in one worker
```

To wire tools into a worker programmatically, add the imported nodes to a calfkit `Worker`
(which takes a `Client` for the Kafka connection — see the calfkit SDK for setup):

```python
from calfkit_tools.hermes.node import terminal, read_file, write_file

worker.add_nodes(terminal, read_file, write_file)
```

## Available tools

Eleven tools across two vendored sources. Import each from its source's `node` package.

### hermes — vendored from [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent) (MIT)

```python
from calfkit_tools.hermes.node import terminal, read_file, web_search  # etc.
```

| Tool | What it does |
| --- | --- |
| `terminal` | Execute a shell command in the session's terminal environment. |
| `process` | Manage background processes started with `terminal(background=True)`. |
| `read_file` | Read a text file with line numbers and pagination. |
| `write_file` | Write content to a file, completely replacing existing content. |
| `patch` | Make targeted find-and-replace edits in files. |
| `search_files` | Search file contents or find files by name. |
| `execute_code` | Run a Python script that can call the hermes tools programmatically. |
| `todo` | Manage a per-session task list (binds the `InMemoryTodoStore` resource). |
| `web_search` | Search the web and return ranked links. |
| `web_extract` | Extract readable content from one or more web pages. |

`HERMES_NODES` bundles all ten tools above; `InMemoryTodoStore` is the worker-lifetime store
the `todo` node binds as a resource.

### web_fetch — vendored from [pydantic/pydantic-ai](https://github.com/pydantic/pydantic-ai) (MIT)

```python
from calfkit_tools.web_fetch.node import web_fetch
```

| Tool | What it does |
| --- | --- |
| `web_fetch` | Fetch a single URL through an SSRF-protected fetcher and return its content as markdown (binary responses come back base64-encoded with their media type). |

## Importing a specific tool

Every tool follows one import shape:

```python
from calfkit_tools.<source>.node import <tool>
```

where `<source>` is the upstream project the tool was vendored from. For example:

```python
from calfkit_tools.hermes.node import terminal, search_files, web_search
from calfkit_tools.web_fetch.node import web_fetch
```

Tools are grouped by source so each vendored tree stays self-contained — its own provenance,
license, and tests. See [`docs/reference/tool-contracts.md`](docs/reference/tool-contracts.md)
for each tool's full parameter, resource-wiring, and reply-shape contract.

## Security

Several tools act on the host running the node — review these before deploying:

- `terminal`, `process`, and `execute_code` **run real commands**. On the default local
  backend they run directly on the host; use a remote shell backend
  (`[shell-docker]` / `[shell-modal]` / `[shell-daytona]`) to sandbox them.
- `write_file` and `patch` modify the real filesystem; `read_file` and `search_files` read it.
- `web_fetch` is SSRF-guarded by default: private/loopback addresses are blocked, requests time
  out after 30s, and content is capped.

State is held in memory and isolated per calling agent ([ADR-0004](docs/adr/0004-node-tenancy-key-and-in-memory-state.md)),
so run one process per stateful node. Durable state and further hardening are on the roadmap.

## Deploying tools as nodes

Each tool is a 1:1 calfkit `ToolNodeDef` serving name-derived topics (`tool.<name>.input` /
`tool.<name>.output`). For node contracts, deps/resource wiring, env configuration, and the
trust model, see:

- [`docs/design/node-port.md`](docs/design/node-port.md) — the node-port design.
- [`docs/reference/tool-contracts.md`](docs/reference/tool-contracts.md) — roll-up of every
  node's interface contract.
- `vendor/<source>/NODE.md` — the authoritative, per-source contract.

## Contributing

`calfkit-tools` grows by vendoring new tools from upstream projects, one source at a time. See
[`CONTRIBUTING.md`](CONTRIBUTING.md) for the dev setup and how to add a source.

## License

Apache-2.0 (first-party glue) AND MIT (vendored upstream trees under `_vendor/`). The
per-source `vendor/<source>/LICENSE` and `METADATA.yaml` files are authoritative; see
[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md) for the aggregated attribution index.
