"""Behavioral tests for the LOCAL-backend surface of ``terminal_tool``.

Scope: this file targets the *local / supported* execution paths of
``terminal_tool()`` and its local helpers that the existing
``test_vendored_terminal_*`` files leave uncovered. It deliberately does NOT
exercise the dormant remote-backend code (docker / modal / singularity / ssh /
daytona create+requirements branches, the modal gateway, or the interactive
sudo-TTY prompt) — those are reachable only via backends the node omits.

Covered uncovered branches:
  * ``terminal_tool()`` foreground: TimeoutError -> 124, transient-error retry
    with exponential backoff then success, retries-exhausted -> error JSON,
    output truncation, real ``LocalEnvironment`` exit-note.
  * Approval JSON branches: blocked, pending_approval, smart-approved note,
    user-approved note, workdir blocked.
  * Background (local): silent-bg hint, notify+watch conflict resolution.
  * Tokenizer / sudo-rewriter edges: double-quote escape, ``#``-comment line,
    multi-line second-line sudo.
  * Lifecycle: ``get_active_env`` / ``is_persistent_env`` None-vs-set,
    ``_stop_cleanup_thread`` join.
  * BUG-019: substring-based foreground timeout detection misclassifies a
    non-timeout exception whose message merely contains "timeout".

The autouse harness (tests/hermes/conftest.py) scrubs HERMES_*/TERMINAL_* and
resets terminal_tool / approval / file_tools module globals per test; we set
only what each test needs via monkeypatch / direct patching.

Most tests use a fake env (``MagicMock`` with a scripted ``.execute``) injected
into ``_active_environments`` so ``terminal_tool`` skips environment creation;
the exit-note test uses a real ``LocalEnvironment`` (POSIX/Linux-CI-green).
"""

import json
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from calfkit_tools.hermes._vendor.tools import terminal_tool


_TT = "calfkit_tools.hermes._vendor.tools.terminal_tool"


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------
def _make_env_config(**overrides):
    """Return a minimal ``_get_env_config()``-shaped dict with overrides."""
    config = {
        "env_type": "local",
        "timeout": 180,
        "cwd": "/tmp",
        "host_cwd": None,
        "modal_mode": "auto",
        "docker_image": "",
        "singularity_image": "",
        "modal_image": "",
        "daytona_image": "",
    }
    config.update(overrides)
    return config


def _run_with_fake_env(env, *, guard_result=None, config=None, **tool_kwargs):
    """Invoke ``terminal_tool`` against a pre-seeded fake env.

    Patches ``_get_env_config``, ``_start_cleanup_thread`` and ``_check_all_guards``
    and seeds ``env`` into ``_active_environments["default"]`` so the function
    skips creation and dispatch straight into the foreground/background body.
    Returns the parsed JSON result dict.
    """
    if guard_result is None:
        guard_result = {"approved": True}
    if config is None:
        config = _make_env_config()

    with patch(f"{_TT}._get_env_config", return_value=config), \
         patch(f"{_TT}._start_cleanup_thread"), \
         patch.dict(f"{_TT}._active_environments", {"default": env}, clear=False), \
         patch.dict(f"{_TT}._last_activity", {"default": 0.0}, clear=False), \
         patch(f"{_TT}._check_all_guards", return_value=guard_result):
        return json.loads(terminal_tool.terminal_tool(**tool_kwargs))


# ===========================================================================
# Foreground execution: timeout, retry/backoff, exhaustion, truncation
# ===========================================================================
class TestForegroundExecution:
    """The retry-loop body of ``terminal_tool`` (lines ~2258-2368)."""

    def test_timeout_exception_returns_exit_code_124(self):
        """env.execute raising an error whose message says 'timeout' -> exit 124.

        Detection is substring-based on the message (see BUG-019), so the
        message must literally contain "timeout". Note: a ``TimeoutError`` whose
        message reads "timed out" (two words) would NOT match — that gap is
        itself BUG-019 and is pinned separately below.
        """
        env = MagicMock()
        env.cwd = "/tmp"
        env.execute.side_effect = TimeoutError("execution exceeded timeout")

        with patch(f"{_TT}.time.sleep") as mock_sleep:
            result = _run_with_fake_env(env, command="sleep 999", timeout=30)

        assert result["exit_code"] == 124
        assert "timed out after 30 seconds" in result["error"]
        # 124 path returns immediately — no retry, no backoff sleep.
        assert env.execute.call_count == 1
        mock_sleep.assert_not_called()

    def test_transient_error_retries_with_backoff_then_succeeds(self):
        """Two non-timeout failures then success: retried with 2**n backoff."""
        env = MagicMock()
        env.cwd = "/tmp"
        env.execute.side_effect = [
            ConnectionError("transient blip 1"),
            ConnectionError("transient blip 2"),
            {"output": "recovered", "returncode": 0},
        ]

        with patch(f"{_TT}.time.sleep") as mock_sleep:
            result = _run_with_fake_env(env, command="echo hi")

        assert result["exit_code"] == 0
        assert result["output"] == "recovered"
        assert result["error"] is None
        # Retried twice before the third (successful) call.
        assert env.execute.call_count == 3
        # Exponential backoff: 2**1 then 2**2.
        assert [c.args[0] for c in mock_sleep.call_args_list] == [2, 4]

    def test_retries_exhausted_returns_error_json(self):
        """All attempts (1 + 3 retries) fail -> exit_code -1 error JSON."""
        env = MagicMock()
        env.cwd = "/tmp"
        env.execute.side_effect = RuntimeError("backend exploded")

        with patch(f"{_TT}.time.sleep") as mock_sleep:
            result = _run_with_fake_env(env, command="echo hi")

        assert result["exit_code"] == -1
        assert "Command execution failed" in result["error"]
        assert "RuntimeError" in result["error"]
        # 1 initial attempt + 3 retries.
        assert env.execute.call_count == 4
        assert mock_sleep.call_count == 3

    def test_output_truncation_inserts_notice(self):
        """Output longer than get_max_bytes is head+tail truncated with a notice."""
        env = MagicMock()
        env.cwd = "/tmp"
        big = "A" * 300
        env.execute.return_value = {"output": big, "returncode": 0}

        # Force a tiny cap so a small output trips truncation deterministically.
        # get_max_bytes is imported inside terminal_tool at call time, so patch
        # it at its source module.
        with patch(
            "calfkit_tools.hermes._vendor.tools.tool_output_limits.get_max_bytes",
            return_value=100,
        ):
            result = _run_with_fake_env(env, command="cat bigfile")

        assert "[OUTPUT TRUNCATED" in result["output"]
        assert "chars omitted" in result["output"]
        # Head (40 chars) + notice + tail (60 chars) is far shorter than 300.
        assert len(result["output"]) < len(big)
        assert result["exit_code"] == 0

    def test_no_truncation_when_under_cap(self):
        """Output at/under the cap is passed through verbatim (no notice)."""
        env = MagicMock()
        env.cwd = "/tmp"
        env.execute.return_value = {"output": "short output", "returncode": 0}

        with patch(
            "calfkit_tools.hermes._vendor.tools.tool_output_limits.get_max_bytes",
            return_value=100,
        ):
            result = _run_with_fake_env(env, command="echo short")

        assert result["output"] == "short output"
        assert "[OUTPUT TRUNCATED" not in result["output"]


class TestForegroundTimeoutBug019:
    """BUG-019: foreground timeout detection is substring-based, not type-based."""

    def test_non_timeout_exception_misclassified_as_124(self):
        """A non-timeout error whose MESSAGE contains 'timeout' is wrongly 124.

        The classifier is ``"timeout" in str(e).lower()`` (terminal_tool.py
        ~2277), so a plain ``RuntimeError("connection read timeout")`` — which
        is a transient backend error, NOT a command timeout — is misreported as
        a 124 command-timeout instead of being retried. This test pins that
        CURRENT (buggy) behavior; flipping it to type-based detection
        (``isinstance(e, subprocess.TimeoutExpired)``) would change it.
        """
        env = MagicMock()
        env.cwd = "/tmp"
        # NOT a TimeoutError — a generic runtime error that merely mentions
        # "timeout" in its message (e.g. a library "read timeout").
        env.execute.side_effect = RuntimeError("connection read timeout")

        with patch(f"{_TT}.time.sleep") as mock_sleep:
            result = _run_with_fake_env(env, command="echo hi", timeout=45)

        # BUG-019: substring match short-circuits to a 124 command-timeout
        # verdict and returns immediately — the transient error is NEVER
        # retried, even though it is not a real command timeout.
        assert result["exit_code"] == 124
        assert "timed out after 45 seconds" in result["error"]
        assert env.execute.call_count == 1  # no retry happened
        mock_sleep.assert_not_called()

    def test_timeout_error_without_literal_substring_is_not_detected(self):
        """The inverse facet of BUG-019: a real ``TimeoutError`` is missed.

        Detection is on the literal substring "timeout"; a genuine
        ``TimeoutError("command timed out")`` reads "timed out" (two words) and
        does NOT contain "timeout", so it is NOT classified as a 124 timeout —
        it falls through to the transient-retry path and finally exhausts to a
        generic -1 error. Type-based detection
        (``isinstance(e, subprocess.TimeoutExpired)``) would report 124 here.
        This pins the current substring behavior.
        """
        env = MagicMock()
        env.cwd = "/tmp"
        env.execute.side_effect = TimeoutError("command timed out")

        with patch(f"{_TT}.time.sleep"):
            result = _run_with_fake_env(env, command="sleep 999", timeout=30)

        # NOT 124 — the real timeout went unrecognized and was retried.
        assert result["exit_code"] == -1
        assert "Command execution failed" in result["error"]
        assert "TimeoutError" in result["error"]
        assert env.execute.call_count == 4  # 1 + 3 retries, then exhausted


# ===========================================================================
# Real LocalEnvironment exit-note (POSIX / Linux-CI-green)
# ===========================================================================
class TestForegroundExitNote:
    """A real local grep with no matches (rc 1) gets the friendly exit note."""

    def test_grep_no_matches_attaches_exit_code_meaning(self):
        """Real LocalEnvironment: ``grep <nomatch> /etc/passwd`` -> rc1 + note.

        Uses /etc/passwd (present on Linux CI and macOS) so grep reliably exits
        1 ("no matches"), not 2 (file-not-found). The exit-code interpreter then
        attaches a non-error meaning to the result.
        """
        from calfkit_tools.hermes._vendor.tools.environments.local import (
            LocalEnvironment,
        )

        env = LocalEnvironment(cwd="/tmp")
        result = _run_with_fake_env(
            env,
            command="grep zzz_no_such_token_xyzzy /etc/passwd",
        )

        assert result["exit_code"] == 1
        assert result["error"] is None
        assert result["exit_code_meaning"] == "No matches found (not an error)"
        assert result["output"] == ""


# ===========================================================================
# Approval / guard JSON branches
# ===========================================================================
class TestApprovalBranches:
    """The ``_check_all_guards`` result -> JSON status branches (~1979-2025)."""

    def test_blocked_command_returns_status_blocked(self):
        """Guard not approved (no pending) -> status 'blocked' with message."""
        env = MagicMock()
        env.cwd = "/tmp"
        env.execute.return_value = {"output": "should not run", "returncode": 0}

        guard = {
            "approved": False,
            "description": "rm -rf danger",
            "message": "Denied: destructive command",
        }
        result = _run_with_fake_env(env, command="rm -rf /", guard_result=guard)

        assert result["status"] == "blocked"
        assert result["exit_code"] == -1
        assert result["error"] == "Denied: destructive command"
        env.execute.assert_not_called()

    def test_blocked_command_falls_back_when_no_message(self):
        """Blocked without an explicit message uses the synthesized fallback."""
        env = MagicMock()
        env.cwd = "/tmp"

        guard = {"approved": False, "description": "scary thing"}
        result = _run_with_fake_env(env, command="danger", guard_result=guard)

        assert result["status"] == "blocked"
        assert "Command denied: scary thing" in result["error"]
        assert "approval prompt" in result["error"]

    def test_pending_approval_returns_status_pending(self):
        """Guard status pending_approval -> the approval-pending JSON shape."""
        env = MagicMock()
        env.cwd = "/tmp"

        guard = {
            "approved": False,
            "status": "pending_approval",
            "command": "deploy prod",
            "description": "deployment flagged",
            "pattern_key": "deploy.prod",
        }
        result = _run_with_fake_env(env, command="deploy prod", guard_result=guard)

        assert result["status"] == "pending_approval"
        assert result["approval_pending"] is True
        assert result["command"] == "deploy prod"
        assert result["description"] == "deployment flagged"
        assert result["pattern_key"] == "deploy.prod"
        env.execute.assert_not_called()

    def test_smart_approved_attaches_approval_note(self):
        """Smart-approved command runs and carries an auto-approval note."""
        env = MagicMock()
        env.cwd = "/tmp"
        env.execute.return_value = {"output": "ran", "returncode": 0}

        guard = {
            "approved": True,
            "smart_approved": True,
            "description": "flagged but safe",
        }
        result = _run_with_fake_env(env, command="git push", guard_result=guard)

        assert result["exit_code"] == 0
        assert result["output"] == "ran"
        assert "auto-approved by smart approval" in result["approval"]
        assert "flagged but safe" in result["approval"]

    def test_user_approved_attaches_approval_note(self):
        """User-approved command runs and carries a user-approval note."""
        env = MagicMock()
        env.cwd = "/tmp"
        env.execute.return_value = {"output": "ran", "returncode": 0}

        guard = {
            "approved": True,
            "user_approved": True,
            "description": "dangerous but confirmed",
        }
        result = _run_with_fake_env(env, command="rm file", guard_result=guard)

        assert result["exit_code"] == 0
        assert "approved by the user" in result["approval"]
        assert "dangerous but confirmed" in result["approval"]

    def test_workdir_injection_blocked(self):
        """A workdir with shell metacharacters is blocked after guard passes."""
        env = MagicMock()
        env.cwd = "/tmp"
        env.execute.return_value = {"output": "nope", "returncode": 0}

        result = _run_with_fake_env(
            env,
            command="ls",
            workdir="/tmp/$(rm -rf /)",
        )

        assert result["status"] == "blocked"
        assert result["exit_code"] == -1
        assert "workdir contains disallowed character" in result["error"]
        env.execute.assert_not_called()

    def test_force_skips_guard_entirely(self):
        """force=True bypasses the guard check (it is never consulted)."""
        env = MagicMock()
        env.cwd = "/tmp"
        env.execute.return_value = {"output": "forced", "returncode": 0}

        config = _make_env_config()
        with patch(f"{_TT}._get_env_config", return_value=config), \
             patch(f"{_TT}._start_cleanup_thread"), \
             patch.dict(f"{_TT}._active_environments", {"default": env}, clear=False), \
             patch.dict(f"{_TT}._last_activity", {"default": 0.0}, clear=False), \
             patch(f"{_TT}._check_all_guards") as mock_guard:
            result = json.loads(
                terminal_tool.terminal_tool(command="rm -rf /tmp/x", force=True)
            )

        assert result["exit_code"] == 0
        assert result["output"] == "forced"
        mock_guard.assert_not_called()


# ===========================================================================
# Background (local) branches
# ===========================================================================
class TestBackgroundLocal:
    """Local background spawn: silent-bg hint and notify/watch conflict."""

    @staticmethod
    def _make_bg_env():
        env = MagicMock()
        env.cwd = "/tmp"
        env.env = {}
        return env

    @staticmethod
    def _make_registry():
        session = MagicMock()
        session.id = "sess-abc"
        session.pid = 4321
        session.watcher_platform = ""  # gateway routing off
        registry = MagicMock()
        registry.spawn_local.return_value = session
        return registry, session

    def _run_bg(self, env, registry, **tool_kwargs):
        config = _make_env_config()
        with patch(f"{_TT}._get_env_config", return_value=config), \
             patch(f"{_TT}._start_cleanup_thread"), \
             patch.dict(f"{_TT}._active_environments", {"default": env}, clear=False), \
             patch.dict(f"{_TT}._last_activity", {"default": 0.0}, clear=False), \
             patch(f"{_TT}._check_all_guards", return_value={"approved": True}), \
             patch("calfkit_tools.hermes._vendor.tools.process_registry.process_registry", registry), \
             patch("calfkit_tools.hermes._vendor.tools.approval.get_current_session_key", return_value=""):
            return json.loads(terminal_tool.terminal_tool(**tool_kwargs))

    def test_silent_background_emits_hint(self):
        """background=True with neither notify nor watch -> silent-bg hint."""
        env = self._make_bg_env()
        registry, session = self._make_registry()

        result = self._run_bg(env, registry, command="python worker.py", background=True)

        assert result["exit_code"] == 0
        assert result["session_id"] == "sess-abc"
        assert result["pid"] == 4321
        assert "runs SILENTLY" in result["hint"]
        assert "notify_on_complete=true" in result["hint"]
        registry.spawn_local.assert_called_once()

    def test_notify_on_complete_suppresses_silent_hint(self):
        """notify_on_complete=True -> no silent-bg hint, notify flag echoed."""
        env = self._make_bg_env()
        registry, session = self._make_registry()

        result = self._run_bg(
            env, registry,
            command="pytest -q", background=True, notify_on_complete=True,
        )

        assert "hint" not in result
        assert result["notify_on_complete"] is True
        assert session.notify_on_complete is True

    def test_notify_and_watch_conflict_drops_watch(self):
        """Both flags set -> watch_patterns dropped with an explanatory note."""
        env = self._make_bg_env()
        registry, session = self._make_registry()

        result = self._run_bg(
            env, registry,
            command="server", background=True,
            notify_on_complete=True, watch_patterns=["ready"],
        )

        # notify_on_complete wins; watch_patterns is dropped.
        assert result["notify_on_complete"] is True
        assert "watch_patterns_ignored" in result
        assert "duplicate notifications" in result["watch_patterns_ignored"]
        assert "watch_patterns" not in result  # not echoed back as active
        # The session keeps notify but never gets watch_patterns set.
        assert session.notify_on_complete is True

    def test_watch_only_sets_patterns(self):
        """watch_patterns alone (no notify) is honored and echoed back."""
        env = self._make_bg_env()
        registry, session = self._make_registry()

        result = self._run_bg(
            env, registry,
            command="tail -f log", background=True, watch_patterns=["ERROR"],
        )

        assert result["watch_patterns"] == ["ERROR"]
        assert session.watch_patterns == ["ERROR"]
        # watch_patterns is itself a completion signal, so no silent-bg hint.
        assert "hint" not in result

    def test_background_spawn_failure_returns_error(self):
        """A spawn_local exception is wrapped into a 'Failed to start' error."""
        env = self._make_bg_env()
        registry = MagicMock()
        registry.spawn_local.side_effect = OSError("no fork for you")

        result = self._run_bg(env, registry, command="python s.py", background=True)

        assert result["exit_code"] == -1
        assert "Failed to start background process" in result["error"]
        assert "no fork for you" in result["error"]


# ===========================================================================
# Tokenizer / sudo-rewriter edges
# ===========================================================================
class TestShellTokenizer:
    """``_read_shell_token``: quote/escape handling (lines ~464-497)."""

    def test_double_quote_with_escaped_quote_is_one_token(self):
        r"""A ``\"`` escape inside double quotes does not terminate the token."""
        cmd = r'echo "a\"b c" tail'
        token, end = terminal_tool._read_shell_token(cmd, len("echo "))
        # The whole quoted run (through the closing quote) is one token; the
        # escaped inner quote must not be treated as the terminator.
        assert token == r'"a\"b c"'
        assert cmd[end:] == " tail"

    def test_single_quotes_are_literal(self):
        """Inside single quotes, a backslash is literal and stops at the quote."""
        cmd = r"echo 'a\b' rest"
        token, end = terminal_tool._read_shell_token(cmd, len("echo "))
        assert token == r"'a\b'"
        assert cmd[end:] == " rest"

    def test_token_stops_at_operator(self):
        """An unquoted token ends at shell operators like ``|``."""
        token, end = terminal_tool._read_shell_token("foo|bar", 0)
        assert token == "foo"
        assert "foo|bar"[end:] == "|bar"


class TestSudoRewriter:
    """``_rewrite_real_sudo_invocations``: comment + multi-line edges (~500-558)."""

    def test_real_leading_sudo_rewritten(self):
        """A genuine leading ``sudo`` word is rewritten to non-interactive form."""
        rewritten, found = terminal_tool._rewrite_real_sudo_invocations("sudo apt update")
        assert found is True
        assert rewritten == "sudo -S -p '' apt update"

    def test_comment_line_sudo_not_rewritten(self):
        """``sudo`` inside a ``#`` comment line is left untouched."""
        cmd = "# sudo rm -rf /\necho safe"
        rewritten, found = terminal_tool._rewrite_real_sudo_invocations(cmd)
        assert found is False
        assert rewritten == cmd

    def test_second_line_sudo_rewritten(self):
        """A ``sudo`` at the start of the SECOND line is rewritten (newline resets)."""
        cmd = "echo start\nsudo systemctl restart nginx"
        rewritten, found = terminal_tool._rewrite_real_sudo_invocations(cmd)
        assert found is True
        assert rewritten == "echo start\nsudo -S -p '' systemctl restart nginx"

    def test_word_containing_sudo_not_rewritten(self):
        """A token like ``pseudosudo`` is not a sudo command word."""
        rewritten, found = terminal_tool._rewrite_real_sudo_invocations("pseudosudo run")
        assert found is False
        assert rewritten == "pseudosudo run"

    def test_quoted_sudo_mention_not_rewritten(self):
        """``sudo`` appearing as plain text after another command is not rewritten."""
        cmd = "echo sudo is dangerous"
        rewritten, found = terminal_tool._rewrite_real_sudo_invocations(cmd)
        assert found is False
        assert rewritten == cmd


# ===========================================================================
# Lifecycle helpers
# ===========================================================================
class TestLifecycle:
    """``get_active_env`` / ``is_persistent_env`` / ``_stop_cleanup_thread``."""

    def test_get_active_env_none_when_absent(self):
        """No registered env for the task -> None (lines ~1406-1410)."""
        assert terminal_tool.get_active_env("no-such-task") is None

    def test_get_active_env_returns_registered(self):
        """A registered env (under the resolved 'default' key) is returned."""
        sentinel = MagicMock(name="env")
        with patch.dict(f"{_TT}._active_environments", {"default": sentinel}, clear=False):
            # An ordinary task_id collapses to "default".
            assert terminal_tool.get_active_env("some-task") is sentinel

    def test_is_persistent_env_false_when_absent(self):
        """is_persistent_env is False when there is no active env."""
        assert terminal_tool.is_persistent_env("no-such-task") is False

    def test_is_persistent_env_reflects_persistent_flag(self):
        """is_persistent_env mirrors the env's ``_persistent`` attribute."""
        persistent = MagicMock()
        persistent._persistent = True
        ephemeral = MagicMock()
        ephemeral._persistent = False

        with patch.dict(f"{_TT}._active_environments", {"default": persistent}, clear=False):
            assert terminal_tool.is_persistent_env("t") is True
        with patch.dict(f"{_TT}._active_environments", {"default": ephemeral}, clear=False):
            assert terminal_tool.is_persistent_env("t") is False

    def test_stop_cleanup_thread_clears_flag_and_joins(self):
        """_stop_cleanup_thread flips the running flag off and joins the thread."""
        fake_thread = MagicMock(spec=threading.Thread)
        with patch(f"{_TT}._cleanup_thread", fake_thread), \
             patch(f"{_TT}._cleanup_running", True):
            terminal_tool._stop_cleanup_thread()
            # Running flag is reset to False (read it back off the module).
            assert terminal_tool._cleanup_running is False
        fake_thread.join.assert_called_once_with(timeout=5)

    def test_stop_cleanup_thread_noop_when_no_thread(self):
        """With no thread object, _stop_cleanup_thread just clears the flag."""
        with patch(f"{_TT}._cleanup_thread", None), \
             patch(f"{_TT}._cleanup_running", True):
            terminal_tool._stop_cleanup_thread()  # must not raise
            assert terminal_tool._cleanup_running is False
