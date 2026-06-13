"""Behavioral tests for tools/approval.py — the command-execution consent gate.

Covers the uncovered gateway blocking flow (approve/deny/timeout/notify-fail/
pending), smart-mode, cron-deny, the CLI prompt branches, _format_tirith, session
yolo toggles, and the gateway-queue lifecycle. Pins BUG-003 (root-delete variants
miss the hardline floor → yolo-bypassable).

The conftest resets approval's module globals between tests; the ContextVar
session key is bound/reset by the `session` fixture. tirith is neutralized for
determinism so only the dangerous-command path drives the decision.
"""
import threading
import time

import pytest

from calfkit_tools.hermes._vendor.tools import approval
from calfkit_tools.hermes._vendor.tools.approval import (
    check_all_command_guards,
    disable_session_yolo,
    enable_session_yolo,
    is_session_yolo_enabled,
    _format_tirith_description,
)

_TIRITH = "calfkit_tools.hermes._vendor.tools.tirith_security.check_command_security"
_GET_APP = "prompt_toolkit.application.current.get_app_or_none"


@pytest.fixture
def session(monkeypatch):
    monkeypatch.setattr(_TIRITH, lambda cmd: {"action": "allow", "findings": [], "summary": ""})
    key = "sess-test"
    token = approval.set_current_session_key(key)
    try:
        yield key
    finally:
        approval.reset_current_session_key(token)


def _resolve_after(session_key, choice, *, resolve_all=False, timeout=3.0):
    """Background thread: once an approval is queued, resolve it with `choice`."""
    def run():
        deadline = time.time() + timeout
        while time.time() < deadline:
            if approval.has_blocking_approval(session_key):
                approval.resolve_gateway_approval(session_key, choice, resolve_all)
                return
            time.sleep(0.005)
    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t


# ---------------------------------------------------------------------------
# BUG-003 — hardline floor vs yolo-bypassable dangerous layer
# ---------------------------------------------------------------------------

def test_BUG003_rootdelete_variants_miss_hardline_floor_under_yolo(session):
    # rm -rf / is HARDLINE → blocked even under yolo (the floor is checked first).
    enable_session_yolo(session)
    assert check_all_command_guards("rm -rf /", "local")["approved"] is False
    # rm -rf // is NOT hardline (only dangerous), so yolo bypasses it → approved.
    # Same destructive intent, but it slips past the unconditional floor.
    assert check_all_command_guards("rm -rf //", "local")["approved"] is True
    assert check_all_command_guards("cd / && rm -rf .", "local")["approved"] is True


# ---------------------------------------------------------------------------
# Session yolo toggles
# ---------------------------------------------------------------------------

def test_session_yolo_toggle_and_empty_key_guards():
    assert is_session_yolo_enabled("") is False
    enable_session_yolo("")          # empty key → no-op
    assert is_session_yolo_enabled("") is False
    enable_session_yolo("k1")
    assert is_session_yolo_enabled("k1") is True
    disable_session_yolo("k1")
    assert is_session_yolo_enabled("k1") is False
    disable_session_yolo("")         # empty key → no-op, no raise


# ---------------------------------------------------------------------------
# Cron-deny
# ---------------------------------------------------------------------------

def test_cron_deny_blocks_dangerous_command(session, monkeypatch):
    monkeypatch.setenv("HERMES_CRON_SESSION", "1")  # not cli/gateway/ask
    monkeypatch.setattr(approval, "_get_cron_approval_mode", lambda: "deny")
    result = check_all_command_guards("rm -rf /tmp/data", "local")
    assert result["approved"] is False
    assert "cron" in result["message"].lower()


def test_cron_allows_safe_command(session, monkeypatch):
    monkeypatch.setenv("HERMES_CRON_SESSION", "1")
    monkeypatch.setattr(approval, "_get_cron_approval_mode", lambda: "deny")
    assert check_all_command_guards("echo hello", "local")["approved"] is True


# ---------------------------------------------------------------------------
# Smart mode (auxiliary-LLM risk assessment)
# ---------------------------------------------------------------------------

@pytest.fixture
def cli(monkeypatch):
    monkeypatch.setenv("HERMES_INTERACTIVE", "1")


def test_smart_mode_auto_approves(session, cli, monkeypatch):
    monkeypatch.setattr(approval, "_get_approval_mode", lambda: "smart")
    monkeypatch.setattr(approval, "_smart_approve", lambda cmd, desc: "approve")
    result = check_all_command_guards("rm -rf /tmp/x", "local")
    assert result["approved"] is True
    assert result["smart_approved"] is True
    # Auto-approval persists the pattern for the session.
    assert check_all_command_guards("rm -rf /tmp/y", "local")["approved"] is True


def test_smart_mode_denies(session, cli, monkeypatch):
    monkeypatch.setattr(approval, "_get_approval_mode", lambda: "smart")
    monkeypatch.setattr(approval, "_smart_approve", lambda cmd, desc: "deny")
    result = check_all_command_guards("rm -rf /tmp/x", "local")
    assert result["approved"] is False
    assert result["smart_denied"] is True


def test_smart_mode_escalate_falls_through_to_prompt(session, cli, monkeypatch):
    monkeypatch.setattr(approval, "_get_approval_mode", lambda: "smart")
    monkeypatch.setattr(approval, "_smart_approve", lambda cmd, desc: "escalate")
    # Provide a CLI callback so the manual prompt resolves deterministically.
    result = check_all_command_guards(
        "rm -rf /tmp/x", "local",
        approval_callback=lambda *a, **k: "deny",
    )
    assert result["approved"] is False
    assert result["outcome"] == "denied"


# ---------------------------------------------------------------------------
# Gateway blocking flow
# ---------------------------------------------------------------------------

@pytest.fixture
def gateway(session, monkeypatch):
    monkeypatch.setenv("HERMES_EXEC_ASK", "1")  # routes to the gateway block path
    calls = []
    approval.register_gateway_notify(session, lambda data: calls.append(data))
    return calls


def test_gateway_approve_once(session, gateway):
    _resolve_after(session, "once")
    result = check_all_command_guards("rm -rf /tmp/x", "local")
    assert result["approved"] is True
    assert result["user_approved"] is True
    assert len(gateway) == 1  # the user was notified


def test_gateway_deny_is_hard_block_with_anti_evasion(session, gateway):
    _resolve_after(session, "deny")
    result = check_all_command_guards("rm -rf /tmp/x", "local")
    assert result["approved"] is False
    assert result["outcome"] == "denied"
    assert result["user_consent"] is False
    assert "do NOT retry" in result["message"].lower() or "do not retry" in result["message"].lower()


def test_gateway_timeout_is_not_consent(session, gateway, monkeypatch):
    monkeypatch.setattr(approval, "_get_approval_config", lambda: {"gateway_timeout": 0})
    result = check_all_command_guards("rm -rf /tmp/x", "local")  # nobody resolves
    assert result["approved"] is False
    assert result["outcome"] == "timeout"
    assert "silence is not consent" in result["message"].lower()


def test_gateway_notify_failure_blocks(session, monkeypatch):
    monkeypatch.setenv("HERMES_EXEC_ASK", "1")

    def boom(_data):
        raise RuntimeError("send failed")
    approval.register_gateway_notify(session, boom)
    result = check_all_command_guards("rm -rf /tmp/x", "local")
    assert result["approved"] is False
    assert "failed to send approval request" in result["message"].lower()


def test_gateway_pending_when_no_callback(session, monkeypatch):
    monkeypatch.setenv("HERMES_EXEC_ASK", "1")  # ask context but NO notify cb registered
    result = check_all_command_guards("rm -rf /tmp/x", "local")
    assert result["status"] == "pending_approval"
    assert result["approval_pending"] is True


# ---------------------------------------------------------------------------
# Gateway queue lifecycle
# ---------------------------------------------------------------------------

def test_resolve_gateway_empty_queue_returns_zero():
    assert approval.resolve_gateway_approval("nobody", "once") == 0


def test_clear_session_unblocks_with_deny(session):
    entry = approval._ApprovalEntry({"command": "x"})
    with approval._lock:
        approval._gateway_queues.setdefault(session, []).append(entry)
    approval.clear_session(session)
    assert entry.event.is_set()
    assert entry.result == "deny"
    assert approval.has_blocking_approval(session) is False


def test_unregister_gateway_notify_signals_blocked_entries(session):
    approval.register_gateway_notify(session, lambda d: None)
    entry = approval._ApprovalEntry({"command": "x"})
    with approval._lock:
        approval._gateway_queues.setdefault(session, []).append(entry)
    approval.unregister_gateway_notify(session)
    assert entry.event.is_set()  # blocked thread released (no hang on run end)


# ---------------------------------------------------------------------------
# CLI prompt branches (prompt_dangerous_approval)
# ---------------------------------------------------------------------------

def test_prompt_callback_exception_denies():
    def boom(*a, **k):
        raise RuntimeError("cb boom")
    assert approval.prompt_dangerous_approval("rm -rf x", "danger", approval_callback=boom) == "deny"


def test_prompt_fails_closed_when_prompt_toolkit_active(monkeypatch):
    # prompt_toolkit owns the terminal and no callback → fail closed (no input()).
    monkeypatch.setattr(_GET_APP, lambda: object())  # an "active app"
    assert approval.prompt_dangerous_approval("rm -rf x", "danger", approval_callback=None) == "deny"


@pytest.mark.parametrize(
    "typed, allow_permanent, expected",
    [("o", True, "once"), ("s", True, "session"), ("a", True, "always"),
     ("a", False, "session"),   # [a]lways downgrades to session when not allowed
     ("nope", True, "deny")],
)
def test_prompt_input_choices(monkeypatch, typed, allow_permanent, expected):
    monkeypatch.setattr(_GET_APP, lambda: None)          # no active TUI → input() path
    monkeypatch.setattr("builtins.input", lambda *a, **k: typed)
    out = approval.prompt_dangerous_approval(
        "rm -rf x", "danger", timeout_seconds=5,
        allow_permanent=allow_permanent, approval_callback=None,
    )
    assert out == expected


# ---------------------------------------------------------------------------
# _format_tirith_description
# ---------------------------------------------------------------------------

def test_format_tirith_full_finding():
    out = _format_tirith_description(
        {"findings": [{"severity": "high", "title": "Homograph", "description": "lookalike domain"}]}
    )
    assert "[high] Homograph: lookalike domain" in out


def test_format_tirith_title_only():
    out = _format_tirith_description({"findings": [{"severity": "low", "title": "Shortened URL"}]})
    assert "[low] Shortened URL" in out


def test_format_tirith_no_findings_uses_summary():
    assert "boom" in _format_tirith_description({"findings": [], "summary": "boom"})


def test_format_tirith_findings_without_titles_fall_back_to_summary():
    out = _format_tirith_description({"findings": [{"severity": "high"}], "summary": "scan hit"})
    assert "scan hit" in out
