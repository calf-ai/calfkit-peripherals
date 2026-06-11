# Stage D — the node layer: every vendored tool as a calfkit tool node

> **Note (2026-06-10):** Pre-unification port-design record. Packaging has since been unified into a single `calfkit-tools` distribution — see [`../project-structure.md`](../project-structure.md) for the current layout (`src/calfkit_tools/<source>/`; provenance in `vendor/<source>/`). `components/<source>/` paths and per-component package names (`calfkit-hermes`, `calfkit-pydantic-web-fetch`) below describe the original design and are kept as history.

**Status:** accepted design · 2026-06-10 · governs the `node/` layer of every component
**Decisions recorded:** ADR-0004 (tenancy key + in-memory state); ADR-0002 (env-var
provider selection); ADR-0003 (todo Operator/Store seam). Glossary: `CONTEXT.md`
(*Session*, *Deps (wiring)*, *Resource (wiring)*).

## 1. Goal & constraints

Wrap all vendored tools as **1:1 calfkit tool nodes** — one tool = one
independently deployable node, no multi-tool proxy nodes. The wrapper is a
**thin layer over each tool's public seam**: hermes tools are driven wholesale
through `registry.dispatch(name, args, task_id=..., **kw)`; pydantic web_fetch
through its engine callable. No reaching into vendor internals.

- Multitenancy per ADR-0004: `session_key = f"{ctx.agent_name}:{deps.get('session_id', 'default')}"`,
  fed to hermes as its `task_id`.
- All state in-memory; durable Stores deferred (owner decision).
- `calfkit>=0.9.0,<0.10` becomes a **core dependency** of each component.
- Stateful nodes are correct at one process per node (see ADR-0004 consequence).

## 2. Node inventory (11 nodes)

| Node (func name) | Component | Seam | Sync/async | Stateful |
|---|---|---|---|---|
| `terminal` | hermes-agent | `registry.dispatch("terminal", ...)` | sync (thread pool) | yes — shell env/cwd per session_key |
| `process` | hermes-agent | `registry.dispatch("process", ...)` | sync | yes — background process registry |
| `read_file` | hermes-agent | `registry.dispatch("read_file", ...)` | sync | yes — read trackers |
| `write_file` | hermes-agent | `registry.dispatch("write_file", ...)` | sync | yes |
| `patch` | hermes-agent | `registry.dispatch("patch", ...)` | sync | yes |
| `search_files` | hermes-agent | `registry.dispatch("search_files", ...)` | sync | yes |
| `todo` | hermes-agent | `registry.dispatch("todo", ..., store=seeded)` | sync | yes — node-owned in-memory Store |
| `web_search` | hermes-agent | provider `search()` via web_search_registry | sync (thread pool) | no |
| `web_extract` | hermes-agent | provider `extract()` + `url_safety` guard | async | no |
| `execute_code` | hermes-agent | `registry.dispatch("execute_code", ...)` | sync | yes (bridges to other tools) — **ported last, low priority** |
| `web_fetch` | pydantic-web-fetch | `WebFetchLocalTool.__call__` | async | no |

Node ids/topics are calfkit name-derived (`@agent_tool`): `tool_<name>`,
`tool.<name>.input` / `tool.<name>.output`. Upstream tool names are kept verbatim —
they are the LLM-facing names upstream designed prompts/descriptions around.

## 3. Wrapper shape

Each node is a typed function whose parameters **mirror the upstream tool's JSON
schema** (the schema upstream registered for the LLM), minus injected keys
(`task_id`, `store`), with a leading `ToolContext`:

```python
@agent_tool
def terminal(ctx: ToolContext, command: str, background: bool = False, ...) -> dict:
    """<docstring lifted from the upstream schema description>"""
    return _dispatch("terminal", {...}, session_key=_session_key(ctx))
```

- calfkit derives the LLM schema from type hints + docstring (vendored
  pydantic-ai); `ctx` is auto-hidden from the schema.
- hermes handlers are blocking-sync → wrapper funcs are **sync**; calfkit runs
  them in its thread pool. hermes' own locks make the seam thread-safe.
- `registry.dispatch` returns a JSON **string**; the wrapper parses it and
  returns the dict (raw string fallback if unparseable) so agents receive
  structured JSON, content identical to upstream.
- Upstream error convention (`{"error": ...}` dicts) is passed through as-is.
  Exceptions that escape (e.g. `WebFetchError`) propagate — calfkit converts
  them to a structured `FailedToolCall` for the agent; never a hang.
- `discover_builtin_tools()` runs once at node-module import (idempotent),
  otherwise the registry is silently empty.

### Deps contract (request-scoped wiring)

| Key | Tools | Meaning |
|---|---|---|
| `session_id` (optional, str) | all stateful hermes tools | Refines tenancy scope below the agent, for one-agent-many-end-users deployments. Absent → `"default"` (agent-lifetime scope). |

Documented per component in `NODE.md`. No other deps keys in this stage.

### Resource contract (lifespan-scoped wiring)

- `todo` node: `@todo.resource("todo_state")` → a node-owned
  `dict[session_key, list[item]]` guarded by a lock (the in-memory **Store**:
  `get/put/delete` keyed by `session_key`). Per call: seed a vendored
  `TodoStore` via the ADR-0003 constructor-seed patch
  (`TodoStore.__init__(self, items=None)` — lands in `patches/` now), dispatch
  with `store=`, persist `store.read()` back **only when the call wrote**
  (`todos is not None`); reads never `put()` (id-collapse hazard, ADR-0003).
- shell/files/process keep state in the vendored per-`task_id` registries —
  no node resource needed; hermes' idle eviction (`TERMINAL_LIFETIME_SECONDS`,
  default 300s) is the eviction story.
- web providers: registered at node-module import from a node-owned
  name→class list (ddgs, brave-free, searxng, tavily), each guarded by
  ImportError (extras-dependent). Selection per ADR-0002:
  `WEB_SEARCH_BACKEND` (default `ddgs`), `WEB_EXTRACT_BACKEND` (default
  `tavily`), resolved per call via `registry.get_provider(name)` — the
  vendored config-file resolver stays dormant.

### web_fetch specifics

- Engine constructed with the fixed safe defaults from `NODE.md`
  (`allow_local_urls=False`, `timeout=30`, `max_content_length=50_000`);
  request surface is `url: str` only.
- Text → `WebFetchResult` dict `{url, title, content}` returned as-is.
- Binary → `{"data_base64": <b64>, "media_type": ...}` (JSON-serializable
  re-wrap of `FetchedBinary`).

### web_extract specifics

- Guard every URL with the vendored `async_is_safe_url` (fail-closed) before
  handing to the provider; unsafe URLs become per-URL error entries, never
  fetched. Provider `extract()` is sync → `asyncio.to_thread`.
- Node must not export `HERMES_ALLOW_PRIVATE_URLS` to its own process env.

### execute_code specifics (last)

- `dispatch("execute_code", {"code": ...}, task_id=session_key, enabled_tools=<set>)`.
- `enabled_tools` = node-config (env `EXECUTE_CODE_ENABLED_TOOLS`, comma-sep;
  default: the file+terminal+todo set). Trust model documented in `NODE.md`:
  arbitrary code on the node host — same trust class as `terminal`; deploy
  sandboxed or single-tenant.

## 4. Layout & exports

```
components/hermes-agent/src/calfkit_hermes/node/
    __init__.py      # re-exports all node defs + HERMES_NODES list
    _tenancy.py      # _session_key(ctx) helper (duplicated per component — components stay self-contained)
    shell.py         # terminal, process (+ execute_code later)
    files.py         # read_file, write_file, patch, search_files
    todo.py          # todo + in-memory Store resource
    web.py           # web_search, web_extract + provider registration
components/pydantic-web-fetch/src/calfkit_pydantic_web_fetch/node/
    __init__.py      # web_fetch node def
```

Usage: `from calfkit_hermes.node import terminal` → `Worker(client, nodes=[terminal])`
or `calfkit run calfkit_hermes.node:terminal`. `HERMES_NODES` is a convenience
list for hosting all hermes nodes in one Worker — still one node per tool
(separate topics/identities), not a proxy.

## 5. Testing (TDD, no broker)

Unit-level, constructing `ToolContext` directly (or a minimal stand-in if its
constructor proves unwieldy — verify first). Must cover, per tool:

1. **Isolation:** two different `agent_name`s (and same agent with two
   `deps.session_id`s) get disjoint state — shell cwd/env, todos.
2. **Default scope:** no `session_id` → state persists across two calls by the
   same agent (agent-lifetime default).
3. **Wiring:** dispatch called with the right name/args/task_id (spy on the
   seam, not internals); result parsed to dict.
4. **todo:** write persists to the Store, read does NOT `put()`; fresh session
   starts empty; seeded items skip re-validation (patch seam).
5. **web:** provider selection by env; unavailable provider → clean error;
   web_extract SSRF guard blocks private URLs; web_fetch binary→base64 and
   `WebFetchError` propagation.
6. **Schema sanity:** each node def's `tool_schema` exposes the expected
   parameter names (catches signature drift from upstream schemas).

Existing 369 vendor tests stay green. Run: `uv run --with pytest pytest`
(hermes web tests need the dev group). Known macOS-only pre-existing failure:
`test_file_tools_write_read_patch_round_trip` (pytest `tmp_path` under
`/private/var/` trips the vendored sensitive-path guard) — not a port bug.

## 6. Docs to land with the code

- `components/hermes-agent/NODE.md` — **create** (README link currently
  dangles): tool contract per node, deps/resource wiring, env config, trust
  model, single-process constraint.
- `components/pydantic-web-fetch/NODE.md` — fill in the deferred Kafka half.
- Fix stale `components/pydantic-web-fetch/README.md` (claims Stage-A
  scaffold; component is Stage-C complete → now Stage D).
- Root `README.md` status section.

## 7. Phases

A. Branch + pyproject deps + this doc/ADR (done in this commit).
B. `web_fetch` node (cleanest, stateless) — TDD.
C. hermes `shell.py` + `files.py` (dispatch seam + tenancy) — TDD.
D. hermes `todo.py` (patch seam + Store resource) — TDD.
E. hermes `web.py` (providers + guard) — TDD.
F. `execute_code` (last, only if not a big lift).
G. Docs (NODE.md × 2, READMEs), full test sweep, deep review rounds
   (`/pr-review-toolkit` + `/simplify`) until findings converge, PR.
