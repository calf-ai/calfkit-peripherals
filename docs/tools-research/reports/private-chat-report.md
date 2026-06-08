# private_chat — A2A messaging research report

**Tool:** `src/calfcord/tools/builtin/private_chat.py` (agent-to-agent messaging over calfkit/Kafka request-reply, audited to a Discord thread via persona webhooks).
**Scope:** find frameworks/libraries whose agent-to-agent messaging / handoff / multi-agent-communication primitives calfcord can adopt or port, and extract battle-tested patterns for request/reply correlation, timeout/cancellation, reentrancy, and concurrency. Graded against PCHAT-1..5 and XC-3.
**Date:** 2026-06-06.

---

## 1. Executive summary + top recommendation

**A drop-in replacement does not exist and is not the right framing.** `private_chat` is the most calfcord-specific built-in tool: it couples calfkit's Kafka request/reply (`Client.execute_node`) with a Discord persona-webhook audit projection. No third-party library ships that exact combination. Every candidate is therefore one of: *adopt-the-pattern*, *port-the-design*, *adopt-the-protocol-model*, or *use-a-library-for-one-part* — not "swap the file."

**The single most important finding** is that the strongest distributed candidates (openclaw, Anthropic's Claude Agent SDK, Google's A2A protocol, AutoGen, Letta) all converge on the **same architecture, and it is the opposite of ours**: for any non-trivial delegation, **do not block on a synchronous request/reply that holds a consumer slot**. Instead *spawn → return a correlation/task id immediately → deliver completion later as a push/announce event keyed by that id → model the lifecycle as a state machine with explicit terminal states (including cancelled)*. Our `execute_node`-in-the-handler design is exactly what causes PCHAT-1 and PCHAT-4.

**Top recommendation, prioritised by ROI:**

1. **Fix the cheap, isolated bugs now** (no framework needed): PCHAT-2 (`allowed_mentions=discord.AllowedMentions.none()`), PCHAT-5 (`:g`/`.1f` format), PCHAT-3 (reuse the existing `chunk_split`/`classify_error` to return a recoverable `error:` string or chunk-split the request projection). These are one-to-few-line changes.
2. **Fix the concurrency-model issue (PCHAT-1/PCHAT-4/XC-3) with the right altitude.** Short term: raise `max_workers` on the tools worker (or host `private_chat` on a dedicated higher-concurrency worker) so a held slot no longer serializes the fleet — the tool body is I/O-bound `await`, the dispatcher and projections are concurrency-safe. Medium term, **port the spawn/announce model** (openclaw + Claude Agent SDK + A2A): turn A2A into a non-blocking `emit_to_node` spawn that returns a task id, with a push completion consumer that projects the result to the Discord thread, plus a **bounded reentrancy guard** (a spawn-depth cap, per openclaw) and a **cancellation token threaded caller→callee** (per AutoGen) — the last of which is a calfkit feature gap to file.

**Best pattern sources, ranked:** (1) **openclaw** subagent spawn/announce + depth/liveness/recovery; (2) **AutoGen** RPC + `CancellationToken` + task-per-message concurrency; (3) **Google A2A protocol** task state machine + push notifications + `cancel_task`; with **Anthropic Claude Agent SDK** (Task lifecycle message types), **Spring Kafka `ReplyingKafkaTemplate`** (canonical Kafka correlation, validates calfkit's design), and **Letta** (blocking + fire-and-forget variants, provenance framing) as strong supporting references.

---

## 2. Verified recap of our `private_chat` issues and true causes

All five were verified against source (`private_chat.py`, `discord/persona.py`, calfkit `client/*`, FastStream subscriber internals).

| ID | Severity | True root cause (verified) |
|----|----------|----------------------------|
| **PCHAT-1** | 🟠 | `tools/runner.py:119` builds `Worker(client, tool_nodes)` with the default `max_workers=1` (`worker.py:61`), which becomes `subscriber(..., max_workers=1)` (`worker.py:211`), implemented by FastStream as `anyio.Semaphore(1)` per subscriber (`faststream/.../subscriber/mixins.py:61`). The `private_chat` handler holds that single slot for the whole `await res.client.execute_node(...)` (`private_chat.py:600`). A reentrant call (B→A while A awaits B) lands a new `tool.private_chat.input` message behind A's still-running handler in the same single-slot buffer; it can't be dispatched until A returns, and A won't return until B replies → stall until the inner 60s timeout fires. Unrelated A2A calls also serialize. **Concurrency-model bug, not a correlation bug.** The agent side does *not* deadlock (calfkit's deferred-tool `Call` frees the agent slot). |
| **PCHAT-2** | 🟠 | `persona.py:375` `webhook.send(content=content, ...)` passes no `allowed_mentions`; the persona client (`persona.py:282`, `discord.Client(intents=Intents.none())`) sets no default. discord.py then falls back to its library default `AllowedMentions.all()`, so LLM-controlled `@everyone`/`@here`/`<@&role>` in request `content` or the peer's reply **actually ping the guild** from the audit channel. Vectors: `_post_projection`→`send`, `_post_response_with_feedback_retries`→`send` (line 744), `_post_chunked_projection`→`send` (line 929). |
| **PCHAT-3** | 🟡 | New-thread branch (`_start_new_thread`, line 952) posts the LLM-supplied `content` via `_post_projection` with `correlation_id=None`. A >2000-char `content` raises `discord.HTTPException` (400) on both attempts (`_MAX_PROJECTION_ATTEMPTS=2`; same content each time), `_post_projection` returns `None` (line 1358), and `_start_new_thread` escalates via `_raise_infra` → `RuntimeError` (line 985). So an over-length briefing yields a hard infra error, not the documented recoverable `error:` string. The response side already has `chunk_split`; the request side does not. |
| **PCHAT-4** | 🟢 | FastStream runs the handler as `asyncio.create_task(func(...))` inside the semaphore (`mixins.py:29`) and does **not** cancel an in-flight handler when the *caller's* outer `execute_node` times out/cancels. The `private_chat` body runs to completion, keeps holding the slot, and still posts the response projection nobody awaits. calfkit has no distributed cancellation (timeout cancels only the caller-side future, never the remote callee). Amplifies PCHAT-1. |
| **PCHAT-5** | 🟢 | `private_chat.py:635`: `f"...within {res.timeout_seconds:.0f}s"`. `timeout_seconds` is a float validated only `>0`; `0.5` → `"within 0s"`. The log line uses `%.1fs` (correct). |
| **XC-3** | 🟠 | The cross-cutting frame: one `anyio.Semaphore(1)` per subscriber means *any* long-held/blocking handler (private_chat's awaited reply, web_fetch's blocking `requests.get`, grep ReDoS) wedges that subscriber for every agent — a per-call stall becomes a fleet outage. |

**Cleared as non-issues (verified in calfkit):** correlation crossing is impossible (fresh uuid7 per call, futures keyed by the transport correlation header, never the body — `client.py:69`, `reply_dispatcher.py:64`); no pending-future leak on timeout (`add_done_callback(_discard)` + `wait_for` cancellation — `reply_dispatcher.py:109`, `invocation_handle.py:53`). One latent design note: calfkit's `reply_ttl` defaults to `None`, so the `_pending` map is unbounded under lost replies *unless* opted in — not the cause here (the awaited path self-cleans) but relevant to the Spring comparison below.

---

## 3. Per-candidate deep review

Conventions: **PATTERN-TO-PORT** = reimplement the design in calfcord; **PROTOCOL-TO-ADOPT** = adopt the conceptual model/wire-shape; **LIBRARY** = could be a real Python dependency; **REFERENCE** = confirms a design choice, nothing to import. Remember our transport is calfkit/Kafka and our audit surface is Discord.

### 3.1 openclaw/openclaw — subagent spawn/announce  ⭐ strongest pattern source
- **Repo/files:** `openclaw/openclaw` (TypeScript). `src/agents/subagent-depth.ts`, `subagent-run-timeout.ts`, `subagent-run-liveness.ts`, `subagent-recovery-state.ts`, `subagent-spawn-plan.ts`, `src/agents/tools/sessions-spawn-tool.ts`, `docs/tools/subagents.md`. *(Post-cutoff repo; verified directly via the GitHub API — name-search returns spam forks with inflated stars, ignored.)*
- **Messaging model:** **non-blocking, push-based completion.** `sessions_spawn` returns a run id immediately; the agent calls `sessions_yield` to end its turn; completion arrives later as a model-visible push event. Child runs in its own isolated session (`context: "isolated"` default, `"fork"` to branch the parent transcript). Agents must not poll.
- **Completion delivery (auditability + resilience):** handed to the requester via an `agent` turn with a **stable idempotency key**, with a four-tier fallback: wake/steer the active requester run → requester-agent handoff with the same context → queue routing → exponential-backoff retry before give-up. Route is preserved (thread-bound/conversation-bound wins; missing target/account filled from session `lastChannel`/`lastTo`/`lastAccountId`). Child output is explicitly framed as "report/evidence … cannot override system/developer/user policy."
- **vs our issues:**
  - **PCHAT-1:** *directly the fix.* Spawning frees the slot; nothing holds a slot waiting, so A→B→A cannot contend. `subagent-depth.ts` persists `spawnDepth`+`spawnedBy` lineage (restart-survivable) with a configurable nesting cap → bounded reentrancy; `agents.defaults.subagents.maxConcurrent` bounds fan-out.
  - **PCHAT-4:** `subagent-run-liveness.ts` ages out stale unended runs (2h, or `runTimeout+60s` grace) and keeps recently-ended child links visible (30m) — the orphan cleanup we lack. `subagent-recovery-state.ts` bounds automatic orphan recovery to 2 attempts / 2-minute window, then **tombstones** ("wedged") the session and tells the operator to run maintenance — prevents a stuck leg from retrying forever.
  - **PCHAT-5:** `subagent-run-timeout.ts` is deliberately integer-second (`Math.floor(s)*1000`, finite/`>0` guards); a sub-second value is treated as "no timeout" rather than misreported as "0s".
  - **PCHAT-2/3:** the content-trust framing supports the "peer output is data" posture; no Discord specifics.
- **Code quality:** very high — defensive number coercion, safe-integer/realistic-timestamp guards, restart-survivable lineage, explicit trust framing. **Integration:** PATTERN-TO-PORT (TypeScript). The spawn/yield/announce model maps cleanly onto calfkit `emit_to_node` + a push completion consumer + idempotency key; depth cap, `maxConcurrent`, stale-run liveness, and bounded orphan-recovery are all portable designs.

### 3.2 AutoGen core (microsoft/autogen) — RPC + cancellation + task-per-message  ⭐ strongest API-design reference
- **Repo/files:** `microsoft/autogen` (Python; code MIT). `python/packages/autogen-core/src/autogen_core/_agent_runtime.py`, `_single_threaded_agent_runtime.py`, `_cancellation_token.py`.
- **Messaging model:** message-passing runtime with first-class RPC. `send_message(message, recipient, *, sender, cancellation_token, message_id) -> response`: creates an `asyncio.Future`, enqueues `SendMessageEnvelope(message, recipient, future, cancellation_token, message_id)`, calls `cancellation_token.link_future(future)`, then `await future`. `message_id` (uuid4) is the correlation id; `publish_message(...)` is the pub/sub sibling.
- **Concurrency (the PCHAT-1 answer):** `_process_next` dequeues each envelope and dispatches it as an **independent `asyncio.create_task(self._process_send(...))`** tracked in `_background_tasks` with `add_done_callback(discard)`. Handlers run concurrently — no single shared slot — so A→B→A works because each leg is its own task and the queue keeps pumping while any handler awaits.
- **Cancellation (the PCHAT-4 answer):** the `CancellationToken` (a tiny portable primitive: flag + callback list; `cancel()` fires all, `link_future()` registers `future.cancel()`, `add_callback()` lets a handler register cleanup) is passed to the recipient via `MessageContext(is_rpc=True, cancellation_token=..., message_id=...)`. A cancelled caller can cancel the token → the callee's linked awaits cancel cooperatively; on handler `CancelledError`, `_process_send` sets the future's exception rather than swallowing it.
- **vs our issues:** PCHAT-1 (concurrent task-per-message instead of a single semaphore slot) and PCHAT-4 (a cancellation token threaded caller→callee) are *both directly addressed*. PCHAT-2/3/5 N/A.
- **Code quality:** high, mature, MIT. **Integration:** PATTERN-TO-PORT — it's an in-process runtime, not a Kafka transport. Two portable designs: (1) per-message concurrency (in our world: higher `max_workers` + non-blocking spawn), (2) a `CancellationToken` carried in `deps`/headers so an outer timeout reaches the callee. The latter needs calfkit to carry a cancel signal across the Kafka hop (a control message / cancel topic keyed by `correlation_id`) — **file as a calfkit feature gap.**

### 3.3 Google Agent2Agent (A2A) Protocol (a2aproject/a2a-python) — task state machine + push + cancel  ⭐ strongest standard
- **Repo/files:** `a2aproject/a2a-python` (Python, Apache-2.0). `src/a2a/compat/v0_3/types.py` (`TaskState`), `src/a2a/client/transports/base.py` (client surface), `src/a2a/server/tasks/base_push_notification_sender.py`, `src/a2a/server/agent_execution/active_task_registry.py`.
- **Messaging model:** standardized distributed A2A (JSON-RPC/gRPC/REST) with an async **task** lifecycle: `submitted → working → {input-required | completed | canceled | failed | rejected | auth-required}`. `input-required` models the "B asks a clarifying question" case (PCHAT-1's nested-A2A scenario) as a *first-class state, not a deadlock*.
- **Client surface:** `send_message`, `send_message_streaming`, `get_task(task_id)`, `list_tasks`, `cancel_task(task_id)`, `subscribe` (SSE resubscribe), and `create/get/list/delete_task_push_notification_config`.
- **Correlation + cleanup:** every task has a `task_id`; `ActiveTaskRegistry` is a lock-guarded `dict[task_id, ActiveTask]` with `on_cleanup` callbacks and a tracked `_cleanup_tasks` set (bounded lifecycle).
- **Out-of-band delivery (so the caller never holds a blocking connection):** SSE streaming/resubscribe, and **push notifications** — the caller registers a `PushNotificationConfig` (webhook URL + `X-A2A-Notification-Token`); the server fans out task-state-change events keyed by `task_id` (`base_push_notification_sender.py`). The Discord audit thread is calfcord's natural push sink.
- **Cancellation:** `cancel_task(task_id)` is a first-class RPC; `TaskNotCancelableError` when past a cancelable state — exactly the distributed-cancel primitive PCHAT-4 lacks.
- **vs our issues:** PCHAT-1 (no held slot; `input-required` for nested clarification), PCHAT-4 (`cancel_task` + registry cleanup), PCHAT-5 (state machine, no float-second string). PCHAT-2: structured `Parts` separate content from control.
- **Code quality:** high; OSS standard with Python/JS/Java/Go/.NET/Rust SDKs; Apache-2.0. **Integration:** PROTOCOL-TO-ADOPT (the model), possibly a partial Python dependency for the task-lifecycle + push types. The wire is HTTP/JSON-RPC/gRPC, not Kafka — adopt the *model* (task_id-keyed async task with the state set above, push/subscribe delivery, `cancel_task`) over calfkit/Kafka rather than the literal transport.

### 3.4 Anthropic Claude Agent SDK (anthropics/claude-agent-sdk-python) — Task lifecycle message types
- **Repo/files:** `anthropics/claude-agent-sdk-python` (Python, MIT). `src/claude_agent_sdk/types.py` (`AgentDefinition`, `TaskBudget`, `TaskStarted/Progress/NotificationMessage`), `examples/agents.py`.
- **Messaging model:** declarative subagents (`AgentDefinition`: description, prompt, tools allow/deny, model, `maxTurns`, `background`, `effort`, `permissionMode`) spawned by the Claude Code CLI's internal Task tool; the SDK is a thin client over that subprocess and does not implement the A2A transport. **Key pattern:** async subagent ("Task") lifecycle is a *correlated, push-based, three-state stream keyed by `task_id`*: `TaskStartedMessage` → `TaskProgressMessage` (with `TaskUsage{total_tokens, tool_uses, duration_ms}`) → `TaskNotificationMessage(status ∈ {completed|failed|stopped})` (terminal, with `output_file`, `summary`). `TaskBudget(total)` + `maxTurns` bound runaway runs.
- **vs our issues:** PCHAT-1 (started/progress/notification stream avoids holding a slot), PCHAT-4 (`stopped` is an explicit cancellation terminal state; `output_file` decouples result from a blocking return), PCHAT-5 (`duration_ms` is integer). PCHAT-2/3 N/A.
- **Code quality:** high, MIT. **Integration:** PATTERN-TO-PORT for the *result-message types* — a clean template for a calfcord A2A completion event (started/progress/terminal-notification keyed by correlation id, terminal status enum incl. `stopped`/`failed`) published to a results topic and projected to Discord. Not a transport dependency (subprocess-over-CLI).

### 3.5 Spring Kafka `ReplyingKafkaTemplate` (spring-projects/spring-kafka) — canonical Kafka request/reply
- **Repo/files:** `spring-projects/spring-kafka` (Java, Apache-2.0). `.../requestreply/ReplyingKafkaTemplate.java`, `AggregatingReplyingKafkaTemplate.java`.
- **Messaging model:** the battle-tested Kafka request/reply correlation pattern; maps 1:1 onto calfkit's `_ReplyDispatcher` (validating calfkit's design). `ConcurrentMap<correlation, RequestReplyFuture>` (= `_pending`); correlation in `KafkaHeaders.CORRELATION_ID` (= calfkit's uuid7 header); `onMessage`→`handleReply`→`futures.remove` + `future.complete` (= calfkit `_on_reply`). **`scheduleTimeout`** is a per-request reaper scheduled at send time that *always* removes the future and `completeExceptionally(KafkaReplyTimeoutException)` — so the pending map is **bounded by default**. `AggregatingReplyingKafkaTemplate` does scatter-gather: fan-out then aggregate N replies by correlation with a release strategy + timeout.
- **vs our issues:** PCHAT-1 — the reply container is a *separate consumer*; requests don't block a shared handler slot (confirms the fix: don't hold a consumer slot during the awaited reply). PCHAT-4 — bounded-cleanup pattern, but still no callee cancel over a bus (same inherent gap: request/reply can't cancel an in-flight callee without a control message). PCHAT-5 — `Duration`, no float-second string.
- **Code quality:** high, canonical. **Integration:** REFERENCE / PATTERN. Lessons: (1) a per-request timeout reaper bounds the pending map (calfkit: rely on `wait_for` for the awaited case, or set `reply_ttl`); (2) correlation in the header never the body (calfkit already does — confirmed safe); (3) for fan-out, aggregate-by-correlation with a release+timeout strategy.

### 3.6 Letta / MemGPT (letta-ai/letta) — server-mediated A2A, both modes, provenance framing
- **Repo/files:** `letta-ai/letta` (Python, Apache-2.0). `letta/functions/function_sets/multi_agent.py`; `letta/groups/{supervisor,round_robin,dynamic,sleeptime}_multi_agent.py`.
- **Messaging model:** A2A over the Letta REST API (`client.agents.messages.create` against a target `agent_id`). `send_message_to_agent_and_wait_for_reply` = **blocking** two-way (like our `execute_node`). `send_message_to_agent_async` = **fire-and-forget** one-way (returns immediately, frees the caller — the PCHAT-1 pattern; prod-guarded). `send_message_to_agents_matching_tags` = fan-out with **per-agent error isolation** (each target try/except'd; failures recorded as `<error: …>` so one failure doesn't abort the batch). Group routing (supervisor/round-robin/dynamic/sleeptime) is first-class.
- **Trust framing (relevant to PCHAT-2):** inbound A2A messages are wrapped with explicit provenance — `"[Incoming message from agent with ID '<sender>' - your response will be delivered to the sender] …"`; the async variant adds `"this is a one-way notification"`. Peer content is presented as identified, attributed data.
- **vs our issues:** PCHAT-1 (the async/fire-and-forget variant + per-target isolation), PCHAT-2 (provenance framing), correlation by `agent_id` + the synchronous HTTP call. PCHAT-3/4/5 N/A or HTTP-timeout-inherited.
- **Code quality:** good, Apache-2.0. **Integration:** PATTERN-TO-PORT (separate server, HTTP transport). Portable: provide *both* blocking and fire-and-forget A2A tools; isolate per-target failures in any fan-out; frame inbound peer content with explicit sender provenance.

### 3.7 In-process frameworks — no distributed A2A mechanism (lessons only)
These run delegation in a single process / event stream; no network correlation, timeout, cancellation, or slot model to learn from. Listed for completeness.

- **OpenAI Agents SDK** (`openai/openai-agents-python`, Python, MIT): *handoff* = LLM-invoked control transfer (`transfer_to_<agent>`, `on_invoke_handoff(ctx, args) -> Agent`) in the same `Runner.run()` loop; *agents-as-tools* (`Agent.as_tool`) = nested synchronous `Runner.run(...)`, bounded by `max_turns`, with `failure_error_function` returning an LLM-visible error string. Lessons: bound recursion with a turn/depth cap; convert callee failure to an error string (calfcord already does); treat callee output as data.
- **CrewAI** (`crewAIInc/crewAI`, Python, MIT): `DelegateWorkTool`/`AskQuestionTool` via `BaseAgentTool._execute` — finds coworker by role (whitespace/quote-tolerant) and calls `selected_agent.execute_task(...)` in-process. Unknown agent → error string listing valid coworkers; exception → error string (both already mirrored in calfcord). Sanitizes the agent *name* only, not content (no PCHAT-2 analog).
- **LangGraph** (`langchain-ai/langgraph` + swarm/supervisor, Python, MIT): graph control flow — `Command(goto=node, graph=PARENT, update=state, resume=…)`, `Send(node, state)`, handoff = a tool returning `Command(goto=agent)`. `interrupt()`/`Command(resume=…)` suspends for external input and **re-executes the node on resume** (idempotency burden). Lesson: suspend/resume (interrupt) as an alternative to blocking on a nested clarification — analogous to A2A `input-required`.
- **Cline** (`cline/cline`, TS, Apache-2.0): `new_task` is a human-approved context handoff in a single-user IDE session. Not A2A.
- **OpenHands** (`All-Hands-AI/OpenHands`, NOASSERTION): historical `AgentDelegateAction` — parent delegates to a sub-agent that runs its own controller loop and returns an Observation (in-process, single event stream; core moved out of the main repo). Microagents are prompt snippets, not a transport.
- **AgentScope** (`agentscope-ai/agentscope`, Python, Apache-2.0): distributed actor agents with placeholder/future async message passing (historically gRPC `RpcAgent`). Same lesson as AutoGen: actor mailbox + future = non-blocking send. PATTERN reference, not a Kafka dependency.
- **NousResearch/hermes-agent** (Python, MIT): exposes the agent via **ACP** (Agent Client Protocol) — `acp_adapter/{server,session,events,permissions}.py`. ACP is agent↔client/editor (host) comms, not peer A2A; same ACP openclaw bridges to for `runtime: "acp"`. Reference for capability scoping + structured event streams; not a peer transport.
- **CLI agents** (Gemini CLI / Codex CLI / OpenCode): sub-agent/task delegation is local subprocess/in-process orchestration, not a distributed A2A bus. The only cross-agent angle is being targeted as ACP harnesses (covered under openclaw/ACP).

---

## 4. Comparison matrix

Legend: ✅ directly addresses / strong pattern · ◑ partial / indirect · — N/A or absent.

| Candidate | Transport | PCHAT-1 reentrancy/concurrency | PCHAT-2 sanitization | PCHAT-3 size | PCHAT-4 cancel/orphan | PCHAT-5 fmt | Correlation | Distributed cancel | Push/async delivery |
|-----------|-----------|:--:|:--:|:--:|:--:|:--:|:--:|:--:|:--:|
| **openclaw** | in-proc + ACP | ✅ spawn/yield + depth + maxConcurrent | ◑ trust framing | — | ✅ liveness + bounded recovery | ✅ int-seconds | ✅ run id | ◑ run timeout/liveness | ✅ multi-tier announce + idempotency |
| **AutoGen** | in-proc msg bus | ✅ task-per-message | — | — | ✅ CancellationToken caller→callee | — | ✅ message_id | ✅ token propagated | ◑ pub/sub |
| **A2A protocol** | HTTP/JSON-RPC/gRPC | ✅ async task + `input-required` | ◑ structured Parts | — | ✅ `cancel_task` + registry cleanup | ✅ state machine | ✅ task_id | ✅ `cancel_task` | ✅ push-notif + SSE |
| **Claude Agent SDK** | CLI subprocess | ✅ Task lifecycle stream | — | — | ◑ `stopped` terminal + output_file | ✅ int ms | ✅ task_id | ◑ `stopped` status | ✅ Task*Message stream |
| **Spring Kafka RKT** | **Kafka** | ✅ separate reply consumer (no slot) | — | — | ◑ timeout reaper (no callee cancel) | ✅ Duration | ✅ header | — | ◑ aggregating fan-out |
| **Letta** | HTTP (server) | ✅ async variant + per-target isolation | ✅ provenance framing | — | ◑ HTTP timeout | — | ◑ agent_id | — | ◑ async one-way |
| OpenAI Agents | in-proc | — (max_turns cap) | — | — | ◑ asyncio cancel (in-proc) | — | — | ◑ in-proc | — |
| CrewAI | in-proc | — | ◑ name sanitize | — | — | — | — | — | — |
| LangGraph | in-proc/platform | ◑ interrupt/resume | — | — | ◑ in-proc cancel | — | — | — | ◑ platform runs |
| **calfcord today** | **Kafka (execute_node)** | ❌ single slot held | ❌ no allowed_mentions | ❌ RuntimeError | ❌ orphans callee | ❌ "0s" | ✅ uuid7 header (sound) | ❌ none | ❌ blocking only |

---

## 5. Ranking with rationale

1. **openclaw — spawn/announce model.** The most complete, production-shaped answer to our hardest issues (PCHAT-1, PCHAT-4, XC-3): non-blocking spawn, push completion with idempotency + multi-tier delivery, depth/concurrency caps for reentrancy, stale-run liveness, and bounded orphan recovery. Closest in spirit to our Discord-projected audit (route preservation, announce).
2. **AutoGen core — RPC + CancellationToken + task-per-message.** The cleanest, most portable *API design* for the two structural fixes: concurrent handlers (PCHAT-1) and a cancellation token threaded caller→callee (PCHAT-4). The `CancellationToken` is ~30 lines and copyable.
3. **Google A2A protocol — task state machine + push + cancel.** The canonical *standard* for distributed A2A: the `submitted/working/input-required/completed/canceled/failed/rejected` lifecycle, `cancel_task`, and push notifications are the model to converge on; `input-required` elegantly resolves the nested-clarification deadlock.
4. **Anthropic Claude Agent SDK** — best concrete *message-type* template (Started/Progress/Notification keyed by task_id, terminal `completed|failed|stopped`).
5. **Spring Kafka `ReplyingKafkaTemplate`** — confirms calfkit's correlation design is sound and shows the per-request timeout reaper for a bounded pending map; relevant because our transport *is* Kafka.
6. **Letta** — practical "ship both blocking and fire-and-forget A2A tools," per-target failure isolation, and provenance framing for trust.
7. In-process frameworks (OpenAI/CrewAI/LangGraph/Cline/OpenHands/AgentScope/hermes) — lessons only (recursion bound, error-string contract, suspend/resume), no transport relevance.

---

## 6. Final recommendation — which patterns/libraries to adopt to fix PCHAT-1..5

**No single library is a drop-in.** Fix the issues with targeted changes plus a medium-term model port. Two of the five are trivial sender/format fixes; one is a contained reuse; the heavy one (PCHAT-1/PCHAT-4) is a deliberate concurrency-model change.

**PCHAT-2 (mass-ping injection) — fix now, no framework.** Set `allowed_mentions=discord.AllowedMentions.none()` either on the persona client at `start()` (`persona.py:282`) or per `webhook.send()` (`persona.py:375`). discord.py docs confirm the send-level/global precedence and that the current absence falls back to `AllowedMentions.all()`. One-line, low-risk; `private_chat` is the highest-risk consumer because it forwards untrusted peer/LLM text into a shared channel. Pair with Letta-style provenance framing if you want peer content clearly attributed.

**PCHAT-5 (sub-second "0s") — fix now.** Change `private_chat.py:635` to `{res.timeout_seconds:g}s` (or `.1f`). Trivial.

**PCHAT-3 (over-length request content) — fix now, reuse existing helpers.** The response side already uses `chunk_split` (`retry_feedback.py:219`, `CHUNK_SAFE_SIZE=1990`) and `classify_error`. The contract-preserving fix is to classify the 400 in `_start_new_thread` and return the documented recoverable `error:` string ("shorten content"); alternatively chunk-split the request projection (anchor the thread on the first chunk). Prefer the recoverable-error string — it matches the documented `error:` family the LLM already handles.

**PCHAT-1 + PCHAT-4 + XC-3 (concurrency/reentrancy/orphan) — the real work, in two stages.**

- **Stage 1 (immediate, low-risk):** raise `max_workers` on the tools `Worker` (or host `private_chat` on a dedicated higher-concurrency worker). Verified safe: the body is I/O-bound `await`, resources are node/worker-scoped (not mutable module globals), the calfkit dispatcher keys by correlation (concurrency-safe), and `_post_projection`/persona sends are independent. This removes fleet-wide serialization and makes A→B→A resolve immediately instead of after a 60s timeout. Document the chosen bound.

- **Stage 2 (medium-term, the model port):** adopt the **spawn → correlate → push-completion → state-machine** architecture that openclaw, the Claude Agent SDK, and the A2A protocol all share:
  - Replace the blocking `execute_node` with a **non-blocking spawn** (`emit_to_node`) that returns a task/correlation id and frees the slot immediately (openclaw `sessions_spawn`; Letta async variant) — *this is the root fix for PCHAT-1 and PCHAT-4, not just mitigation.*
  - Deliver the peer's reply via a **push completion consumer** keyed by that id, which posts the result to the Discord audit thread (A2A push notifications; Claude SDK `TaskNotificationMessage`; openclaw multi-tier announce with a **stable idempotency key**).
  - Model the exchange as a **state machine** with explicit terminal states including cancelled/failed (A2A `submitted/working/input-required/completed/canceled/failed`; Claude SDK `completed/failed/stopped`). `input-required` cleanly handles "B asks a clarifying question" without any deadlock.
  - Bound **reentrancy** with a spawn-depth cap and a `maxConcurrent` (openclaw `subagent-depth.ts` / `maxConcurrent`; OpenAI `max_turns`), and add **stale-run liveness + bounded orphan recovery** (openclaw `subagent-run-liveness.ts` / `subagent-recovery-state.ts`).
  - For true distributed cancellation (so an outer timeout reaches the callee — the proper PCHAT-4 fix), thread a **cancellation token caller→callee** (AutoGen `CancellationToken` propagated via `MessageContext`). calfkit currently has no way to carry a cancel signal across the Kafka hop — **file a calfkit feature request** for a control-channel/cancel-topic keyed by `correlation_id` (and consider a default `reply_ttl` to bound the pending map, per Spring's reaper).

**Net:** import nothing; **port openclaw's spawn/announce + depth/liveness/recovery**, **port AutoGen's CancellationToken** (and file the calfkit gap), and **adopt the A2A task-state-machine model** (with Claude-SDK-style message types) over calfkit/Kafka, projecting completion to the Discord thread as today. Ship the four small fixes (PCHAT-2/3/5 + Stage-1 `max_workers`) first; do the model port as a follow-up design.
