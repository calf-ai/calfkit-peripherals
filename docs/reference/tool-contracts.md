# Tool node contracts

Interface reference for every calfkit tool node in this repo. Each component's
`NODE.md` is the authoritative per-component contract; this page is the
roll-up index.

| Component | Authoritative contract | Nodes |
|---|---|---|
| `components/hermes-agent` | [`NODE.md`](../../components/hermes-agent/NODE.md) | `terminal`, `process`, `read_file`, `write_file`, `patch`, `search_files`, `todo`, `execute_code`, `web_search`, `web_extract` |
| `components/pydantic-web-fetch` | [`NODE.md`](../../components/pydantic-web-fetch/NODE.md) | `web_fetch` |

## Common contract (all nodes)

- **Type:** calfkit `ToolNodeDef` (calfkit `>=0.9.0,<0.10`), one node per tool.
- **Identity:** node id `tool_<name>`; topics `tool.<name>.input` /
  `tool.<name>.output` (calfkit name-derived).
- **Envelope:** JSON. Arguments are validated against the schema derived from
  the function signature below; the leading `ctx` parameter is internal and
  not part of the LLM-facing schema.
- **Reply:** the value listed per node. A raised exception becomes a calfkit
  `FailedToolCall` reply.
- **Imports:** `from calfkit_hermes.node import <name>` (also `HERMES_NODES`,
  the list of all ten) · `from calfkit_pydantic_web_fetch.node import web_fetch`.

### Wiring channels

| Channel | Key | Applies to | Contract |
|---|---|---|---|
| Deps (request-scoped, client-wired) | `session_id` (`str`, optional) | all stateful hermes nodes | Refines the tenancy scope below the agent. Absent → `"default"` (agent-lifetime scope). |
| Resource (lifespan-scoped, node-provided) | `todo_state` | `todo` | An `InMemoryTodoStore` registered by the node's own `@resource` hook; not user-wired. |

Tenancy key (stateful hermes nodes):
`session_key = f"{agent_name}:{deps.get('session_id', 'default')}"`, where
`agent_name` is stamped from the `x-calf-emitter` header. A missing
`agent_name` raises (fail-closed). See
[ADR-0004](../adr/0004-node-tenancy-key-and-in-memory-state.md).

### Environment configuration

Defined in [`hermes NODE.md` § Configuration](../../components/hermes-agent/NODE.md#configuration-env-vars-per-adr-0002):
`WEB_SEARCH_BACKEND` (default `ddgs`), `WEB_EXTRACT_BACKEND` (default
`tavily`), `BRAVE_SEARCH_API_KEY` / `SEARXNG_URL` / `TAVILY_API_KEY`,
`EXECUTE_CODE_ENABLED_TOOLS`, `TERMINAL_ENV` (default `local`),
`TERMINAL_LIFETIME_SECONDS` (default `300`). `HERMES_ALLOW_PRIVATE_URLS` must
not be set. The pydantic component reads no environment variables.

### Scaling constraints

Stateful hermes nodes are correct at one process per node and
`Worker(max_workers=1)` (the default; required for `todo`). Stateless nodes
(`web_search`, `web_extract`, `web_fetch`) scale horizontally. See
[ADR-0004](../adr/0004-node-tenancy-key-and-in-memory-state.md) and each
NODE.md's deployment section.

---

## hermes-agent nodes

State for all nodes below is in-memory and scoped by `session_key`; replies
are the upstream tool's JSON payload parsed to a `dict` (upstream's
`{"error": ...}` convention passes through). Contract detail:
[`hermes NODE.md`](../../components/hermes-agent/NODE.md).

### `terminal`

Execute a shell command in the session's terminal environment.

| Param | Type | Required | Default |
|---|---|---|---|
| `command` | `str` | yes | — |
| `background` | `bool` | no | `false` |
| `timeout` | `int ≥ 1 \| null` | no | `null` (upstream: 180s) |
| `workdir` | `str \| null` | no | `null` |
| `pty` | `bool` | no | `false` |
| `notify_on_complete` | `bool` | no | `false` |
| `watch_patterns` | `list[str] \| null` | no | `null` |

Reply: `dict` with `output`, `exit_code`, `error`; `background=true` replies
include the `proc_*` handle as `session_id`. State: per-session shell env/cwd
snapshot, evicted after `TERMINAL_LIFETIME_SECONDS` idle.

### `process`

Manage background processes started with `terminal(background=true)`.

| Param | Type | Required | Default |
|---|---|---|---|
| `action` | `"list" \| "poll" \| "log" \| "wait" \| "kill" \| "write" \| "submit" \| "close"` | yes | — |
| `session_id` | `str \| null` | all actions except `list` | `null` |
| `data` | `str \| null` | for `write`/`submit` | `null` |
| `timeout` | `int ≥ 1 \| null` | no | `null` |
| `offset` | `int \| null` | no | `null` (log: last 200 lines) |
| `limit` | `int ≥ 1 \| null` | no | `null` |

`session_id` here is the upstream `proc_*` handle, not the tenancy
`deps.session_id`. Handle actions are ownership-guarded per session; a foreign
or unknown handle replies `{"status": "not_found", "error": "No process with
ID <id>"}`. The process registry is capped at 64 entries globally (upstream
LRU).

### `read_file`

| Param | Type | Required | Default |
|---|---|---|---|
| `path` | `str` | yes | — |
| `offset` | `int ≥ 1` | no | `1` |
| `limit` | `int ≤ 2000` | no | `500` |

Reply: `dict` with `content` (line-numbered window), and a non-blocking
`_warning` field when staleness tracking fires.

### `write_file`

| Param | Type | Required | Default |
|---|---|---|---|
| `path` | `str` | yes | — |
| `content` | `str` | yes | — |
| `cross_profile` | `bool` | no | `false` |

Writes execute through the session's shell environment (shell-fused). The
sensitive-path and cross-profile guards are upstream conveniences, not
security boundaries.

### `patch`

| Param | Type | Required | Default |
|---|---|---|---|
| `mode` | `"replace" \| "patch"` | no | `"replace"` |
| `path` | `str \| null` | for `replace` | `null` |
| `old_string` | `str \| null` | for `replace` | `null` |
| `new_string` | `str \| null` | for `replace` | `null` |
| `replace_all` | `bool` | no | `false` |
| `patch` | `str \| null` | for `patch` (V4A format) | `null` |
| `cross_profile` | `bool` | no | `false` |

Upstream's hand-written schema marks `mode` required; the node defaults it to
`"replace"` (behavior-identical — the upstream handler applies the same
default).

### `search_files`

| Param | Type | Required | Default |
|---|---|---|---|
| `pattern` | `str` | yes | — |
| `target` | `"content" \| "files"` | no | `"content"` |
| `path` | `str` | no | `"."` |
| `file_glob` | `str \| null` | no | `null` |
| `limit` | `int` | no | `50` |
| `offset` | `int` | no | `0` |
| `output_mode` | `"content" \| "files_only" \| "count"` | no | `"content"` |
| `context` | `int` | no | `0` |

Reply shape varies by `output_mode`: `matches` (content), `files`
(files_only), `counts` (count).

### `todo`

| Param | Type | Required | Default |
|---|---|---|---|
| `todos` | `list[{id: str, content: str, status: "pending" \| "in_progress" \| "completed" \| "cancelled"}] \| null` | no | `null` (read) |
| `merge` | `bool` | no | `false` |

`todos=null` reads; any list (including `[]`, which clears) writes and
persists. Item fields are all required: the node rejects partial items that
upstream's internal validation would salvage (deliberate — it matches the
advertised upstream schema). State: per-session list in the node-owned
`todo_state` resource. Requires `Worker(max_workers=1)`.

### `execute_code`

| Param | Type | Required | Default |
|---|---|---|---|
| `code` | `str` | yes | — |

Runs Python in a child process (5-minute timeout, 50 KB stdout cap, max 50
in-script tool calls). In-script tool calls run under the caller's
`session_key`. The in-script tool set is `EXECUTE_CODE_ENABLED_TOOLS`; only
registry-bridged tools (`terminal`, `read_file`, `write_file`, `patch`,
`search_files`) are callable in-script, and an override resolving to zero
sandbox-callable tools replies with an error (fail-closed). Same trust class
as `terminal`: arbitrary code on the node host.

### `web_search`

Stateless; no `ctx`, no tenancy.

| Param | Type | Required | Default |
|---|---|---|---|
| `query` | `str` | yes | — |
| `limit` | `int` | no | `5` |

Reply: the provider contract verbatim —
`{"success": true, "data": {"web": [{title, url, description, position}]}}`;
unknown/unavailable backend → `{"success": false, "error": ...}`. Provider
selected by `WEB_SEARCH_BACKEND`.

### `web_extract`

Stateless; no `ctx`, no tenancy. Async.

| Param | Type | Required | Default |
|---|---|---|---|
| `urls` | `list[str]` | yes | — |

Reply: `{"success": true, "data": [{url, title, content, raw_content?,
metadata?, error?}]}`. Every URL passes the fail-closed SSRF guard first;
blocked URLs become per-URL error entries and never reach the provider.
Result order is not guaranteed — correlate by `url`. Provider selected by
`WEB_EXTRACT_BACKEND` and must support extract (Tier-0: `tavily`).

---

## pydantic-web-fetch node

Contract detail: [`pydantic NODE.md`](../../components/pydantic-web-fetch/NODE.md).

### `web_fetch`

Stateless; no `ctx`, no tenancy. Async.

| Param | Type | Required | Default |
|---|---|---|---|
| `url` | `str` | yes | — |

SSRF-guard settings are fixed, not caller-overridable: `allow_local = false`,
`max_redirects = 10`, `timeout = 30`, `max_content_length = 50_000`, 5 MiB
streaming byte cap. Reply: text → `{url, title, content}` (markdown,
truncated at 50 000 chars with a `[Content truncated]` marker); binary →
`{data_base64, media_type}`; failure → raises `WebFetchError` (becomes a
`FailedToolCall`).
