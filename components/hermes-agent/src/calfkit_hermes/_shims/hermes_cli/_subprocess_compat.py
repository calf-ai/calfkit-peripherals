"""Shim for hermes-agent ``hermes_cli._subprocess_compat`` (faithful, tiny).

``windows_hide_flags`` returns Win32 creationflags that hide a child console window
(0 on non-Windows). Must return an int (it is passed unconditionally as Popen
``creationflags=`` in process_registry.py), never None.
"""
import platform

_IS_WINDOWS = platform.system() == "Windows"


def windows_hide_flags() -> int:
    if not _IS_WINDOWS:
        return 0
    import subprocess

    return getattr(subprocess, "CREATE_NO_WINDOW", 0)
