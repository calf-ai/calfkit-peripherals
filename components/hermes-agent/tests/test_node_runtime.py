"""Tests for the node-layer shared runtime: tenancy keying + dispatch seam.

Design: docs/design/node-port.md §3; ADR-0004.
"""

import json

import pytest
from calfkit import ToolContext

from calfkit_hermes.node._runtime import dispatch, ensure_tools_discovered, session_key


def make_ctx(agent_name="agent-a", deps=None, resources=None):
    return ToolContext(
        deps=deps or {},
        agent_name=agent_name,
        tool_call_id="tc-1",
        tool_name="test",
        messages=[],
        run_id="run-1",
        resources=resources or {},
    )


class TestSessionKey:
    def test_default_scope_is_agent_lifetime(self):
        # No session_id wired in deps -> agent-scoped "default" session.
        assert session_key(make_ctx(agent_name="agent-a")) == "agent-a:default"

    def test_deps_session_id_refines_scope(self):
        ctx = make_ctx(agent_name="agent-a", deps={"session_id": "user-7"})
        assert session_key(ctx) == "agent-a:user-7"

    def test_agent_prefix_isolates_identical_session_ids(self):
        a = make_ctx(agent_name="agent-a", deps={"session_id": "s"})
        b = make_ctx(agent_name="agent-b", deps={"session_id": "s"})
        assert session_key(a) != session_key(b)

    def test_non_string_session_id_is_coerced(self):
        ctx = make_ctx(deps={"session_id": 42})
        assert session_key(ctx) == "agent-a:42"

    def test_empty_session_id_falls_back_to_default(self):
        ctx = make_ctx(deps={"session_id": ""})
        assert session_key(ctx) == "agent-a:default"

    def test_missing_agent_name_raises_fail_closed(self):
        # FIX 3: an unstamped caller (agent_name falsy) must NOT be silently
        # merged into a shared "None:default" bucket — fail closed instead.
        ctx = make_ctx(agent_name=None)
        with pytest.raises(ValueError):
            session_key(ctx)

    def test_empty_agent_name_raises_fail_closed(self):
        ctx = make_ctx(agent_name="")
        with pytest.raises(ValueError):
            session_key(ctx)


class TestEnsureToolsDiscovered:
    def test_populates_registry_idempotently(self):
        from calfkit_hermes._vendor.tools.registry import registry

        ensure_tools_discovered()
        first = set(registry.get_all_tool_names())
        assert "terminal" in first
        ensure_tools_discovered()  # second call must be a no-op, not a re-import
        assert set(registry.get_all_tool_names()) == first


class TestDispatch:
    def test_returns_parsed_dict(self):
        ensure_tools_discovered()
        # todo without a store returns a clean upstream error dict (ADR-0003).
        result = dispatch("todo", {}, session_key="agent-a:default")
        assert isinstance(result, dict)
        assert "error" in result

    def test_passes_session_key_as_task_id(self, monkeypatch):
        from calfkit_hermes._vendor.tools import registry as registry_mod

        seen = {}

        def fake_dispatch(name, args, **kwargs):
            seen.update({"name": name, "args": args, **kwargs})
            return json.dumps({"ok": True})

        monkeypatch.setattr(registry_mod.registry, "dispatch", fake_dispatch)
        result = dispatch("terminal", {"command": "pwd"}, session_key="agent-a:s1")
        assert seen["name"] == "terminal"
        assert seen["args"] == {"command": "pwd"}
        assert seen["task_id"] == "agent-a:s1"
        assert result == {"ok": True}

    def test_extra_kwargs_forwarded(self, monkeypatch):
        from calfkit_hermes._vendor.tools import registry as registry_mod

        seen = {}

        def fake_dispatch(name, args, **kwargs):
            seen.update(kwargs)
            return json.dumps({})

        monkeypatch.setattr(registry_mod.registry, "dispatch", fake_dispatch)
        sentinel = object()
        dispatch("todo", {}, session_key="k", store=sentinel)
        assert seen["store"] is sentinel

    def test_unparseable_result_returned_raw(self, monkeypatch):
        from calfkit_hermes._vendor.tools import registry as registry_mod

        monkeypatch.setattr(
            registry_mod.registry, "dispatch", lambda *a, **k: "not json"
        )
        assert dispatch("terminal", {}, session_key="k") == "not json"

    def test_non_dict_json_is_still_parsed(self, monkeypatch):
        from calfkit_hermes._vendor.tools import registry as registry_mod

        monkeypatch.setattr(registry_mod.registry, "dispatch", lambda *a, **k: "[1, 2]")
        assert dispatch("terminal", {}, session_key="k") == [1, 2]

    def test_none_valued_args_dropped_before_forwarding(self, monkeypatch):
        # FIX 1a: wrappers forward explicit None for omitted params. Upstream
        # handlers read every field via args.get(default), so a PRESENT key
        # with value None overrides the default (e.g. read_log(offset=None) ->
        # TypeError). Dropping None keys restores absent == default semantics.
        from calfkit_hermes._vendor.tools import registry as registry_mod

        seen = {}

        def fake_dispatch(name, args, **kwargs):
            seen["args"] = args
            return json.dumps({})

        monkeypatch.setattr(registry_mod.registry, "dispatch", fake_dispatch)
        dispatch(
            "process",
            {"action": "log", "session_id": "proc_x", "offset": None, "limit": None},
            session_key="k",
        )
        assert seen["args"] == {"action": "log", "session_id": "proc_x"}

    def test_present_non_none_args_are_kept(self, monkeypatch):
        from calfkit_hermes._vendor.tools import registry as registry_mod

        seen = {}

        def fake_dispatch(name, args, **kwargs):
            seen["args"] = args
            return json.dumps({})

        monkeypatch.setattr(registry_mod.registry, "dispatch", fake_dispatch)
        dispatch(
            "process",
            {"action": "log", "session_id": "proc_x", "offset": 0, "limit": 10},
            session_key="k",
        )
        assert seen["args"] == {
            "action": "log",
            "session_id": "proc_x",
            "offset": 0,
            "limit": 10,
        }


class TestSessionIsolationRegistration:
    """dispatch() must register the session_key with the vendored override
    registry — upstream's sanctioned seam for per-task sandbox isolation —
    otherwise ``_resolve_container_task_id`` collapses every task_id to
    "default" and all sessions share one shell environment (ADR-0004).
    """

    def test_dispatch_registers_session_key(self, monkeypatch):
        from calfkit_hermes._vendor.tools import registry as registry_mod
        from calfkit_hermes._vendor.tools import terminal_tool

        monkeypatch.setattr(registry_mod.registry, "dispatch", lambda *a, **k: "{}")
        key = "agent-reg-test:default"
        terminal_tool._task_env_overrides.pop(key, None)

        dispatch("terminal", {"command": "true"}, session_key=key)

        assert key in terminal_tool._task_env_overrides
        assert terminal_tool._resolve_container_task_id(key) == key
        terminal_tool.clear_task_env_overrides(key)

    def test_dispatch_does_not_clobber_existing_overrides(self, monkeypatch):
        from calfkit_hermes._vendor.tools import registry as registry_mod
        from calfkit_hermes._vendor.tools import terminal_tool

        monkeypatch.setattr(registry_mod.registry, "dispatch", lambda *a, **k: "{}")
        key = "agent-clobber-test:default"
        terminal_tool.register_task_env_overrides(key, {"docker_image": "img"})

        dispatch("terminal", {"command": "true"}, session_key=key)

        assert terminal_tool._task_env_overrides[key] == {"docker_image": "img"}
        terminal_tool.clear_task_env_overrides(key)
