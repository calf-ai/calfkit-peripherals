# todos tool — research report

Deep-research comparison of calfcord's `todo_view`/`todo_write` tool against the
task/todo/plan tooling of major open-source agent frameworks. Every candidate
claim is cited to repo + file path (+ line). Realtime evidence log:
`docs/tools-research/logs/todos-log.md`.

---

## 1. Executive summary + top recommendation

calfcord's todos tool is the **cleanest of all the builtin tools**. It is
correctly multitenant (keyed on the stable `ctx.agent_name`), its full-replace
`view`/`plan` model matches LLM expectations, and it has only **one real runtime
defect** (TODO-1: an unbounded `_executors` dict) plus a docstring-wording bug
(TODO-2) and the shared shutdown-hygiene gap (XC-1). No survveyed framework has a
*dramatically* better tool that justifies a rip-and-replace.

**Top recommendation: STAY-AND-FIX, porting two specific ideas from
`hermes-agent`.** Bound the `_executors` dict (LRU + cap, or TTL) to close
TODO-1, register a worker `on_shutdown`/`@resource` to close XC-1, and fix the
TODO-2 wording. While there, adopt two well-proven design upgrades from
`hermes-agent/tools/todo_tool.py` (Python, MIT, ~280 lines): (a) an **`id` field
+ optional `merge` mode** so the LLM can patch one item instead of re-sending the
whole list, and (b) **active-only re-injection on context compaction**. These
are additive, low-risk, and directly portable.

The strongest single reference design is **hermes-agent's `todo_tool.py`**
(closest Python fit). The strongest *architectural* lesson — for calfcord's
distributed topology specifically — is **hermes's kanban rationale**: in a
distributed system the task store must be reachable from the agent/tool process,
**never coupled to a per-host filesystem** (mirrors calfcord's own CLAUDE.md).
That rules out the file-per-task / local-SQLite persistence used by OpenCode,
Gemini's tracker, and Claude Code; if calfcord ever wants persistence it must be
a network-shared store, not local disk.

---

## 2. Verified recap of our todos tool's issues and true causes

Source: `src/calfcord/tools/builtin/todos.py`; wrapped upstream
`openhands.tools.task_tracker.definition` (`openhands-tools==1.23.1`).

**Storage model (verified).** calfcord constructs `TaskTrackerExecutor(save_dir=None)`
(`todos.py:57`). Upstream keeps the list purely in memory: `self._task_list:
list[TaskItem] = []` (`definition.py:162`); `plan` does `self._task_list =
action.task_list` (full replace, `definition.py:176`); persistence to
`save_dir/TASKS.json` is dead since `save_dir=None`. So calfcord's lists are
pure RAM, wiped on `calfkit-tools` restart. Schema is flat:
`{title:str(required), notes:str="", status: todo|in_progress|done}`
(`definition.py:32-39`) — no id, priority, subtasks, timestamps, or ordering
field (order = list order). Two commands only: `view` (read), `plan` (full
replace) (`definition.py:42-52`). (Doc nit: `todos.py:4` prose implies upstream
persists to `TASKS.md`; it actually uses `TASKS.json`, `definition.py:257`.)

- **TODO-1 — `_executors` grows unbounded (CONFIRMED, Medium).**
  `_executors: dict[str, TaskTrackerExecutor]` (`todos.py:43`) is only ever
  `.get()`/inserted in `_executor_for` (`todos.py:54-58`); the only removal is
  `_reset_for_tests()` (`todos.py:130`). One executor — each retaining that
  agent's full `_task_list` — per distinct `agent_id`, for the process lifetime.
  In a long-lived `calfkit-tools` process serving a churning roster, the dict and
  retained lists grow without bound. Slow leak, not a crash. **True cause:** no
  cap/TTL/LRU on the registry, and the executor itself has no expiry.

- **TODO-2 — docstring/error wrongly blame the runner (CONFIRMED, Low).**
  `_require_agent_id` (`todos.py:65-78`) says `agent_name` is "populated by
  calfkit from the x-calf-emitter Kafka header" and "the calfkit-tools runner
  must populate ctx.agent_name." Per the audit, `ToolContext.agent_name` is
  actually stamped by `ToolNodeDef.run` from `ctx.emitter_node_id`
  (`calfkit nodes/tool.py:101`), which is derived from the header one layer up in
  `SessionRunContext._stamp_transport`. The runner never touches it. **True
  cause:** wording bug only — an operator would inspect the wrong component.

- **XC-1 — module-global executors never torn down (CONFIRMED, context).**
  `_executors` is a module global; nothing registers a worker `on_shutdown` /
  `@resource` to clear it on `calfkit-tools` stop. For todos the consequence is
  mild (RAM only — `TaskTrackerExecutor` has no OS resources, no `close()`), but
  it is the same shutdown-hygiene gap as shell/fs/search and compounds TODO-1.

**Reality check.** This is the highest-quality builtin: correctly isolated, no
disk I/O, no blocking, no SSRF/injection class of bug. The bar for "adopt a
replacement" is therefore high — a candidate must be clearly better, not merely
different.

---

## 3. Per-candidate deep review

Ordering: incumbent first, then by relevance/quality.

### 3.1 OpenHands `task_tracker` — incumbent baseline
- **Repo/path:** `All-Hands-AI/software-agent-sdk` →
  `openhands/tools/task_tracker/definition.py` (shipped as `openhands-tools==1.23.1`).
  Language Python, license MIT.
- **Implementation:** in-memory `_task_list` per executor; optional
  `save_dir/TASKS.json` persistence (file-based, single file, one executor per
  conversation natively via `TaskTrackerTool.create`, `definition.py:406-431`).
  Schema `{title,notes,status}`; commands `view`/`plan` (full replace).
- **vs our issues:** This *is* the wrapped code. TODO-1/XC-1 are calfcord's
  registry, not upstream. TODO-2 is calfcord's docstring. The single-file
  `save_dir` persistence is per-conversation; calfcord deliberately bypasses it
  (`save_dir=None`).
- **Code quality:** fine; the 130-line `TASK_TRACKER_DESCRIPTION` essay is heavy.
- **Pros:** already integrated; zero schema churn. **Cons:** no id/merge; no
  bounding (the leak is structural once you key per-agent in a long-lived proc).
- **Integration:** already a dependency. Staying here + fixing the registry is
  the lowest-risk path.

### 3.2 hermes-agent — `todo_tool.py` (closest Python fit) ⭐
- **Repo/path:** `NousResearch/hermes-agent` →
  `hermes/tools/todo_tool.py` (in-memory) and
  `hermes/tools/kanban_tools.py` + `hermes/hermes_cli/kanban_db.py` (persistent).
  Language Python, license MIT.
- **Implementation (`todo_tool.py`):** `TodoStore` (`:25`) holds
  `self._items: list[dict]` and **lives on the AIAgent instance — one per
  session** (`:8,27`), so it is GC'd with the session (no module-global). Schema
  `{id:str(required, agent-chosen), content, status: pending|in_progress|completed|cancelled}`
  (`:209-263`), list order = priority. `write()` (`:38`) supports **both**
  `merge=false` full-replace and `merge=true` incremental update-by-id+append, in
  one tool; `_dedupe_by_id` (`:146`) and `_validate` (`:124`) normalize input.
  Single `todo` tool (read with no args, write with `todos`) returns the full
  list + summary counts as JSON (`:156-195`). **Compaction survival**:
  `format_for_injection` (`:90`) re-injects only `pending`/`in_progress` items
  after compression, deliberately dropping completed/cancelled.
- **kanban variant:** SQLite at `~/.hermes/kanban.db`, multi-board, rich statuses
  `{triage,todo,scheduled,ready,running,blocked,review,done,archived}`
  (`kanban_db.py:100`), `priority:int`, WAL + CAS on `tasks.status` + worker
  claim/heartbeat/reclaim — a distributed task **queue**. **Design rationale
  (`kanban_tools.py:11-23`):** tools run in the agent's Python process so they
  always reach the shared DB "regardless of terminal backend" (Docker/Modal/SSH)
  — directly the calfcord "share over the network, not the filesystem" principle.
- **vs our issues:** TODO-1/XC-1 solved by binding the store to the session
  object (no unbounded module-global). TODO-2 n/a.
- **Code quality:** excellent — self-contained, stdlib-only, well-documented,
  tight schema description.
- **Pros:** Python + MIT + single file ≈ drop-in; richer than ours (id, merge,
  dedup, compaction injection) while structurally avoiding the leak. **Cons:** in
  calfcord the store can't literally live on an agent object (the tools worker is
  a *separate process* from the agent), so per-agent keying remains — meaning we
  still must add eviction/TTL/cap; the kanban path needs a network store.
- **Integration:** **HIGHEST.** Port the `TodoStore` ideas (id + merge mode +
  compaction injection) onto our per-agent registry; keep the registry but bound
  it.

### 3.3 Claude Code / Claude Agent SDK — the design our docstring cites
- **Repo/path:** `anthropics/claude-agent-sdk-python` is a **thin wrapper over
  the Claude Code CLI subprocess** (`_internal/transport/subprocess_cli.py`); it
  has **no built-in todo tool of its own** (TodoWrite/Task tools live in the
  Claude Code binary, referenced only as `allowed_tools` strings). Design is from
  Anthropic docs (`code.claude.com/docs/en/agent-sdk/todo-tracking`) +
  `Piebald-AI/claude-code-system-prompts`. Language n/a (binary), license MIT.
- **Implementation:** *Legacy TodoWrite* = `{content, status:
  pending|in_progress|completed, activeForm}`, one call rewrites the full `todos`
  array, in-memory/"conversation-scoped", lost on context reset, re-injected into
  the system prompt every request (calfcord mirrors this minus `activeForm`,
  plus `notes`). **As of Claude Code v2.1.142 / TS SDK 0.3.142 the DEFAULT is the
  new Task tools** — `TaskCreate`/`TaskUpdate`/`TaskGet`/`TaskList`, id-keyed
  (`taskId`), incremental patch updates, schema adds `subject, description,
  activeForm, metadata, addBlocks, addBlockedBy, owner`, `status:"deleted"` to
  delete, **persisted to `~/.claude/tasks/<id>/` as JSON files and broadcast
  across sessions.**
- **vs our issues:** Validates calfcord's concern — Anthropic itself moved OFF
  the flat-in-memory-full-replace model (our exact model) to persisted +
  incremental + id-keyed. Their persistence is filesystem (per-task JSON), so no
  unbounded in-process dict.
- **Pros:** the most mature reference (deps graph, owner, cross-session).
  **Cons:** not a Python dependency; persistence is local disk (wrong for
  calfcord's distributed model unless re-pointed at a network store).
- **Integration:** **design-to-learn-from, not adopt.** The id-keyed incremental
  model is worth porting (hermes already packages it in Python).

### 3.4 Gemini CLI — WriteTodos + trackerTools
- **Repo/path:** `google-gemini/gemini-cli` →
  `packages/core/src/tools/write-todos.ts`, `tools/trackerTools.ts`,
  `services/trackerService.ts`, `services/trackerTypes.ts`. TypeScript, Apache-2.0.
- **Implementation:** *WriteTodos* — `{description, status:
  pending|in_progress|completed|cancelled|blocked}` (`tools/tools.ts:937-947`),
  full overwrite, validates one-in_progress (`write-todos.ts:120-126`),
  **near-stateless** (echoes list into `llmContent`, no server store). *tracker
  tools* — id-keyed CRUD suite (`tracker_create/update/get/list_tasks`,
  `add_dependency`, `visualize`); schema `{id:6-hex, title, description, type:
  epic|task|bug, status: open|in_progress|blocked|closed, parentId?,
  dependencies[], subagentSessionId?, metadata?}` (`trackerTypes.ts:30-42`);
  **storage = file-per-task `<trackerDir>/<id>.json`** with zod validation and
  corruption tolerance (`trackerService.ts`). The richest schema surveyed
  (subtasks + dependency graph + task types + subagent linkage).
- **vs our issues:** Both avoid an unbounded module-global (WriteTodos keeps
  nothing; tracker persists to disk by dir). TODO-2 n/a.
- **Pros:** richest data model; clean zod-validated service. **Cons:** TS (port,
  not dep); file-per-task = filesystem coupling (wrong for distributed calfcord);
  the rich schema is likely over-engineered for calfcord's needs.
- **Integration:** design-to-port; the trackerTools schema is the best reference
  if calfcord ever needs subtasks/deps.

### 3.5 Codex CLI — `update_plan` (stateless)
- **Repo/path:** `openai/codex` →
  `codex-rs/core/src/tools/handlers/plan.rs`, `plan_spec.rs`. Rust, Apache-2.0.
- **Implementation:** **STATELESS handler** (`plan.rs:58-91`) — parses args,
  emits `EventMsg::PlanUpdate(args)`, returns the constant "Plan updated"; the
  server keeps **no** plan state. The plan lives entirely in the conversation
  transcript (the tool-call args are the record) + the client/TUI render
  (`tui/src/history_cell/plans.rs`). Schema `{plan: [{step, status:
  pending|in_progress|completed}], explanation?}`, full replace, at most one
  in_progress (`plan_spec.rs`).
- **vs our issues:** TODO-1/XC-1 **N/A by construction** — nothing retained
  server-side, zero memory growth, perfect isolation. The cleanest possible
  answer to the leak.
- **Pros:** elegant, ~100 lines total. **Cons:** offloads the whole list to the
  transcript + client; no server-readable `view` (the model reads its own prior
  tool call from context). **Critical for calfcord:** a tool worker does NOT see
  the agent's transcript, so a pure-stateless `update_plan` would leave the agent
  unable to `todo_view` — viable only if list ownership moves into the agent loop,
  not the tools worker. That is an architecture change, not a tool swap.
- **Integration:** design-to-port; the "stateless, list-in-transcript" pattern is
  the strongest leak fix but requires relocating list ownership to the agent.

### 3.6 OpenCode — `todowrite`
- **Repo/path:** `sst/opencode` →
  `packages/opencode/src/tool/todo.ts`, `src/session/todo.ts`,
  `src/tool/todowrite.txt`. TypeScript, MIT.
- **Implementation:** schema `{content, status:
  pending|in_progress|completed|cancelled, priority: high|medium|low}`
  (`session/todo.ts:10-16`). **Storage = SQLite** (drizzle `TodoTable`), `update()`
  = delete-all-rows-for-session then insert (full-replace, durable,
  `session/todo.ts:42-64`), `get()` orders by `position`. Isolation per
  `sessionID` (DB key). Publishes `todo.updated` event for live UI.
- **vs our issues:** TODO-1 **solved by design** (state is SQLite keyed by
  session, not an in-process per-agent dict). XC-1 **solved** (DB is a managed
  Layer/Service, not a module-global). TODO-2 n/a.
- **Pros:** clean, durable, event-driven; `priority` + `cancelled` are nice.
  **Cons:** TS (port); local SQLite file = filesystem coupling (wrong for
  distributed calfcord unless network-backed).
- **Integration:** design-to-port; good reference for "persisted, session-keyed,
  full-replace" if calfcord chooses durability.

### 3.7 Cline — focus chain (no dedicated tool)
- **Repo/path:** `cline/cline` →
  `apps/vscode/src/core/task/focus-chain/{index.ts,file-utils.ts,prompts.ts}`.
  TypeScript, Apache-2.0.
- **Implementation:** **no separate todo tool** — a `task_progress` parameter is
  piggybacked onto *every* tool call; the model passes a free-form **markdown
  checklist** (`- [ ]`/`- [x]`) which `updateFCListFromToolResponse` (`index.ts:273`)
  writes to a per-task markdown file (`getFocusChainFilePath`, `file-utils.ts:9`).
  A **chokidar watcher** (`index.ts:63-98`) syncs *user* hand-edits back to the
  model. Escalating prompt reminders with progress % (`index.ts:143-216`).
  Per-task `FocusChainManager` with explicit `dispose()` (`index.ts:397-407`).
- **vs our issues:** TODO-1/XC-1 — no module-global; per-task manager with proper
  dispose() (the cleanup discipline calfcord lacks). Schema is unstructured
  (binary checkbox; weaker than ours).
- **Pros:** human-editable list (interesting for a Discord-editable todo); proper
  lifecycle. **Cons:** piggybacked-param model doesn't fit calfkit's discrete-tool
  surface; markdown-only loses structured statuses/validation.
- **Integration:** design-to-port at most; the dispose() discipline is the
  takeaway for XC-1.

### 3.8 openclaw — goal-tools + passthrough (no native list tool)
- **Repo/path:** `openclaw/openclaw` →
  `src/agents/tools/goal-tools.ts`; TodoWrite passthrough at
  `src/llm/providers/anthropic.ts:98`. TypeScript, license "Other".
- **Implementation:** **no native multi-item todo** — whitelists Claude Code's
  `TodoWrite` when proxying to Anthropic. Has `goal-tools.ts`: create/get/update a
  **single per-thread goal** (`objective` + `token_budget`; model-updatable status
  `complete|blocked`; optional note), persisted to the session store. Ecosystem
  *skills* (`task-todo`, `taskflow`, `task-router-skill`) are community add-ons,
  not first-party tools.
- **vs our issues:** n/a — different (single-goal) abstraction; nothing to port.
- **Integration:** low; non-standard license.

### Frameworks that LACK a first-party todo/task tool (findings)
- **OpenAI Agents SDK (Python)** — `openai/openai-agents-python`: **no
  todo/task-tracker tool in core.** The only `Todo*` symbols are read-only typed
  *views* of Codex's output in `src/agents/extensions/experimental/codex/items.py:97-107`
  (`TodoItem{text, completed}`, `TodoListItem`) — consumed from Codex, not
  provided. Nothing to adopt.
- **Claude Agent SDK (Python)** — has no todo tool of its own (it shells out to
  the Claude Code CLI; the tool is server-side). See 3.3.

---

## 4. Comparison matrix

Themes: **Store** (where state lives) · **Bound** (does it avoid unbounded
in-process growth?) · **Isolation** · **Schema richness** · **Update** (replace
vs incremental) · **Lifecycle** (explicit teardown?).

| Candidate | Lang/Lic | Store | TODO-1 (bound) | XC-1 (teardown) | Isolation | Schema | Update | Surfacing |
|---|---|---|---|---|---|---|---|---|
| **calfcord (ours)** | Py/MIT | RAM, module-global dict | ❌ unbounded | ❌ none | per-agent | `{title,notes,status×3}` | full-replace | markdown `view` |
| OpenHands (upstream) | Py/MIT | RAM (or `TASKS.json`) | ❌ (if per-key) | ❌ | per-conv | `{title,notes,status×3}` | full-replace | markdown |
| **hermes `todo`** ⭐ | Py/MIT | RAM on session obj | ✅ GC w/ session | ✅ implicit | per-session | `{id,content,status×4}` | replace **+ merge** | JSON + compaction inject |
| hermes kanban | Py/MIT | SQLite (`~/.hermes`) | ✅ DB | ✅ | per-board | `{id,title,status×9,prio}` | incremental CRUD | DB/dashboard |
| Claude Task tools | bin/—  | disk `~/.claude/tasks` | ✅ disk | ✅ | per-list | `{subject,desc,deps,owner,meta,status×4}` | incremental by `taskId` | sys-prompt inject |
| Gemini WriteTodos | TS/Apache | none (stateless) | ✅ N/A | ✅ N/A | transcript | `{description,status×5}` | full-replace | echo + UI |
| Gemini tracker | TS/Apache | file-per-task | ✅ disk | ✅ | per-dir | `{id,title,desc,type,status×4,parentId,deps}` | incremental CRUD | visualize |
| Codex `update_plan` | Rust/Apache | none (stateless) | ✅ N/A | ✅ N/A | transcript | `{step,status×3}` | full-replace | event→TUI |
| OpenCode `todowrite` | TS/MIT | SQLite | ✅ DB | ✅ | per-session | `{content,status×4,priority}` | full-replace | event→UI |
| Cline focus chain | TS/Apache | md file per task | ✅ per-task+dispose | ✅ dispose() | per-task | markdown checkbox | full-replace (param) | prompt reminders |
| openclaw goal-tools | TS/Other | session store | ✅ | ✅ | per-session | single goal | incremental | — |
| OpenAI Agents SDK | Py/MIT | — (no tool) | — | — | — | — | — | — |

Status-count notation: status×N = N allowed status values.

---

## 5. Ranking with rationale

Ranked by value as a model/source for fixing *our* tool (not by raw feature
count):

1. **hermes-agent `todo_tool.py`** — Python, MIT, self-contained; structurally
   avoids the leak; adds id + merge + compaction-injection without abandoning the
   full-replace model the LLM already expects. The kanban file additionally
   articulates the exact distributed-store lesson calfcord cares about.
2. **Stay on OpenHands `task_tracker` + bound the registry** — zero schema churn,
   lowest risk; the only thing missing is the bounding/teardown, which is
   calfcord-side anyway.
3. **Codex `update_plan` (stateless pattern)** — the most elegant leak fix in
   principle, but needs list ownership moved into the agent loop (architecture
   change), and a tools-worker `view` can't be served statelessly. High concept,
   high integration cost.
4. **Claude Code Task tools** — best mature reference design (id/deps/owner,
   cross-session), but binary-only and disk-persisted; learn, don't adopt.
5. **Gemini trackerTools** — richest schema (subtasks/deps/types); over-built for
   calfcord and filesystem-coupled.
6. **OpenCode `todowrite`** — clean persisted/session-keyed reference; TS +
   local SQLite.
7. **Cline focus chain** — distinctive (human-editable, proper dispose()), but
   the piggybacked-param shape doesn't fit calfkit; the dispose() discipline is
   the one takeaway.
8. **openclaw goal-tools** — different abstraction; non-standard license.
9. **OpenAI Agents SDK / Claude Agent SDK (Python)** — no first-party tool;
   nothing to adopt.

---

## 6. Final recommendation

**STAY-AND-FIX, with a small, surgical port from hermes-agent.** Do not adopt a
new dependency or rewrite the tool — ours is already the best-isolated builtin and
every "replacement" trades the leak for either filesystem coupling (wrong for
calfcord's distributed model) or an architecture change.

Concretely:

1. **Close TODO-1 (must-fix).** Replace the unbounded `dict` with a bounded
   structure — an `OrderedDict`-based LRU with a max-agents cap, and/or TTL
   eviction of idle agents. Document the bound. (calfcord *can't* bind the store
   to an agent object as hermes does — the tools worker is a separate process —
   so per-agent keying stays and the cap is the fix.)
2. **Close XC-1 (should-fix).** Register the registry teardown via calfkit's
   worker lifecycle (a runner `on_shutdown` that clears `_executors`, or convert
   it to a worker `@resource`). Mirror Cline's `dispose()` discipline. Add an
   `atexit` belt-and-suspenders.
3. **Fix TODO-2 (quick).** Reword the docstring/error to "calfkit stamps
   `ctx.agent_name` from the emitter node id (sourced from the `x-calf-emitter`
   header)"; drop the "runner must populate" claim. Also fix the `TASKS.md` →
   `TASKS.json` prose nit.
4. **Optional, additive upgrades (port from `hermes/tools/todo_tool.py`),
   pending design sign-off — these change the LLM-facing schema, so brainstorm
   first:**
   - Add an **`id`** field + an optional **`merge`** mode so the model can patch
     one item instead of re-sending the entire list (saves tokens on long lists;
     this is the single change Anthropic/Gemini/Claude all converged on). Keep
     full-replace as the default to preserve current behavior.
   - Add **active-only re-injection** of the list when calfcord compacts agent
     context, dropping completed/cancelled items (hermes
     `format_for_injection`) — keeps long-running agents on-plan across
     compaction.
   - Consider adding a **`cancelled`** status (OpenCode/Gemini/hermes all have
     it) so failed work is recorded without faking `done`.

**Explicitly do NOT** introduce local-disk or local-SQLite persistence
(OpenCode / Gemini tracker / Claude Code). calfcord's tools are not guaranteed to
share a filesystem with the agent (CLAUDE.md; mirrored by hermes's kanban
rationale). If durable cross-restart todos are ever wanted, that is a separate
design: a **network-shared** store keyed per agent (broker- or DB-backed), not a
file on the tools host.

**Drop-the-tool?** No. The tool is genuinely useful, correctly isolated, and the
prompt-managed/stateless alternatives (Codex/Gemini WriteTodos) only work when
the list rides the transcript the agent loop owns — which the calfkit tools
worker does not see. A dedicated, per-agent, bounded in-memory tool remains the
right pattern for calfcord today.
