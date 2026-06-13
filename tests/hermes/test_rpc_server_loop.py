"""Behavioral tests for code_execution_tool._rpc_server_loop — the in-sandbox
tool-call bridge.

These exercise the security-relevant guards (allow-list, call-limit, terminal
blocked-param stripping = sandbox-escape prevention) plus malformed-frame
handling, handler-exception survival, and batched newline-framed messages.

The loop calls ``server_sock.accept()``, so the test feeds it a mocked server
whose ``accept()`` returns one end of a real ``socket.socketpair()``; the test
holds the other end as the client. No real subprocess or listener is created.
"""
import json
import socket
import threading
from unittest.mock import MagicMock

import pytest

from calfkit_tools.hermes._vendor.tools import code_execution_tool as cet

_HANDLER = "calfkit_tools.hermes._shims.model_tools.handle_function_call"


def _read_responses(sock, n, timeout=2.0):
    sock.settimeout(timeout)
    buf = b""
    out = []
    while len(out) < n:
        try:
            chunk = sock.recv(65536)
        except socket.timeout:
            break
        if not chunk:
            break
        buf += chunk
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            if line.strip():
                out.append(json.loads(line.decode()))
    return out


def _drive(monkeypatch, frames, *, allowed=frozenset({"terminal"}), max_calls=10,
           handler=None, n_responses=None, batched=False):
    """Run the RPC loop, send each frame, return (responses, captured_calls, counter, log)."""
    a, b = socket.socketpair()
    server = MagicMock()
    server.accept.return_value = (b, ("", 0))
    server.settimeout = lambda *_: None

    captured = []

    def default_handler(tool, args, task_id=None):
        captured.append((tool, dict(args)))
        return json.dumps({"ok": True, "tool": tool})

    monkeypatch.setattr(_HANDLER, handler or default_handler)
    log, counter = [], [0]
    th = threading.Thread(
        target=cet._rpc_server_loop,
        args=(server, "task1", log, counter, max_calls, allowed),
        daemon=True,
    )
    th.start()
    try:
        payloads = [f if isinstance(f, (bytes, bytearray)) else json.dumps(f).encode() for f in frames]
        if batched:
            a.sendall(b"\n".join(payloads) + b"\n")
        else:
            for p in payloads:
                a.sendall(p + b"\n")
        responses = _read_responses(a, n_responses if n_responses is not None else len(frames))
    finally:
        a.close()
        th.join(2)
    return responses, captured, counter, log


def test_allow_list_rejects_unavailable_tool(monkeypatch):
    responses, captured, counter, _ = _drive(
        monkeypatch, [{"tool": "web_search", "args": {}}], allowed=frozenset({"terminal"})
    )
    assert "not available in execute_code" in responses[0]["error"]
    assert "terminal" in responses[0]["error"]  # lists what IS available
    assert captured == []          # handler never dispatched
    assert counter[0] == 0         # not counted against the limit


def test_call_limit_blocks_after_max(monkeypatch):
    responses, captured, counter, _ = _drive(
        monkeypatch,
        [{"tool": "terminal", "args": {"command": "ls"}},
         {"tool": "terminal", "args": {"command": "pwd"}}],
        max_calls=1, n_responses=2,
    )
    assert "ok" in responses[0]                       # first dispatched
    assert "limit reached" in responses[1]["error"]   # second blocked
    assert len(captured) == 1                         # only one real dispatch
    assert counter[0] == 1


def test_terminal_blocked_params_are_stripped(monkeypatch):
    # Sandbox-escape guard: background/pty/notify_on_complete/watch_patterns must
    # never reach the real terminal handler from inside execute_code.
    responses, captured, _, _ = _drive(
        monkeypatch,
        [{"tool": "terminal", "args": {
            "command": "ls", "background": True, "pty": True,
            "notify_on_complete": 1, "watch_patterns": ["x"],
        }}],
    )
    assert responses[0]["ok"] is True
    assert captured == [("terminal", {"command": "ls"})]  # only command survives


def test_malformed_json_frame_is_typed_error(monkeypatch):
    responses, captured, counter, _ = _drive(monkeypatch, [b"{not valid json"])
    assert "Invalid RPC request" in responses[0]["error"]
    assert captured == []
    assert counter[0] == 0  # parse failures don't count


def test_handler_exception_is_caught_and_loop_survives(monkeypatch):
    def boom(tool, args, task_id=None):
        raise RuntimeError("handler boom")

    responses, _, counter, log = _drive(
        monkeypatch,
        [{"tool": "terminal", "args": {"command": "a"}},
         {"tool": "terminal", "args": {"command": "b"}}],
        handler=boom, n_responses=2,
    )
    # Both calls produced an error response (loop did NOT die on the first).
    assert len(responses) == 2
    assert all("handler boom" in r["error"] for r in responses)
    assert counter[0] == 2        # failed calls still increment the counter
    assert len(log) == 2          # observability log records both


def test_batched_frames_in_one_recv_all_processed(monkeypatch):
    responses, captured, _, _ = _drive(
        monkeypatch,
        [{"tool": "terminal", "args": {"command": "one"}},
         {"tool": "terminal", "args": {"command": "two"}}],
        n_responses=2, batched=True,
    )
    assert len(responses) == 2
    assert [c[1]["command"] for c in captured] == ["one", "two"]
