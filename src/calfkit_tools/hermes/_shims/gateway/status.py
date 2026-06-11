"""Shim for hermes-agent ``gateway.status`` (faithful, tiny).

``_pid_exists`` is a cross-platform "is this PID alive" check that does NOT signal the
target (critical on Windows, where ``os.kill(pid, 0)`` is not a no-op). Used by the
background-process registry's detached crash-recovery path.
"""
import os
from typing import Optional


def _pid_exists(pid: Optional[int]) -> bool:
    if not pid:
        return False
    try:
        import psutil

        return psutil.pid_exists(pid)
    except Exception:
        pass
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True
