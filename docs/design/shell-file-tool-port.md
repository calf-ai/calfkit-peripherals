# Design: Shell + File tool — vendoring & calfkit integration

**Status:** Draft (deep-review rounds 1–2 complete). Source = **hermes-agent** (confirmed; agent-zero re-evaluated and rejected — see §10).
**Convergence (round 2):** Stages A–C **CONVERGED** — ready to implement, incl. `execute_code` (PTC) **kept** (cheap — see §12) + a defined test gate. **Stage D NOT converged** — a dedicated security + multi-tenant + Kafka-contract design (and a round-3 review) is required before any Stage-D work (see §12).
**Date:** 2026-06-07
**Owners:** Ryan

This doc captures the design for the first agent tool(s) vendored into `calfkit-peripherals`,
incorporating the findings of the 6-agent adversarial review of the hermes-agent plan
(summarized in §11). It also frames the open decision between **hermes-agent** and
**agent-zero** as the source.

---

## 1. Purpose & scope

Bring a production-grade **shell** tool (and, since it rides along, a **file** edit toolset)
into calfkit by vendoring from an upstream agent codebase, exposing it as a calfkit **node
tool** over calfkit's Kafka interface. Requirements:

- **Multi-tenant:** many agents share one worker; per-agent session isolation.
- **Local backend first**, with a pluggable-backend seam (remote backends ported but optional).
- **Stateful** shell (cwd/env/installed tools persist across calls for a given agent).
- Clean provenance/licensing; re-syncable from upstream.

## 2. Repo architecture (polyglot component monorepo)

calfkit's tool interface is **Kafka (language-agnostic)** — a node tool is a *process* that
consumes a request topic and produces a reply. So this repo is a **polyglot monorepo of node
components**, not a single Python package. See memory `vendoring-architecture`.

```
calfkit-peripherals/
  THIRD_PARTY_NOTICES.md          # aggregated index across all components
  components/
    hermes-agent/                 # one self-contained node component per upstream source
      NODE.md  METADATA.yaml  LICENSE  patches/
      pyproject.toml              # package `calfkit-hermes`, per-backend extras
      src/calfkit_hermes/
        _vendor/                  # upstream tree, import-rewritten, namespaced
        _shims/                   # app-runtime edge replacements
        node/                     # calfkit Kafka adapter + thin tool wrappers (Stage D)
      tests/                      # vendored upstream tests (regression)
```

- **Vendor by source** (provenance, import-isolation, re-sync). **Expose by tool** (consumer).
- Per-component package, installed by calfkit via `uv add "calfkit-<name>[...] @ git+…#subdirectory=components/<name>"`, pinned to a tag.
- Non-Python components later ship their own artifact (binary/container) as a Kafka service.

## 3. Component: hermes-agent (shell + files)

- **Source:** `NousResearch/hermes-agent` @ `5a36f76a00cc448948856a5c1b52710aafec264e`, MIT © 2025 Nous Research. Repo is Apache-2.0 (MIT-into-Apache OK; preserve MIT notice).
- **Vendor set: 42 Python files** (bounded closure; app-infra = shim boundary). Only `kanban_tools` is cut; **`code_execution_tool.py` (PTC) is KEPT** (cheap — the RPC dispatcher delegates to the already-vendored `registry.dispatch`; see §12):
  - `tools/environments/`: base, local, docker, ssh, modal, managed_modal, daytona, modal_utils, singularity, file_sync, `__init__`
  - `tools/`: terminal_tool, code_execution_tool, process_registry, file_tools, **file_state** (added — was missed), file_operations, fuzzy_match, patch_parser, approval, tirith_security, registry, interrupt, ansi_strip, env_passthrough, path_security, tool_backend_helpers, tool_output_limits, credential_files, binary_extensions, budget_config, lazy_deps, managed_tool_gateway, thread_context, `__init__`
  - `agent/`: file_safety, redact, async_utils, i18n, skill_utils
  - top-level: hermes_constants.py, utils.py
- **Cut:** `tools/kanban_tools.py` (drops the `hermes_cli.kanban_db` SQLite dep; the only edge — `skill_utils.py:208` — is function-local + try/except, fails closed). Safe.
- **Authored (not copied):** empty `_vendor/agent/__init__.py` (upstream imports `jiter_preload` → crash), `_vendor/__init__.py`, `_shims/**/__init__.py`.
- **Vendored data:** `locales/en.yaml` (i18n) + repoint `i18n._locales_dir()`; else approval/i18n strings render as bare keys.
- **Base deps:** `psutil`, `requests` (managed_modal top-level import), `PyYAML`, `ptyprocess`. **Extras:** `shell-docker` (docker), `shell-modal` (modal), `shell-daytona` (daytona-sdk), `shell-ssh` (paramiko). `requires-python = ">=3.11"`.

### 3.1 Import-rewrite spec

Mechanical rewrite of `import`/`from` statements only, **most-specific-prefix-wins** (the
`agent.*` namespace splits across `_vendor` and `_shims`):

1. `hermes_cli[.*]` → `calfkit_hermes._shims.hermes_cli[.*]`
2. `gateway.*` → `calfkit_hermes._shims.gateway.*`
3. `model_tools` → `calfkit_hermes._shims.model_tools`
4. `agent.auxiliary_client` → `calfkit_hermes._shims.agent.auxiliary_client`
5. `agent.lsp[.*]` → `calfkit_hermes._shims.agent.lsp[.*]`
6. `agent.*` (else) → `calfkit_hermes._vendor.agent.*`
7. `tools.*` → `calfkit_hermes._vendor.tools.*`
8. `hermes_constants` / `utils` → `calfkit_hermes._vendor.*`

**Manual fix-ups the rewriter cannot do (each one is a *silent* runtime break if missed):**

- `tools/registry.py` — `importlib.import_module(f"tools.{stem}")` must repoint to the new
  package root, else the tool registry is **silently empty** at runtime.
- `agent/i18n.py` — `__file__`-relative `locales/` lookup must repoint to the bundled data.
- `tools/code_execution_tool.py` — `__file__`-relative child `PYTHONPATH` must repoint or drop.
- `agent/skill_utils.py:208` — kanban branch references the cut module; delete or accept the
  (already try/except'd) silent `False`.
- Re-run a **corrected closure** before copying (the original missed `from pkg import submodule`,
  which is how `file_state` was dropped) to confirm no other submodule imports are missing.

### 3.2 Shim classification (post-review round 2)

`code_execution_tool` (PTC) is kept; `model_tools.handle_function_call` is needed but is a **~5-line slim dispatcher** delegating to the vendored `registry.dispatch` (which already does lookup + async-bridge via `_run_async` + error-sanitize) — no closure re-expansion (the heavy real dispatcher with Tool Search bridge / hooks / guardrails / `toolsets.py` is NOT needed). See §6.1 for the in-script approval-scope note.

| Class | Modules/symbols | Note |
|---|---|---|
| **Import-safe stub (env hygiene moved to Stage-D allowlist)** | `hermes_cli.auth.PROVIDER_REGISTRY`, `hermes_cli.config.OPTIONAL_ENV_VARS` | These feed `local.py`'s subprocess *blocklist*. Round 2: Stage D **replaces the blocklist with an allowlist** (§6.1), making the blocklist data moot — so for A–C these only need to import cleanly (empty is fine; the allowlist, not the blocklist, governs real env hygiene). Do **not** copy the drift-prone upstream tables. |
| **Vendor verbatim (pure / load-bearing)** | `hermes_cli.config.cfg_get`, `model_tools._run_async`, `model_tools._sanitize_tool_error` (+ its 5 module-level regex/consts) | Stdlib-only; faithful copies. |
| **Stage-D stub (deferred)** | `gateway.session_context` (`_UNSET`/`_VAR_MAP`/`get_session_env`) | The per-tenant isolation mechanism. Shipped as a minimal env-backed stub for pre-Stage-D (single-tenant) use; Stage D replaces it with the real per-request ContextVar mechanism re-keyed on `session_key` (§6, §12). A stub here is acceptable *only* because Stage D rebuilds it (round-2 review flagged the divergence from an earlier "vendor verbatim" classification). |
| **Faithful but tiny** | `hermes_cli.config.get_hermes_home` (**re-export the vendored `hermes_constants` impl**; called at import → must return a `Path`), `gateway.status._pid_exists`, `hermes_cli._subprocess_compat.windows_hide_flags` (return `0`, not `None`) | Import-time type constraints. |
| **Safe no-op / identity** | `agent.lsp.*` (must **fail-closed** — return `None`/raise, not a half-object), `agent.auxiliary_client.call_llm`, `hermes_cli.auth.resolve_nous_access_token`, `hermes_cli.plugins.invoke_hook`, `hermes_cli.config.{load_config,read_raw_config,save_config,load_env,get_env_value,get_config_path}`, `hermes_cli.nous_account.*`, `hermes_cli.profiles.get_active_profile_name` | All consumed inside try/except with sane defaults. |

## 4. Statefulness model (and the "install a CLI tool" question)

hermes is **spawn-per-call + snapshot**. Across calls for one agent:

- **Persists:** cwd, exported env vars, aliases, shell functions (via the snapshot re-dumped
  each command), and **any binary installed to disk on a `PATH` location already in the snapshot.**
- **Caveat:** installers that only edit rc files (`nvm`/`pyenv`/`rustup`) extend `PATH` in the
  rc, which hermes does **not** re-source per command — the binary is installed but unreachable
  until `PATH` is exported in-band once (then the snapshot persists it) or the session resets.
- **Does not persist:** unexported vars, `set`/`shopt` options, `ulimit`, traps, in-shell bg jobs.

**agent-zero** (true persistent PTY shell) persists *everything*, including rc-file `PATH`
changes via `source` — strictly more faithful on this axis. See §10.

## 5. Execution model & exit codes

hermes: one-shot command per call, **real exit codes** (`exit $__hermes_ec`), clean pipe
output, the `select()` drain that defeats the `setsid … &` grandchild-pipe hang, process-group
kill. Long-running / interactive work goes through the **background process registry**
(`process` tool: poll/wait/kill/log + stdin write/submit/close).

## 6. Stage D — calfkit Kafka host-integration layer (NOT thin)

The review's headline correction: hermes' multi-tenancy, interrupt, and liveness assume a
threaded, ContextVar-aware, long-lived host (its gateway). A Kafka node breaks those
assumptions. Stage D must provide:

1. **Per-tenant environment registry.** Foreground exec keys env on `task_id`, which
   **collapses to `"default"` for all agents** (`terminal_tool.py:1012`) — i.e. *no* per-agent
   isolation by default. Re-key on `session_key` (the real per-tenant id, today only used for
   background bookkeeping), pass it into `LocalEnvironment`, and add an **LRU-bounded registry
   with idle eviction + explicit shutdown** (env eviction does not exist upstream; only a global
   300s daemon).
2. **Session affinity + single-flight.** The snapshot is "last-writer-wins"; concurrent
   same-tenant calls corrupt env state. **Partition Kafka by `session_key`** and serialize
   per-session execution.
3. **Cancellation bridge.** Interrupt is thread-ident-scoped; a Kafka cancel message can't
   target the executing thread. Maintain a `session_key → cancel/thread` map and translate.
4. **Liveness.** The activity callback is thread-local with no Kafka equivalent → long commands
   look dead. Add a heartbeat over Kafka or a duration cap.
5. **Offloaded execution.** Execution is blocking (poll loops, `time.sleep`); running inline in
   the consumer wedges it (group rebalance). Offload to a worker thread/pool; keep the consumer
   polling.
6. **Notification draining.** `completion_queue`/`pending_watchers` are only drained by hermes'
   gateway → they leak in our worker. Drain into Kafka or disable notify/watch.

### 6.1 Security posture (MANDATED baseline — round 2)

Round 2 found the round-1 "wire or accept" framing insufficient for multi-tenant. Mandates:

- **Secrets (CRITICAL, new in R2):** hermes' subprocess env-blocklist only knows *hermes'*
  provider keys — it does **not** block **calfkit's own infra secrets** (Kafka SASL,
  schema-registry, cloud creds), which therefore leak into every tenant's shell
  (`local.py:310-316`). **Replace the blocklist with an explicit allowlist**
  (`PATH/HOME/USER/LANG/LC_*/TERM/TMPDIR/SHELL` + tenant-registered passthrough; hard-deny
  calfkit secret names). hermes already ships this shape for `execute_code` (`_scrub_child_env`).
  This also deletes the `PROVIDER_REGISTRY`/`OPTIONAL_ENV_VARS` shim-drift problem.
- **Isolation:** host exec on a shared worker is not defensible. **Require a per-tenant sandbox
  backend (`shell-docker`) OR one-tenant-per-worker.** `TERMINAL_ENV=local` only under a
  conscious single-trusted-tenant decision.
- **Approval (fail-closed):** set `HERMES_CRON_SESSION=1` + `approvals.cron_mode: deny` →
  dangerous-pattern/`execute_code` commands are **blocked**, no human needed. Do NOT ship
  "accept trusted execution." Real interactive approval is a Stage-D subsystem (sync→async Kafka
  bridge + contextvar propagation), not a flag.
- **`execute_code` (PTC) kept, but must be gated in Stage D:** it runs arbitrary Python, and its
  in-script tool calls go via `registry.dispatch`, which **bypasses the approval/guard layer**.
  Stage D must (a) route in-script `terminal`/file calls through the same guard as direct calls,
  and (b) rely on the sandbox for containment (treat `execute_code` as trusted-within-sandbox).
  Under fail-closed approval, explicitly allow `execute_code` as a first-class tool.
- **Per-tenant FS confinement:** `HERMES_WRITE_SAFE_ROOT` per tenant + separate cwd/`HERMES_HOME`.
  Note `file_safety` guards are explicitly *not* security boundaries.
- **Kill-switches + import ordering:** `TIRITH_ENABLED=false`, `HERMES_DISABLE_LAZY_INSTALLS=1`,
  `HERMES_HOME`=per-tenant writable; ensure `HERMES_YOLO_MODE`/`SUDO_PASSWORD` unset, the **worker
  UID has no NOPASSWD sudo**, and `GITHUB_TOKEN`+calfkit secrets are stripped from the worker env.
  **CRITICAL ordering:** `_YOLO_MODE_FROZEN`/`_HERMES_PROVIDER_ENV_BLOCKLIST`/`_REDACT_ENABLED`
  are frozen at **import** — set all security env/config **before importing** the vendored package.

## 7. Packaging & distribution

Single component package `calfkit-hermes` with per-backend extras; `psutil`/`requests`/`PyYAML`/
`ptyprocess` base. Verify the built wheel actually contains `_vendor/**`/`_shims/**` (regular
packages with `__init__.py`, not PEP 420 namespace) via `uv build` + inspect. `uv add` from a
git `#subdirectory` is supported.

## 8. Execution stages

- **A — scaffold:** `THIRD_PARTY_NOTICES.md`, `components/hermes-agent/{METADATA.yaml, LICENSE, pyproject.toml}`, package skeleton. (`NODE.md` deferred — it documents the Kafka contract, a Stage-D artifact.)
- **B — vendor verbatim + rewrite:** copy the **42 files** (only cut: `kanban_tools`) + `file_state` + `locales/*.yaml`; author empty `_vendor/agent/__init__.py` + `_vendor`/`_shims` package markers; run the rewriter (most-specific-prefix-wins) + the 4 manual fix-ups; add provenance headers + secondary-origin attributions. Commit pair: verbatim, then rewrite. (Test files optional here; the string-target patch rewrite can wait for C.)
- **C — shims + test gate:** author the shims per §3.2 (incl. the ~5-line `handle_function_call` over `registry.dispatch`) until the **gate passes**: pure-logic mocked-env tests (`test_fuzzy_match`, `test_patch_parser`, `test_file_operations`, `test_file_tools`) + a build→import→register→local read/write/patch round-trip smoke + an `execute_code` RPC smoke (a script that calls `terminal()`+`read_file()` via the bridge and returns stdout). Port the autouse `conftest.py` fixtures as needed.
- **D — DEFERRED (by decision).** Kafka host-integration + security + multi-tenancy (§6, §6.1, §12). Blocked on the calfkit Kafka contract (`NODE.md`); needs its own design + round-3 review.

## 9. Open decisions / risks

- ~~Source component (hermes vs agent-zero)~~ — resolved: **hermes** (§10).
- ~~`execute_code` in scope~~ — resolved: **KEPT** (PTC; the bridge is ~free via `registry.dispatch`). Stage-D must scope approval for in-script tool calls (§6.1).
- Security posture + multi-tenancy — **deferred to Stage D** (§6.1, §12); not on the A–C path.
- Stage-D prerequisite: the calfkit **Kafka contract** (`NODE.md`) must be defined first.
- Risk to watch in A–C: porting the autouse `conftest.py` fixtures for the test gate.

## 10. Source decision: hermes (confirmed); agent-zero re-evaluated

A 4-agent head-to-head re-evaluated agent-zero. **Decision: hermes remains the base.**
agent-zero does NOT clear the "handles more issues AND fewer new issues" bar.

- **Where agent-zero is better:** (a) **statefulness fidelity** — a true persistent PTY shell,
  so rc-file `PATH` (nvm/pyenv/rustup), venv activation, and shell options all persist
  automatically, with no hermes snapshot caveat (§4); (b) the **four-tier coexisting timeout**
  (first-output / idle / dialog / hard-cap) — the only model solving SHELL-3 + SHELL-4 together;
  (c) ~20× smaller vendor set (~1.3k LOC, one backend) with a clean, zero-intra-dep 422-LOC
  `tty_session.py` engine; (d) cleaner single-MIT license (no secondary-origin attributions).
- **Where agent-zero loses (decisive for a structured-reply Kafka node tool):** (a) **no exit
  codes at all** — completion is brittle prompt-regex; a node tool needs authoritative
  success/failure; (b) **worse security defaults** — full `os.environ` to the subprocess with
  *no* blocklist; `ssh_enabled="auto"` → SSH-as-root with blind `AutoAddPolicy` exactly in our
  non-dockerized headless case; `runtime.call_development_function` defaults to an **RFC
  remote-exec that phones home** when not dockerized; (c) **more new landmines** — import-time
  `sys.std*.reconfigure`, `__del__`→`nest_asyncio`+`asyncio.run`, unbounded output buffers, no
  session TTL/LRU, output-corrupting scrubbing; (d) **no background-process registry**; (e)
  live-process-per-session cost with no eviction.

**Adopt from agent-zero into the hermes base:**
1. **The four-tier timeout model** in the tool's timeout handling — closes hermes' SHELL-4-by-
   design gap with a genuine idle + hard combo + yield-to-background.
2. **(Optional, Phase 2) an opt-in persistent-PTY session mode** built on agent-zero's clean
   `tty_session.py` engine, for workloads needing rc-file/venv/shell-option fidelity — gated so
   the default path keeps hermes' exit-code guarantee. The convergent panel design:
   *agent-zero's async PTY engine wrapped in hermes' bounded multi-tenant registry*, stripped of
   its `__del__`/stdio-reconfigure side effects.

Implementation proceeds on the hermes base; convergence-to-no-CRITICAL (project rule) applies to
the Stage-D security/multi-tenancy design.

## 11. Review findings appendix (round 1, 6 agents)

- **Vendor set:** complete after adding `file_state.py` + `locales/` + authored `agent/__init__.py`. Kanban cut clean. Count = 42.
- **Shims:** mostly trivial, except the security-data pair (`PROVIDER_REGISTRY`/`OPTIONAL_ENV_VARS`) and the verbatim-pure set (`session_context`, `cfg_get`, `_run_async`, `_sanitize_tool_error`); a few faithful-tiny with import-time type constraints.
- **Rewrite:** not pure-prefix; most-specific-wins + 4 manual fix-ups (registry f-string is the dangerous one — silent empty registry).
- **Architecture:** hermes does NOT provide foreground per-agent isolation by default (`task_id→"default"`); snapshot is concurrency-unsafe; interrupt/liveness are thread-scoped; execution is blocking. Stage D is a real layer.
- **Security:** approval gate auto-bypasses headless; full-env secret inheritance; host-exec default; auto-download + runtime-pip kill-switches needed.
- **License:** safe; add secondary-origin attributions (opencode MIT, nearai/ironclaw elect-MIT, free-code MIT) for `redact.py`/`binary_extensions.py`/`tool_output_limits.py`/`fuzzy_match.py`; keep "Ported from" headers.

## 12. Round-2 convergence status

Reviewers 1 & 4 independently verified the vendor/rewrite/shim mechanics; reviewers 2, 3 & 5
attacked Stage D.

**Stages A–C — CONVERGED.** Vendor set independently reproduced to the exact list (zero delta);
`file_state` is provably the only submodule miss; all 4 rewrite fix-ups + the shim classification
confirmed against source. Conditions before Stage C:
- **Keep `execute_code` (PTC)** (→ 42 files). Reversed the round-2 cut after finding the RPC
  dispatcher is cheap: `model_tools.handle_function_call` is a **~5-line slim shim over the
  already-vendored `registry.dispatch`** (lookup + async-bridge + error-sanitize), so it does NOT
  re-expand the closure (the heavy real dispatcher — Tool Search bridge, hooks, guardrails,
  `toolsets.py` — is NOT needed). Caveats: PTC bridges only to **this node's own tools** (terminal
  + 4 file tools; `web_search`/`web_extract` aren't vendored → simply not offered, graceful);
  cross-node PTC is out of scope. Stage-D security must scope approval for in-script tool calls
  (§6.1) and contain via sandbox. Bonus: `code_execution_tool` already uses
  `thread_context.propagate_context_to_thread` — the contextvars-into-thread bridge Stage D needs.
- **Define the test gate:** the pure-logic, mocked-env tests (`test_fuzzy_match`,
  `test_patch_parser`, `test_file_operations`, `test_file_tools`) + a build→import→register→local
  read/write/patch round-trip smoke. Account for porting the autouse `conftest.py` fixtures.
- LOW nits: `get_hermes_home` shim = re-export the vendored `hermes_constants` impl (not a stub);
  bundle all `locales/*.yaml` or pin `display.language=en`; add `resolve_nous_access_token` (safe
  no-op) to the `hermes_cli.auth` shim; fix §11 per-file attribution mapping (redact→nearai/
  ironclaw; fuzzy_match & tool_output_limits→opencode; binary_extensions→free-code); strip the
  unreachable `ruamel`-using `utils` function. Positive: no `signal.signal()` handlers.

**Stage D — NOT CONVERGED.** Round 2 found NEW CRITICALs round 1 missed; §6/§6.1 are a
problem-list, not a design. Stage D must not start until a dedicated design (+ round-3 review)
addresses:
- *Security (§6.1):* calfkit-own-secret leak → allowlist env model; import-frozen flags;
  sandbox-or-single-tenant; fail-closed approval; cut execute_code; per-tenant FS confinement.
- *Multi-tenancy:* re-keying on `session_key` must span **all** cross-tenant globals, not just
  terminal's env registry — `file_tools._active_environments` (raw `task_id`), `_file_ops_cache`,
  `_read_tracker`, `_patch_failure_tracker`, `file_state._reads`, `_creation_locks`,
  `_sudo_password_cache` (thread-scoped → pool-reuse leak), `approval._permanent_approved`
  (global), `process_registry` global `MAX_PROCESSES=64`+LRU (cross-tenant eviction), the global
  watch circuit-breaker. The **`contextvars.copy_context()` bridge into the offload pool thread**
  is the unaddressed linchpin every session-key-keyed control depends on.
- *Concurrency:* single-flight must serialize foreground re-dump against **background** reader
  threads (else snapshot corruption persists); cancel must **kill the process group**, not just
  set the thread-interrupt flag (and clear it on thread return); LRU eviction must refcount/pin
  in-flight envs.
- *Kafka contract (CRITICAL, blocking):* `NODE.md` is undefined — no request/reply schema, no
  tool→topic mapping (terminal, process, read_file, write_file, patch, search_files), no
  error/timeout/cancel shapes. **Stage D cannot be designed until the calfkit Kafka contract is
  pinned.** Also: file-tool outputs vs broker `max.message.bytes`.
- *Timeout graft:* split the agent-zero 4-tier adoption — kill-based deadlines (first-output/idle)
  are Phase-1-compatible (preserve exit codes); **yield-to-background-without-killing is
  incompatible** with hermes' foreground model and belongs to the Phase-2 persistent-PTY mode only.
- *File toolset is shell-fused:* `file_operations.py`'s writer is a shell script via
  `env.execute()`; the file tools inherit all of Stage D (offloading, affinity, env registry,
  sandbox choice) — not a lightweight pure-Python tool.
- *Lifecycle:* neutralize upstream `atexit` + the 300s cleanup daemon in favor of the Stage-D registry.
