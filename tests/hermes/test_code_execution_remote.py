"""Behavioral tests for the REMOTE (file-based RPC) execution path of
``code_execution_tool`` — ``_execute_remote`` and ``_rpc_poll_loop``.

This path is dormant in the node (the default terminal backend is ``local``,
which takes the UDS path in ``execute_code``), but it is the largest uncovered
block in the module and is unit-testable by driving it with a *fake env*: a
stand-in for the terminal backend whose ``.execute(cmd, ...)`` returns scripted
``{"output": ..., "returncode": ...}`` dicts, exactly the contract the real
Docker/SSH/Modal/Daytona environments expose.

These complement (do not duplicate) two existing suites:
  * ``test_vendored_code_execution.py`` has ONE remote case
    (``test_execute_remote_uses_backend_temp_dir_for_sandbox``) — we reuse its
    FakeEnv shape but cover different behavior (python-missing, exit-code→status
    mapping, tool-set fallback, output post-processing).
  * ``test_rpc_server_loop.py`` covers the LOCAL UDS ``_rpc_server_loop`` — here
    we cover the parallel REMOTE bridge ``_rpc_poll_loop`` (allow-list, call
    limit, malformed-request handling) which talks over files via env.execute().

The tool dispatcher is patched at
``calfkit_tools.hermes._shims.model_tools.handle_function_call`` (imported by
name inside the loop, so patching the module attribute intercepts it). The
autouse ``_hermetic_environment`` fixture (conftest.py) scrubs leaky env vars.
"""
import base64
import json
import re
import threading
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

from calfkit_tools.hermes._vendor.tools.code_execution_tool import (
    SANDBOX_ALLOWED_TOOLS,
    _execute_remote,
    _rpc_poll_loop,
)

_MOD = "calfkit_tools.hermes._vendor.tools.code_execution_tool"
_HANDLER = "calfkit_tools.hermes._shims.model_tools.handle_function_call"


# ===========================================================================
# _execute_remote — fake terminal backend
# ===========================================================================

class RemoteFakeEnv:
    """A scripted stand-in for the terminal backend used by ``_execute_remote``.

    ``_execute_remote`` issues a fixed command sequence via ``env.execute()``:
        1. ``command -v python3 ... && echo OK``   (availability probe)
        2. ``mkdir -p <dir>/rpc``
        3. (file ship: ``echo '<b64>' | base64 -d > ...``)  — unless patched out
        4. ``cd <dir> && <env> python3 script.py``  (the user's script)
        5. ``rm -rf <dir>``                          (cleanup, in finally)

    We key responses off command substrings and let the test parameterise the
    python probe and the script run.
    """

    def __init__(self, *, python_ok=True, script_output="done\n", script_rc=0,
                 temp_dir="/tmp"):
        self.python_ok = python_ok
        self.script_output = script_output
        self.script_rc = script_rc
        self._temp_dir = temp_dir
        self.commands = []  # list[(command, cwd, timeout)]

    def get_temp_dir(self):
        return self._temp_dir

    def execute(self, command, cwd=None, timeout=None):
        self.commands.append((command, cwd, timeout))
        if "command -v python3" in command:
            return {"output": "OK\n" if self.python_ok else "", "returncode": 0}
        if "python3 script.py" in command:
            return {"output": self.script_output, "returncode": self.script_rc}
        # mkdir / file-ship / cleanup all return benign empty output.
        return {"output": "", "returncode": 0}

    def shipped(self, basename):
        """Decode the content shipped to ``<dir>/<basename>`` via base64 echo.

        Returns the decoded UTF-8 text, or None if that file was never shipped.
        The real ``_ship_file_to_remote`` runs ``echo '<b64>' | base64 -d > path``;
        we recover ``content`` by base64-decoding the single-quoted token.
        """
        for cmd, _, _ in self.commands:
            if "> " in cmd and cmd.rstrip().endswith(basename) and "base64 -d" in cmd:
                m = re.search(r"echo '([A-Za-z0-9+/=]+)'", cmd)
                if m:
                    return base64.b64decode(m.group(1)).decode("utf-8")
        return None


def _run_remote(env, *, code="print('hi')", task_id="task-1",
                enabled_tools=("terminal",), ship=False, cfg=None):
    """Drive ``_execute_remote`` against ``env`` with the RPC poll thread stubbed.

    ``ship=False`` patches out ``_ship_file_to_remote`` (most tests don't care
    about the shipped bytes); ``ship=True`` lets the real ship run so the test
    can inspect ``env.shipped(...)``. The poll thread is always a MagicMock so no
    background ``env.execute`` happens and the test stays deterministic.
    """
    cfg = cfg or {"timeout": 30, "max_tool_calls": 5}
    fake_thread = MagicMock()
    with ExitStack() as es:
        es.enter_context(patch(f"{_MOD}._load_config", return_value=cfg))
        es.enter_context(patch(f"{_MOD}._get_or_create_env", return_value=(env, "docker")))
        es.enter_context(patch(f"{_MOD}.threading.Thread", return_value=fake_thread))
        es.enter_context(patch(_HANDLER, return_value=json.dumps({"ok": True})))
        if not ship:
            es.enter_context(patch(f"{_MOD}._ship_file_to_remote"))
        return json.loads(_execute_remote(code, task_id, list(enabled_tools)))


def test_remote_python3_missing_returns_error_without_running_script():
    """If ``command -v python3`` doesn't print OK, _execute_remote bails with a
    typed error naming the backend type, makes 0 tool calls, and NEVER ships or
    runs the user's script."""
    env = RemoteFakeEnv(python_ok=False)
    result = _run_remote(env, code="print('should not run')")

    assert result["status"] == "error"
    # Message: "Python 3 is not available in the <env_type> terminal environment."
    assert "Python 3 is not available in the docker" in result["error"]
    assert result["tool_calls_made"] == 0
    # The probe ran, but the script never did (no python3 script.py command).
    assert any("command -v python3" in c for c, _, _ in env.commands)
    assert not any("python3 script.py" in c for c, _, _ in env.commands)


def test_remote_exit_code_124_maps_to_timeout_with_clock_suffix():
    """returncode 124 (GNU timeout) → status 'timeout', and the ⏰ + 'timed out'
    notice is appended to output so the model always surfaces it (#10807)."""
    env = RemoteFakeEnv(script_output="partial work\n", script_rc=124)
    result = _run_remote(env, cfg={"timeout": 7, "max_tool_calls": 5})

    assert result["status"] == "timeout"
    assert "timed out" in result.get("error", "")
    assert "⏰" in result["output"]          # ⏰
    assert "timed out after 7s" in result["output"]
    assert "partial work" in result["output"]    # original stdout preserved


def test_remote_exit_code_124_timeout_suffix_when_no_output():
    """Timeout with empty stdout still yields a ⏰ message (not an empty output)."""
    env = RemoteFakeEnv(script_output="", script_rc=124)
    result = _run_remote(env)
    assert result["status"] == "timeout"
    assert result["output"].startswith("⏰")


def test_remote_exit_code_130_maps_to_interrupted():
    """returncode 130 (SIGINT) → status 'interrupted' with the interrupt notice."""
    env = RemoteFakeEnv(script_output="some output\n", script_rc=130)
    result = _run_remote(env)
    assert result["status"] == "interrupted"
    assert "interrupted" in result["output"]
    assert "some output" in result["output"]


def test_remote_nonzero_exit_code_maps_to_error():
    """A generic non-zero exit (e.g. 2) → status 'error' with 'exited with code N'.
    Note: unlike the LOCAL path, the remote path has no stderr channel, so the
    error string is the exit-code sentence, not a traceback."""
    env = RemoteFakeEnv(script_output="ran but failed\n", script_rc=2)
    result = _run_remote(env)
    assert result["status"] == "error"
    assert result["error"] == "Script exited with code 2"
    assert "ran but failed" in result["output"]


def test_remote_success_exit_zero():
    """The happy path is pinned alongside the failure modes: rc 0 → success."""
    env = RemoteFakeEnv(script_output="hello world\n", script_rc=0)
    result = _run_remote(env)
    assert result["status"] == "success"
    assert "hello world" in result["output"]
    assert result["tool_calls_made"] == 0


def test_remote_empty_enabled_tools_ships_all_sandbox_tools():
    """enabled_tools=[] → the sandbox falls back to the FULL SANDBOX_ALLOWED_TOOLS
    set, and the *actually-shipped* hermes_tools.py (file transport) contains a
    stub for every one of them. We assert against the decoded shipped bytes, not
    a generator spy, to pin the whole ship pipeline."""
    env = RemoteFakeEnv(script_output="ok\n", script_rc=0)
    result = _run_remote(env, enabled_tools=[], ship=True)
    assert result["status"] == "success"

    tools_src = env.shipped("hermes_tools.py")
    assert tools_src is not None, "hermes_tools.py was never shipped"
    for tool in SANDBOX_ALLOWED_TOOLS:
        assert f"def {tool}(" in tools_src, f"missing stub for {tool}"
    # File transport, not UDS: no socket import, uses the file-RPC marker.
    assert "HERMES_RPC_DIR" in tools_src
    assert "AF_UNIX" not in tools_src
    # The user's script is shipped verbatim too.
    assert env.shipped("script.py") is not None


def test_remote_subset_enabled_tools_ships_only_intersection():
    """A non-empty enabled set ships only (SANDBOX_ALLOWED_TOOLS ∩ enabled)."""
    env = RemoteFakeEnv(script_output="ok\n", script_rc=0)
    _run_remote(env, enabled_tools=["terminal", "read_file", "vision_analyze"], ship=True)
    src = env.shipped("hermes_tools.py")
    assert "def terminal(" in src
    assert "def read_file(" in src
    assert "def web_search(" not in src      # not in the enabled set
    assert "def vision_analyze(" not in src  # not a sandbox tool at all


def test_remote_output_truncated_ansi_stripped_and_secrets_redacted():
    """Large remote stdout (>50KB) is head/tail truncated, ANSI escapes are
    stripped, and secrets are redacted — all in the same post-processing block
    the local path uses. We plant the ANSI + secrets inside the first ~1KB so
    they land in the preserved HEAD (40% of 50KB)."""
    secret_sk = "sk-ABCDEFGH1234567890wxyz"   # 25 chars; >=18 triggers redaction
    secret_akia = "AKIAIOSFODNN7EXAMPLE"
    ansi_red = "\x1b[31m"
    ansi_reset = "\x1b[0m"
    header = (
        f"{ansi_red}START{ansi_reset} "
        f"leaked openai key {secret_sk} and aws {secret_akia} done\n"
    )
    body = "X" * 60_000  # push total well past MAX_STDOUT_BYTES (50_000)
    env = RemoteFakeEnv(script_output=header + body, script_rc=0)

    result = _run_remote(env)
    out = result["output"]

    # Truncation notice present with char-count wording.
    assert "OUTPUT TRUNCATED" in out
    assert "chars omitted" in out
    assert "total" in out
    # ANSI escape sequences removed.
    assert "\x1b[" not in out
    assert "START" in out  # the text between escapes survives
    # Secrets redacted: the unique middle of each token is gone, prefix remains.
    assert "DEFGH1234567890" not in out   # sk- token middle
    assert secret_sk not in out
    assert "IOSFODNN7" not in out         # AKIA token middle
    assert secret_akia not in out
    assert "..." in out                   # redaction mask marker


def test_remote_injects_timezone_into_script_env_prefix(monkeypatch):
    """When HERMES_TIMEZONE is set, _execute_remote prepends ``TZ=<tz>`` to the
    script's env prefix so datetime.now() in the sandbox reflects the user's
    wall clock. (The hermetic fixture scrubs HERMES_* by default, so the other
    tests already pin the no-TZ branch.)"""
    monkeypatch.setenv("HERMES_TIMEZONE", "America/New_York")
    env = RemoteFakeEnv(script_output="ok\n", script_rc=0)
    result = _run_remote(env)
    assert result["status"] == "success"

    run_cmd = next(c for c, _, _ in env.commands if "python3 script.py" in c)
    assert "TZ=America/New_York" in run_cmd
    assert "HERMES_RPC_DIR=" in run_cmd  # base prefix still present


def test_remote_env_execute_exception_is_caught_as_error_json():
    """If a backend command raises, _execute_remote returns a typed error JSON
    (status=error, the exception text) rather than propagating — and still runs
    cleanup. We raise on the python probe so failure is deterministic."""
    class BoomEnv(RemoteFakeEnv):
        def execute(self, command, cwd=None, timeout=None):
            self.commands.append((command, cwd, timeout))
            if "command -v python3" in command:
                raise RuntimeError("backend exploded")
            return {"output": "", "returncode": 0}

    env = BoomEnv()
    result = _run_remote(env)
    assert result["status"] == "error"
    assert "backend exploded" in result["error"]
    assert result["tool_calls_made"] == 0
    # finally-block cleanup still attempted.
    assert any("rm -rf" in c for c, _, _ in env.commands)


# ===========================================================================
# _rpc_poll_loop — the remote tool-call bridge (file-based)
# ===========================================================================

class PollFakeEnv:
    """Scripted backend for ``_rpc_poll_loop``.

    The loop polls the remote filesystem via env.execute():
        ``ls -1 <dir>/req_* ...``     -> newline-joined request file paths
        ``cat <req_file>``            -> the request JSON (or garbage)
        ``echo '<b64>'|base64 -d > res...`` (response write — on dispatch/guard)
        ``rm -f <req_file>``          (request removal)

    To run exactly one productive cycle without an infinite loop, we serve the
    request listing on the FIRST ``ls`` and return empty thereafter, and we set
    ``stop_event`` as soon as any ``rm -f`` of a ``req_`` file is observed (every
    branch — dispatch, allow-list, limit, malformed — ends by removing the
    request file). ``th.join()`` in the driver then proves the loop terminated.
    """

    def __init__(self, stop_event, req_payloads, *, stop_after_rm=1):
        """``req_payloads``: ordered dict {req_path: raw_cat_output_str}.

        ``stop_after_rm``: stop the loop after this many request-file removals,
        so a single ``ls`` cycle carrying N requests can be driven to completion
        before the loop is told to exit.
        """
        self.stop_event = stop_event
        self.req_payloads = dict(req_payloads)
        self._stop_after_rm = stop_after_rm
        self._rm_count = 0
        self._listed = False
        self.commands = []  # list[(command, cwd, timeout)]
        self.responses = {}  # res_file -> decoded response text

    def execute(self, command, cwd=None, timeout=None):
        self.commands.append((command, cwd, timeout))

        if command.startswith("ls -1"):
            if self._listed:
                return {"output": "", "returncode": 0}
            self._listed = True
            return {"output": "\n".join(self.req_payloads) + "\n", "returncode": 0}

        if command.startswith("cat "):
            for path, payload in self.req_payloads.items():
                if path in command:
                    return {"output": payload, "returncode": 0}
            return {"output": "", "returncode": 0}

        # Response write: "echo '<b64>' | base64 -d > <res>.tmp && mv ..."
        if "base64 -d" in command and "/res_" in command:
            m = re.search(r"echo '([A-Za-z0-9+/=]+)'", command)
            m_res = re.search(r"> (\S+)\.tmp", command)
            if m and m_res:
                self.responses[m_res.group(1)] = base64.b64decode(
                    m.group(1)).decode("utf-8")
            return {"output": "", "returncode": 0}

        if command.startswith("rm -f") and "/req_" in command:
            # Every code path ends by removing the request file. Stop once the
            # expected number of requests for this cycle have been handled.
            self._rm_count += 1
            if self._rm_count >= self._stop_after_rm:
                self.stop_event.set()
            return {"output": "", "returncode": 0}

        return {"output": "", "returncode": 0}


def _drive_poll(monkeypatch, req_payloads, *, allowed=frozenset({"terminal"}),
                max_calls=10, counter=None, handler=None, timeout=3.0,
                stop_after_rm=1):
    """Run ``_rpc_poll_loop`` for one request cycle and return (env, captured).

    ``captured`` records dispatched (tool, args) so allow-list/limit tests can
    assert the handler was NOT invoked. ``stop_after_rm`` lets a cycle carrying
    multiple requests finish before the loop exits.
    """
    stop_event = threading.Event()
    env = PollFakeEnv(stop_event, req_payloads, stop_after_rm=stop_after_rm)
    captured = []

    def default_handler(tool, args, task_id=None):
        captured.append((tool, dict(args)))
        return json.dumps({"ok": True, "tool": tool})

    monkeypatch.setattr(_HANDLER, handler or default_handler)

    log, ctr = [], counter if counter is not None else [0]
    th = threading.Thread(
        target=_rpc_poll_loop,
        args=(env, "/sbx/rpc", "task1", log, ctr, max_calls, allowed, stop_event),
        daemon=True,
    )
    th.start()
    th.join(timeout)
    # Belt-and-suspenders: if anything went wrong, don't leave a live thread.
    stop_event.set()
    assert not th.is_alive(), "poll loop did not terminate (possible infinite loop)"
    return env, captured, ctr, log


def _req(seq, tool, args):
    return json.dumps({"tool": tool, "args": args, "seq": seq})


def test_poll_dispatches_allowed_tool_and_writes_response(monkeypatch):
    """Happy path for the remote bridge: an allowed tool is dispatched through
    handle_function_call, the result is written back as res_<seq>, the request
    file is removed, and the call is counted + logged."""
    env, captured, ctr, log = _drive_poll(
        monkeypatch, {"/sbx/rpc/req_000001": _req(1, "terminal", {"command": "ls"})},
    )

    assert captured == [("terminal", {"command": "ls"})]
    assert ctr[0] == 1
    assert len(log) == 1 and log[0]["tool"] == "terminal"
    # Response written to the seq-derived path with the handler's JSON.
    written = env.responses.get("/sbx/rpc/res_000001")
    assert written is not None
    assert json.loads(written) == {"ok": True, "tool": "terminal"}


def test_poll_processes_multiple_requests_in_seq_order(monkeypatch):
    """A single poll listing can carry several request files; the loop sorts
    them and dispatches each, counting every call and writing a response keyed
    by its own seq. Exercises the inner per-request for-loop more than once."""
    env, captured, ctr, log = _drive_poll(
        monkeypatch,
        {
            "/sbx/rpc/req_000001": _req(1, "terminal", {"command": "first"}),
            "/sbx/rpc/req_000002": _req(2, "read_file", {"path": "f"}),
        },
        allowed=frozenset({"terminal", "read_file"}),
        stop_after_rm=2,
    )
    assert captured == [("terminal", {"command": "first"}),
                        ("read_file", {"path": "f"})]
    assert ctr[0] == 2
    assert len(log) == 2
    assert json.loads(env.responses["/sbx/rpc/res_000001"])["tool"] == "terminal"
    assert json.loads(env.responses["/sbx/rpc/res_000002"])["tool"] == "read_file"


def test_poll_malformed_request_is_removed_without_dispatch(monkeypatch):
    """A request file whose contents aren't valid JSON is logged, ``rm -f``'d to
    avoid an infinite retry, and skipped — the handler is never called and the
    loop does not hang (a single stop_event cycle drives it)."""
    env, captured, ctr, log = _drive_poll(
        monkeypatch, {"/sbx/rpc/req_000007": "this is not json {{{"},
    )
    assert captured == []          # never dispatched
    assert ctr[0] == 0             # not counted
    assert log == []               # not logged as a tool call
    # The bad request file was removed.
    assert any(c.startswith("rm -f") and "/sbx/rpc/req_000007" in c
               for c, _, _ in env.commands)
    # No response file was written for a malformed request.
    assert env.responses == {}


def test_poll_disallowed_tool_writes_error_response_without_dispatch(monkeypatch):
    """The remote bridge enforces the same allow-list as the UDS server: a tool
    not in ``allowed`` gets an error response written back (listing what IS
    available) and is never dispatched or counted."""
    env, captured, ctr, log = _drive_poll(
        monkeypatch,
        {"/sbx/rpc/req_000003": _req(3, "web_search", {"query": "x"})},
        allowed=frozenset({"terminal", "read_file"}),
    )
    assert captured == []
    assert ctr[0] == 0
    resp = env.responses.get("/sbx/rpc/res_000003")
    assert resp is not None
    payload = json.loads(resp)
    assert "not available in execute_code" in payload["error"]
    # Lists the available tools (sorted).
    assert "read_file" in payload["error"] and "terminal" in payload["error"]


def test_poll_call_limit_writes_error_response_without_dispatch(monkeypatch):
    """When the shared counter is already at the max, the bridge refuses further
    dispatch and writes a 'limit reached' error response (no handler call)."""
    env, captured, ctr, log = _drive_poll(
        monkeypatch,
        {"/sbx/rpc/req_000005": _req(5, "terminal", {"command": "ls"})},
        max_calls=3,
        counter=[3],  # already at the limit
    )
    assert captured == []
    assert ctr[0] == 3  # unchanged — blocked call doesn't increment
    resp = env.responses.get("/sbx/rpc/res_000005")
    assert resp is not None
    assert "Tool call limit reached (3)" in json.loads(resp)["error"]


def test_poll_strips_blocked_terminal_params_before_dispatch(monkeypatch):
    """Sandbox-escape guard parity with the UDS server: background/pty/
    notify_on_complete/watch_patterns are stripped from terminal args before the
    real handler sees them, on the remote bridge too."""
    env, captured, ctr, log = _drive_poll(
        monkeypatch,
        {"/sbx/rpc/req_000009": _req(9, "terminal", {
            "command": "ls", "background": True, "pty": True,
            "notify_on_complete": 1, "watch_patterns": ["x"],
        })},
    )
    assert captured == [("terminal", {"command": "ls"})]  # only command survives
    assert ctr[0] == 1


def test_poll_handler_exception_is_caught_and_written_back(monkeypatch):
    """If the dispatched handler raises, the bridge catches it, writes a
    tool_error response back, still counts the call, and the loop survives."""
    def boom(tool, args, task_id=None):
        raise RuntimeError("handler boom")

    env, captured, ctr, log = _drive_poll(
        monkeypatch,
        {"/sbx/rpc/req_000011": _req(11, "terminal", {"command": "x"})},
        handler=boom,
    )
    # Counter still increments for a failed dispatch (matches UDS behavior).
    assert ctr[0] == 1
    assert len(log) == 1
    resp = env.responses.get("/sbx/rpc/res_000011")
    assert resp is not None
    assert "handler boom" in json.loads(resp)["error"]


def test_poll_stop_event_set_before_start_does_no_work(monkeypatch):
    """If stop_event is already set, the loop body never runs — it doesn't even
    list the remote dir. Guards against a spurious first poll on shutdown."""
    stop_event = threading.Event()
    stop_event.set()
    env = PollFakeEnv(stop_event, {"/sbx/rpc/req_000001": _req(1, "terminal", {})})
    monkeypatch.setattr(_HANDLER, lambda *a, **k: json.dumps({"ok": True}))

    th = threading.Thread(
        target=_rpc_poll_loop,
        args=(env, "/sbx/rpc", "t", [], [0], 10, frozenset({"terminal"}), stop_event),
        daemon=True,
    )
    th.start()
    th.join(2.0)
    assert not th.is_alive()
    assert env.commands == []  # no ls, no cat — nothing polled
