"""Behavioral tests for the process_registry control surface.

Targets the uncovered stdin/kill/count/format helpers with fake sessions (no
real subprocess needed — the existing test_vendored_process_registry.py already
covers real spawns). Pins BUG-012 (PTY write byte-count), BUG-013 (kill records
-15 even for force-SIGKILL), and BUG-015 (_clean_shell_noise over-broad strip).
"""
from unittest.mock import MagicMock

import pytest

from calfkit_tools.hermes._vendor.tools.process_registry import (
    ProcessRegistry,
    ProcessSession,
    format_uptime_short,
)


class _FakePty:
    def __init__(self):
        self.written = b""
        self.terminated = None
        self.eof = False

    def write(self, data):
        self.written += data

    def terminate(self, force=False):
        self.terminated = force


class _FakeStdin:
    def __init__(self):
        self.data = ""
        self.flushed = False

    def write(self, d):
        self.data += d

    def flush(self):
        self.flushed = True


class _FakePopen:
    def __init__(self):
        self.stdin = _FakeStdin()


@pytest.fixture
def reg(monkeypatch):
    r = ProcessRegistry()
    # Isolate the kill/move side effect (checkpoint write) from the unit under test.
    monkeypatch.setattr(r, "_write_checkpoint", lambda: None)
    return r


def _add(reg, **kw):
    sess = ProcessSession(id=kw.pop("id", "proc_x"), command=kw.pop("command", "sleep 30"))
    for k, v in kw.items():
        setattr(sess, k, v)
    reg._running[sess.id] = sess
    return sess


# ---------------------------------------------------------------------------
# format_uptime_short / _clean_shell_noise
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "secs, expected",
    [(-5, "0s"), (0, "0s"), (59, "59s"), (60, "1m 0s"), (3599, "59m 59s"), (3600, "1h 0m"), (3661, "1h 1m")],
)
def test_format_uptime_short_boundaries(secs, expected):
    assert format_uptime_short(secs) == expected


def test_clean_shell_noise_strips_known_warnings():
    out = ProcessRegistry._clean_shell_noise(
        "bash: cannot set terminal process group\nreal output"
    )
    assert out == "real output"


def test_BUG015_clean_shell_noise_strips_legitimate_first_line():
    # BUG-015 (docs/known-bugs.md): the match is a substring-anywhere, not
    # anchored — a legitimate first line that merely *contains* a noise phrase
    # is silently dropped.
    out = ProcessRegistry._clean_shell_noise("no job control in this shell")
    assert out == ""  # legitimate single-line output stripped entirely


# ---------------------------------------------------------------------------
# write_stdin / submit_stdin
# ---------------------------------------------------------------------------

def test_write_stdin_not_found(reg):
    assert reg.write_stdin("nope", "x")["status"] == "not_found"


def test_write_stdin_already_exited(reg):
    _add(reg, id="p1", exited=True)
    r = reg.write_stdin("p1", "x")
    assert r["status"] == "already_exited"


def test_write_stdin_popen_stdin_unavailable(reg):
    _add(reg, id="p2", process=None)  # DEVNULL/closed stdin path
    r = reg.write_stdin("p2", "x")
    assert r["status"] == "error"
    assert "stdin not available" in r["error"]


def test_write_stdin_popen_writes_and_flushes(reg):
    sess = _add(reg, id="p3", process=_FakePopen())
    r = reg.write_stdin("p3", "hello")
    assert r == {"status": "ok", "bytes_written": 5}
    assert sess.process.stdin.data == "hello"
    assert sess.process.stdin.flushed is True


def test_submit_stdin_appends_newline(reg):
    sess = _add(reg, id="p4", process=_FakePopen())
    reg.submit_stdin("p4", "cmd")
    assert sess.process.stdin.data == "cmd\n"


def test_BUG012_pty_write_stdin_reports_char_count_not_bytes(reg):
    # BUG-012: the PTY path encodes to UTF-8 but returns len(data) (char count),
    # so multibyte input reports the wrong bytes_written.
    pty = _FakePty()
    _add(reg, id="p5", _pty=pty)
    r = reg.write_stdin("p5", "é")  # 1 char, 2 bytes in UTF-8
    assert r == {"status": "ok", "bytes_written": 1}  # char count (the bug)
    assert pty.written == "é".encode("utf-8")          # 2 bytes actually written


# ---------------------------------------------------------------------------
# kill_process branches
# ---------------------------------------------------------------------------

def test_kill_not_found(reg):
    assert reg.kill_process("nope")["status"] == "not_found"


def test_kill_already_exited(reg):
    _add(reg, id="k0", exited=True, exit_code=7)
    r = reg.kill_process("k0")
    assert r["status"] == "already_exited"
    assert r["exit_code"] == 7


def test_kill_env_ref_sandbox(reg):
    env = MagicMock()
    sess = _add(reg, id="k1", task_id="t1", env_ref=env, pid=999)
    r = reg.kill_process("k1")
    assert r["status"] == "killed"
    env.execute.assert_called_once()
    assert sess.exited is True


def test_kill_detached_host_pid_dead_is_already_exited(reg, monkeypatch):
    monkeypatch.setattr(reg, "_is_host_pid_alive", lambda pid: False)
    _add(reg, id="k2", detached=True, pid_scope="host", pid=424242)
    r = reg.kill_process("k2")
    assert r["status"] == "already_exited"


def test_kill_recovered_unkillable_returns_error(reg):
    # No pty / process / env_ref and not detached → cannot be killed.
    _add(reg, id="k3", detached=False)
    r = reg.kill_process("k3")
    assert r["status"] == "error"
    assert "cannot be killed" in r["error"]


def test_BUG013_pty_kill_records_sigterm_for_force_sigkill(reg):
    # BUG-013: PTY terminate(force=True) sends SIGKILL (-9), but the recorded
    # exit_code is hardcoded -15 (SIGTERM).
    pty = _FakePty()
    sess = _add(reg, id="k4", _pty=pty)
    r = reg.kill_process("k4")
    assert r["status"] == "killed"
    assert pty.terminated is True          # force=True (SIGKILL) was sent
    assert sess.exit_code == -15           # but -15/SIGTERM is recorded (the bug)


# ---------------------------------------------------------------------------
# count_running / kill_all
# ---------------------------------------------------------------------------

def test_count_running(reg):
    assert reg.count_running() == 0
    _add(reg, id="c1")
    _add(reg, id="c2")
    assert reg.count_running() == 2


def test_kill_all_filters_by_task_id(reg):
    env = MagicMock()
    _add(reg, id="a1", task_id="t1", env_ref=env, pid=1)
    _add(reg, id="a2", task_id="t1", env_ref=env, pid=2)
    t2 = _add(reg, id="b1", task_id="t2", env_ref=env, pid=3)
    killed = reg.kill_all("t1")
    assert killed == 2
    assert t2.exited is False  # t2 untouched


# ---------------------------------------------------------------------------
# _env_temp_dir
# ---------------------------------------------------------------------------

def test_env_temp_dir_absolute(reg):
    env = MagicMock()
    env.get_temp_dir.return_value = "/sandbox/tmp/"
    assert ProcessRegistry._env_temp_dir(env) == "/sandbox/tmp"


def test_env_temp_dir_non_absolute_falls_back(reg):
    env = MagicMock()
    env.get_temp_dir.return_value = "relative/path"
    assert ProcessRegistry._env_temp_dir(env) == "/tmp"


def test_env_temp_dir_raises_falls_back(reg):
    env = MagicMock()
    env.get_temp_dir.side_effect = RuntimeError("boom")
    assert ProcessRegistry._env_temp_dir(env) == "/tmp"
