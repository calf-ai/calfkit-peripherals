# Shell tool — deep research report

**Scope:** evaluate replacements/fixes for calfcord's `shell` builtin tool
(`src/calfcord/tools/builtin/shell.py`, wrapping `openhands-tools==1.23.1`'s
`TerminalExecutor`) against the verified issue set SHELL-1…5, XC-1/XC-3, and
KNOWN-1/2/3 from `docs/reviews/builtin-tools-audit.md`.

**Method:** read OUR source + the wrapped upstream to confirm every root cause,
then read the ACTUAL shell/terminal source of every candidate framework (cloned
into `/tmp/shell-research`, or fetched raw). Realtime notes:
`docs/tools-research/logs/shell-log.md`.

---

## 1. Executive summary + top recommendation

calfcord's shell tool is a thin wrapper over OpenHands' `TerminalExecutor`. Its
problems are **not in the executor's command-running** (that part is solid:
PS1-sentinel exit codes, tmux pane pool, secret masking). They are in (a) **how
the observation is rendered to the model** — the timeout/truncation signal lives
in `metadata.suffix` which our flatten helper drops (SHELL-1/2/5, XC-6), (b) the
**timeout semantics** OpenHands chose (no-change and hard timeout are mutually
exclusive — SHELL-3/4), and (c) the **module-global lifecycle/isolation** wrapping
(XC-1, KNOWN-1/2/3) which is calfcord's wiring, not OpenHands'.

Critically, **I confirmed against OpenHands main (latest) that upgrading
`openhands-tools` does NOT fix SHELL-1 or SHELL-2** — the timeout handlers still
omit `is_error=True` and still hide the notice in `metadata.suffix`.

I surveyed 11 candidates. None is a drop-in Python dependency that solves
everything — the best shell implementations are in Rust (Codex) or TypeScript
(OpenCode/Gemini/Cline/openclaw), and the two Python ones (hermes-agent,
agent-zero) are app code, not packaged libraries. So the decision is between
**stay-and-fix the wrapper** vs **port a better design**.

**Top recommendation: STAY-AND-FIX now, PORT agent-zero's session+timeout design
later (Option C, phased).**

- **Phase 1 (small, immediate):** keep `openhands-tools` and fix the *wrapper*:
  render `obs.to_llm_content` (or stitch `metadata.prefix+text+suffix`) so the
  timeout/truncation notice reaches the model (SHELL-1/XC-6); detect timeout
  (`exit_code==-1` + suffix) and prefix `error:` (SHELL-2); omit the `exit code:`
  line when `None` (SHELL-5); offload the blocking call with `asyncio.to_thread`
  (XC-3/KNOWN-3); register `_executor.close()` on a calfkit `on_shutdown` hook +
  `atexit` (XC-1); document the timeout sharp edges (SHELL-3/4); force the
  subprocess (single-session) backend, not the pool, to fix cwd statefulness
  (KNOWN-1). This is ~a day and removes every 🟠 except multitenancy.
- **Phase 2 (if/when multitenancy + a better timeout model are needed):** replace
  the executor with a port of **agent-zero's `TTYSession`** (asyncio+PTY, fully
  non-blocking) behind calfcord's tool, adding its four-tier timeout
  (first-output / idle / dialog / hard-cap) and **per-agent numbered sessions**
  keyed on `ctx.agent_name` (KNOWN-2). agent-zero is MIT, Python, and its
  `tty_session.py` is self-contained enough to port.

Runner-up design references: **Codex's exec-server** (the gold standard if we
ever want a distributed, PTY-backed, lifecycle-managed process server — matches
calfcord's distributed topology, but it's Rust) and **hermes-agent's
ProcessRegistry** (Python, the closest architectural twin: session-keyed,
TTL/LRU-bounded, pluggable backends, file-based cwd persistence).

---

## 2. Verified recap of our shell tool's issues and true causes

All confirmed by reading `shell.py`, `_observation.py`, `workspace.py`, and the
upstream `openhands/tools/terminal/**`.

| ID | Confirmed root cause |
|----|----------------------|
| **SHELL-1** | `_observation.py:52-57` flattens only `obs.content[*].text` (= raw output). The timeout/truncation framing lives in `metadata.prefix`/`metadata.suffix`, assembled ONLY by the unused `definition.py:124-152 to_llm_content`. The `maybe_truncate` trailer (`MAX_CMD_OUTPUT_SIZE=30000`) is also applied *inside* `to_llm_content`, so our path even returns un-truncated output and loses the truncation notice. |
| **SHELL-2** | `terminal_session.py` `_handle_nochange_timeout_command` (:324) and `_handle_hard_timeout_command` (:363) build `CmdOutputMetadata()` (`metadata.py:23` default `exit_code=-1`) and call `from_text(..., exit_code=-1)` **without** `is_error=True`. The observation's separate `timeout: bool` field (`definition.py:107`) is also left `False`. So `shell.py:132` emits `"<output>\n\nexit code: -1"` — no `error:`, no notice. **Still true on OpenHands main.** |
| **SHELL-3** | Poll loop (`terminal_session.py:580-628`): no-change timeout fires only on *unchanged* output; with `timeout=None` the hard branch (`:615 if action.timeout is not None`) is skipped — a steadily-emitting command (`tail -f`) loops forever. Blocking + `max_workers=1` wedges the worker. |
| **SHELL-4** | `terminal_session.py:591 is_blocking = action.timeout is not None`; no-change gated on `not is_blocking` (:597). Passing any numeric `timeout` DISABLES the ~30s silence bail. `shell(cmd, timeout=600)` on a silent hang blocks the full 600s. |
| **SHELL-5** | Some `is_error=True` upstream paths leave `exit_code=None`; `shell.py:132` unconditionally appends `exit code: None`. |
| **XC-1** | `shell._executor` module-global; `TerminalExecutor.close()`/`TmuxPanePool.close()` exist but nothing calls them. Pool uses fixed socket `TMUX_SOCKET_NAME="openhands"` (`constants.py:37`) → every restart leaks a tmux server+panes+bash. No `atexit`/`__del__`. |
| **XC-3 / KNOWN-3** | `obs = _get_executor()(action)` (`shell.py:124`) is a sync poll loop (`time.sleep`) called inside `async def shell`, not offloaded. `max_workers=1` → one blocking call freezes all tools for all agents. |
| **KNOWN-1** | Pool `checkout=popleft()` / `checkin=append()` (`tmux_pane_pool.py:228,245`) → FIFO round-robin across ≤4 panes → cwd/env set in one call invisible to the next. The subprocess single-session backend is actually the reliably-stateful one. |
| **KNOWN-2** | One module-global executor + one shared workspace for all agents; `ctx.agent_name` ignored (`shell.py:113 _ = ctx`). |

---

## 3. Per-candidate deep review

### 3.1 OpenHands / `openhands-tools` (incumbent baseline)
- **Repo/paths:** `OpenHands/software-agent-sdk` → `openhands-tools/openhands/tools/terminal/**` (we wrap `==1.23.1`; OpenHands main pins `==1.25.0`). Language: Python. License: MIT.
- **How it works:** auto-selects a **tmux pane pool** (if tmux present, the shipped Docker path) or a single **subprocess** session; persistent; parses exit codes via PS1 sentinels; no-change + hard timeouts; secret masking; `to_llm_content` builds `prefix+text+suffix` and is the *only* code that renders the timeout notice. `TerminalTool.declared_resources` opts the pooled backend out of serialization (relies on pane isolation, not per-agent).
- **Issue-by-issue:** shares **all** of SHELL-1…5, XC-1, XC-3, KNOWN-1/2/3 — because these are *our wrapper's* failure to use `to_llm_content`, our lifecycle wiring, and OpenHands' deliberate timeout-mutual-exclusion. **Verified: latest OpenHands does not set `is_error` on timeout nor move the notice out of `metadata.suffix`,** so a version bump fixes nothing here.
- **Quality:** the command-runner itself is high quality (tmux recovery, env-name validation, secret masking, pooled concurrency). The model-facing rendering split (`content` vs `metadata`) is the trap.
- **Integration:** already a dependency. **Stay-and-fix is the cheapest path** — every SHELL-* fix is in our 2 wrapper files; XC-1/XC-3 are runner wiring; KNOWN-1 is "force `subprocess` backend".

### 3.2 OpenAI Agents SDK — `openai/openai-agents-python`
- **Repo/paths:** `src/agents/tool.py:892-920` (`LocalShellTool`, deprecated), `:1103-1148` (`ShellTool`, next-gen); reference executor `examples/tools/shell.py`. Python, MIT.
- **How it works:** the SDK is **protocol + approval-flow only** — it does NOT execute anything. The developer supplies an `executor: ShellExecutor` callable. `ShellTool` adds per-call approval (`needs_approval: bool|fn`, `on_approval`) and a hosted-vs-local environment split. The reference executor uses `asyncio.create_subprocess_shell` + `asyncio.wait_for(timeout)`; on `TimeoutError` → `proc.kill()` + structured `ShellCallOutcome(type="timeout", exit_code)`.
- **Issue-by-issue:** by construction **solves** SHELL-1 (structured `stdout`/`stderr`/`outcome`), SHELL-2 (explicit `type="timeout"`), SHELL-3 (`wait_for` hard cap), SHELL-5; **solves** XC-3/KNOWN-3 (fully async). No persistent session → XC-1/KNOWN-1 n/a. SHELL-4 n/a (one timeout, always enforced). Multitenancy is the dev's job.
- **Quality:** clean, minimal, idiomatic. But it's a *pattern*, not an implementation — you still write the executor.
- **Integration:** the **executor-callable + approval-hook design** is worth adopting regardless of which engine we pick. Not a shell implementation to import.

### 3.3 OpenCode — `sst/opencode`
- **Repo/paths:** `packages/core/src/tool/bash.ts` (V2, 206 lines), `packages/opencode/src/tool/shell.ts` (legacy, 660) + `src/shell/shell.ts`. TypeScript, MIT.
- **How it works:**
  - **V2 `bash.ts`:** one-shot. `ChildProcess.make` + `appProcess.run({timeout, maxOutputBytes/maxErrorBytes=1MB})`. `DEFAULT_TIMEOUT_MS=120000`, `MAX_TIMEOUT_MS=600000` (hard cap), `MAX_CAPTURE_BYTES=1MB`. `detached` + `forceKillAfter:3s`. `toModelOutput` emits `timedOut → "Command timed out before completion."` and `stdoutTruncated/stderrTruncated → "[stdout capture truncated…]"`, with the exit-code line always present. Statefulness via `workdir` param (V2 dropped persistent sessions; TODOs defer background jobs).
  - **Legacy `shell.ts`:** fresh login-shell per call (`bash -l -c 'source ~/.bashrc; cd -- "$1"; eval CMD'`) → reliable cwd via the arg, no round-robin. Races exit/abort/timeout; **process-group kill** (`killTree`: `process.kill(-pid)` SIGTERM→SIGKILL) — fixes the grandchild/streaming hang. Ring-buffer truncation keeping the tail + **spills full output to a file** (`"…output truncated… Full output saved to: <file>"`, `outputPath`); timeout surfaced via a `<shell_metadata>` block in the model output. tree-sitter bash parsing for command-prefix approvals.
- **Issue-by-issue:** **solves** SHELL-1/2/3/4/5, XC-3. V2 has no persistent session (XC-1/KNOWN-1 n/a, per-call statefulness); legacy `killTree` + per-call spawn solve KNOWN-1; XC-1 partial (per-call processes, no leaked server). Multitenancy per-call/per-session.
- **Quality:** excellent, modern (Effect-based), well-commented; the file-spill truncation and `<shell_metadata>` notice are exactly the SHELL-1 fix.
- **Integration:** TypeScript → design-port only. `killTree`, the file-spill truncation, and the timeout-metadata-in-output pattern are great port references.

### 3.4 Codex CLI — `openai/codex`
- **Repo/paths:** one-shot `codex-rs/core/src/exec.rs` (1570); persistent `codex-rs/exec-server/{process.rs,local_process.rs}` (≈1220); sandbox `linux-sandbox/src/landlock.rs`, `bwrap/`, seatbelt, `execpolicy/`. Rust, Apache-2.0.
- **How it works:**
  - **One-shot (`exec.rs`):** `consume_output` (:1348) `tokio::select!` over `child.wait` / expiration / ctrl-c. Timeout → `kill_child_process_group()` + `start_kill()` (whole group) → `exit_code=124` + `timed_out=true`, returned as typed `CodexErr::Sandbox(SandboxErr::Timeout{output})` — never silent. `await_output` drains stdout/stderr with a 2s `io_drain_timeout` and **aborts** hanging read tasks if pipes stay open after kill (the grandchild-FD hang our SHELL-3 has). Output capped at `EXEC_OUTPUT_MAX_BYTES` during read (no OOM). `is_likely_sandbox_denied` heuristic.
  - **Persistent ("unified exec", `exec-server`):** `ExecProcess` trait (start/read/write/terminate) + `ExecBackend` (local-vs-remote/PTY, pluggable). Process registry by `ProcessId`; explicit `terminate_process` + `shutdown`; **bounded replay buffer** with per-process byte eviction (`RETAINED_OUTPUT_BYTES_PER_PROCESS`) + event-count cap; `read()` can wait-with-timeout via `output_notify`; explicit `Exited{exit_code}`/`Closed`/`Failed` events.
- **Issue-by-issue:** **solves** SHELL-1…5, XC-1 (explicit lifecycle), XC-3 (async + group-kill + drain-timeout). Persistent model solves KNOWN-1 (per-`ProcessId` sessions) and KNOWN-2-ish (process-scoped). **Best-in-class for XC-2** (real OS sandboxing: landlock/seccomp/seatbelt/bubblewrap/restricted-token + execpolicy).
- **Quality:** outstanding; the reference implementation for robust exec.
- **Integration:** Rust → not a Python dependency. Either **port the design** (group-kill, select-over-timeout, drain-timeout, bounded-replay persistent session, pluggable `ExecBackend`) or run `codex exec-server` as a **sidecar subprocess** behind a Python adapter. The pluggable-backend shape maps perfectly onto calfcord's distributed topology.

### 3.5 Claude Code Bash tool — `anthropics/claude-agent-sdk-python` + docs
- **Repo/paths:** the Python SDK is a thin client; `_internal/transport/subprocess_cli.py` spawns the closed-source `claude` CLI. The Bash tool runs inside the CLI — no readable source. Design from `platform.claude.com/docs/.../bash-tool`. MIT (SDK).
- **How it works (design):** persistent bash session maintained **client-side** (the API is stateless; your app owns the `Popen` session); `restart` rebuilds it; cwd/env persist. **Timeout → `is_error: true` + "Error: Command timed out after N seconds"** (timeouts ARE errors — the documented contract is the exact opposite of our SHELL-2). Truncation → "… Output truncated (N total lines) …". No streaming, no background in the base tool (the CLI adds `run_in_background`/`BashOutput`/`KillShell` on top). Safety guidance: allowlist (not blocklist), reject shell operators, `shell=False` + `shlex.split`, `ulimit`, Docker/VM, command logging, output sanitization.
- **Issue-by-issue (design):** prescribes solving SHELL-1/2/3/4/5; lifecycle/isolation are "the app's responsibility" (client-side session ⇒ you own XC-1/KNOWN-1/2).
- **Integration:** design reference only. Its **"timeouts are errors with an explicit message"** contract directly validates the SHELL-1/2 fix; its allowlist guidance informs XC-2.

### 3.6 Cline — `cline/cline`
- **Repo/paths:** `apps/vscode/src/integrations/terminal/**`; non-VSCode fallback `…/standalone/StandaloneTerminalProcess.ts`. TypeScript, Apache-2.0.
- **How it works:** primary path uses VS Code's Terminal + shell-integration API (persistent VS Code terminal). Standalone fallback: `child_process` + `terminateProcessTree`. A **"hot" model** (`isHot`/`hotTimer`, `PROCESS_HOT_TIMEOUT_NORMAL=2s`, `PROCESS_HOT_TIMEOUT_COMPILING=15s`) detects active output to decide "done outputting". **Incremental retrieval** (`lastRetrievedIndex`, `MAX_UNRETRIEVED_LINES=500`) streams output in chunks; `MAX_FULL_OUTPUT_SIZE=1MB` ring buffer keeps the tail; a `continue` event lets a long command keep running in the background while the agent proceeds. Explicit `exitCode`/`signal`/`completed`.
- **Issue-by-issue:** **solves** SHELL-1/2/5; SHELL-3/4 **partial** (hot-detection + abort, no fixed hard timeout in standalone); XC-1/KNOWN-1/2 tied to the (persistent) VS Code session or the standalone process. The "hot"/incremental-retrieval idea is novel and relevant for long-running output.
- **Quality:** good, but the best path is deeply VS Code-coupled.
- **Integration:** most coupled/least portable. Design-port only; the "hot + incremental retrieval" pattern is the takeaway.

### 3.7 Gemini CLI — `google-gemini/gemini-cli`
- **Repo/paths:** `packages/core/src/services/shellExecutionService.ts` (1624) + `executionLifecycleService.ts`. TypeScript, Apache-2.0.
- **How it works:** **node-pty** (`@lydell/node-pty`) primary + `child_process` fallback; `@xterm/headless` to serialize PTY output (proper ANSI). `AbortSignal` cancel → `killProcessGroup`. `isBinary` detection (`binary_detected`). Environment sanitization; pluggable `SandboxManager`. Output: `SCROLLBACK_LIMIT=300000` lines, `MAX_CHILD_PROCESS_BUFFER_SIZE=16MB`, `appendAndTruncate` ring buffer keeping the tail; on truncation appends `"[GEMINI_CLI_WARNING: Output truncated. The buffer is limited to NMB.]"` into the model output. Explicit `exitCode`/`signal`/`aborted` in `ExecutionResult`. `ExecutionLifecycleService` is a registry keyed by `executionId`/pid with explicit exit info + cleanup; `backgroundProcessHistory` keyed by `sessionId+pid` with `status`/`exitCode`/`endTime`.
- **Issue-by-issue:** **solves** SHELL-1 (warning-in-output), SHELL-2 (explicit `aborted`/`exitCode`), SHELL-3/4 (AbortSignal + group-kill), SHELL-5, XC-1 (lifecycle registry + cleanup), XC-3 (async pty), KNOWN-1/2 (session-keyed history).
- **Quality:** very complete and battle-tested; the xterm-headless ANSI serialization is the most correct output handling of any candidate.
- **Integration:** TS + node-pty + xterm native deps → not a Python library. Design-port only; the lifecycle-registry + truncation-warning-in-output patterns are strong references.

### 3.8 openclaw — `openclaw/openclaw`
- **Repo/paths:** `src/agents/bash-tools.exec.ts` (1991) + `bash-tools.process.ts` (748) + `src/infra/exec-*` (approvals, safe-bin-policy, allowlist-pattern, auto-review, host-gateway). TypeScript, MIT (header text MIT; GitHub mislabels "Other").
- **How it works:** routes host/sandbox/node execution targets; full policy + per-command approval pipeline; PTY support; `process-send-keys` to interact with a running process. Foreground result carries `timedOut` + `exitCode` + `aggregated` + `durationMs` (model sees timeout). `truncateMiddle` truncation, `DEFAULT_MAX_OUTPUT`. **"Yield to background"**: `yieldMs`/`OPENCLAW_BASH_YIELD_MS` auto-promotes a long foreground command to a tracked **background session** (with `notifyOnExit` completion) instead of blocking — an elegant SHELL-3/4 answer. Strong security layer: `exec-safe-bin-policy` (binary allowlist), `exec-allowlist-pattern`, `exec-approvals`, `exec-auto-review`.
- **Issue-by-issue:** **solves** SHELL-1/2/3/4/5, XC-1 (tracked sessions), XC-3 (async + background handoff); KNOWN-1/2 via per-session model. The security/approval infra is the most relevant to XC-2 among the TS candidates.
- **Quality:** very mature and large; heavily decomposed.
- **Integration:** TypeScript → design-port only. The "auto-yield-to-background + notify-on-exit" and binary-allowlist policy are the standout patterns.

### 3.9 hermes-agent — `NousResearch/hermes-agent`  *(Python, most architecturally aligned)*
- **Repo/paths:** `tools/process_registry.py`, `tools/terminal_tool.py` (2611), `tools/environments/{base,local,docker,ssh,modal,daytona,…}.py`. Python, MIT.
- **How it works:** `BaseEnvironment` ABC with **pluggable backends** (local/docker/ssh/modal/daytona/singularity) — the same shape as calfcord's distributed deploy. `_wait_for_process` (`base.py:655`): adaptive poll (5ms→200ms) checks interrupt + hard deadline; timeout → process-group `_kill_process` (SIGTERM→SIGKILL via psutil tree) + `drain_thread.join` → returns partial output + `"[Command timed out after Ns]"` + `returncode 124`; interrupt → `"[Command interrupted]"` + `130`. A **separate `drain_thread`** reads stdout non-blocking (no grandchild-FD hang). **Foreground returns instantly on exit** even with a high timeout (no silence-bail needed). `ProcessRegistry`: thread-safe; `MAX_OUTPUT_CHARS=200KB` rolling buffer; `FINISHED_TTL_SECONDS=1800` + `MAX_PROCESSES=64` **LRU pruning**; `task_id`/`session_key` **isolation**; crash-recovery checkpoint (`processes.json`); `watch_patterns` + rate-limited notify; PTY support. **cwd persists across one-shot calls** via a `_cwd_file` written/read with a `pwd -P` marker (no round-robin pane problem). `terminal_tool` returns structured JSON (`output`/`exit_code`/`error`), timeout → `exit_code 124` + explicit error string.
- **Issue-by-issue:** **solves** SHELL-1/2/3/5; SHELL-4 n/a by design (one always-enforced timeout + instant-exit); **XC-1** (TTL/LRU bounded + explicit lifecycle); **XC-3** (drain thread, non-blocking); **KNOWN-1** (file-based cwd persistence); **KNOWN-2** (`task_id`/`session_key` isolation).
- **Quality:** very thorough, production-grade (crash recovery, watchers, Windows handling, psutil tree-kill).
- **Integration:** Python/MIT but **app code, not a packaged lib** (imports `hermes_cli.*`, prompts). **Port the design/modules** (ProcessRegistry, `_wait_for_process`, file-cwd persistence, pluggable `BaseEnvironment`) — it's the closest architectural twin to what calfcord wants for a distributed, multi-tenant shell.

### 3.10 agent-zero — `frdel/agent-zero`  *(Python, best timeout model)*
- **Repo/paths:** `plugins/_code_execution/helpers/{tty_session.py (422), shell_local.py, shell_ssh.py}`, `tools/code_execution_tool.py` (558). Python, MIT.
- **How it works:** pluggable `LocalInteractiveSession | SSHInteractiveSession` (distributed-relevant); **numbered concurrent persistent sessions** that survive across calls, with explicit per-session or all-session `reset` (true cwd/env statefulness). `TTYSession`: `pty.openpty` + `asyncio.create_subprocess_shell` + `add_reader` pump → **fully non-blocking async I/O**; `close()/kill()/terminate()` + a `__del__` net. **Best timeout model of all candidates** — `get_terminal_output` runs FOUR coexisting timers: `first_output_timeout(30)` + `between_output_timeout(15, idle/no-change)` + `dialog_timeout(5, interactive-prompt)` + `max_exec_timeout(240, hard cap)`. Unlike OpenHands these are not mutually exclusive, so SHELL-3 and SHELL-4 are both solved. Prompt-pattern early-return (returns the instant the shell prompt reappears → fast). On every timeout/pause/max-time it returns `truncated_output + an explicit framework message` (`fw.code.max_time`/`pause_time`/`no_out_time`) telling the agent the command is still running and how to continue via the `output` action + session id — the SHELL-1 fix done right. Truncation via `truncate_text_agent` (~1MB).
- **Issue-by-issue:** **solves** SHELL-1 (always surfaces state + how-to-continue), SHELL-2 (explicit framework message), **SHELL-3 AND SHELL-4** (coexisting idle+hard timeouts), SHELL-5; **XC-3/KNOWN-3** (asyncio+PTY non-blocking); **KNOWN-1** (real persistent numbered sessions); **KNOWN-2** (numbered sessions → key on `ctx.agent_name`); XC-1 partial (`close`/`__del__`, needs explicit shutdown wiring).
- **Quality:** clean, focused; `tty_session.py` is fairly self-contained.
- **Integration:** Python/MIT, plugin-structured (imports agent-zero helpers/prompts). **Port `TTYSession` + the four-tier timeout** behind calfcord's tool. The most directly portable *engine* for the SHELL-* set, especially the timeout model.

---

## 4. Comparison matrix

`✓` = solves · `~` = partial · `×` = shares/unsolved · `–` = n/a by design.
Themes: **Async** = non-blocking exec · **Persist** = reliable cwd/env across calls
· **Multi** = per-agent isolation · **Sandbox** = OS-level containment · **Py?** =
Python-importable.

| Candidate | Lang/License | SHELL-1 | SHELL-2 | SHELL-3 | SHELL-4 | SHELL-5 | XC-1 | XC-3 | KNOWN-1 | KNOWN-2 | Async | Persist | Multi | Sandbox | Py? |
|---|---|:--:|:--:|:--:|:--:|:--:|:--:|:--:|:--:|:--:|:--:|:--:|:--:|:--:|:--:|
| **OpenHands (incumbent)** | Py / MIT | × | × | × | × | × | × | × | × | × | × | ~ | × | × | **yes (dep)** |
| OpenAI Agents (pattern) | Py / MIT | ✓ | ✓ | ✓ | – | ✓ | – | ✓ | – | dev | ✓ | × | dev | dev | pattern |
| OpenCode V2 bash | TS / MIT | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | – | per-call | ✓ | ~ | per-call | ~ | no |
| OpenCode legacy shell | TS / MIT | ✓ | ✓ | ✓ | ✓ | ✓ | ~ | ✓ | ✓ | session | ✓ | ✓ | session | ~ | no |
| Codex one-shot | Rust / Apache-2 | ✓ | ✓ | ✓ | ✓ | ✓ | – | ✓ | – | per-call | ✓ | × | per-call | **✓✓** | no |
| Codex exec-server | Rust / Apache-2 | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ProcessId | ✓ | ✓ | ✓ | **✓✓** | no |
| Claude Code (design) | closed / – | ✓ | ✓ | ✓ | ✓ | ✓ | client | client | client | client | n/a | ✓ | client | guide | no |
| Cline standalone | TS / Apache-2 | ✓ | ✓ | ~ | ~ | ✓ | ✓ | ✓ | session | session | ✓ | ✓ | session | × | no |
| Gemini CLI | TS / Apache-2 | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | sessionId | sessionId | ✓ | ✓ | sessionId | ~ | no |
| openclaw | TS / MIT | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | session | session | ✓ | ✓ | session | **✓** | no |
| **hermes-agent** | **Py / MIT** | ✓ | ✓ | ✓ | – | ✓ | ✓ | ✓ | ✓ | **✓ (task_id)** | ✓ | ✓ | ✓ | ~ | port |
| **agent-zero** | **Py / MIT** | ✓ | ✓ | ✓ | ✓ | ✓ | ~ | ✓ | ✓ | numbered | ✓ | ✓ | numbered | × | port |

(OpenHands incumbent shares everything because the failures are in *our wrapper +
OpenHands' timeout-mutual-exclusion*, not the command runner — and they are all
fixable in our two wrapper files.)

---

## 5. Ranking (with rationale)

1. **Stay-and-fix the OpenHands wrapper** — lowest cost, highest leverage. Every
   SHELL-* is in `shell.py`/`_observation.py`; XC-1/XC-3 are runner wiring;
   KNOWN-1 is a backend choice. Keeps the solid tmux/PS1 command-runner.
2. **agent-zero (port)** — best Python *engine* to graduate to: asyncio+PTY
   (non-blocking), the only candidate that solves SHELL-3 **and** SHELL-4 together,
   real persistent numbered sessions (KNOWN-1/2), MIT, self-contained `tty_session.py`.
3. **hermes-agent (port)** — closest *architecture*: pluggable backends
   (= calfcord distributed topology), session-keyed + TTL/LRU-bounded registry,
   file-based cwd persistence, crash recovery, MIT.
4. **Codex exec-server (design ref / sidecar)** — gold standard for a distributed,
   PTY-backed, lifecycle-managed, sandboxed process server; Rust ⇒ port or sidecar.
5. **OpenCode** (file-spill truncation + `killTree` + metadata-in-output) and
   **Gemini CLI** (xterm-headless ANSI + lifecycle registry + truncation-warning)
   — best TS design references.
6. **openclaw** (auto-yield-to-background + binary-allowlist policy) and
   **Claude Code design** (timeouts-are-errors contract; allowlist guidance) —
   good supporting references.
7. **OpenAI Agents SDK** — adopt the executor-callable + approval-hook *shape*,
   not an implementation.
8. **Cline** — most coupled (VS Code); only the "hot + incremental retrieval"
   idea is portable.

---

## 6. Final recommendation

**Adopt Option C: stay-and-fix now (Phase 1), then port agent-zero's engine if
multitenancy/long-running output become first-class needs (Phase 2).**

**Why not "just upgrade openhands-tools":** verified — even latest OpenHands omits
`is_error` on timeout and keeps the notice in `metadata.suffix`. A bump fixes
nothing in SHELL-1/2/3/4.

**Why not adopt a non-Python candidate as a dependency:** the best
implementations (Codex/OpenCode/Gemini/Cline/openclaw) are Rust/TS — not
importable into our Python + Kafka worker. They are design references, and Codex
exec-server is the only one worth considering as a *sidecar* (heavy; revisit only
if we want OS-level sandboxing, XC-2).

**Why not port hermes/agent-zero wholesale today:** both are app code, not pip
packages; a full port is Phase-2 scope. agent-zero's `tty_session.py` is the
cleanest *engine* to port (asyncio+PTY, four-tier timeout, numbered sessions);
hermes' `ProcessRegistry` + pluggable `BaseEnvironment` is the *architecture* to
mirror when we make the shell distributed and multi-tenant.

**Phase 1 fix list (keep `openhands-tools`):**
- SHELL-1/XC-6: render `obs.to_llm_content` (or stitch `metadata.prefix+text+suffix`);
  reconcile its `ERROR_MESSAGE_HEADER` with our `error:` convention.
- SHELL-2: detect timeout (`obs.exit_code == -1` + non-empty `metadata.suffix`),
  prefix `error:`; add a test driving a *real* timed-out `TerminalObservation`.
- SHELL-5: omit the `exit code:` line when `obs.exit_code is None`.
- SHELL-3/4: document the sharp edges; consider a wrapper default hard ceiling and
  steer long/streaming commands to background.
- XC-3/KNOWN-3: `await asyncio.to_thread(_get_executor(), action)`.
- XC-1: register `_executor.close()` via a calfkit `on_shutdown` hook (and `atexit`).
- KNOWN-1: force the **subprocess** single-session backend (the reliably-stateful
  one) instead of the round-robin pool; fix the inverted docstring.

**Phase 2 (when needed):** replace the executor with a port of agent-zero's
`TTYSession` + four-tier timeout, keyed per `ctx.agent_name` for isolation
(KNOWN-2); mirror hermes' TTL/LRU bound (XC-1) and pluggable backend
(distributed). If OS-level confinement (XC-2) becomes a requirement, evaluate a
Codex exec-server sidecar.
