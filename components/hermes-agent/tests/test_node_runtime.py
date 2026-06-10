"""Tests for the node-layer shared runtime: tenancy keying + dispatch seam.

Design: docs/design/node-port.md §3; ADR-0004.
"""

import json

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
