"""Behavioral tests for the ``gateway.status`` shim (``_pid_exists``).

This shim is a cross-platform "is this PID alive" check used by the
background-process registry's detached crash-recovery path
(``process_registry._pid_exists`` re-exports / wraps this). It is intentionally
careful to NOT signal the target process.

These tests exercise the REAL function body across every branch:

* the falsy-pid short-circuit,
* the ``psutil.pid_exists`` fast path (present, returns True/False),
* the ``except Exception`` fallthrough when psutil is absent OR raises, and
* the POSIX ``os.kill(pid, 0)`` fallback, including its
  ``ProcessLookupError`` / ``PermissionError`` / ``OSError`` arms.

Existing suites mock ``_pid_exists`` away; here we call it directly so the
fallback logic is actually covered. ``os.kill(pid, 0)`` has the same semantics
on the Linux CI box and the macOS dev box (POSIX), so the kill-based arms are
driven via patched ``os.kill`` to stay deterministic and side-effect free.
"""
import os
import sys

import pytest

from calfkit_tools.hermes._shims.gateway import status


# ---------------------------------------------------------------------------
# Falsy pid short-circuit: ``if not pid: return False``
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("falsy", [None, 0])
def test_falsy_pid_returns_false_without_touching_os(falsy, monkeypatch):
    """None / 0 return False before psutil or os.kill is ever consulted."""
    # If the short-circuit failed, these would blow up loudly instead of
    # silently returning the wrong answer.
    def _boom(*_args, **_kwargs):  # pragma: no cover - must not run
        raise AssertionError("os.kill must not be called for a falsy pid")

    monkeypatch.setattr(os, "kill", _boom)
    monkeypatch.setitem(sys.modules, "psutil", None)  # would ImportError if imported

    assert status._pid_exists(falsy) is False


# ---------------------------------------------------------------------------
# Real, un-mocked end-to-end behavior (no psutil/os.kill patching).
# These run whether or not psutil is installed and assert the true answer.
# ---------------------------------------------------------------------------
def test_live_self_pid_is_alive():
    """The current interpreter's own PID is unambiguously alive."""
    assert status._pid_exists(os.getpid()) is True


def test_dead_high_pid_is_not_alive():
    """A very high, almost-certainly-unused PID reports as not alive.

    Picks a PID that is free *right now* to avoid the small race of a real
    process happening to own a fixed constant.
    """
    pid = _find_free_pid()
    assert status._pid_exists(pid) is False


# ---------------------------------------------------------------------------
# psutil fast path: when present, its verdict is returned verbatim.
# ---------------------------------------------------------------------------
def test_psutil_present_true_is_passed_through(monkeypatch):
    fake = _FakePsutil(result=True)
    monkeypatch.setitem(sys.modules, "psutil", fake)
    # os.kill must not be reached on the fast path.
    monkeypatch.setattr(os, "kill", _never_call)

    assert status._pid_exists(4321) is True
    assert fake.calls == [4321]


def test_psutil_present_false_is_passed_through(monkeypatch):
    fake = _FakePsutil(result=False)
    monkeypatch.setitem(sys.modules, "psutil", fake)
    monkeypatch.setattr(os, "kill", _never_call)

    assert status._pid_exists(4321) is False
    assert fake.calls == [4321]


# ---------------------------------------------------------------------------
# psutil ABSENT -> ``except Exception`` -> os.kill(pid, 0) fallback.
# Forcing ``sys.modules['psutil'] = None`` makes ``import psutil`` raise
# ImportError (a subclass of Exception), which is exactly the fallthrough.
# ---------------------------------------------------------------------------
def test_psutil_absent_falls_back_to_oskill_alive(monkeypatch):
    """psutil missing + os.kill succeeds (no exception) -> alive."""
    monkeypatch.setitem(sys.modules, "psutil", None)
    seen = []
    monkeypatch.setattr(os, "kill", lambda pid, sig: seen.append((pid, sig)))

    assert status._pid_exists(1234) is True
    # The probe must be the no-op signal 0, never a real signal.
    assert seen == [(1234, 0)]


def test_psutil_absent_oskill_process_lookup_error_is_dead(monkeypatch):
    """ProcessLookupError from os.kill -> not alive (False)."""
    monkeypatch.setitem(sys.modules, "psutil", None)
    monkeypatch.setattr(os, "kill", _raiser(ProcessLookupError()))

    assert status._pid_exists(1234) is False


def test_psutil_absent_oskill_permission_error_is_alive(monkeypatch):
    """PermissionError means the PID exists but is owned by someone else -> alive."""
    monkeypatch.setitem(sys.modules, "psutil", None)
    monkeypatch.setattr(os, "kill", _raiser(PermissionError()))

    assert status._pid_exists(1234) is True


def test_psutil_absent_oskill_generic_oserror_is_dead(monkeypatch):
    """A generic OSError (e.g. ESRCH-shaped / EINVAL) is treated as not alive."""
    monkeypatch.setitem(sys.modules, "psutil", None)
    monkeypatch.setattr(os, "kill", _raiser(OSError("boom")))

    assert status._pid_exists(1234) is False


def test_oskill_exception_arm_ordering_lookup_beats_oserror(monkeypatch):
    """ProcessLookupError is an OSError subclass; the specific arm must win.

    Guards against someone reordering the except-blocks so that the broad
    ``except OSError`` swallows the lookup case. Both arms return False here, so
    to make the *ordering* observable we instead assert PermissionError (also an
    OSError subclass) is routed to its True arm, not the False OSError arm.
    """
    monkeypatch.setitem(sys.modules, "psutil", None)

    monkeypatch.setattr(os, "kill", _raiser(ProcessLookupError()))
    assert status._pid_exists(1234) is False  # specific arm -> False

    monkeypatch.setattr(os, "kill", _raiser(PermissionError()))
    assert status._pid_exists(1234) is True  # specific arm -> True (not OSError->False)


# ---------------------------------------------------------------------------
# psutil PRESENT but raising -> ``except Exception`` -> os.kill fallback.
# Covers the "psutil installed but pid_exists explodes" half of the except arm,
# distinct from the import-failure half above.
# ---------------------------------------------------------------------------
def test_psutil_raises_falls_back_to_oskill(monkeypatch):
    fake = _FakePsutil(exc=RuntimeError("psutil internal error"))
    monkeypatch.setitem(sys.modules, "psutil", fake)
    seen = []
    monkeypatch.setattr(os, "kill", lambda pid, sig: seen.append((pid, sig)))

    assert status._pid_exists(777) is True
    assert fake.calls == [777]
    assert seen == [(777, 0)]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _never_call(*_args, **_kwargs):  # pragma: no cover - asserted unreached
    raise AssertionError("os.kill should not be called on the psutil fast path")


def _raiser(exc):
    def _kill(_pid, _sig):
        raise exc

    return _kill


class _FakePsutil:
    """Minimal stand-in for the ``psutil`` module's ``pid_exists`` surface."""

    def __init__(self, *, result=None, exc=None):
        self._result = result
        self._exc = exc
        self.calls = []

    def pid_exists(self, pid):
        self.calls.append(pid)
        if self._exc is not None:
            raise self._exc
        return self._result


def _find_free_pid():
    """Return a positive PID that no process currently owns.

    Scans downward from the platform max so we land on a high, unused value
    rather than a hard-coded constant that might race a real process.
    """
    candidate = 2**22  # well above typical pid_max on Linux/macOS
    for _ in range(10000):
        try:
            os.kill(candidate, 0)
        except ProcessLookupError:
            return candidate  # nobody home -> safe to use
        except OSError:
            # In use, or not probeable; keep walking down.
            pass
        candidate -= 1
    # Extremely unlikely; fall back to a high constant.
    return 2**22
