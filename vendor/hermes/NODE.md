# hermes node contract (Stage D)

The vendored hermes tools as **calfkit tool nodes** — one deployable node per
tool, driven wholesale through the upstream registry seam
(`registry.dispatch`). Design: [`../../docs/design/node-port.md`](../../docs/design/node-port.md);
tenancy: ADR-0004.

## Deploying

```python
from calfkit import Client, Worker
from calfkit_tools.hermes.node import terminal, HERMES_NODES

client = Client.connect("localhost:9092")          # or env CALFKIT_MESH_URL
await Worker(client, nodes=[terminal]).run()       # one tool = one node
# or host them all in one Worker (still one node per tool, not a proxy):
await Worker(client, nodes=HERMES_NODES).run()
# or: ck run calfkit_tools.hermes.node:terminal
```

Topics are calfkit name-derived: `tool.<name>.input` / `tool.<name>.output`,
node id `tool_<name>`. An agent attaches a node with
`Agent(..., tools=[terminal])` — it needs only the schema + topic, not this
package's implementation.

## Nodes

| Node | Params (mirror upstream schema) | State (in-memory, per session) |
|---|---|---|
| `terminal` | `command`, `background`, `timeout`, `workdir`, `pty`, `notify_on_complete`, `watch_patterns` | shell env/cwd snapshot |
| `process` | `action` (list/poll/log/wait/kill/write/submit/close), `session_id`, `data`, `timeout`, `offset`, `limit` | background OS processes |
| `read_file` | `path`, `offset`, `limit` | read trackers (staleness warnings) |
| `write_file` | `path`, `content`, `cross_profile` | — (shell-fused writer) |
| `patch` | `mode` (replace/patch), `path`, `old_string`, `new_string`, `replace_all`, `patch`, `cross_profile` | patch-failure tracker |
| `search_files` | `pattern`, `target`, `path`, `file_glob`, `limit`, `offset`, `output_mode`, `context` | — |
| `todo` | `todos` (id/content/status items), `merge` | node-owned `InMemoryTodoStore` |
| `execute_code` | `code` | runs under the session's shell env; in-script tool calls inherit the session |
| `web_search` | `query`, `limit` | stateless |
| `web_extract` | `urls` | stateless |

Results are the upstream tools' JSON payloads, parsed to dicts (upstream's
`{"error": ...}` convention passes through). NOTE: the `process` tool's
`session_id` argument is upstream's background-process handle (`proc_*`) —
unrelated to the tenancy `session_id` below.

## Tenancy (ADR-0004)

Every stateful call is scoped by
`session_key = f"{agent_name}:{deps.get('session_id', 'default')}"`:

- `agent_name` is stamped from the `x-calf-emitter` Kafka header (transport,
  not agent-authored) — one agent can never reach another agent's state.
- **Default scope is agent-lifetime**: state persists across invocations by
  the same agent until idle eviction or node restart.
- When one agent deployment serves many end-users, the invoking client
  partitions state per end-user by wiring **Deps**:
  `client.execute_node(..., deps={"session_id": "user-7"})`.

### Wiring contract

| Channel | Key | Tools | Meaning |
|---|---|---|---|
| Deps (request-scoped) | `session_id` (optional, str) | all stateful nodes | refines tenancy scope below the agent |
| Resource (lifespan-scoped) | `todo_state` | `todo` | node-owned in-memory Store; auto-provided by the node's `@resource` hook |

## Configuration (env vars, per ADR-0002)

| Var | Default | Meaning |
|---|---|---|
| `WEB_SEARCH_BACKEND` | `ddgs` | search provider (`ddgs`, `brave-free`, `searxng`, `tavily`) |
| `WEB_EXTRACT_BACKEND` | `tavily` | extract provider (Tier-0: tavily only) |
| `BRAVE_SEARCH_API_KEY` / `SEARXNG_URL` / `TAVILY_API_KEY` | — | per-provider credentials |
| `EXECUTE_CODE_ENABLED_TOOLS` | file+shell+todo set | comma-separated tools callable from in-script PTC. Only registry-bridged tools (`terminal`, `read_file`, `write_file`, `patch`, `search_files`) work in-script — `web_search`/`web_extract` are separate calfkit nodes, not bridged. An override resolving to zero sandbox-callable tools fails closed (error reply, never the full-set fallback) |
| `TERMINAL_ENV` | `local` | shell backend (`docker`/`ssh`/`modal`/… via extras) |
| `TERMINAL_LIFETIME_SECONDS` | `300` | idle shell-environment eviction |

Do **not** set `HERMES_ALLOW_PRIVATE_URLS` in the node's process env — the
`web_extract` SSRF guard must stay fail-closed.

## Deployment constraints & trust model

- **One process per stateful node.** calfkit partitions messages by
  correlation id, not session — horizontal scaling would scatter sessions.
  Stateless nodes (`web_search`, `web_extract`) scale freely.
- **`todo` requires `Worker(max_workers=1)`** (the default): its
  get→apply→put is a read-modify-write; concurrent same-session calls could
  lose writes (the ADR-0003 single-writer constraint, in-process edition).
- **`terminal` and `execute_code` are arbitrary code execution on the node
  host.** Deploy sandboxed (container/VM) or single-tenant-trusted. The
  vendored file/path guards are conveniences, not security boundaries
  (shell-file design doc §6.1); env-allowlist scrubbing and FS confinement
  are deferred hardening.
- **State is in-memory** — lost on restart; durable Stores deferred
  (ADR-0003/0004).

## Known limitations

- `process` handle actions (`poll`/`log`/`wait`/`kill`/`write`/`submit`/
  `close`) look the process up by its globally-unique `proc_*` handle, which
  upstream does **not** scope by tenant. The node closes that gap with an
  ownership guard: before dispatching any handle action it runs a `list`
  scoped to the caller's `session_key` (upstream filters `list` by `task_id`
  and includes finished processes, so owners can still inspect an exited one)
  and verifies the handle is present. A foreign or unknown handle is denied
  with upstream's own not-found response (`{"status": "not_found", "error":
  "No process with ID …"}`) — deny-as-not-found, so the guard never leaks that
  a handle exists for another tenant. Verification stays at the public
  registry seam; no vendor internals are touched.
- The background-process registry caps at 64 entries globally (upstream
  `MAX_PROCESSES` LRU) — shared across all sessions of the node process.
- Session bookkeeping (override-registry entries, todo lists) grows with
  distinct session keys; entries are tiny and bounded by the in-memory
  deployment model. Shell environments themselves are evicted after
  `TERMINAL_LIFETIME_SECONDS` idle.
