# Test-coverage improvement plan (real behavioral tests)

**Status:** PLAN — no tests written, no code changed. Scope: author *real behavioral* tests (edge/error/
boundary/branch/adversarial — never happy-path-only) for the most deficient **supported** modules, after first
excluding genuinely-dead-in-context code. Surfaced bugs are expected and welcome (fixes are a separate task).

Grounded in a 6-way deep source analysis against the current `.coverage` (repo total **70%**, post the
remote-backend omit). Every claim below cites `file:line` in the analysis; this doc distills it to an actionable plan.

---

## Part 1 — Verification: coverage counts exactly the packaged-and-exported surface ✅

Proven by diffing the built wheel against the coverage-counted set:

- **Wheel = on-disk package, exactly.** `uv build` ships **87 `.py`** modules under `calfkit_tools/`; the on-disk
  `src/calfkit_tools` tree is the same 87 (zero diff) — no build-exclude drops shipped code.
- **Counted = packaged − markers − unsupported backends.** Coverage reports on **62** files. The gap to 87 is
  *exactly* **17 `__init__.py`** (package markers, no logic) **+ 8 remote backends** (`docker/modal/modal_utils/
  managed_modal/daytona/ssh/singularity/file_sync`, the opt-in Stage-D surface omitted earlier). `62 + 17 + 8 = 87`.
- **No leaks.** The "counted but not packaged" set is **empty** — every counted file ships in the wheel; nothing
  outside `src/calfkit_tools` (tests, `vendor/scripts`, conftest) is counted.

**Conclusion:** coverage counts precisely the code we package *and* support. The only packaged-but-uncounted files
are package markers and the deliberately-omitted remote backends (packaged for opt-in use, not part of the supported
default surface). Verified clean.

---

## Part 2 — Reachable vs dormant: don't test dead code (do this first)

The single most important finding: several "deficient" modules are **dormant in the calfkit node** — reachable
only through CUT features (skills/web_tools/kanban/browser), omitted remote backends (Modal/Docker/SSH), or a
fail-closed shim. Writing tests for them would exercise dead-in-context code and inflate the number on paths the
node can't reach. The live tool set is fixed: `discover_builtin_tools()` only registers
**`terminal_tool, process_registry, code_execution_tool, file_tools, todo_tool`**; default backend is
`TERMINAL_ENV=local`.

**Recommended additional omits** (consistent with your earlier "omit code we don't export/support" instruction —
this is a 1-block `pyproject.toml` edit, presented here as a decision, not yet applied):

| Module | Miss/Stmts | Why dormant |
|---|---|---|
| `agent/skill_utils.py` | 280/319 (8%) | Served the **CUT** skills feature; its one near-live fn (`get_external_skills_dirs`) is reached only by `credential_files` → the **omitted remote backends** (docker/modal/ssh/singularity/file_sync). `LocalEnvironment` never touches it. |
| `tools/managed_tool_gateway.py` | 80/109 (20%) | Nous managed-gateway routing; both importers are **Modal-only** (`_get_modal_backend_state`, `managed_modal`). Existing tests *mock* its body. |
| `agent/async_utils.py` | 13/20 (27%) | `safe_schedule_threadsafe` has exactly one importer: `environments/modal.py` (already omitted). |
| `_shims/agent/lsp/range_shift.py` | 3/3 (0%) | Called only after `file_operations` checks `get_service()`, which the LSP shim hard-codes to `None` → statically imported, **dynamically unreachable**. |
| `_shims/agent/lsp/reporter.py` | 6/6 (0%) | Same fail-closed LSP gate. |

**Impact:** removes **~382 uncovered statements** (+ ~200 uncovered branches) from the denominator — the dominant
lever — lifting the badge from **70% → ~74-76%** before a single test is written. **Leave counted** (don't omit):
`utils.py` and `hermes_constants.py` (partially live — test the reachable functions, §3F), `gateway/status.py`
(reachable, test it), `auxiliary_client.py` (1-line by-design stub).

Also flag as **dormant-but-still-counted** (cap the achievable ceiling; don't chase): `terminal_tool` remote-backend
branches (~220-260 lines: modal/docker/ssh/daytona/singularity create + requirements), `code_execution`
`_execute_remote`/`_rpc_poll_loop` live-spawn paths (unit-testable via fake-env, not live), `file_operations` LSP
paths (~70 lines), `redact.py` URL-redaction helpers + `RedactingFormatter` (dead code, ~40 lines — intentionally off).

---

## Part 3 — Prioritized behavioral test plan

Phased by value × effort. New tests are **first-party** behavioral tests (author them as
`tests/hermes/test_<area>_behavior.py`; NOT `test_vendored_*` — they're ours, not vendored). All run via
`uv run --with pytest pytest tests/hermes/<file> -q --no-cov`.

### Phase A — Quick wins (pure / simple-mock, highest lift-per-effort)

| Target | Now → target | Test groups (representative non-happy-path cases) | Mocking |
|---|---|---|---|
| `web_providers/searxng.py` | 56→~98% | whole HTTP `search()` body is uncovered: **non-200 → typed error, connect/timeout, malformed JSON, score-sort+limit truncation, missing `results` key, trailing-slash base URL** | patch `httpx.get` w/ fake responses |
| `web_providers/brave_free.py` | 57→~98% | same matrix + **`count` clamp >20**, nested `web.results` missing, limit truncation mismatch | patch `httpx.get` |
| `web_providers/tavily.py` | 86→~98% | **interrupt short-circuit, httpx-raise → typed error, `failed_urls` normalization, empty-urls fallback** | patch `httpx.post`, `is_interrupted` |
| `web_providers/ddgs.py` | 85→~98% | **provider raises mid-iteration, `limit<1` coerced to 1** | fake `DDGS` |
| `agent/file_safety.py` | 77→~94% | **adversarial denylist**: control-plane files (auth.json/config.yaml/webhook), mcp-tokens+pairing, **safe-root jail bypass**, symlink-realpath denylist bypass, `.env` basename block, cross-profile default-target, sandbox-mirror boundary off-by-one | `tmp_path` profile layouts |
| `tools/credential_files.py` | 72→~91% | **symlink-escape rejection (cred exfil), absolute-path reject, malformed-entry skips, `_safe_skills_path` symlink-sanitize, non-docker cache passthrough** | `tmp_path` + real symlinks |
| `agent/redact.py` (live only) | 55→~80% | **over-redaction probes** (`AUTHOR=jane`, telegram false-positive), **false-negative AWS secret**, `force=`/`code_file=` boundaries, DB-connstr w/ vs w/o pw, phone short/long, too-short mask floor | `HERMES_REDACT_SECRETS=true` |
| `agent/i18n.py` | 54→~92% | **placeholder-leak when kwarg missing**, format-error → raw value, missing-key → bare key, cross-locale fallback (synthetic partial catalog), `_normalize_lang` alias/region/non-str, malformed-YAML catalog → `{}`, env>config precedence | temp `HERMES_BUNDLED_LOCALES` dir |
| `tools/tool_backend_helpers.py` | 52→~90% | invalid-mode→default, **provider-key precedence** (voice vs OPENAI fallback vs none), managed-blocked branch, **shim-raises → fail-closed** | `monkeypatch` `_shims.hermes_cli.*` |
| `utils.py` (reachable fns) | 32→~52% | `is_truthy_value` non-str/bool fallback; **`atomic_json_write` temp-cleanup on dump failure (no orphan `.tmp`)**, mode-perms path; `atomic_replace` **symlink preserved**; `_preserve_file_mode` stat-OSError → None | real tmpdir |
| `hermes_constants.py` (reachable fns) | 34→~67% | **`get_hermes_home` profile-fallback warning (one-shot)**, `get_default_hermes_root` under-`~/.hermes` branch, `get_subprocess_home` `{HOME}/home` exists, `display_hermes_home` not-under-home | env override + capture stderr |
| `_shims/gateway/status.py::_pid_exists` | 38→100% | falsy/live/dead pid, **psutil-absent fallback to `os.kill(pid,0)` incl. PermissionError→True** | block psutil import; patch `os.kill` |

### Phase B — Medium (socketpair / fake-env / branch matrices)

| Target | Now → target | Test groups | Mocking |
|---|---|---|---|
| `code_execution_tool.py` | 64→~86% | **`_rpc_server_loop` error-frame matrix** (malformed JSON→tool_error, allow-list reject, call-limit off-by-one, **terminal blocked-param stripping** = sandbox-escape guard, handler-raises survives, batched newline frames); `_scrub_child_env` prefix-substring boundaries; `_resolve_child_python` conda/version<3.8 fallback (must `delenv VIRTUAL_ENV`); `_execute_remote`/`_rpc_poll_loop` error paths via fake-env | `socket.socketpair()`; FakeEnv; patch `_shims.model_tools.handle_function_call` |
| `environments/base.py` | 68→~89% | **`_extract_cwd_from_output` branch matrix + marker-collision bug**, `touch_activity_if_due` missing-key/within-interval/callback-raises, `init_session` failure→login-shell fallback, **deterministic `_wait_for_process` interrupt(130)/timeout(124)/KeyboardInterrupt(orphan-kill)** via fake ProcessHandle, `_quote_cwd_for_cd` tilde/space, `_ThreadedProcessHandle` exec-raises/os.write-fails | fake ProcessHandle (iterator stdout) |
| `environments/local.py` | 74→~89% | **`_sanitize_subprocess_env` (0% today): `_HERMES_FORCE_` rewrite, blocklist strip, profile-HOME inject**, `_make_run_env` ContextVar bridge, `_resolve_shell_init_files` missing/explicit/expandvars-raises, `_prepend_shell_init` quote-injection, `get_temp_dir` precedence + `/tmp`-unwritable, `_kill_process` PermissionError | pure + patch `get_subprocess_home`/`os.killpg` |
| `file_operations.py` | 79→~93% | **search backend fallbacks** (no-rg→grep/find, BSD-find `-printf` retry, `--sortr` retry, **partial-error-keeps-matches**, count/context parsing), **`patch_replace` post-write verification (silent-persistence guard, fully uncovered)**, `read_file_raw` binary/image/cat-fail, lint optional-dep `__SKIP__`, `_python_delete` exec body, `_suggest_similar_files` scoring ladder, `_expand_path` `~user`/`$HOME`-empty | MagicMock env `side_effect` + real-subprocess on `tmp_path` |
| `file_tools.py` | 86→~96% | dedup→hard-block escalation thresholds, **staleness external-edit warning**, patch-failure escalation `_hint` (#3), `_record_patch_failure` 64-cap eviction, `_cap_read_tracker_data` eviction loops, `_check_sensitive_path`/`_is_blocked_device` (symlink to `/dev/zero`, `/proc/self/environ`, resolve-OSError), **`write_file_tool` legacy resolve-fail fallback (whole block uncovered)** | direct + real env |
| `patch_parser.py` | 76→~92% | **V4A validate-before-apply failures**: context mismatch (no write), context-hint window retry, addition-only ambiguous/missing, MOVE dst-exists, DELETE missing, **partial multi-op apply leaves reported state** (overlapping-hunks ask) | real env on `tmp_path` |
| `process_registry.py` | 80→~94% | **`write_stdin`/`submit_stdin` (untested): not_found/already_exited/DEVNULL-unavailable/PTY-byte-count bug**, `kill_process` live tree/PTY/env_ref/detached/else branches, **`kill_all`/`count_running`**, `_reconcile_local_exit` orphan-pipe drain, `spawn_via_env` execute-raises, `format_uptime_short` boundaries | real subprocess (Linux) + fakes |

### Phase C — Real-subprocess + adversarial security (highest value, most setup)

| Target | Now → target | Test groups | Notes |
|---|---|---|---|
| `tools/approval.py` | 77→~93% | **gateway blocking flow** (timeout ≠ consent, deny → anti-evasion BLOCKED, notify-fail), **smart-mode** approve/deny/escalate persistence, **cron-deny** blocks dangerous + execute_code, CLI prompt branches (callback-raises→deny, prompt_toolkit fail-closed, `always`→`session` downgrade), `_format_tirith_description` | thread+Event+notify-cb harness; patch `_get_approval_mode`; prompt_toolkit |
| `tools/tirith_security.py` | 76→~89% | **verdict mapping under bad JSON (degrade enrichment, not verdict)**, `.app`-TLD warn suppression (single vs mixed), cross-device copy fallback, explicit-path-missing (no auto-download = supply-chain), `_background_install` double-check, cosign-retry | mock `subprocess.run`/`shutil`/`which` |
| `tools/terminal_tool.py` (local only) | 58→~80% | **foreground retry/timeout(124)/truncation/exit-note**, background hints + **notify/watch conflict**, **approval blocked/pending/smart/workdir JSON branches**, tokenizer/sudo-rewriter comment+escape edges, lifecycle (`get_active_env`/`is_persistent_env`) | fake env (retry/timeout) + real LocalEnvironment; remote branches stay dormant |
| `process_registry.py` (real) | (folds into B) | `_reader_loop`/`_pty_reader_loop` real run: noise-strip, 200KB truncation, non-zero exit, PTY exitstatus, invalid-UTF-8 decode | real subprocess/ptyprocess (skipif ImportError) |

---

## Part 4 — Bugs & security gaps surfaced (welcome findings)

Tests will **pin current behavior** (so regressions are caught) and the items below are flagged for a *separate*
fix decision. Ranked by severity.

**HIGH (security):**
1. **`rm -rf //` / `rm -rf /.` / `rm -rf /./` bypass the HARDLINE floor** (`approval.py:251`) — they fall to the
   softer DANGEROUS layer, which **`--yolo`/session-yolo/`mode=off` pass through**. A yolo session can wipe root.
2. **`cd / && rm -rf .` is COMPLETELY unguarded** (`approval.py`) — neither hardline nor dangerous fires (regex is
   cwd-blind). Equivalent to `rm -rf /`.
3. **AWS secret access key is not redacted** (`redact.py:85`) — only the `AKIA…` *access-key ID* is matched, not the
   40-char *secret*. The high-value credential leaks in full unless wrapped in a `…SECRET…=` assignment.
4. **Redaction over-redaction corrupts output** (`redact.py:110`) — any env key *containing* `AUTH`/`KEY`/`TOKEN`/
   `SECRET` masks its value: `AUTHOR=jane → AUTHOR=***`; telegram regex masks long-digit:long-id timestamps.

**MEDIUM:**
5. `is_write_denied` **safe-root jail fails open** if `HERMES_WRITE_SAFE_ROOT` resolution raises (`file_safety.py:145`).
6. `.env` read-block is **basename-only** (`file_safety.py:300`) — `.env.bak`/`secrets.env` not blocked (by-design DiD, but skirtable).
7. **CWD-marker collision leaks a partial marker** to the model when command output contains the session marker (`base.py:785`).
8. `touch_activity_if_due({})` **raises KeyError** despite its "swallows all exceptions" docstring (`base.py:67`).
9. `_resolve_child_python` **conda branch is untestable on conda+venv boxes** without `delenv VIRTUAL_ENV` (coverage trap, not a bug).
10. `tirith_security` `tirith_path is None` fail-open/closed branch is **unreachable via the real resolver** (dead defensive code or contract mismatch) (`tirith_security.py:727`).

**LOW (correctness):**
11. PTY `write_stdin` returns char-count as `bytes_written` for multibyte input (`process_registry.py:1213`).
12. `kill_process` records `exit_code=-15` even for PTY `terminate(force=True)` = SIGKILL (`process_registry.py:1149`).
13. `_reader_loop` final `exit_code` can be `None` if `wait(timeout=5)` times out (`process_registry.py:771`).
14. i18n **leaks `{count}` placeholder** to users when a caller omits the kwarg (`i18n.py:284`).
15. Dead code: redact URL helpers + `RedactingFormatter` + `_has_http_method_substring` never called (`redact.py:273-496`).

---

## Part 5 — Coverage projection

| Step | Repo badge (combined line+branch) |
|---|---|
| Today | **70%** (orange) |
| + dormant omits (Part 2) | **~74-76%** |
| + Phase A | **76%** (done) |
| + Phase B | **78%** (done) |
| + Phase C | **81%** (done — approval 77→87, tirith 76→90, code_execution 66→78, terminal_tool 58→63) |

The ceiling on the supported surface is high-80s: the residual is genuinely-dormant remote-backend code in
`terminal_tool`/`code_execution` and the off-by-design LSP/URL-redaction code, which we correctly do not chase.
This moves the badge from orange decisively toward the 90% green threshold while keeping every test a *real*
behavioral assertion.

---

## Part 6 — Mechanics & harness notes (for whoever writes these)

- **Naming:** first-party files `tests/hermes/test_<area>_behavior.py` (e.g. `test_redact_behavior.py`,
  `test_search_backends.py`, `test_rpc_server_loop.py`, `test_approval_gateway.py`). Extend
  `tests/hermes/test_web_providers.py` for providers (don't duplicate `test_node_web.py`'s FakeProvider tests).
- **Env is scrubbed** by the autouse `_hermetic_environment` (HERMES_*/TERMINAL_*/TIRITH_* + credentials) — set what
  you need via `monkeypatch` *inside* the test. `_YOLO_MODE_FROZEN` is import-frozen → use `enable_session_yolo`,
  not `HERMES_YOLO_MODE`.
- **Mock at the shim path:** `calfkit_tools.hermes._shims.model_tools.handle_function_call`,
  `…_shims.hermes_cli.config.load_config`, etc. Config readers return `{}` — monkeypatch `_get_approval_mode`/
  `read_raw_config` to reach config-driven branches.
- **Redaction:** use a ≥18-char `sk-…`/`AKIA…`-shaped token to actually trigger masking.
- **RPC:** drive `_rpc_server_loop` with `socket.socketpair()` in a thread — no live subprocess needed.
- **Remote/`_execute_remote`:** use the existing FakeEnv pattern (scripted `.execute`) — unit-testable though dormant.
- **macOS/conda gotchas (already encoded in conftest):** prefer behavioral/substring assertions over exact resolved
  paths (the `/tmp→/private/tmp` symlink); `delenv VIRTUAL_ENV` for conda-branch determinism; real-subprocess tests
  are Linux-CI-green (tag genuinely-flaky ones `live`).
- **No code changes** in this effort — when a test pins a bug from Part 4, assert current behavior + leave a
  `# BUG(...)` note referencing this doc; fixes are a separate task.
