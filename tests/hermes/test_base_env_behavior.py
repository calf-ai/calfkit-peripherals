"""Behavioral tests for tools/environments/base.py — the shared backend base.

Targets the uncovered branches: activity-callback liveness, the CWD-marker
parser (incl. the collision bug), init_session failure fallback, the
_ThreadedProcessHandle exception path, _quote_cwd_for_cd / _embed_stdin_heredoc,
and the deterministic _wait_for_process interrupt/timeout/KeyboardInterrupt
paths (driven by a fake ProcessHandle with an iterator stdout so no real
subprocess is spawned).
"""
import threading

import pytest

from calfkit_tools.hermes._vendor.tools.environments import base
from calfkit_tools.hermes._vendor.tools.environments.base import (
    BaseEnvironment,
    _ThreadedProcessHandle,
    set_activity_callback,
    touch_activity_if_due,
)


class _Env(BaseEnvironment):
    def _run_bash(self, *a, **k):  # pragma: no cover - overridden per test
        raise NotImplementedError

    def cleanup(self):
        pass


class _FakeProc:
    """ProcessHandle stand-in. stdout is a plain iterator → _drain_iterable path."""

    def __init__(self, *, polls=None, output=("",), returncode=0):
        self._polls = list(polls) if polls else []
        self._i = 0
        self.stdout = iter(output)
        self.returncode = returncode
        self.pid = 4321
        self.killed = False

    def poll(self):
        if self._i < len(self._polls):
            v = self._polls[self._i]
            self._i += 1
            return v
        return self.returncode

    def kill(self):
        self.killed = True


@pytest.fixture(autouse=True)
def _reset_activity_cb():
    yield
    set_activity_callback(None)  # thread-local — don't leak into other tests


# ---------------------------------------------------------------------------
# touch_activity_if_due
# ---------------------------------------------------------------------------

def test_BUG006_touch_activity_raises_on_missing_last_touch():
    # BUG-006 (docs/known-bugs.md): the docstring claims it "swallows all
    # exceptions", but state["last_touch"] is read BEFORE the try block, so an
    # empty state raises KeyError. Pins current behavior.
    with pytest.raises(KeyError):
        touch_activity_if_due({}, "label")


def test_touch_activity_fires_callback_when_due():
    fired = []
    set_activity_callback(lambda msg: fired.append(msg))
    import time
    now = time.monotonic()
    state = {"last_touch": now - 20, "start": now - 20, "interval": 10}
    touch_activity_if_due(state, "running")
    assert fired and "running" in fired[0]
    assert state["last_touch"] >= now  # advanced


def test_touch_activity_noop_within_interval():
    fired = []
    set_activity_callback(lambda msg: fired.append(msg))
    import time
    now = time.monotonic()
    touch_activity_if_due({"last_touch": now, "start": now, "interval": 10}, "x")
    assert fired == []


def test_touch_activity_swallows_callback_error():
    def boom(_msg):
        raise RuntimeError("cb boom")
    set_activity_callback(boom)
    import time
    now = time.monotonic()
    # Must NOT raise even though the callback raises.
    touch_activity_if_due({"last_touch": now - 20, "start": now - 20}, "x")


# ---------------------------------------------------------------------------
# get_sandbox_dir
# ---------------------------------------------------------------------------

def test_get_sandbox_dir_default_under_hermes_home(monkeypatch, tmp_path):
    monkeypatch.delenv("TERMINAL_SANDBOX_DIR", raising=False)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hh"))
    p = base.get_sandbox_dir()
    assert p.is_dir()
    assert p.name == "sandboxes"


def test_get_sandbox_dir_custom_env(monkeypatch, tmp_path):
    custom = tmp_path / "mysandboxes"
    monkeypatch.setenv("TERMINAL_SANDBOX_DIR", str(custom))
    assert base.get_sandbox_dir() == custom
    assert custom.is_dir()


# ---------------------------------------------------------------------------
# _quote_cwd_for_cd / _embed_stdin_heredoc
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "cwd, expected",
    [("~", "~"), ("~/", "$HOME"), ("~/a b", "$HOME/'a b'"), ("/abs/p", "/abs/p")],
)
def test_quote_cwd_for_cd(cwd, expected):
    assert BaseEnvironment._quote_cwd_for_cd(cwd) == expected


def test_embed_stdin_heredoc_shape():
    out = BaseEnvironment._embed_stdin_heredoc("cat", "line1\nline2")
    assert out.startswith("cat << 'HERMES_STDIN_")
    assert "\nline1\nline2\n" in out
    # The closing delimiter matches the opening one.
    delim = out.split("'", 2)[1]
    assert out.rstrip().endswith(delim)


# ---------------------------------------------------------------------------
# _ThreadedProcessHandle exception path
# ---------------------------------------------------------------------------

def test_threaded_handle_exec_raises_sets_returncode_1():
    def boom():
        raise RuntimeError("exec boom")
    h = _ThreadedProcessHandle(boom)
    assert h.wait(timeout=2) == 1
    assert isinstance(h._error, RuntimeError)
    assert h.poll() == 1


# ---------------------------------------------------------------------------
# init_session failure fallback
# ---------------------------------------------------------------------------

def test_init_session_failure_sets_snapshot_not_ready():
    class _RaisingEnv(_Env):
        def _run_bash(self, *a, **k):
            raise RuntimeError("spawn failed")
    env = _RaisingEnv(cwd="/w", timeout=5)
    env._snapshot_ready = True
    env.init_session()
    assert env._snapshot_ready is False  # fell back to bash -l per command


# ---------------------------------------------------------------------------
# _extract_cwd_from_output
# ---------------------------------------------------------------------------

def _env_with_marker(marker="MK"):
    env = _Env(cwd="/start", timeout=5)
    env._cwd_marker = marker
    return env


def test_extract_cwd_updates_and_strips_marker():
    env = _env_with_marker()
    result = {"output": "cmd output\nMK/new/dir MK\n"}
    # Note: marker pair around the cwd; spaces inside are stripped by .strip().
    env._extract_cwd_from_output(result)
    assert env.cwd == "/new/dir"
    assert "MK" not in result["output"]
    assert "cmd output" in result["output"]


def test_extract_cwd_single_marker_is_noop():
    env = _env_with_marker()
    result = {"output": "has one MK token only"}
    env._extract_cwd_from_output(result)
    assert env.cwd == "/start"                      # unchanged
    assert result["output"] == "has one MK token only"  # untouched


def test_extract_cwd_empty_path_leaves_cwd():
    env = _env_with_marker()
    result = {"output": "x\nMKMK\n"}
    env._extract_cwd_from_output(result)
    assert env.cwd == "/start"  # empty cwd_path → not assigned


def test_BUG008_extract_cwd_marker_collision_leaks_fragment():
    # BUG-008 (docs/known-bugs.md): if the command's own output contains the
    # session marker token, the rfind-based parser mis-pairs — it parses cwd
    # from the real pair but leaves the stray marker fragment in the output.
    env = _env_with_marker()
    result = {"output": "XMK\nMK/real MK\n"}
    env._extract_cwd_from_output(result)
    assert env.cwd == "/real"
    assert result["output"] == "XMK"  # stray "MK" leaked back to the model


# ---------------------------------------------------------------------------
# _wait_for_process — interrupt / timeout / KeyboardInterrupt / natural exit
# ---------------------------------------------------------------------------

def test_wait_natural_exit_returns_output_and_code(monkeypatch):
    monkeypatch.setattr(base, "is_interrupted", lambda: False)
    env = _Env(cwd="/w", timeout=5)
    proc = _FakeProc(polls=[0], output=("done",), returncode=0)  # poll() != None → no loop
    result = env._wait_for_process(proc, timeout=5)
    assert result == {"output": "done", "returncode": 0}


def test_wait_interrupt_kills_and_returns_130(monkeypatch):
    monkeypatch.setattr(base, "is_interrupted", lambda: True)
    env = _Env(cwd="/w", timeout=5)
    proc = _FakeProc(polls=[None], output=("partial",), returncode=0)
    result = env._wait_for_process(proc, timeout=5)
    assert result["returncode"] == 130
    assert "[Command interrupted]" in result["output"]
    assert proc.killed is True


def test_wait_timeout_kills_and_returns_124(monkeypatch):
    monkeypatch.setattr(base, "is_interrupted", lambda: False)
    env = _Env(cwd="/w", timeout=0)
    proc = _FakeProc(polls=[None, None, None], output=("",), returncode=0)
    result = env._wait_for_process(proc, timeout=0)
    assert result["returncode"] == 124
    assert "timed out" in result["output"]
    assert proc.killed is True


def test_wait_keyboardinterrupt_kills_and_reraises(monkeypatch):
    monkeypatch.setattr(base, "is_interrupted", lambda: False)

    def boom_sleep(_secs):
        raise KeyboardInterrupt()
    monkeypatch.setattr(base.time, "sleep", boom_sleep)
    env = _Env(cwd="/w", timeout=100)
    proc = _FakeProc(polls=[None, None], output=("",), returncode=0)
    with pytest.raises(KeyboardInterrupt):
        env._wait_for_process(proc, timeout=100)
    assert proc.killed is True  # orphan-prevention kill before re-raise


# ---------------------------------------------------------------------------
# _kill_process swallows expected errors
# ---------------------------------------------------------------------------

def test_kill_process_swallows_process_lookup_error():
    class _BadProc:
        def kill(self):
            raise ProcessLookupError("gone")
    env = _Env(cwd="/w", timeout=5)
    env._kill_process(_BadProc())  # must not raise
