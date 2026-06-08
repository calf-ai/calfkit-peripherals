# Shell tool research — realtime log

## Step 1 — VERIFICATION of our issues (read OUR source + upstream openhands)

Files read:
- `src/calfcord/tools/builtin/shell.py` (wrapper)
- `src/calfcord/tools/builtin/_observation.py` (flatten helper)
- `src/calfcord/tools/builtin/workspace.py` (shared workspace root)
- upstream `openhands/tools/terminal/impl.py` (TerminalExecutor)
- upstream `openhands/tools/terminal/definition.py` (TerminalAction/Observation, `to_llm_content`)
- upstream `openhands/tools/terminal/terminal/terminal_session.py` (poll loop, timeout handlers)
- upstream `openhands/tools/terminal/terminal/tmux_pane_pool.py` (pool checkout/checkin)
- upstream `openhands/tools/terminal/metadata.py` (CmdOutputMetadata, default exit_code=-1)

### SHELL-1 — CONFIRMED. Root cause is two-layered.
- `_observation.py:52-57` walks `obs.content[*].text` ONLY. For a `TerminalObservation`,
  `content` is a single TextContent = raw command output. The timeout/truncation framing
  lives in `metadata.prefix`/`metadata.suffix` (definition.py:135) and is ONLY assembled by
  the unused `to_llm_content` property (definition.py:124-152). So:
  - nochange-timeout suffix `"[The command has no new output after 30 seconds...]"`
    (terminal_session.py:311-314) is dropped.
  - hard-timeout suffix `"[The command timed out after N seconds...]"`
    (terminal_session.py:350-353) is dropped.
  - truncation trailer from `maybe_truncate` (MAX_CMD_OUTPUT_SIZE=30000, constants.py:18)
    is applied INSIDE `to_llm_content` (definition.py:144), which we never call — so OUR
    flatten path returns un-truncated `obs.text` and also loses the truncation notice.
  Net: the agent literally never sees that anything timed out or was truncated.

### SHELL-2 — CONFIRMED.
- Both `_handle_nochange_timeout_command` (terminal_session.py:324-329) and
  `_handle_hard_timeout_command` (terminal_session.py:363-368) build
  `metadata = CmdOutputMetadata()` -> default `exit_code=-1` (metadata.py:23) and call
  `TerminalObservation.from_text(..., exit_code=metadata.exit_code)` with NO `is_error=True`.
  So `obs.is_error == False`, `obs.exit_code == -1`. Our wrapper (shell.py:128-132) emits
  `"<output>\n\nexit code: -1"` with no `error:` prefix. The TerminalObservation DOES have a
  separate `timeout: bool` field (definition.py:107) but the timeout handlers DON'T set it
  either — verified it stays default False. So even checking `obs.timeout` wouldn't help.

### SHELL-3 — CONFIRMED.
- Poll loop (terminal_session.py ~580-628): nochange-timeout only fires when
  `not is_blocking AND time_since_last_change >= no_change_timeout_seconds`. A steadily-
  emitting command (`tail -f`) resets last_change_time forever, so nochange never fires; with
  `timeout=None` the hard-timeout branch (line 615 `if action.timeout is not None`) is skipped
  entirely. Loop runs forever. Blocking + max_workers=1 => wedges the whole tools worker.

### SHELL-4 — CONFIRMED.
- terminal_session.py:591 `is_blocking = action.timeout is not None`; nochange-timeout gated on
  `not is_blocking` (line 597). So passing ANY numeric timeout DISABLES the ~30s silence bail.
  `shell(cmd, timeout=600)` on a silent hang blocks the full 600s instead of bailing at 30s.

### SHELL-5 — CONFIRMED.
- Some upstream error paths set `is_error=True` but leave `exit_code=None`. Our wrapper
  unconditionally appends `f"exit code: {obs.exit_code}"` -> renders `exit code: None`.

### XC-1 — CONFIRMED.
- `shell._executor` is a module-global lazily built once (shell.py:43,74-79). `TerminalExecutor.close()`
  AND `TmuxPanePool.close()` exist (impl.py:562-569, pool tmux_pane_pool.py) but NOTHING calls them.
  Pool uses a FIXED socket name `TMUX_SOCKET_NAME="openhands"` (constants.py:37), so every restart
  that served >=1 shell call leaks a tmux server + panes + bash on a shared socket. No atexit/__del__ net
  on the executor. (PooledTmuxTerminal.close only kills its own window; the pool/server persists.)

### XC-3 / KNOWN-3 — CONFIRMED.
- `obs = _get_executor()(action)` (shell.py:124) is a SYNC call (poll loop uses time.sleep) inside
  `async def shell`, NOT offloaded via asyncio.to_thread. Worker max_workers=1 => one blocking shell
  call freezes the entire tools event loop for every agent.

### KNOWN-1 — CONFIRMED.
- Pool `checkout` = `self._available.popleft()` (tmux_pane_pool.py:228); `checkin` =
  `self._available.append(terminal)` (line 245). FIFO deque => consecutive calls round-robin across
  up to DEFAULT_MAX_PANES=4 panes. cd/env set in pane A is invisible from pane B. The subprocess
  single-session backend is actually the reliably-stateful one (docstring is inverted).

### KNOWN-2 — CONFIRMED.
- One module-global executor + one shared working_dir = get_workspace_root() for ALL agents.
  `ctx.agent_name` is ignored (shell.py:113 `_ = ctx`). No per-agent isolation.

---

## Step 2 — Candidate framework survey (in progress)

### Candidate: OpenHands (incumbent) — All-Hands-AI/OpenHands + openhands-tools
- We wrap openhands-tools==1.23.1. OpenHands MAIN now pins openhands-tools==1.25.0
  (pyproject.toml:62,251). The terminal tool is NOT in the main repo — it lives entirely
  in the separate `openhands-tools` PyPI package (the exact code we already read).
  => The "newer OpenHands impl" is just a version bump of the same package. Need to check
     1.24/1.25 changelog for SHELL-1/2 fixes. License: MIT.
- Architecture recap (already in Step 1): tmux pane pool OR single subprocess session;
  persistent; PS1-sentinel exit-code parsing; no-change + hard timeout; `to_llm_content`
  builds prefix+suffix but is unused by us. is_pooled => DeclaredResources(()) to allow
  parallelism (relies on pool-level pane isolation, not per-agent).

### Candidate: OpenAI Agents SDK (openai/openai-agents-python) — Python, MIT
- src/agents/tool.py:892-920 LocalShellTool (deprecated) and :1103-1148 ShellTool (next-gen).
  BOTH are protocol/schema + approval-flow ONLY. The SDK does NOT execute anything — the dev
  supplies `executor: ShellExecutor` callable. So no reusable shell impl, but a clean DESIGN:
  executor abstraction, per-call approval hooks (needs_approval bool|fn, on_approval),
  hosted-vs-local environment split, structured ShellResult.
- Reference executor: examples/tools/shell.py:25-74 — `asyncio.create_subprocess_shell` +
  `asyncio.wait_for(timeout)`; on TimeoutError -> proc.kill() + structured
  ShellCallOutcome(type="timeout", exit_code). FULLY ASYNC, one-shot (non-persistent),
  timeout surfaced as structured field. Solves SHELL-2/3/4/XC-3/KNOWN-3 by construction;
  shares SHELL-1-ish only if you flatten badly (it gives structured output). No persistent
  session => no XC-1/KNOWN-1. Statefulness: none (cwd fixed on executor).

### Candidate: OpenCode (sst/opencode) — TypeScript, MIT
- TWO impls:
  (A) V2 core: packages/core/src/tool/bash.ts (206 lines). One-shot. ChildProcess.make +
      appProcess.run({timeout, maxOutputBytes/maxErrorBytes=1MB}). DEFAULT_TIMEOUT_MS=120000,
      MAX_TIMEOUT_MS=600000 (HARD CAP), MAX_CAPTURE_BYTES=1MB. timedOut bool + explicit
      "Command timed out before completion." in toModelOutput; stdoutTruncated/stderrTruncated
      flags + "[stdout capture truncated...]" notice IN model output. exit code line always
      present. detached + forceKillAfter 3s. Permission/approval per command. Statefulness via
      workdir param (no persistent session in V2 — TODOs note background jobs deferred).
  (B) Legacy persistent-ish: packages/opencode/src/tool/shell.ts (660 lines) + shell/shell.ts.
      Fresh login-shell spawn per call (`bash -l -c 'source ~/.bashrc; cd -- "$1"; eval CMD'`),
      cwd passed as arg => reliable cwd statefulness without round-robin panes. Races
      exit/abort/timeout (lines 555-568); kill via process-group killTree (process.kill(-pid),
      SIGTERM then SIGKILL after 200ms) — solves the grandchild-FD streaming-hang (our SHELL-3).
      Truncation: ring buffer keeps last 2*maxBytes, spills FULL output to a file, surfaces
      "...output truncated... Full output saved to: <file>" + outputPath; timeout surfaced via
      <shell_metadata> block IN model output (lines 575-597). tree-sitter bash parsing for
      command-prefix permission analysis (BashArity). SOLVES SHELL-1/2/3/4 cleanly.
- shell/shell.ts: rich shell-binary resolution (login/posix detection, /etc/shells, gitbash),
  killTree process-group cleanup. Good port reference.

### Candidate: Codex CLI (openai/codex) — Rust, Apache-2.0
Two execution models:
- ONE-SHOT engine: codex-rs/core/src/exec.rs (1570 lines).
  - DEFAULT_EXEC_COMMAND_TIMEOUT_MS=10000; timeout configurable per call (ExecExpiration).
  - consume_output (1348): tokio::select! over child.wait / expiration / ctrl_c.
    On timeout -> kill_child_process_group() + child.start_kill() => kills WHOLE process group
    (solves grandchild/streaming-hang = SHELL-3). exit_code set to EXEC_TIMEOUT_EXIT_CODE=124,
    timed_out=true (finalize_exec_result:771-790) and returned as TYPED CodexErr::Sandbox(
    SandboxErr::Timeout{output}) — NEVER silent (solves SHELL-2 + SHELL-1's "agent can't tell").
  - IO drain: await_output uses io_drain_timeout=2s and ABORTS the read tasks if pipes stay open
    after kill (lines 1451-1469, comment 74-81) — prevents the open-FD hang our SHELL-3 has.
  - Output cap: EXEC_OUTPUT_MAX_BYTES (DEFAULT_OUTPUT_BYTES_CAP), append_capped during read so a
    runaway command can't OOM (solves the spirit of XC-3/OOM); aggregate_output rebalances
    stdout/stderr under the cap. Fully async (tokio).
  - is_likely_sandbox_denied heuristic distinguishes sandbox-denial from normal failure.
- PERSISTENT model ("unified exec"): codex-rs/exec-server/ (process.rs + local_process.rs, 980 lines).
  - ExecProcess trait (start/read/write/terminate) + ExecBackend (local vs remote/PTY) — pluggable,
    relevant to OUR distributed topology. PTY-backed (codex_utils_pty::ExecCommandSession).
  - Process registry keyed by ProcessId; explicit terminate_process + shutdown (solves XC-1 lifecycle).
  - Bounded replay buffer: per-process RETAINED_OUTPUT_BYTES_PER_PROCESS eviction (lines 558-568) +
    event count cap (solves truncation/OOM for long-running). Live fan-out + history replay; read()
    can wait-with-timeout via output_notify (line 346) — solves SHELL-1's "no new output" surfacing.
  - Explicit Exited{exit_code}/Closed/Failed events => timeout/exit always discriminable (SHELL-2/5).
- Sandboxing (best in class): macOS seatbelt, Linux landlock+seccomp (linux-sandbox/src/landlock.rs),
  bubblewrap (bwrap/), Windows restricted-token; execpolicy (Starlark command policy). Directly
  relevant to our XC-2 (no workspace containment) — Codex has real OS-level confinement.
- Integration: Rust. NOT a Python-importable dependency. Would require porting the DESIGN
  (process-group kill, select-over-timeout, drain-timeout, bounded-replay persistent session,
  pluggable backend) into Python, or running codex exec-server as a sidecar subprocess.

### Codex changelog check needed: does openhands-tools 1.24/1.25 fix SHELL-1/2? (TODO)

### OpenHands main (latest) re-check — DOES NOT FIX SHELL-1/2
- Fetched OpenHands/software-agent-sdk main terminal_session.py. The timeout handlers
  (_handle_nochange_timeout_command, _handle_hard_timeout_command) STILL call
  TerminalObservation.from_text(..., exit_code=metadata.exit_code) WITHOUT is_error=True,
  and the timeout text STILL lives ONLY in metadata.suffix. So bumping openhands-tools
  1.23.1 -> 1.25+ does NOT resolve SHELL-1 or SHELL-2. They remain OUR wrapper's job
  (render to_llm_content / stitch metadata.suffix; detect exit_code==-1 + suffix => error).

### Candidate: Claude Code Bash tool (anthropics/claude-agent-sdk-python) — design only
- The Python SDK is a THIN client: _internal/transport/subprocess_cli.py spawns the
  closed-source Claude Code CLI (`claude` binary, npm @anthropic-ai/claude-code). The Bash
  tool executes INSIDE the CLI — no readable/importable Python source. Only the DOCUMENTED
  design is available (persistent shell, timeout param, background w/ BashOutput/KillShell,
  output truncation). Not a portable dependency. Useful only as a design reference.

### Candidate: Cline (cline/cline) — TypeScript, Apache-2.0
- VS Code-coupled. apps/vscode/src/integrations/terminal/. Primary path uses VS Code's
  Terminal + shell-integration API; StandaloneTerminalProcess.ts is the non-VSCode fallback
  (CLI/JetBrains) using child_process.
- StandaloneTerminalProcess: spawn + terminateProcessTree cleanup. "hot" model (isHot/hotTimer,
  PROCESS_HOT_TIMEOUT_NORMAL=2s, PROCESS_HOT_TIMEOUT_COMPILING=15s) detects active output to
  decide when a command is "done outputting". Incremental retrieval (lastRetrievedIndex,
  MAX_UNRETRIEVED_LINES=500) streams output to the model in chunks. MAX_FULL_OUTPUT_SIZE=1MB
  ring buffer keeps tail (slice(-MAX/2)). exitCode/signal explicit; "completed" event.
- No fixed hard timeout in standalone (relies on user/abort + hot detection); "continue" lets
  a long command keep running in background while the agent proceeds. Statefulness: relies on
  the VS Code terminal session (persistent) in the primary path.
- Integration: TS + VS Code API coupling => not a library; design-port only, and the most
  coupled/least portable of the candidates.

### Candidate: Gemini CLI (google-gemini/gemini-cli) — TypeScript, Apache-2.0
- packages/core/src/services/shellExecutionService.ts (1624 lines) + executionLifecycleService.ts.
- node-pty (@lydell/node-pty) primary, child_process fallback. @xterm/headless to serialize PTY
  output (proper ANSI handling -> serializeTerminalToObject). AbortSignal-driven cancel ->
  killProcessGroup (process-utils). isBinary detection (binary_detected event). Environment
  sanitization, pluggable SandboxManager (NoopSandboxManager default).
- Output: SCROLLBACK_LIMIT=300000 lines; MAX_CHILD_PROCESS_BUFFER_SIZE=16MB; appendAndTruncate
  ring buffer keeps tail; on truncation appends "[GEMINI_CLI_WARNING: Output truncated. The
  buffer is limited to NMB.]" INTO the model-facing output (solves SHELL-1 spirit). Explicit
  exitCode/signal/aborted in ExecutionResult (solves SHELL-2). ExecutionLifecycleService is a
  registry keyed by executionId/pid w/ explicit exitedExecutionInfo + cleanup (lifecycle).
- backgroundProcessHistory keyed by sessionId+pid w/ status/exitCode/endTime (multitenant-ish).
- Integration: TS + node-pty + xterm native deps => not a Python library; design-port only.

### Candidate: openclaw (openclaw/openclaw) — TypeScript, license "other"
- src/agents/bash-tools.exec.ts (1991 lines) + bash-tools.process.ts (748) + a big exec-* infra
  suite (exec-approvals, exec-safe-bin-policy, exec-allowlist-pattern, exec-auto-review, host-gateway).
- Routes host/sandbox/node targets; policy + approval pipeline; PTY support; process-send-keys
  (interact with running process). Foreground result carries timedOut + exitCode + aggregated +
  durationMs (model sees timeout, solves SHELL-1/2). truncateMiddle truncation; DEFAULT_MAX_OUTPUT.
- "Yield to background" model: yieldMs / OPENCLAW_BASH_YIELD_MS auto-promotes a long foreground
  command to a tracked BACKGROUND SESSION instead of blocking (elegant answer to SHELL-3/4) +
  notifyOnExit completion notifications. Background session handoff w/ sessionId.
- Rich SECURITY layer: exec-safe-bin-policy (allowlist binaries), exec-allowlist-pattern,
  exec-approvals (per-command approval), exec-auto-review — strong XC-2-relevant containment.
- Integration: TS, license "other" (must verify before any use). Design-port only.

### Candidate: hermes-agent (NousResearch/hermes-agent) — PYTHON, MIT  *** most aligned ***
- tools/process_registry.py (session-keyed background process registry) + tools/terminal_tool.py
  (2611 lines) + tools/environments/{base,local,docker,ssh,modal,daytona,...}.py.
- BaseEnvironment ABC w/ PLUGGABLE backends (local/docker/ssh/modal/daytona/singularity) ===
  conceptually identical to calfcord's distributed deploy topology (run shell on any host/sandbox).
- _wait_for_process (base.py:655): adaptive poll (5ms->200ms) loop checks interrupt + hard
  deadline; on timeout -> _kill_process (process-group SIGTERM->SIGKILL) + drain_thread.join ->
  returns partial output + "[Command timed out after Ns]" + returncode 124 (SOLVES SHELL-1/2);
  interrupt -> "[Command interrupted]" + returncode 130. Separate drain_thread reads stdout
  non-blocking (no grandchild-FD hang -> SHELL-3). FOREGROUND returns INSTANTLY on exit even
  with high timeout (no silence-bail needed -> SHELL-4 N/A by design).
- ProcessRegistry: thread-safe; MAX_OUTPUT_CHARS=200KB rolling buffer (truncation);
  FINISHED_TTL_SECONDS=1800 + MAX_PROCESSES=64 LRU pruning (SOLVES XC-1/TODO-1 unbounded growth);
  task_id + session_key ISOLATION (SOLVES KNOWN-2 multitenancy); crash-recovery checkpoint
  (processes.json); watch_patterns + rate-limited notify; PTY support; psutil tree-kill (cross-platform).
- cwd persistence WITHOUT a persistent session: writes cwd to a _cwd_file via a `pwd -P` marker
  parsed back each call -> reliable cwd across one-shot calls (SOLVES KNOWN-1 round-robin).
- terminal_tool: FOREGROUND_MAX_TIMEOUT hard cap; background + notify_on_complete; structured
  JSON result (output/exit_code/error); timeout -> exit_code 124 + explicit error string.
- Integration: PYTHON, MIT. Closest to drop-in PORT for calfcord. Not a clean pip dependency
  (it's an app, not a packaged lib; imports hermes_cli.*), so PORT THE DESIGN/MODULES, not import.

## Step 3 — Expanded search (Python-importable / strong impls)

### Candidate: Claude Code Bash tool (Anthropic docs) — design reference
- Persistent bash session maintained CLIENT-SIDE (API is stateless; the app owns the
  Popen session). `restart` flag rebuilds the session. State (cwd/env) persists across calls.
- TIMEOUT: surfaced as tool_result with is_error=true + "Error: Command timed out after N
  seconds" — EXPLICITLY an error (the exact opposite of our SHELL-2; the documented contract
  says timeouts ARE errors). Reference impl uses subprocess.run(timeout=...) / a Popen+reader.
- TRUNCATION: truncate to N lines + "... Output truncated (N total lines) ..." notice (the
  SHELL-1 fix pattern). No streaming in base bash tool; "results returned after completion".
- Background: NOT in the base bash_2025xxxx tool. Claude Code CLI layers run_in_background +
  BashOutput + KillShell on top (closed source).
- Safety: docs push allowlist (not blocklist), reject shell operators, shell=False +
  shlex.split, ulimit, Docker/VM isolation, command logging, output sanitization (secrets).
- Integration: design reference only (CLI is closed-source; SDK just spawns it).

### Candidate: agent-zero (frdel/agent-zero) — PYTHON, MIT  *** best Python timeout model ***
- plugins/_code_execution/helpers/tty_session.py (422) + shell_local.py + shell_ssh.py;
  tools/code_execution_tool.py (558). Pluggable LocalInteractiveSession | SSHInteractiveSession
  (distributed-relevant). NUMBERED concurrent persistent sessions; explicit `reset` per-session
  or all-sessions; sessions survive across calls (true persistent statefulness — solves KNOWN-1).
- TTYSession: pty.openpty + asyncio.create_subprocess_shell + add_reader pump -> FULLY
  NON-BLOCKING async I/O (solves XC-3/KNOWN-3 by construction). close()/kill()/terminate()
  + __del__ net (addresses XC-1 lifecycle, though __del__ is best-effort).
- BEST timeout model of all candidates: get_terminal_output has FOUR coexisting timers —
  first_output_timeout(30) + between_output_timeout(15, idle/no-change) + dialog_timeout(5,
  interactive-prompt) + max_exec_timeout(240, hard cap). Unlike openhands these are NOT mutually
  exclusive, so SHELL-3 AND SHELL-4 are BOTH solved. Prompt-pattern early-return (returns the
  instant the shell prompt reappears -> fast like hermes/codex). Dialog detection for interactive.
- TIMEOUT SURFACING (the SHELL-1 fix done right): on every timeout/pause/max-time it returns
  truncated_output + an explicit framework message (fw.code.max_time / pause_time / no_out_time)
  telling the agent the command is still running and how to continue with the `output` action +
  session id. Model ALWAYS sees the state. truncate via truncate_text_agent (~1MB).
- Integration: PYTHON, MIT. Plugin-structured (imports agent-zero helpers/prompts), so PORT the
  TTYSession + 4-tier-timeout design rather than pip-import. tty_session.py is fairly self-contained.

## SUMMARY OF ISSUE COVERAGE (solve / partial / share)
                       SHELL-1 SHELL-2 SHELL-3 SHELL-4 SHELL-5 XC-1  XC-3  KNOWN-1 KNOWN-2
OpenHands(incumbent)   share*  share   share   share   share   share share share  share
  (*to_llm_content exists but UNUSED by us; even latest doesn't set is_error on timeout)
OpenAI Agents(design)  solve   solve   solve   n/a     solve   n/a   solve n/a    dev-provided
OpenCode V2 bash       solve   solve   solve   solve   solve   solve solve n/a    per-call
OpenCode legacy shell  solve   solve   solve   solve   solve   partial solve solve  session
Codex one-shot         solve   solve   solve   solve   solve   n/a   solve n/a    per-call
Codex exec-server      solve   solve   solve   solve   solve   solve solve solve  ProcessId
Claude Code(design)    solve   solve   solve   solve   solve   client client client client
Cline standalone       solve   solve   partial partial solve   solve  solve session session
Gemini CLI             solve   solve   solve   solve   solve   solve solve sessionId sessionId
openclaw               solve   solve   solve   solve   solve   solve solve session session
hermes-agent (PY)      solve   solve   solve   solve   solve   solve solve  solve  solve(task_id)
agent-zero (PY)        solve   solve   solve   solve   solve   solve solve  solve  numbered-sess

## DONE
- Final report written: docs/tools-research/reports/shell-report.md
- Licenses verified: OpenHands MIT, OpenAI-Agents MIT, OpenCode MIT, Codex Apache-2.0,
  Cline Apache-2.0, Gemini-CLI Apache-2.0, openclaw MIT (header), hermes-agent MIT,
  agent-zero MIT, claude-agent-sdk MIT.
- KEY: upgrading openhands-tools does NOT fix SHELL-1/2 (verified vs OpenHands main).
- RECOMMENDATION: Option C — stay-and-fix wrapper now (Phase 1); port agent-zero
  TTSession + 4-tier timeout later (Phase 2); mirror hermes ProcessRegistry/pluggable
  backend for distributed+multitenant; Codex exec-server as design ref / optional sidecar.
