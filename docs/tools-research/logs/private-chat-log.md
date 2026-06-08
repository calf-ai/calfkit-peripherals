# private_chat tool research log

Started: 2026-06-06

## Goal
Find frameworks/libraries whose A2A messaging / handoff / multi-agent comms primitives we can ADOPT or PORT, and extract battle-tested patterns for request/reply correlation, timeout/cancellation, reentrancy, concurrency. Grade against PCHAT-1..5 and XC-3.

## Issues to verify
- PCHAT-1: A2A reentrancy deadlock / fleet-wide serialization (max_workers=1 slot held for whole awaited reply; A->B->A stalls; unrelated A2A calls serialize)
- PCHAT-2: no allowed_mentions guard -> @everyone/@here/role-ping injection via audit projection
- PCHAT-3: over-length request content (>2000) on new thread -> RuntimeError instead of recoverable error; no chunk-split on request side
- PCHAT-4: outer-call cancellation/timeout orphans running tool body (slot held to completion)
- PCHAT-5: sub-second timeout misreported as "0s"
- XC-3: max_workers=1 + blocking/long-held slots -> per-call stalls become fleet outages

---

## Step 1: Verify our source

### VERIFIED root causes (read from source)

**PCHAT-1 (reentrancy deadlock / fleet serialization) — CONFIRMED, true cause:**
- `tools/runner.py:119` builds `Worker(client, tool_nodes)` with default `max_workers=1` (worker.py:61).
- worker.py:208-213 passes `max_workers=self._max_workers` to `self._client._connection.subscriber(*topics, group_id, max_workers=...)`.
- FastStream impl: `_internal/endpoint/subscriber/mixins.py:61` -> `self.limiter = anyio.Semaphore(max_workers)`, with `max_buffer_size=max_workers` (line 59). Each inbound message is consumed `async with self.limiter`. So `max_workers=1` = exactly ONE handler coroutine in flight per subscriber.
- `private_chat.py:600` `await res.client.execute_node(...)` is an in-handler await of a Kafka request/reply. The handler coroutine does NOT return until the reply (or 60s timeout). The semaphore slot is held the entire time.
- Mechanics of the deadlock: A's `tool.private_chat.input` msg occupies the slot -> body awaits B's reply on `agent.B.in`. If B's run calls `private_chat(target=A)`, that produces a NEW `tool.private_chat.input` msg that lands in the SAME single-subscriber buffer behind A's still-running handler. It cannot be picked up until A's handler returns. A's handler won't return until B replies. B can't progress its private_chat tool call. -> stall until the inner leg's 60s timeout fires. Also: two UNRELATED A2A calls serialize (one slow call blocks all A2A fleet-wide).
- NOTE: the AGENT side does NOT deadlock — calfkit's deferred-tool model returns a `Call` and frees the agent's slot; the bottleneck is purely the tools worker's single subscriber slot. This is a CONCURRENCY-MODEL bug, not a correlation bug.

**PCHAT-2 (no allowed_mentions) — CONFIRMED:**
- `discord/persona.py:375` `await webhook.send(content=content, username=..., avatar_url=..., thread=..., embeds=..., view=..., wait=True)` — NO `allowed_mentions` kwarg.
- `persona.py:282` builds the client as `discord.Client(intents=discord.Intents.none())` with no `allowed_mentions=` default. discord.py's default `AllowedMentions` parses everyone/roles/users -> `@everyone`/`@here`/`<@&role>` in LLM content fire real pings from the audit channel.
- Vectors: request projection (`content`, caller-LLM-controlled) via `_post_projection`->`send`; response projection (target reply text, callee-LLM-controlled) via `_post_response_with_feedback_retries`->`send` (line 744) and `_post_chunked_projection`->`send` (line 929). All untrusted.

**PCHAT-3 (over-length request content -> RuntimeError) — CONFIRMED:**
- `_start_new_thread` (private_chat.py:952) calls `_post_projection(... correlation_id=None)`. `_post_projection` (line 1242) retries `_MAX_PROJECTION_ATTEMPTS=2` times; over-length content (>2000) raises `discord.HTTPException` (400) on both attempts (NOT NotFound/Forbidden so it's retried with same content), then on the request side (`correlation_id is None`) returns `None` (line 1358). Back in `_start_new_thread`, `sent is None` -> `_raise_infra(...)` (line 985) -> RuntimeError. So an LLM writing a >2000-char briefing gets a hard infra error, not a recoverable `error:` string. Response side HAS chunk_split fallback; request side does NOT.

**PCHAT-4 (outer cancellation orphans body) — CONFIRMED:**
- FastStream runs the handler as `asyncio.create_task(func(...))` inside the semaphore (mixins.py:29). If the CALLER agent's outer execute_node (awaiting the tool ReturnCall) times out, nothing cancels the running private_chat handler task — FastStream does not cancel in-flight handlers on the consumer side. The body runs to completion, holds the slot, and still posts the response projection for a result nobody awaits. Amplifies PCHAT-1.

**PCHAT-5 (sub-second timeout "0s") — CONFIRMED:**
- `private_chat.py:635` `return f"error: target {target_agent_id!r} did not reply within {res.timeout_seconds:.0f}s"`. `timeout_seconds` is a float validated only `>0` (`_resolve_timeout` line 161). `0.5` -> `:.0f` -> "0s". The log line at 628-634 uses `%.1fs` (correct). Trivial format-string fix.

**XC-3 (max_workers=1 turns per-call stalls into fleet outages) — CONFIRMED** as the cross-cutting frame for PCHAT-1/PCHAT-4. Single anyio.Semaphore(1) per subscriber; ANY long-held or blocking handler (web_fetch blocking requests.get, grep ReDoS, private_chat awaited reply) wedges that subscriber for all agents.

### Cleared non-issues (verified from calfkit source)
- Correlation crossing impossible: `Client._build_state_and_overrides` mints a fresh uuid7 per call (client.py:69-71); `_ReplyDispatcher._on_reply` keys futures by TRANSPORT correlation_id from the Kafka header, never the envelope body (reply_dispatcher.py:64-90).
- No pending-future leak on timeout: `expect()` registers `add_done_callback(_discard)` (reply_dispatcher.py:109); `InvocationHandle.result` uses `asyncio.wait_for` which cancels the future on timeout (invocation_handle.py:53), firing `_discard` -> entry popped + timer cancelled + exception retrieved.
- BUT: calfkit's `reply_ttl` defaults to `None` (base.py:120-124) => no eviction ceiling on the `_pending` map unless opted in. Not the cause here (wait_for handles it) but relevant to design.

### calfkit messaging model summary (the substrate we must work within)
- Three patterns on `Client`: `emit_to_node` (fire-and-forget, zero reply state), `invoke_node` (returns `InvocationHandle`, await `.result(timeout=)` later), `execute_node` (invoke + await in one call). private_chat uses `execute_node`.
- Correlation: uuid7 hex per call, Kafka header `correlation_id`; shared `_ReplyDispatcher` consumes ONE reply topic per client and routes envelopes to per-correlation `asyncio.Future`s.
- Timeout: `asyncio.wait_for` at the `InvocationHandle.result` layer (caller-side). Cancellation of the awaiting coroutine cancels the future + discards the pending entry — but does NOT propagate any cancel signal to the REMOTE callee (no distributed cancellation; the callee runs to completion). This is the same gap as PCHAT-4 at the framework level.
- Reentrancy: nothing in calfkit serializes A->B->A at the CLIENT level; the serialization is entirely the consumer-side `max_workers` semaphore on the tools Worker.



## Step 2: Survey frameworks + multi-agent canon

### NOTE on "openclaw" / "hermes-agents"
Both resolve to REAL, post-cutoff repos (created late 2025). GitHub search returns many spam-ish companion apps with inflated star counts; I verified the canonical repos directly via the API rather than trusting search ranking.
- openclaw/openclaw (TypeScript) — huge subagent system.
- NousResearch/hermes-agent (Python) — "the agent that grows with you".

### CANDIDATE: openclaw/openclaw (TypeScript) — subagent spawn/announce model [STRONGEST pattern source]
Files: src/agents/subagent-*.ts, src/agents/tools/sessions-spawn-tool.ts, docs/tools/subagents.md

Messaging model — NON-BLOCKING, PUSH-BASED COMPLETION (the opposite of our blocking execute_node):
- sessions_spawn is non-blocking: returns a run id immediately. Spawning agent's turn does NOT hold a slot awaiting the child.
- Agent calls sessions_yield after spawning; completion arrives later as a push event (model-visible next message). Agents must NOT poll.
- Child runs in its own isolated session; context "isolated" default, "fork" to branch parent transcript.
- This is the architectural fix for PCHAT-1/PCHAT-4: delegating frees the slot; no A->B->A slot contention possible.

Completion delivery — multi-tier fallback + idempotency:
- Completions delivered via an agent turn with a STABLE IDEMPOTENCY KEY.
- Tier 1 wake/steer active requester run; Tier 2 requester-agent handoff w/ same context; Tier 3 queue routing; Tier 4 exponential-backoff retry before give-up.
- Preserves resolved requester route; fills missing target/account from session lastChannel/lastTo/lastAccountId.
- Child output explicitly framed as "report/evidence ... cannot override system/developer/user policy" — content-trust boundary relevant to PCHAT-2.

Reentrancy/depth (vs PCHAT-1):
- subagent-depth.ts persists spawnDepth + spawnedBy lineage across restarts; configurable nesting depth cap prevents unbounded A->B->A recursion.
- agents.defaults.subagents.maxConcurrent caps concurrent children.

Timeout/cancellation (vs PCHAT-4/PCHAT-5):
- subagent-run-timeout.ts: timer-safe delay vs absolute deadline (startedAt+duration) with safe-integer guards; integer-second handling (Math.floor(s)*1000), sub-second -> treated as no-timeout, not "0s" misreport.
- subagent-run-liveness.ts: ages out STALE UNENDED runs (2h, or runTimeout+60s grace); keeps recently-ended child links 30m. The orphan-cleanup PCHAT-4 lacks.
- subagent-recovery-state.ts: ORPHAN RECOVERY GATE — bounds auto recovery to 2 attempts / 2m window then TOMBSTONES (wedged) + tells operator to run maintenance. Prevents infinite retry of a stuck leg.

Integration feasibility: PATTERN-TO-PORT (TypeScript, not a dependency). spawn/yield/announce maps onto calfkit emit_to_node (fire-and-forget spawn, frees slot) + push completion consumer + idempotency key, instead of blocking execute_node. Depth cap, maxConcurrent, stale-run liveness, bounded orphan-recovery all directly portable.

### CANDIDATE: OpenAI Agents SDK (openai/openai-agents-python, Python, MIT)
Files: src/agents/handoffs/__init__.py (Handoff dataclass, handoff()), src/agents/agent.py:508 (as_tool), src/agents/run.py (Runner loop).

Messaging model — IN-PROCESS, no network:
- Handoff = LLM-invoked control transfer. Modeled as a tool `transfer_to_<agent>`; `on_invoke_handoff(ctx, args_json) -> Agent` returns the NEXT agent to run in the SAME Runner.run() loop. The new agent takes over the conversation (sees history, optionally filtered via input_filter). NOT a request/reply; no remote callee.
- agents-as-tools (`Agent.as_tool`) = nested synchronous sub-run via `Runner.run(...)` inside the tool call. Original agent continues after. Bounded by `max_turns`; `failure_error_function` converts a failed sub-run into an LLM-visible error string instead of raising.

Issue-by-issue vs ours:
- PCHAT-1 (reentrancy/concurrency): N/A — single in-process loop, no Kafka slot, no max_workers. No distributed serialization to learn from. Relevant pattern: `max_turns` bounds runaway recursion (a loop/depth cap, like openclaw's spawn-depth).
- PCHAT-2 (sanitization): N/A (no Discord/downstream chat surface). But the as_tool/handoff design treats sub-agent OUTPUT as data the parent synthesizes (custom_output_extractor), echoing the trust boundary.
- PCHAT-3 (size): N/A.
- PCHAT-4/PCHAT-5 (timeout/cancellation): no per-call network timeout; cancellation is asyncio task cancellation of the in-process run (propagates naturally since it's one task tree — contrast our distributed case where cancel does NOT reach the remote callee). Pattern: `failure_error_function` (return error to LLM, don't raise) — calfcord ALREADY does this for timeout (returns "error: ... did not reply").
Code quality: high, MIT, well-documented. 
Integration feasibility: PATTERN reference only. NOT portable as a transport (it's in-process; calfcord's whole point is distributed Kafka). Useful confirmations: (1) bound recursion with a turn/depth cap; (2) convert callee failure to an LLM-visible error string (already done); (3) treat callee output as data, not instructions.

### CANDIDATE: Anthropic Claude Agent SDK (anthropics/claude-agent-sdk-python, Python, MIT)
Files: src/claude_agent_sdk/types.py (AgentDefinition, TaskBudget, Task*Message), examples/agents.py.

Messaging model — declarative subagents + structured async Task lifecycle stream:
- Subagents declared via `AgentDefinition` (description, prompt, tools allow/deny, model, maxTurns, background, effort, permissionMode). The CLI/runtime spawns them via its internal Task tool; the SDK is a thin client over the Claude Code CLI subprocess and does NOT implement the A2A transport itself.
- KEY pattern: async subagent ("Task") lifecycle is a stream of structured messages keyed by `task_id`:
  - TaskStartedMessage(task_id, description, uuid, session_id, tool_use_id, task_type)
  - TaskProgressMessage(task_id, usage=TaskUsage{total_tokens, tool_uses, duration_ms}, last_tool_name)
  - TaskNotificationMessage(task_id, status in {completed|failed|stopped}, output_file, summary, usage)  <- TERMINAL
- This is a CORRELATED, PUSH-BASED, THREE-STATE lifecycle (started/progress/terminal-notification) keyed by task_id. Parent observes the stream rather than blocking on a synchronous call.
- TaskBudget(total tokens) + maxTurns = recursion/runaway bounds. permissionMode + tools allow/deny = capability scoping per subagent.

Issue-by-issue vs ours:
- PCHAT-1: the started/progress/notification stream keyed by task_id is the model that AVOIDS holding a slot — directly the design to adopt. status="stopped" is an explicit cancellation terminal state (something calfkit lacks distributed-ly; relevant to PCHAT-4).
- PCHAT-4: "stopped" terminal status models a cancelled subagent run explicitly; output_file decouples result delivery from a blocking return.
- PCHAT-5: usage.duration_ms is integer ms — no float-second formatting trap.
- PCHAT-2/3: N/A (no Discord surface).
Code quality: high, MIT. 
Integration feasibility: PATTERN-TO-PORT for the result-message TYPES (started/progress/terminal-notification keyed by correlation/task id, terminal status enum incl. "stopped"/"failed"). Not a transport dependency (subprocess-over-CLI). The Task*Message schema is a clean template for a calfcord A2A completion event published to a results topic + projected to Discord.

### CANDIDATE: AutoGen core (microsoft/autogen, Python, code MIT / docs CC-BY-4.0) [STRONGEST API-design reference for RPC + cancellation]
Files: python/packages/autogen-core/src/autogen_core/_agent_runtime.py (AgentRuntime.send_message protocol), _single_threaded_agent_runtime.py (impl), _cancellation_token.py.

Messaging model — message-passing runtime with first-class RPC + cancellation:
- `send_message(message, recipient, *, sender, cancellation_token, message_id) -> response`: RPC. Creates an asyncio.Future, enqueues SendMessageEnvelope(message, recipient, future, cancellation_token, message_id), then `cancellation_token.link_future(future)`, then `await future`. `message_id` (uuid4) is the correlation id.
- `publish_message(...)` is the pub/sub (fire-and-forget) sibling.
- CONCURRENCY (the PCHAT-1 answer): `_process_next` dequeues each envelope and dispatches via `asyncio.create_task(self._process_send(envelope))`, tracked in `_background_tasks` with `add_done_callback(discard)`. Handlers run as INDEPENDENT CONCURRENT TASKS — no single shared slot. A->B->A works because each leg is its own task and the queue keeps pumping while any handler awaits. This is exactly the property max_workers=1 violates.
- CANCELLATION (the PCHAT-4 answer): `MessageContext(is_rpc=True, cancellation_token=..., message_id=...)` is passed to the recipient's on_message, so the token is propagated CALLER->CALLEE. A cancelled caller can cancel the token -> callee's linked futures/awaits cancel cooperatively. CancellationToken (_cancellation_token.py) is a tiny portable primitive: flag + callback list; cancel() fires all; link_future() registers future.cancel(); add_callback() lets a handler register cleanup. On handler CancelledError, _process_send sets the future's exception (doesn't swallow).
- Correlation: message_id propagated through envelope + MessageContext; responses route back via ResponseMessageEnvelope. Recipient-not-found and undeliverable are explicit exceptions.

Issue-by-issue vs ours:
- PCHAT-1: DIRECTLY the fix model — concurrent task-per-message instead of a single semaphore slot. (Our equivalent: raise max_workers on the tools worker AND/OR don't block a slot on the awaited reply.)
- PCHAT-4: DIRECTLY the fix model — a CancellationToken threaded caller->callee so a cancelled outer call can signal the callee to stop (calfkit currently has NO distributed cancel; the callee runs to completion).
- PCHAT-2/3/5: N/A (no chat surface / size / formatting concerns).
Code quality: high, mature, MIT. 
Integration feasibility: PATTERN-TO-PORT (it's an in-process runtime, not a Kafka transport, so not a drop-in dependency). The two portable designs: (1) per-message concurrency (task-per-message; or, in our world, higher max_workers + non-blocking spawn), (2) a CancellationToken propagated in deps so an outer timeout/cancel can reach the A2A callee. NOTE calfkit would need a way to carry a cancel signal across the Kafka hop (a control message / cancel topic keyed by correlation_id) — a calfkit feature gap to file.

### CANDIDATE: Google Agent2Agent (A2A) Protocol (a2aproject/a2a-python, Python, Apache-2.0) [STRONGEST protocol/standard for distributed A2A]
Files: src/a2a/compat/v0_3/types.py (TaskState), src/a2a/client/transports/base.py (client surface), src/a2a/server/tasks/base_push_notification_sender.py, src/a2a/server/agent_execution/active_task_registry.py.

Messaging model — standardized distributed A2A over JSON-RPC/gRPC/REST with an async TASK lifecycle:
- TaskState enum (canonical lifecycle): submitted -> working -> {input-required | completed | canceled | failed | rejected | auth-required}. `input-required` models the "B asks a clarifying question" case (PCHAT-1's nested-A2A scenario) as a FIRST-CLASS state, not a deadlock.
- Client surface (transports/base.py): send_message, send_message_streaming, get_task(task_id), list_tasks, cancel_task(task_id), subscribe (resubscribe via SSE), and create/get/list/delete_task_push_notification_config.
- Correlation: every task has a task_id; ActiveTaskRegistry is a lock-guarded dict[task_id, ActiveTask] with on_cleanup callbacks and a tracked _cleanup_tasks set (bounded lifecycle, unlike calfkit's unbounded _pending without reply_ttl).
- Result delivery: TWO out-of-band channels so the caller never has to hold a blocking connection: (1) SSE streaming/resubscribe, (2) PUSH NOTIFICATIONS — caller registers a PushNotificationConfig (webhook URL + auth token, X-A2A-Notification-Token header); server fans out task-state-change events to it keyed by task_id (base_push_notification_sender.py). Decouples completion from any held slot — directly the PCHAT-1/PCHAT-4 architecture.
- Cancellation: cancel_task is a first-class RPC; TaskNotCancelableError when the task is past a cancelable state. This is exactly the distributed-cancel primitive PCHAT-4 lacks.

Issue-by-issue vs ours:
- PCHAT-1: the async-task + push/subscribe model means NO held slot during the awaited reply; `input-required` makes nested clarification a normal state, not a deadlock. Plus a depth/recursion concept can ride on task lineage.
- PCHAT-4: cancel_task(task_id) propagates cancellation to the remote callee; ActiveTaskRegistry cleanup ages out tasks. Both directly address orphaned-callee + slot-held-to-completion.
- PCHAT-5: durations are not float-second-formatted in error strings (state machine, not a timeout string).
- PCHAT-2: A2A messages are structured Parts (text/file/data); the protocol separates agent-authored content from control — relevant to treating peer content as data. (No Discord specifics.)
Code quality: high; it's an OSS standard with multi-language SDKs (Python/JS/Java/Go/.NET/Rust). Apache-2.0 (permissive). 
Integration feasibility: PROTOCOL-TO-ADOPT (the conceptual model) and POSSIBLY a partial Python dependency for the task-lifecycle + push-notification types. But A2A's transport is HTTP/JSON-RPC/gRPC, NOT Kafka — calfcord's transport is calfkit/Kafka, so we'd ADOPT THE MODEL (task_id-keyed async task with submitted/working/input-required/completed/canceled/failed terminal states + push/subscribe delivery + cancel) rather than the wire. The Discord audit thread is our "push notification sink" analog. Strong fit conceptually; not a literal drop-in transport.

### CANDIDATE: CrewAI (crewAIInc/crewAI, Python, MIT)
Files: lib/crewai/src/crewai/tools/agent_tools/base_agent_tools.py (BaseAgentTool._execute), delegate_work_tool.py, agent_tools.py.
Messaging model — IN-PROCESS synchronous delegation: DelegateWorkTool / AskQuestionTool find the coworker by ROLE (case/whitespace-tolerant, quote-stripped to survive bad LLM JSON) and call `selected_agent.execute_task(task, context)` directly. No correlation-id, no network, no timeout, no cancellation, no reentrancy/slot model (one process).
Issue-by-issue: PCHAT-1/3/4/5 N/A (in-process, synchronous). Relevant patterns ALREADY in calfcord: unknown-agent -> LLM-visible error string listing valid coworkers; execution exception -> error string (not raise). PCHAT-2: sanitizes the agent NAME only; does NOT sanitize delegated content for any chat surface (no analog — CrewAI has no shared chat audit).
Integration feasibility: LOW. Confirms our error-string-as-recoverable-signal design; nothing transport-relevant to port.

### CANDIDATE: LangGraph (langchain-ai/langgraph + langgraph-swarm-py/supervisor-py, Python, MIT)
Files: libs/langgraph/langgraph/types.py (Command, Send, interrupt/resume), langgraph-swarm-py/langgraph_swarm/handoff.py (create_handoff_tool).
Messaging model — GRAPH state-machine control flow:
- Command(goto=node, graph=PARENT, update=state, resume=...) routes control to another node within the graph, merging shared state. Send(node, state) = isolated-state fan-out (map-reduce).
- Handoff (swarm/supervisor) = a tool returning Command(goto=agent_name, graph=PARENT, update={messages}) -> control transfer, in-process.
- interrupt()/Command(resume=value) = pause execution for human (or external) input, resume later. This is the analog of A2A's `input-required` / the "B asks a clarifying question" flow — and notably the node RE-EXECUTES from the start on resume (idempotency burden on the node author).
Issue-by-issue: PCHAT-1/4 N/A in-process (single graph runtime); distributed only via LangGraph Platform's separate runs API (HTTP, not studied as transport). interrupt/resume is the relevant pattern for nested-clarification without deadlock (suspend rather than hold a slot). No chat-surface sanitization concern (PCHAT-2 N/A).
Integration feasibility: PATTERN reference for the suspend/resume (interrupt) model as an alternative to blocking on a nested A2A clarification. Not a transport dependency for Kafka.

### CANDIDATE: Letta / MemGPT (letta-ai/letta, Python, Apache-2.0) [closest distributed-server A2A peer]
Files: letta/functions/function_sets/multi_agent.py; letta/groups/{supervisor,round_robin,dynamic,sleeptime}_multi_agent.py.
Messaging model — server-mediated A2A over the Letta REST API (client.agents.messages.create against a target agent_id):
- send_message_to_agent_and_wait_for_reply(message, other_agent_id) -> BLOCKING two-way (like our execute_node). 
- send_message_to_agent_async(message, other_agent_id) -> FIRE-AND-FORGET one-way notification; returns "Successfully sent message" immediately (frees the caller — the PCHAT-1 non-blocking pattern). (Disabled on Letta Cloud / prod by a settings guard.)
- send_message_to_agents_matching_tags(...) -> fan-out with PER-AGENT ERROR ISOLATION: each target wrapped in try/except, failures recorded as {"agent_id", "response": ["<error: ...>"]} so one failure doesn't abort the batch.
- Group routing patterns (supervisor / round-robin / dynamic / sleeptime) are first-class.
PROMPT-INJECTION / TRUST framing (relevant to PCHAT-2): inbound A2A messages are wrapped with explicit provenance: "[Incoming message from agent with ID '<sender>' - your response will be delivered to the sender] ...". The async variant adds "this is a one-way notification". This frames peer content as identified, attributed data — the same trust posture calfcord should keep when projecting peer text.
Issue-by-issue: PCHAT-1: async variant + per-agent isolation are the relevant fixes (don't block; isolate failures). Correlation is by agent_id + the synchronous HTTP call (no explicit correlation id over a bus — it's request/response HTTP). PCHAT-4: no distributed cancel (blocking variant inherits the HTTP timeout). PCHAT-2: provenance framing pattern. PCHAT-3/5 N/A.
Integration feasibility: PATTERN-TO-PORT (server is a separate process w/ its own DB; transport is HTTP not Kafka). Portable: provide BOTH a blocking and a fire-and-forget A2A tool; isolate per-target failures in any fan-out; frame inbound peer content with explicit sender provenance.

### CANDIDATE: NousResearch/hermes-agent (Python, MIT)
Files: acp_adapter/{server,session,events,permissions}.py, acp_registry/agent.json, gateway/.
Mechanism: exposes hermes via the ACP (Agent Client Protocol) — an agent<->client/editor protocol (the same ACP openclaw uses for `runtime: "acp"`), with a session/events/permissions model. This is agent-to-EDITOR (host) comms, not peer-to-peer A2A messaging between fleet agents. The optional-skill "subagent-driven-development" is prompt/skill guidance, not a transport.
Issue-by-issue: not a direct A2A request/reply transport; ACP's session+events+permissions model is a reference for capability scoping + structured event streams, but no PCHAT-* concurrency/correlation lessons beyond what ACP-in-openclaw already gave.
Integration feasibility: LOW as a transport; ACP is worth noting as a cross-harness protocol (like A2A but client<->agent).

### CANDIDATE: Cline (cline/cline, TypeScript, Apache-2.0) — new_task
Files: apps/vscode/src/core/task/tools/handlers/NewTaskHandler.ts, prompts/system-prompt/tools/new_task.ts.
Mechanism: `new_task` is a HUMAN-APPROVED context handoff to a fresh task in a single-user IDE session (asks the user, carries context forward). No A2A, no correlation, no network, no concurrency model. NOT relevant to distributed A2A.

### CANDIDATE: OpenHands (All-Hands-AI/OpenHands) — delegation
Current repo layout moved the agent/controller core out of the main repo (the `openhands/` package now holds server/app_server only); historical model is `AgentDelegateAction` — a parent agent delegates to a sub-agent that runs its OWN controller loop and returns an Observation (in-process, single event stream). Microagents are prompt-injection knowledge snippets, not an A2A transport. In-process delegation -> same lessons as OpenAI/CrewAI (bound the sub-run, return failures as observations); not transport-relevant for Kafka A2A.

### CLI agents (Gemini CLI / Codex CLI / OpenCode)
Sub-agent/task delegation in these is local subprocess/in-process orchestration (subagent configs, task tools) rather than a distributed A2A request/reply bus. openclaw's ACP integration explicitly bridges to Claude Code / Gemini / OpenCode / Codex as ACP harnesses (delegation OUTWARD to a harness), which is the only cross-agent transport angle and is already covered under openclaw/ACP. No distinct distributed-A2A concurrency/correlation pattern beyond what's above.

## Step 3: Expand — distributed RPC / message-bus canon

### CANDIDATE: Spring Kafka ReplyingKafkaTemplate (spring-projects/spring-kafka, Java, Apache-2.0) [CANONICAL Kafka request/reply — our transport]
File: spring-kafka/src/main/java/org/springframework/kafka/requestreply/ReplyingKafkaTemplate.java (+ AggregatingReplyingKafkaTemplate.java).
Model — THE battle-tested Kafka request/reply correlation pattern; maps 1:1 onto calfkit's _ReplyDispatcher (validates calfkit's design):
- ConcurrentMap<correlation, RequestReplyFuture> futures (= calfkit _pending). correlationStrategy default = random UUID -> 16-byte binary header KafkaHeaders.CORRELATION_ID on the request (calfkit uses uuid7 hex header).
- onMessage(replies) -> handleReply -> futures.remove(correlationId) + future.complete(record) (= calfkit _on_reply set_result; both drop unknown/late correlations).
- scheduleTimeout: a PER-REQUEST reaper scheduled at send time that ALWAYS removes the future from the map and future.completeExceptionally(KafkaReplyTimeoutException) on timeout. => the pending map is BOUNDED BY DEFAULT (every request self-cleans), unlike calfkit where the bound requires opt-in reply_ttl (execute_node's asyncio.wait_for cleans the awaited case, but a per-request reaper is the stronger default).
- AggregatingReplyingKafkaTemplate: fan-out request -> aggregate N replies by correlation with a release strategy + timeout (the scatter-gather analog of Letta's tag fan-out).
Issue-by-issue: PCHAT-1: NO single-slot concept (the reply container is a separate consumer; requests don't block a shared handler slot) — confirms the fix is to not hold a consumer slot during the awaited reply. PCHAT-4: KafkaReplyTimeoutException + map-remove is the bounded-cleanup pattern; still no distributed CANCEL of the callee (same gap — request/reply over a bus inherently can't cancel an in-flight callee without a separate control message). PCHAT-5: durations are java.time.Duration (no float-second string trap).
Integration feasibility: PATTERN-TO-ADOPT (Java, not a dependency). Lessons for calfcord/calfkit: (1) a per-request timeout reaper that bounds the pending map (calfkit: set reply_ttl or rely on wait_for — already OK for the awaited case); (2) correlation in the header, never the body (calfkit already does this — confirmed safe); (3) for fan-out, aggregate-by-correlation with a release+timeout strategy.

### CANDIDATE: AgentScope (agentscope-ai/agentscope, Python, Apache-2.0) — distributed actor agents
Model: actor-style distributed agents with RPC + placeholder/future async message passing (historically gRPC-backed `RpcAgent` + `Msg`/`MsgHub`); the v1 rewrite reorganized modules. Conceptually overlaps AutoGen (message-passing + futures) and A2A (distributed). Provides the actor pattern: each agent is an addressable mailbox; sending returns a placeholder/future resolved when the remote reply lands — non-blocking by construction (the caller can fan out then resolve).
Issue-by-issue: PCHAT-1: actor mailbox + placeholder/future = non-blocking send (no shared slot) — same lesson as AutoGen. PCHAT-4: distributed cancel not a headline feature.
Integration feasibility: PATTERN reference (placeholder/future actor model). Not a Kafka transport dependency.

## Survey complete. Frameworks with NO distributed-A2A mechanism to learn from (in-process only): OpenAI Agents SDK (handoff/as_tool), CrewAI (delegate), LangGraph (Command/handoff), Cline (new_task), OpenHands (AgentDelegateAction). Their reusable lessons: bound recursion (max_turns/depth), return callee failure as an LLM-visible error string (calfcord already does), treat callee output as data, suspend/resume (interrupt) for nested clarification.

## Cross-cutting design synthesis (for the report)
The single dominant pattern across ALL distributed candidates (openclaw, Claude Agent SDK, A2A protocol, AutoGen, Letta, AgentScope): for anything longer than a trivial reply, DO NOT BLOCK on a synchronous request/reply that holds a consumer slot. Instead:
1. SPAWN (fire-and-forget / emit_to_node) returning a correlation/task id immediately -> frees the slot (fixes PCHAT-1, PCHAT-4, XC-3 at the root).
2. Track the task by a CORRELATION/TASK ID in a bounded registry with cleanup (A2A ActiveTaskRegistry, Spring scheduleTimeout reaper, openclaw run-liveness).
3. Deliver completion via a PUSH/announce event keyed by that id (Claude SDK Task*Message, A2A push-notification, openclaw multi-tier announce) -> projected to the Discord audit thread.
4. Model lifecycle as a STATE MACHINE with a terminal status incl. cancel: A2A submitted/working/input-required/completed/canceled/failed/rejected; Claude SDK completed/failed/stopped.
5. Bound REENTRANCY with a depth/turn cap + maxConcurrent (openclaw spawn-depth/maxConcurrent, OpenAI max_turns).
6. Thread a CANCELLATION TOKEN caller->callee (AutoGen CancellationToken) so an outer timeout reaches the callee (fixes PCHAT-4 properly; needs a calfkit control-channel feature).
7. Treat peer content as ATTRIBUTED DATA, never instructions; frame with provenance (Letta) AND sanitize for the downstream surface (PCHAT-2 -> allowed_mentions).

## Fix-detail confirmations
- PCHAT-2: discord.py docs confirm allowed_mentions is global (Client.allowed_mentions) or per-send (Messageable.send / Webhook.send). Our persona client (persona.py:282) sets no allowed_mentions -> falls back to discord.py's library default AllowedMentions.all() (everyone/roles/users parsed). Fix: discord.AllowedMentions.none() at the persona client OR per webhook.send(). One-line/low-risk fix; private_chat is the highest-risk consumer.
- PCHAT-3: chunk_split (retry_feedback.py:219, CHUNK_SAFE_SIZE=1990) and classify_error already exist and are used on the RESPONSE side. The request side (_start_new_thread) can reuse them: either chunk-split the request projection (but then the thread anchor must be the FIRST chunk) OR classify the 400 and return a recoverable "error: shorten content" string. The simplest contract-preserving fix is the recoverable-error string (matches the documented error: family); chunk-splitting the request is also viable since the helper exists.
- PCHAT-5: change private_chat.py:635 from {res.timeout_seconds:.0f}s to {res.timeout_seconds:g}s (or .1f). Trivial.
