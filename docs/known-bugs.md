# Known bugs & fragilities

A running log of defects and fragile behaviors found while raising test coverage of the vendored
tool code. These are **not** fixed here — the coverage effort *pins current behavior* so that a future
fix shows up as a deliberate change. Fixes are tracked separately.

**Status legend**
- **PINNED** — a regression test asserts the current (buggy) behavior; cited as `file::test`.
- **VERIFIED** — confirmed by direct execution this session; pinning test still to be written.
- **FLAGGED** — surfaced in deep source analysis; **not yet independently confirmed** (verify before relying on it).
- **RESOLVED** — investigated and found NOT to be a bug; a non-reproduction test guards against a future regression.

**Severity** — HIGH (security/data-loss), MEDIUM (incorrect guard / contract violation), LOW (correctness/UX).

> Note: the vendored modules are documented defense-in-depth, *not* hard security boundaries (the terminal
> tool runs as the same OS user and can bypass any of these guards). "Security" severity here means a guard
> behaves differently than its own stated contract, not that it is the last line of defense.

---

## HIGH

### BUG-001 — AWS secret access key is not redacted (secret leak)
- **Where:** `agent/redact.py` `_PREFIX_PATTERNS` (only `AKIA[A-Z0-9]{16}`, the *access key ID*).
- **What:** There is no pattern for the 40-char AWS *secret access key*. When it is not wrapped in a
  `…SECRET…=` assignment (e.g. space-separated in a log line), it passes through `redact_sensitive_text`
  completely unredacted.
- **Status:** PINNED — `tests/hermes/test_redact_behavior.py::test_BUG_aws_secret_access_key_value_leaks_without_assignment`.

### BUG-002 — Redaction over-redacts non-secret values
- **Where:** `agent/redact.py` `_SECRET_ENV_NAMES` / `_ENV_ASSIGN_RE` (the name alternation includes the bare
  substring `AUTH`, plus `KEY`/`TOKEN`/`SECRET`).
- **What:** Any env key *containing* one of those substrings has its value masked, even when it is not a
  secret: `AUTHOR=jane → AUTHOR=***`, `COAUTHOR_NAME=bob → ***`, `PUBLIC_API_KEY_NAME=my-config → ***`.
  Corrupts legitimate tool output.
- **Status:** PINNED — `test_redact_behavior.py::test_BUG_over_redacts_non_secret_key_containing_AUTH`.

### BUG-003 — Root-delete / privilege variants miss the HARDLINE floor (yolo-bypassable)
- **Where:** `tools/approval.py` `detect_hardline_command` (`HARDLINE_PATTERNS`).
- **What:** `rm -rf /` is HARDLINE (blocked unconditionally, *before* the yolo/`mode=off` early-return —
  verified: hardline check precedes the yolo return in `check_all_command_guards`). But trivially-equivalent
  forms are **not** hardline — verified this session:
  | command | hardline | dangerous |
  |---|---|---|
  | `rm -rf /` | ✅ | ✅ |
  | `rm -rf //` / `rm -rf /.` / `rm -rf /./` | ❌ | ✅ (`delete in root path`) |
  | `cd / && rm -rf .` | ❌ | ✅ (`recursive delete`) |
  | `rm -rf ${HOME}` (brace form; `$HOME`/`~` are matched) | ❌ | ✅ (`recursive delete`) |
  | `sudo --stdin whoami` (`-S` is matched; `--stdin` is not by the sudo guard) | ❌ | ✅ (`sudo with privilege flag`) |
  These are **caught by the softer DANGEROUS layer** (so not unguarded), but DANGEROUS is bypassed by
  `--yolo` / session-yolo / `approval_mode==off`, whereas HARDLINE is not. Net: in a yolo session,
  `rm -rf //` executes while `rm -rf /` is blocked.
- **Correction:** an earlier analysis note that `cd / && rm -rf .` is "completely unguarded" is **wrong** —
  direct execution shows `detect_dangerous_command` returns `recursive delete`. The real issue is the
  missing *hardline* classification, not a total bypass.
- **Status:** PINNED — `tests/hermes/test_approval_behavior.py::test_BUG003_rootdelete_variants_miss_hardline_floor_under_yolo`
  (asserts `rm -rf /` is blocked under yolo while `rm -rf //` and `cd / && rm -rf .` are approved).

---

## MEDIUM

### BUG-004 — Telegram-token regex over-redacts timestamp-like ids
- **Where:** `agent/redact.py` `_TELEGRAM_RE = (bot)?(\d{8,}):([-A-Za-z0-9_]{30,})`.
- **What:** Any long-digit `:` long-id pair matches — e.g. a nanosecond timestamp + request id
  (`1700000000123:abc…36chars`) is masked to `…:***`, corrupting non-secret output.
- **Status:** PINNED — `test_redact_behavior.py::test_BUG_telegram_regex_over_redacts_timestamp_like_ids`.

### BUG-005 — `.env` read-block is basename-only (skirtable)
- **Where:** `agent/file_safety.py` `get_read_block_error` / `_BLOCKED_PROJECT_ENV_BASENAMES`.
- **What:** The project-`.env` read guard is a basename **allowlist**, so trivially-renamed secret files are
  not blocked: `.env.bak`, `secrets.env`, `.env.prod.local` → allowed. Documented as defense-in-depth, but
  easy to skirt.
- **Status:** PINNED — `test_file_safety_behavior.py::test_BUG_read_block_env_is_basename_only`.

### BUG-006 — `touch_activity_if_due` raises despite "swallows all exceptions" docstring
- **Where:** `tools/environments/base.py` `touch_activity_if_due` (reads `state["last_touch"]`/`state["start"]`
  *before* the try block).
- **What:** `touch_activity_if_due({}, "x")` raises `KeyError: 'last_touch'`, contradicting the docstring.
  Currently masked because both call sites wrap it in their own try/except, but a future caller relying on the
  documented contract would break.
- **Status:** PINNED — `tests/hermes/test_base_env_behavior.py::test_BUG006_touch_activity_raises_on_missing_last_touch`.

### BUG-007 — Safe-root write-jail fails open on resolution error
- **Where:** `agent/file_safety.py` `get_safe_write_root` (returns `None` on any exception) +
  `is_write_denied` (jail enforced only `if safe_root`).
- **What:** A malformed/unresolvable `HERMES_WRITE_SAFE_ROOT` silently **disables** the jail (fail-open). An
  operator setting a write-jail would likely expect fail-closed.
- **Status:** FLAGGED (the exception path is hard to trigger; the normal jail behavior is pinned in
  `test_file_safety_behavior.py::test_write_safe_root_jail`).

### BUG-008 — CWD-marker collision can leak a partial marker into output
- **Where:** `tools/environments/base.py` `_extract_cwd_from_output` (anchors on the *last* marker via `rfind`).
- **What:** If a command's own stdout contains the per-session marker token, the parser can mis-pair and leave
  a dangling marker fragment in the output returned to the model.
- **Status:** PINNED — `tests/hermes/test_base_env_behavior.py::test_BUG008_extract_cwd_marker_collision_leaks_fragment`.

### BUG-009 — `tirith_security` fail-open/closed branch is unreachable via the real resolver
- **Where:** `tools/tirith_security.py` `check_command_security` (`if tirith_path is None: …`).
- **What:** `_resolve_tirith_path` returns a string on every path (`expanded`/`found`/`hermes_bin`/`installed`),
  never `None`, so the `tirith_path is None` fail-open/closed block is **dead defensive code** — unreachable via
  the real resolver, only via monkeypatch.
- **Status:** CONFIRMED — `tests/hermes/test_tirith_security_behavior.py::TestBug009PathNoneBranch` (asserts the
  real resolver never returns `None`, and covers the dead branch via a patched resolver).

---

## LOW

### BUG-010 — i18n leaks `{placeholder}` when a kwarg is omitted
- **Where:** `agent/i18n.py` `t()` (`value.format()` only runs when `format_kwargs` is truthy).
- **What:** `t("gateway.draining")` (catalog value has `{count}`) returns the raw `…{count}…` to the user
  instead of formatting or erroring.
- **Status:** PINNED — `test_i18n_behavior.py::test_placeholder_leak_when_kwarg_omitted`.

### BUG-011 — `atomic_json_write` swallows the chmod error when `mode=` is set
- **Where:** `_vendor/utils.py` `atomic_json_write` (post-replace `os.chmod` wrapped in `except OSError: pass`).
- **What:** When `mode=` is requested and the post-replace `chmod` fails, the error is silently swallowed; the
  write still reports success.
- **Status:** PINNED — `test_utils_atomic.py::TestAtomicJsonWriteMode::test_chmod_failure_after_replace_is_swallowed`.

### BUG-012 — PTY `write_stdin` reports char count as bytes written
- **Where:** `tools/process_registry.py` `write_stdin` (PTY path encodes UTF-8 but returns `len(data)`).
- **What:** For multibyte input, `bytes_written` is the character count, not the byte count;
  `submit_stdin` inherits it.
- **Status:** PINNED — `tests/hermes/test_process_registry_behavior.py::test_BUG012_pty_write_stdin_reports_char_count_not_bytes`.

### BUG-013 — `kill_process` records `exit_code=-15` even for SIGKILL
- **Where:** `tools/process_registry.py` `kill_process` (PTY `terminate(force=True)` sends SIGKILL but the
  code records `-15`).
- **What:** `process(action='kill')` / `list_sessions` report `-15` (SIGTERM) for a force-killed PTY process.
- **Status:** PINNED — `tests/hermes/test_process_registry_behavior.py::test_BUG013_pty_kill_records_sigterm_for_force_sigkill`.

### BUG-014 — `_reader_loop` final `exit_code` can be `None`
- **Where:** `tools/process_registry.py` `_reader_loop` (after stdout EOF, `wait(timeout=5)` may time out,
  leaving `returncode` `None`).
- **What:** A process whose stdout EOFs before it reaps within 5s gets `exited=True` but `exit_code=None`
  reported downstream.
- **Status:** FLAGGED (analysis). To confirm/pin in Phase B.

### BUG-015 — `_clean_shell_noise` substring match can strip a legitimate first line
- **Where:** `tools/process_registry.py` `_clean_shell_noise` (drops a leading line *containing* a noise
  substring, not anchored).
- **What:** A command whose first output line legitimately contains a known noise phrase (e.g. `echo "no job
  control in this shell"`) has that line silently stripped.
- **Status:** PINNED — `tests/hermes/test_process_registry_behavior.py::test_BUG015_clean_shell_noise_strips_legitimate_first_line`.

### BUG-016 — `_check_lint` `{file}` substitution — INVESTIGATED, NOT A BUG
- **Where:** `tools/file_operations.py` `_check_lint` (`linter_cmd.replace("{file}", escaped_path)`).
- **Finding:** Investigated in Phase B and **does not reproduce.** `str.replace` only replaces occurrences in
  the *original* string and never re-scans the replacement text, and each linter template has exactly one
  `{file}` placeholder — so a path literally containing `{file}` survives verbatim and the escaped path appears
  exactly once. (`.py/.json/.yaml/.toml` short-circuit to in-process linters and never reach this line.)
- **Status:** RESOLVED (not a bug) — pinned as a non-reproduction in
  `tests/hermes/test_file_operations_behavior.py::TestBug016FilePlaceholderSubstitution`.

### BUG-017 — `patch_replace` BOM verification asymmetry
- **Where:** `tools/file_operations.py` `patch_replace` (post-write verify strips BOM; `write_file` restores
  BOM; `read_file_raw` strips BOM — three independent BOM operations).
- **What:** The three BOM ops must stay mutually consistent; no test exercises BOM + patch together, so a
  divergence would produce a false post-write verification failure.
- **Status:** FLAGGED — investigated in Phase B; the three ops stay consistent on a clean round-trip, so no
  failing case could be isolated. Left as a fragility to watch, not a confirmed defect.

### BUG-018 — `_get_max_read_chars` caches the default permanently
- **Where:** `tools/file_tools.py` `_get_max_read_chars` (caches on first call; the config shim always
  returns `{}`).
- **What:** The read-char cap is fixed to the default for the process lifetime; a future un-shim of the config
  wouldn't take effect without a restart. Correct in-node today; fragile coupling.
- **Status:** FLAGGED (analysis, dormant-by-design).

### BUG-019 — Foreground timeout detection is substring-based, not type-based
- **Where:** `tools/terminal_tool.py` `terminal_tool` (`"timeout" in str(e).lower()` classifies a 124 timeout).
- **What:** Any exception whose message merely contains "timeout" (e.g. a library "read timeout") is
  classified as a command timeout (exit 124). Brittle vs `isinstance(e, subprocess.TimeoutExpired)`.
- **Status:** PINNED — `tests/hermes/test_terminal_tool_behavior.py::TestForegroundTimeoutBug019` (a
  `RuntimeError("connection read timeout")` is misclassified as a 124 timeout; a real `TimeoutError` with no
  "timeout" substring is missed).
