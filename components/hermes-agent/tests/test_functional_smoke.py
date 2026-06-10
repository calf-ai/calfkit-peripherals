"""Functional smoke: the vendored engine actually runs locally — real exit codes and
snapshot-based statefulness (env vars + cwd persist across calls).
"""
import json
import shutil
import tempfile
from pathlib import Path


def _local_env(cwd):
    from calfkit_hermes._vendor.tools.environments.local import LocalEnvironment

    return LocalEnvironment(cwd=str(cwd), timeout=30)


def test_local_shell_returns_real_exit_codes(tmp_path):
    env = _local_env(tmp_path)
    try:
        ok = env.execute("echo hello-calfkit")
        assert ok["returncode"] == 0
        assert "hello-calfkit" in ok["output"]

        bad = env.execute("false")
        assert bad["returncode"] == 1
    finally:
        env.cleanup()


def test_local_shell_persists_env_and_cwd_across_calls(tmp_path):
    env = _local_env(tmp_path)
    try:
        # exported env var persists via the session snapshot
        env.execute("export CALFKIT_SMOKE=abc123")
        assert "abc123" in env.execute("echo $CALFKIT_SMOKE")["output"]

        # cwd persists via the cwd marker/file
        (tmp_path / "sub").mkdir()
        env.execute("cd sub")
        assert "/sub" in env.execute("pwd")["output"]
    finally:
        env.cleanup()


def _dispatch(name, args, task_id):
    from calfkit_hermes._vendor.tools.registry import registry, discover_builtin_tools

    discover_builtin_tools()
    return registry.dispatch(name, args, task_id=task_id)


def test_file_tools_write_read_patch_round_trip():
    # Not pytest's tmp_path: on macOS it lives under /private/var/..., which the
    # vendored sensitive-path guard (_SENSITIVE_PATH_PREFIXES) refuses to write to.
    tmp = Path(tempfile.mkdtemp(dir="/tmp", prefix="hermes-smoke-"))
    try:
        f = tmp / "f.txt"
        task = "smoke-files"

        _dispatch("write_file", {"path": str(f), "content": "alpha\nbeta\n"}, task)
        assert f.read_text() == "alpha\nbeta\n"

        read = json.loads(_dispatch("read_file", {"path": str(f)}, task))
        assert "alpha" in read["content"] and "beta" in read["content"]

        _dispatch("patch", {"mode": "replace", "path": str(f),
                            "old_string": "alpha", "new_string": "gamma"}, task)
        assert f.read_text() == "gamma\nbeta\n"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_process_tool_list_is_empty_on_fresh_session():
    result = json.loads(_dispatch("process", {"action": "list"}, "smoke-proc"))
    assert result["processes"] == []


def test_execute_code_ptc_bridges_to_a_tool(tmp_path):
    # PTC: a Python script that calls a vendored tool via the RPC bridge; only the
    # script's stdout returns. Proves the execute_code engine + handle_function_call shim.
    marker = tmp_path / "ptc.txt"
    code = (
        "from hermes_tools import terminal\n"
        f"terminal(command='echo ptc-ok > {marker}')\n"
        "print('script-done')\n"
    )
    out = _dispatch("execute_code", {"code": code}, "smoke-ptc")
    assert "script-done" in out
    assert marker.exists() and "ptc-ok" in marker.read_text()
