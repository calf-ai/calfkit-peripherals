# todos tool — research log

Realtime findings log for the todos (`todo_view`/`todo_write`) tool research.
Append-only as research proceeds. Cite repo + file path (+ line) for every claim.

---

## Step 1 — Verify OUR source + upstream (DONE)

### Our wrapper: `src/calfcord/tools/builtin/todos.py`
- `_executors: dict[str, TaskTrackerExecutor]` (line 43), `_executors_lock = threading.Lock()` (line 44).
- `_executor_for(agent_id)` (line 47-59): `with lock: ex = _executors.get(agent_id); if None: ex = TaskTrackerExecutor(save_dir=None); _executors[agent_id] = ex`.
  - **INSERT/GET ONLY. No cap, TTL, LRU, or eviction.** Only removal is `_reset_for_tests()` (line 130-133).
- `_require_agent_id(ctx)` (line 62-80): raises RuntimeError if `ctx.agent_name` falsy.
  - **TODO-2 confirmed**: docstring/error say "calfkit ... from the x-calf-emitter Kafka header" and "the calfkit-tools runner must populate ctx.agent_name from the x-calf-emitter header". The audit (todos section) says actual mechanism is `ToolNodeDef.run` stamping from `ctx.emitter_node_id` (nodes/tool.py:101), which derives from the header at `SessionRunContext._stamp_transport`. The runner never touches it. CONFIRMED as a doc/error wording bug.
- `todo_view` (line 83-96): `_executor_for(agent_id)(TaskTrackerAction(command="view"))` → flatten.
- `todo_write(ctx, todos: list[dict])` (line 99-127): validates each dict via `TaskItem.model_validate`, returns `error: invalid todo item(s): ...` on ValidationError, else `TaskTrackerAction(command="plan", task_list=...)`. **Full-list replace semantics** — partial updates unsupported by design.
- Module docstring (line 4-6): "Lists are kept in memory and wiped on calfkit-tools restart — Claude Code's TodoWrite has the same 'conversation-scoped' lifetime, so this matches LLM expectations."
- Module docstring (line 9-10): models task as `{title, notes, status: todo|in_progress|done}`.
- **DOC NIT**: docstring line 4 implies upstream persists to `TASKS.md`; upstream actually uses `TASKS.json` (see below). calfcord passes `save_dir=None` so moot for runtime, but the prose elsewhere may mislead.

### Upstream: `openhands/tools/task_tracker/definition.py` (openhands-tools, installed 0.x)
- NOTE: there is **no `impl.py`** for task_tracker — everything (action, observation, executor, tool def, description) is in `definition.py`. (Audit referenced "impl.py" generically; actual file is definition.py.)
- `TaskItem` (line 32-39): `title: str (required)`, `notes: str = ""`, `status: Literal["todo","in_progress","done"] = "todo"`. Simple flat schema. **No id, no priority, no parent/subtask, no timestamps, no ordering field** (order = list order).
- `TaskTrackerAction` (line 42-52): `command: Literal["view","plan"]`, `task_list: list[TaskItem]`. **Two commands only**: view (read) and plan (full replace).
- `TaskTrackerExecutor` (line 148-266):
  - `save_dir: Path | None`; `self._task_list: list[TaskItem] = []` (line 162). **In-memory list is the source of truth.**
  - `plan`: `self._task_list = action.task_list` (full replace, line 176), saves to `save_dir/TASKS.json` if save_dir.
  - `view`: returns formatted list or "No task list found".
  - `_save_tasks` (line 252): writes `save_dir/TASKS.json` (NOT .md), `json.dump([task.model_dump() ...])`, catches OSError only.
  - `_load_tasks` (line 233): reads `save_dir/TASKS.json`, catches `(OSError, json.JSONDecodeError, TypeError, ValidationError)`.
  - **Persistence is per-executor, file-based, single file. No multi-list/session keying inside the executor** — isolation is entirely the caller's responsibility (calfcord achieves it via one executor per agent_id).
- `TaskTrackerTool.create` (line 406-431): in native openhands, ONE executor per ConversationState, `save_dir = conv_state.persistence_dir`. So upstream's native isolation = per-conversation, persisted to that conversation's dir. calfcord instead keys per-agent, in-memory.
- Lifecycle: executor has NO close()/teardown. State lives for the executor's lifetime. **XC-1 confirmed** — calfcord's module-global dict of these is never torn down.

### Verified issue summary
- TODO-1 (unbounded `_executors`): CONFIRMED. Slow leak: one executor + its retained task list per distinct agent_id, forever, in a long-lived process.
- TODO-2 (wrong blame for agent_name): CONFIRMED as doc/error wording bug.
- XC-1 (module-global never torn down): CONFIRMED (no close on executor anyway, but the dict + retained lists persist).
- Storage model: in-memory `_task_list` per executor; calfcord uses `save_dir=None` (pure RAM, wiped on restart). Upstream supports `save_dir` → single `TASKS.json`.
- Schema: flat `{title, notes, status}`. No ids/priority/subtasks/timestamps.
- Surfacing: full markdown list with status icons via `view`; full-replace via `plan`.

### Severity reality check
This tool is the CLEANEST of all the builtins (audit: "this tool has FEW issues"). It is correctly multitenant. The only real runtime defect is the unbounded dict (Medium), plus a doc nit (Low). So the research bar for "adopt/port a replacement" is HIGH — a candidate must be clearly better, not merely different.

---

## Step 2 — Framework survey (IN PROGRESS)

### Candidate: Claude Code / Claude Agent SDK (anthropics/claude-agent-sdk-python) — Python (MIT)
- **Claude Agent SDK Python is a THIN WRAPPER over the Claude Code CLI subprocess** (`claude-sdk/src/claude_agent_sdk/_internal/transport/subprocess_cli.py`). It has NO built-in todo/task tool of its own — TodoWrite/Task tools live in the Claude Code binary (server-side). The SDK only references them as `allowed_tools` strings. So "porting the Claude SDK" = porting the Claude Code *design* (documented), not Python source.
- **Old TodoWrite design** (what calfcord mirrors): item shape `{content, status, activeForm}`; status `pending|in_progress|completed`; ONE tool call rewrites the full `todos` array (full-replace); in-memory, "conversation-scoped", lost on context reset/compaction; injected into the system prompt after each tool call so the model re-sees its own list every request. (docs: code.claude.com/docs/en/agent-sdk/todo-tracking; Piebald-AI/claude-code-system-prompts tool-description-todowrite.md). calfcord's schema differs: `{title, notes, status}` (no `activeForm`; has `notes`).
- **NEW Task tools design** (DEFAULT since TS SDK 0.3.142 / Claude Code v2.1.142 — the explicit successor to TodoWrite):
  - Split into `TaskCreate` / `TaskUpdate` / `TaskGet` / `TaskList` (incremental, keyed by `taskId`) instead of one full-array rewrite.
  - `TaskCreate` input: `{subject, description, activeForm?, metadata?}`. `TaskUpdate` input: `{taskId, status?, subject?, description?, activeForm?, addBlocks?, addBlockedBy?, owner?, metadata?}`. Status: `pending|in_progress|completed`, plus `status:"deleted"` to delete.
  - Richer schema: dependency edges (`addBlocks`/`addBlockedBy`), `owner`, freeform `metadata`, stable `taskId` (returned in the tool_result `{task:{id,subject}}`).
  - **Persisted to `~/.claude/tasks/<task-list-id>/` as individual JSON files** (home dir, NOT project), "unlimited tasks", and **broadcast across sessions** (multi-session visibility). This is the explicit fix for "flat in-memory list lost on reset".
- **vs our issues**: The migration narrative validates calfcord's concern — Anthropic itself moved OFF the flat-in-memory-full-replace model (our exact model) toward persisted + incremental + id-keyed. TODO-1/XC-1: their persistence is file-per-task on disk so there's no unbounded in-process dict; lifecycle is the filesystem. TODO-2: n/a (no calfkit). 
- **Integration feasibility**: NOT a Python drop-in (no public source; it's the CLI binary). It's a **design to learn from / port**, not a dependency. The id-keyed incremental + persisted model is the most mature reference design.

### Candidate: OpenAI Agents SDK (openai/openai-agents-python) — Python (MIT)
- **Core SDK has NO todo/task-tracker tool.** `openai-agents/src/agents/` lists tools (computer, editor, apply_diff, mcp, function_tool, ...) but no plan/todo tool. The only "Todo" symbols are in `openai-agents/src/agents/extensions/experimental/codex/items.py:97-107`: `TodoItem{text:str, completed:bool}` and `TodoListItem{id, items, type:"todo_list"}` — these are read-only typed VIEWS of **Codex's** thread output items surfaced through the Codex extension, not a tool the Agents SDK provides. (FINDING: OpenAI Agents SDK lacks a native task-tracker; it consumes Codex's.)
- **Integration feasibility**: N/A — nothing to adopt. The schema `{text, completed}` is a boolean-done view, strictly less expressive than ours.

### Candidate: OpenCode (sst/opencode) — TypeScript (MIT)
- **Tool**: `opencode/packages/opencode/src/tool/todo.ts` (`todowrite`), description in `todowrite.txt`. Service in `opencode/packages/opencode/src/session/todo.ts`. (Older copies also at `packages/core/src/tool/todowrite.ts` + `packages/core/src/session/todo.ts`.)
- **Schema** (`todo.ts:9-15`, `session/todo.ts:10-16`): `{content: str, status: "pending"|"in_progress"|"completed"|"cancelled", priority: "high"|"medium"|"low"}`. Adds `priority` and a `cancelled` status vs ours. No id field (order = `position` column).
- **Storage = SQLite** (drizzle ORM, `TodoTable`), `session/todo.ts:42-64`. `update()` = **delete-all-rows-for-session then insert** in a transaction (full-replace, durable). `get()` orders by `position asc`. **Persisted, NOT in-memory.**
- **Isolation**: per `sessionID` (DB key). No cross-session leakage; rows scoped by `session_id`.
- **Surfacing**: publishes `todo.updated` event (`session/todo.ts:19-27,63`) → live TUI/UI panel (`todo-panel`, `session-todo-dock`, sidebar). Tool output also returns `JSON.stringify(todos)` + a `title` of "N todos".
- **Bounding/lifecycle**: NO in-process dict — the DB is the store, bounded by session lifecycle and full-replace; completed/cancelled items are simply included or dropped by the model's next full write. No unbounded RAM growth.
- **vs our issues**:
  - TODO-1 (unbounded `_executors`): **SOLVED by design** — state is in SQLite keyed by session, not an in-process per-agent dict that never evicts. Memory is the DB, not RAM that grows forever.
  - XC-1 (module-global never torn down): **SOLVED** — no module-global executor; DB connection is a managed Layer/Service with lifecycle.
  - TODO-2: n/a.
- **Code quality**: clean, Effect-based DI (Layer/Service), schema-validated, transactional. Description (`todowrite.txt`) is tight and well-written (better than upstream openhands' 130-line essay).
- **Integration feasibility for calfcord**: TypeScript → **design-to-port, not a dependency**. But the design is directly portable: swap the in-memory `_executors` dict for a small persisted store keyed by agent_id. In calfcord's distributed model, "persist" should mean over-the-network shared store (the broker or a shared DB), NOT a local SQLite file on the tools host (tools are not guaranteed to share a filesystem — see CLAUDE.md). Schema add of `priority` + `cancelled` is a nice-to-have.

### Candidate: Codex CLI (openai/codex) — Rust (Apache-2.0)
- **Tool**: `update_plan`. Handler `codex/codex-rs/core/src/tools/handlers/plan.rs`; spec `plan_spec.rs`.
- **Schema** (`plan_spec.rs`): `{plan: [{step: str, status: "pending"|"in_progress"|"completed"}], explanation?: str}`. "At most one step can be in_progress." Simpler than ours (no notes; has optional per-update explanation). Required: `plan` (the steps). 
- **Storage = NONE (STATELESS handler)** (`plan.rs:58-91`): the handler parses args, emits `EventMsg::PlanUpdate(args)`, returns the constant "Plan updated". **The server keeps NO plan state at all.** The plan lives entirely in (a) the conversation transcript (the tool-call args ARE the record) and (b) the client/TUI that renders `PlanUpdate` (`tui/src/history_cell/plans.rs`). Full-replace semantics.
- **Isolation**: trivially perfect — there is no shared state to isolate. Each turn's args belong to that conversation's transcript.
- **vs our issues**:
  - TODO-1: **N/A by construction** — no dict, nothing retained server-side, zero memory growth.
  - XC-1: **N/A by construction** — no module-global executor to tear down.
  - TODO-2: n/a.
- **Code quality**: minimal, elegant. The whole tool is ~100 lines incl. spec.
- **Tradeoff**: offloads the entire list to the transcript + client. The model must re-send the full plan every update (token cost), and there's no server-readable "current list" (no `view`/`get`) — the model reads its own prior tool call from context. Works because Codex always has the transcript; in calfcord a tool worker does NOT see the agent's transcript, so a pure-stateless `update_plan` would give the agent no way to `todo_view`. This is the "framework-native plan state" pattern — viable ONLY if the agent loop (not the tool worker) holds the list.
- **Integration feasibility**: Rust → design-to-port. The *pattern* (stateless tool, list lives in transcript) is the strongest answer to TODO-1/XC-1 but requires moving list ownership from the tools worker into the agent's own context/loop — an architecture change, not a tool swap.

### Candidate: Gemini CLI (google-gemini/gemini-cli) — TypeScript (Apache-2.0)
- Has TWO task systems:
  1. **WriteTodos** (`gemini-cli/packages/core/src/tools/write-todos.ts`): schema `{description, status}` where status ∈ `pending|in_progress|completed|cancelled|blocked` (richest status set; `Todo` type at `tools/tools.ts:937-947`). Full-list overwrite (`WriteTodosToolParams.todos` "will overwrite any existing list"). Validates "only one in_progress" (`:120-126`). **Near-STATELESS like Codex**: `execute()` just echoes the list into `llmContent` and returns `returnDisplay:{todos}` for UI — no server-side store; the list lives in the transcript + a `TodoList` UI render type.
  2. **trackerTools** (`gemini-cli/packages/core/src/tools/trackerTools.ts` + `services/trackerService.ts` + `services/trackerTypes.ts`): the ID-keyed CRUD suite — `tracker_create_task / tracker_update_task / tracker_get_task / tracker_list_tasks / tracker_add_dependency / tracker_visualize`. This is Gemini's clone of Claude Code's new Task tools.
- **trackerTypes schema** (`trackerTypes.ts:30-42`): `{id: 6-char hex, title, description, type: epic|task|bug, status: open|in_progress|blocked|closed, parentId?, dependencies: string[], subagentSessionId?, metadata?}`. **Subtasks (parentId), dependency graph, task types, subagent linkage** — the richest schema surveyed.
- **TrackerService storage** (`trackerService.ts`): **file-per-task on disk** — `<trackerDir>/<id>.json`, `fs.mkdir(recursive)` lazy init, `listTasks()` reads the dir, validates each with zod, tolerates ENOENT, warns on corruption. Isolation = per-`trackerDir` (one dir per conversation/agent). No in-process growth (state is the filesystem).
- **vs our issues**: TODO-1/XC-1 — both systems avoid an unbounded module-global: WriteTodos keeps nothing; trackerTools persists to disk keyed by dir. TODO-2 n/a.
- **Integration feasibility**: TS → design-to-port. trackerTools is the reference for "rich, id-keyed, persisted, subtasks+deps". For calfcord, file-per-task is the same filesystem-coupling caveat as OpenCode's SQLite — in a distributed deploy it'd need a network-shared store.

### Candidate: Cline (cline/cline) — TypeScript (Apache-2.0)
- **NO dedicated todo TOOL.** Cline uses a **"focus chain"**: `cline/apps/vscode/src/core/task/focus-chain/index.ts` (`FocusChainManager`), `file-utils.ts`, `prompts.ts`, `utils.ts`.
- **Mechanism**: a `task_progress` **parameter piggybacked onto EVERY tool call** (not a separate tool). The model passes a free-form **markdown checklist** string (`- [ ]` / `- [x]`) in `task_progress`; `updateFCListFromToolResponse()` (`index.ts:273`) writes it to a per-task markdown file.
- **Storage**: markdown file per task at `getFocusChainFilePath(taskDir, taskId)` (`file-utils.ts:9`). Format: `- [ ] item` / `- [x] done` (`file-utils.ts:20,68-70`). **chokidar file watcher** (`index.ts:63-98`) syncs USER hand-edits back into the model (`todoListWasUpdatedByUser`). Counts parsed by regex (`utils.parseFocusChainListCounts`).
- **Schema**: unstructured markdown checklist (no per-item id/status enum — just checked/unchecked). Status is binary (done/not). Less structured than ours.
- **Surfacing**: escalating prompt reminders with progress % ("TODO LIST UPDATE REQUIRED", `index.ts:143-216`), interval-based re-injection (`shouldIncludeFocusChainInstructions`, `:337`), bidirectional user-edit sync. Plan/Act mode aware.
- **Lifecycle**: per-task `FocusChainManager` with explicit `dispose()` (`:397-407`) closing the watcher + clearing debounce timer — proper cleanup (contrast with our XC-1). State scoped to a task and GC'd with it.
- **vs our issues**: TODO-1/XC-1 — no module-global; per-task manager with dispose(). The "prompt-managed markdown + human-editable + piggybacked param" model is the most distinctive; downside is unstructured (no statuses beyond checkbox, no validation).
- **Integration feasibility**: TS → design-to-port. The piggybacked-`task_progress` pattern doesn't fit calfkit's discrete-tool model cleanly (would need every tool to accept a progress param). The human-editable angle is interesting for calfcord (a Discord-editable list) but heavy.

### Candidate: hermes-agent (NousResearch/hermes-agent) — Python (MIT) ⭐ closest Python fit
- Has TWO task systems:
  1. **`todo` tool** (`hermes/tools/todo_tool.py`): the in-memory session tracker.
  2. **kanban toolset** (`hermes/tools/kanban_tools.py` + `hermes/hermes_cli/kanban_db.py`): the persistent SQLite board.
- **`todo_tool.py` (in-memory) — DIRECTLY ADDRESSES TODO-1/XC-1**:
  - `TodoStore` (`:25`) holds `self._items: list[dict]` and **lives ON THE AIAgent INSTANCE (one per session)** (`:8,27`) — NOT a module-global dict. State is GC'd when the agent/session ends → **no unbounded module-global growth**. This is the architectural fix for our TODO-1.
  - **Schema** (`:209-263`): `{id: str (required, agent-chosen), content: str, status: pending|in_progress|completed|cancelled}`. id-keyed. List order = priority.
  - **Update semantics — BOTH modes** (`write()`, `:38`): `merge=false` (default) full-replace (like ours); `merge=true` incremental update-by-id + append (like Claude's Task tools) — in ONE tool. Plus `_dedupe_by_id` (`:146`) and `_validate` normalization (`:124`).
  - **Single tool**: read when no params, write when `todos` given (`:156`). Returns full list + summary counts as JSON.
  - **Compaction survival** (`format_for_injection`, `:90`): re-injects ONLY active (pending/in_progress) items after context compression, deliberately dropping completed/cancelled "so the model doesn't re-do finished work". Sophisticated surfacing.
  - Behavioral guidance baked into the static schema description (`:209`) so it's cache-stable, never mutates the system prompt mid-conversation.
- **kanban (persistent, distributed)** (`kanban_db.py`): SQLite at `~/.hermes/kanban.db`, multi-board. Rich `VALID_STATUSES = {triage, todo, scheduled, ready, running, blocked, review, done, archived}` (`:100`), `priority: int`, `title/body`. Concurrency via **WAL + CAS on `tasks.status`** and a `claim_lock` + worker heartbeat/reclaim (`:62-116`) — a distributed task QUEUE, not just a list.
  - **KEY DESIGN RATIONALE** (`kanban_tools.py:11-23`): tools run in the agent's Python process so they ALWAYS reach the shared DB "regardless of terminal backend" (Docker/Modal/SSH) — a worker's terminal tool can't run `hermes kanban` inside a container where the DB isn't mounted. **This is exactly calfcord's "tools can't assume a shared filesystem; share over the network" concern (CLAUDE.md).** The lesson: a distributed system's task store must be reachable from the agent process, not coupled to a per-host filesystem.
- **vs our issues**: `todo_tool.py` solves TODO-1/XC-1 by binding the store to the session/agent object (no module-global). TODO-2 n/a.
- **Integration feasibility for calfcord**: **HIGHEST** — Python, MIT, single self-contained file (~280 lines, only stdlib + a local registry). The `TodoStore` class is a near-drop-in: richer than ours (id-keyed, merge mode, dedup, compaction-injection) and structurally avoids the unbounded-dict leak. Caveat: in calfcord the store can't literally live on an "AIAgent instance" (the tools worker is a separate process from the agent), so we'd key it per-agent like today BUT must add the eviction/TTL/cap the audit asks for — OR follow the kanban lesson and back it with a network-shared store keyed per agent.

### Candidate: openclaw (openclaw/openclaw) — TypeScript (license "Other") 
- **NO native multi-item todo tool.** It WHITELISTS Claude Code's `TodoWrite` as a passthrough tool when proxying to Anthropic (`openclaw/src/llm/providers/anthropic.ts:98`, `agents/anthropic-transport-stream.ts:50`) — i.e. it relies on Claude's server-side TodoWrite, doesn't implement its own store.
- **Has `goal-tools.ts`** (`openclaw/src/agents/tools/goal-tools.ts`): create/get/update a SINGLE per-thread **goal** (objective + token_budget; model-updatable status `complete|blocked`; optional note). Persisted to the session store (`resolveStorePath`). This is a lightweight single-objective tracker, not a task LIST. Isolation per session-key.
- Ecosystem skills (search): `task-todo` (SQLite persistent), `taskflow` (markdown + SQLite), `task-router-skill` (distributed queue) — community SKILLS, not core tools; not first-party source.
- **vs our issues**: n/a — no comparable multi-item list tool to port. goal-tools is per-session persisted with no module-global growth, but a different (single-goal) abstraction.
- **Integration feasibility**: low — nothing directly portable for a multi-item todo; license is non-standard "Other".

### Live OpenHands note
- Our pinned wrapped lib is `openhands-tools==1.23.1` (venv METADATA). task_tracker source = `openhands/tools/task_tracker/definition.py` (one file, no impl.py). The agent platform repo (All-Hands-AI/OpenHands) does NOT contain this Python module; it lives in the `software-agent-sdk` (All-Hands-AI) "OpenHands V1" SDK that ships `openhands-tools`. No newer/divergent task_tracker design found there beyond what's in 1.23.1.

---

## Step 2 COMPLETE — coverage summary
Frameworks WITH a first-party todo/task/plan tool:
- OpenHands (incumbent): `task_tracker` — in-memory `_task_list`, optional `save_dir/TASKS.json`, full-replace, `{title,notes,status}`.
- Claude Code: TodoWrite (legacy, in-mem, full-replace, `{content,status,activeForm}`) → Task tools (persisted `~/.claude/tasks/`, id-keyed incremental, deps/owner/metadata). [SDK is a CLI wrapper — design, not source.]
- OpenCode: `todowrite` — SQLite per-session, full-replace, `{content,status(+cancelled),priority}`, event-driven UI.
- Codex: `update_plan` — STATELESS, `{plan:[{step,status}],explanation?}`, list lives in transcript+client.
- Gemini CLI: WriteTodos (near-stateless, `{description,status×5}`) + trackerTools (file-per-task, `{id,title,desc,type,status,parentId,dependencies,...}`).
- Cline: focus chain — markdown file per task, `task_progress` param on every tool, human-editable via watcher.
- hermes-agent: `todo` (in-mem on AIAgent, `{id,content,status×4}`, merge mode, compaction-injection) + kanban (SQLite distributed board).
- openclaw: goal-tools (single per-session goal) + passthrough Claude TodoWrite. (No native multi-item list.)

Frameworks WITHOUT a first-party todo/task tool:
- OpenAI Agents SDK (Python): none in core; only read-only typed views of Codex's todo_list output in the experimental Codex extension.
- Claude Agent SDK (Python): none of its own; thin wrapper over the Claude Code CLI binary (todo lives server-side).

---

## DONE — report written
Final report: `docs/tools-research/reports/todos-report.md`.
Recommendation: STAY-AND-FIX (bound `_executors`, add on_shutdown, fix TODO-2 wording) + optionally port id/merge + compaction-injection from hermes-agent `todo_tool.py`. Do NOT add local-disk/SQLite persistence (distributed-filesystem caveat). Top reference: hermes-agent (Python/MIT). Frameworks lacking a first-party todo tool: OpenAI Agents SDK (Python), Claude Agent SDK (Python, CLI-wrapper).
