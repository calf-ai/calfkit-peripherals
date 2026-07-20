# 🐮 calfkit-tools

[![PyPI version](https://img.shields.io/pypi/v/calfkit-tools.svg)](https://pypi.org/project/calfkit-tools/)
[![Python versions](https://img.shields.io/pypi/pyversions/calfkit-tools)](https://pypi.org/project/calfkit-tools/)
[![Coverage](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/calf-ai/calfkit-peripherals/python-coverage-comment-action-data/endpoint.json)](https://github.com/calf-ai/calfkit-peripherals/tree/python-coverage-comment-action-data)
[![License: Apache-2.0 AND MIT](https://img.shields.io/badge/License-Apache--2.0%20AND%20MIT-blue.svg)](https://github.com/calf-ai/calfkit-peripherals/blob/main/LICENSE)

Tools for [calfkit](https://github.com/calf-ai/calfkit-sdk) agents — shell, files, code, web, and todo — each deployable as a tool node.

[calfkit](https://github.com/calf-ai/calfkit-sdk) is an SDK for building AI agents as
distributed, event-driven microservices over Kafka. `calfkit-tools` gives those agents a
ready-made toolbox of eleven tools. Every tool is a calfkit `ToolNodeDef` — a small service (a
"node") that you run as a process; it exposes the tool on Kafka topics that calfkit agents call.

- **One install, eleven tools** — shell, file ops, code execution, web search/fetch, and task tracking.
- **Import and go** — every tool is importable straight from `calfkit_tools.tools`, ready to deploy.
- **Deploy as nodes** — 1:1 tool→node; tools run on your own host by default (Docker/Modal/Daytona
  sandboxes are opt-in, not required).

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

Requires Python 3.10+. Installing `calfkit-tools` also pulls in the `calfkit` SDK and its
`ck run` CLI. The base install runs every tool with its default backend (shell and code
execution run locally).

Optional extras add **remote shell-execution backends** for the `terminal`, `process`, and
`execute_code` tools — the default local backend needs none of them:

```bash
pip install "calfkit-tools[shell-docker]"    # run shell commands in Docker
pip install "calfkit-tools[shell-modal]"     # ... in Modal sandboxes
pip install "calfkit-tools[shell-daytona]"   # ... in Daytona sandboxes
pip install "calfkit-tools[all]"             # all three
```

## Quickstart

Each tool is a calfkit node, so running one needs a reachable **Kafka broker** (defaults to
`localhost`; point elsewhere with `--host` / `-H` or the `$CALFKIT_MESH_URL` env var). Serve a tool
on the mesh with the `ck run` dev command:

```bash
ck run calfkit_tools.tools:terminal
```

The node consumes `tool.terminal.input` and replies on `tool.terminal.output` — any calfkit
agent on the mesh can now call `terminal`. Host every tool in one worker with the `ALL_TOOLS`
bundle:

```bash
ck run calfkit_tools.tools:ALL_TOOLS   # all eleven tools in one worker
```

To host tools programmatically, add the imported nodes to a calfkit `Worker` (constructed with a
`Client` for the Kafka connection). See the [calfkit SDK](https://github.com/calf-ai/calfkit-sdk)
for `Client` / `Worker` setup and for how an agent calls a deployed tool:

```python
from calfkit_tools.tools import terminal, read_file, write_file

# `worker` is a configured calfkit Worker — see the calfkit SDK for construction.
worker.add_nodes(terminal, read_file, write_file)
```

## Available tools

Eleven tools, all importable from `calfkit_tools.tools`:

```python
from calfkit_tools.tools import terminal, read_file, web_search, web_fetch  # etc.
```

| Tool | What it does |
| --- | --- |
| `terminal` | Execute a shell command in the session's terminal environment. |
| `process` | Manage background processes started with `terminal(background=True)`. |
| `execute_code` | Run a Python script that can call the other tools programmatically. |
| `read_file` | Read a text file with line numbers and pagination. |
| `write_file` | Write content to a file, completely replacing existing content. |
| `patch` | Make targeted find-and-replace edits in files. |
| `search_files` | Search file contents or find files by name. |
| `web_search` | Search the web and return ranked links. |
| `web_extract` | Extract readable content from one or more web pages. |
| `web_fetch` | Fetch a single URL through an SSRF-protected fetcher and return its content as markdown (binary responses come back base64-encoded with their media type). |
| `todo` | Manage a per-session task list. |

`ALL_TOOLS` is the list of all eleven tool nodes; `InMemoryTodoStore` is the worker-lifetime
store the `todo` tool binds as a resource. Both import from `calfkit_tools.tools` too.

## Importing a specific tool

Import any tool straight from the package root:

```python
from calfkit_tools.tools import terminal, search_files, web_search, web_fetch
```

or pull in the whole set at once:

```python
from calfkit_tools.tools import ALL_TOOLS
```

See
[`docs/reference/tool-contracts.md`](https://github.com/calf-ai/calfkit-peripherals/blob/main/docs/reference/tool-contracts.md)
for each tool's full parameter, resource-wiring, and reply-shape contract.

## Security

**The default local backend is not a sandbox** — `terminal`, `process`, and `execute_code` run
real commands directly on the host machine. Review the blast radius before deploying:

- `terminal`, `process`, and `execute_code` run real commands. On the default local backend they
  run directly on the host; use a remote shell backend
  (`[shell-docker]` / `[shell-modal]` / `[shell-daytona]`) to sandbox them.
- `write_file` and `patch` modify the real filesystem; `read_file` and `search_files` read it.
- `web_fetch` is SSRF-guarded by default: private/loopback addresses are blocked, requests time
  out after 30s, and content is capped.

State is held in memory and isolated per calling agent
([ADR-0004](https://github.com/calf-ai/calfkit-peripherals/blob/main/docs/adr/0004-node-tenancy-key-and-in-memory-state.md)),
so run one process per stateful node. Durable state and further hardening are on the roadmap.

## Deploying tools as nodes

Each tool is a 1:1 calfkit `ToolNodeDef` serving name-derived topics (`tool.<name>.input` /
`tool.<name>.output`). For node contracts, deps/resource wiring, env configuration, and the
trust model, see:

- [`docs/design/node-port.md`](https://github.com/calf-ai/calfkit-peripherals/blob/main/docs/design/node-port.md) — the node-port design.
- [`docs/reference/tool-contracts.md`](https://github.com/calf-ai/calfkit-peripherals/blob/main/docs/reference/tool-contracts.md) — roll-up of every tool's interface contract.

## Contributing

Contributions are welcome. See
[`CONTRIBUTING.md`](https://github.com/calf-ai/calfkit-peripherals/blob/main/CONTRIBUTING.md) for
the dev setup and how to add a tool. Questions and bug reports are welcome at
[GitHub Issues](https://github.com/calf-ai/calfkit-peripherals/issues).

## License

Licensed under [Apache-2.0](https://github.com/calf-ai/calfkit-peripherals/blob/main/LICENSE). The
distribution also bundles third-party components under the MIT license; see
[`THIRD_PARTY_NOTICES.md`](https://github.com/calf-ai/calfkit-peripherals/blob/main/THIRD_PARTY_NOTICES.md)
for the full attribution index. The combined SPDX expression is `Apache-2.0 AND MIT`.
