"""Tests for the shell node layer: terminal + process tool nodes.

Design: docs/design/node-port.md §3, §5; ADR-0004.

State for shell/process lives in process-global vendored registries keyed by
``task_id`` (our ``session_key``). Tests stay independent of execution order by
using a unique agent name per test (uuid suffix) so no two tests collide on a
session_key.
"""

import json
import uuid

from calfkit import ToolContext

from calfkit_tools.hermes._vendor.tools.process_registry import PROCESS_SCHEMA
from calfkit_tools.hermes._vendor.tools.terminal_tool import TERMINAL_SCHEMA
from calfkit_tools.hermes.node.shell import process, terminal


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


def agent(prefix):
    """A unique agent name so tests don't collide on the process-global registries."""
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


# The node defs are ToolNodeDefs (not directly callable); the original wrapper
# function is at ``node._tool.function``. These thin invokers keep the tests
# reading like ordinary calls.
def run_terminal(ctx, **kwargs):
    return terminal._tool.function(ctx, **kwargs)


def run_process(ctx, **kwargs):
    return process._tool.function(ctx, **kwargs)


def schema_props(node_def):
    return node_def.tool_schema.parameters_json_schema["properties"]


def schema_required(node_def):
    return set(node_def.tool_schema.parameters_json_schema.get("required", []))


# --------------------------------------------------------------------------- #
# 1. Schema sanity — node defs mirror the upstream registered schemas exactly. #
# --------------------------------------------------------------------------- #
class TestSchemaSanity:
    def test_terminal_param_names_match_upstream(self):
        upstream = set(TERMINAL_SCHEMA["parameters"]["properties"])
        assert set(schema_props(terminal)) == upstream

    def test_terminal_required_matches_upstream(self):
        assert schema_required(terminal) == set(TERMINAL_SCHEMA["parameters"]["required"])
        assert "command" in schema_required(terminal)

    def test_process_param_names_match_upstream(self):
        upstream = set(PROCESS_SCHEMA["parameters"]["properties"])
        assert set(schema_props(process)) == upstream

    def test_process_required_matches_upstream(self):
        assert schema_required(process) == set(PROCESS_SCHEMA["parameters"]["required"])
        assert "action" in schema_required(process)

    def test_process_action_is_enum_with_upstream_actions(self):
        action = schema_props(process)["action"]
        upstream_actions = PROCESS_SCHEMA["parameters"]["properties"]["action"]["enum"]
        assert action["enum"] == upstream_actions
        assert len(upstream_actions) == 8

    def test_ctx_is_hidden_from_both_schemas(self):
        assert "ctx" not in schema_props(terminal)
        assert "ctx" not in schema_props(process)

    # ----- FIX 4: numeric bounds mirror upstream's schema constraints ----- #
    @staticmethod
    def _minimum_of(prop_schema):
        """The lower bound, whether declared flat or inside an anyOf branch."""
        if "minimum" in prop_schema:
            return prop_schema["minimum"]
        for branch in prop_schema.get("anyOf", []):
            if "minimum" in branch:
                return branch["minimum"]
        return None

    def test_terminal_timeout_minimum_matches_upstream(self):
        upstream = TERMINAL_SCHEMA["parameters"]["properties"]["timeout"]["minimum"]
        assert upstream == 1
        assert self._minimum_of(schema_props(terminal)["timeout"]) == upstream

    def test_process_timeout_minimum_matches_upstream(self):
        upstream = PROCESS_SCHEMA["parameters"]["properties"]["timeout"]["minimum"]
        assert upstream == 1
        assert self._minimum_of(schema_props(process)["timeout"]) == upstream

    def test_process_limit_minimum_matches_upstream(self):
        upstream = PROCESS_SCHEMA["parameters"]["properties"]["limit"]["minimum"]
        assert upstream == 1
        assert self._minimum_of(schema_props(process)["limit"]) == upstream

    def test_process_offset_has_no_minimum_like_upstream(self):
        # Upstream declares no minimum on offset; the node must not invent one.
        assert "minimum" not in PROCESS_SCHEMA["parameters"]["properties"]["offset"]
        assert self._minimum_of(schema_props(process)["offset"]) is None


# --------------------------------------------------------------------------- #
# 2. Wiring spy — name/args/task_id forwarded through the dispatch seam.        #
# --------------------------------------------------------------------------- #
class TestWiring:
    def test_terminal_forwards_name_args_and_task_id(self, monkeypatch):
        from calfkit_tools.hermes._vendor.tools import registry as registry_mod

        seen = {}

        def fake_dispatch(name, args, **kwargs):
            seen.update({"name": name, "args": args, **kwargs})
            return json.dumps({"ok": True})

        monkeypatch.setattr(registry_mod.registry, "dispatch", fake_dispatch)

        ctx = make_ctx(agent_name="agent-x", deps={"session_id": "s1"})
        result = run_terminal(ctx, command="pwd")

        assert seen["name"] == "terminal"
        assert seen["task_id"] == "agent-x:s1"
        # Keys reaching the seam are a subset of upstream's params (the wrapper
        # forwards all of them, but _runtime.dispatch drops the None-valued
        # ones the agent omitted — FIX 1). command was supplied, so it survives.
        assert set(seen["args"]) <= set(TERMINAL_SCHEMA["parameters"]["properties"])
        assert seen["args"]["command"] == "pwd"
        # Omitted optionals are dropped, not forwarded as None.
        assert "timeout" not in seen["args"]
        assert result == {"ok": True}

    def test_process_forwards_name_args_and_task_id(self, monkeypatch):
        from calfkit_tools.hermes._vendor.tools import registry as registry_mod

        seen = {}

        def fake_dispatch(name, args, **kwargs):
            seen.update({"name": name, "args": args, **kwargs})
            return json.dumps({"processes": []})

        monkeypatch.setattr(registry_mod.registry, "dispatch", fake_dispatch)

        ctx = make_ctx(agent_name="agent-y")
        result = run_process(ctx, action="list")

        assert seen["name"] == "process"
        assert seen["task_id"] == "agent-y:default"
        # None-valued omitted params are dropped (FIX 1); only action survives.
        assert set(seen["args"]) <= set(PROCESS_SCHEMA["parameters"]["properties"])
        assert seen["args"]["action"] == "list"
        assert "session_id" not in seen["args"]
        assert result == {"processes": []}


# --------------------------------------------------------------------------- #
# 3. Tenancy — real local shell execution via node._tool.function.              #
#                                                                               #
# Isolation rides on _runtime._ensure_session_isolated(): every dispatch        #
# registers its session_key with upstream's per-task override seam, so          #
# _resolve_container_task_id honours it instead of collapsing to "default"      #
# (one shared LocalEnvironment). See ADR-0004.                                  #
# --------------------------------------------------------------------------- #
class TestTenancy:
    def test_same_agent_persists_state_across_calls(self):
        # Agent-lifetime scope (default): an env var set in one call is visible
        # in the next call by the same session_key.
        a = make_ctx(agent_name=agent("persist"))

        run_terminal(a, command="export BAR=persisted")
        out = run_terminal(a, command="echo [$BAR]")

        assert "persisted" in out["output"]

    def test_different_agents_have_isolated_shell_state(self):
        a = make_ctx(agent_name=agent("iso-a"))
        b = make_ctx(agent_name=agent("iso-b"))

        run_terminal(a, command="export FOO=from_a")
        out_b = run_terminal(b, command="echo [$FOO]")

        assert "from_a" not in out_b["output"]

    def test_same_agent_different_session_ids_are_isolated(self):
        name = agent("sess")
        s1 = make_ctx(agent_name=name, deps={"session_id": "s1"})
        s2 = make_ctx(agent_name=name, deps={"session_id": "s2"})

        run_terminal(s1, command="export BAZ=only_s1")
        out2 = run_terminal(s2, command="echo [$BAZ]")

        assert "only_s1" not in out2["output"]


# --------------------------------------------------------------------------- #
# 4. Result shape — terminal returns a parsed dict (upstream JSON contract).    #
# --------------------------------------------------------------------------- #
class TestResultShape:
    def test_terminal_pwd_returns_parsed_dict(self):
        a = make_ctx(agent_name=agent("shape"))
        result = run_terminal(a, command="pwd")
        assert isinstance(result, dict)
        assert "output" in result

    def test_terminal_runs_in_workdir(self, tmp_path):
        # FIX 5: workdir is honoured — the command runs in the given directory.
        a = make_ctx(agent_name=agent("workdir"))
        result = run_terminal(a, command="pwd", workdir=str(tmp_path))
        # macOS /var symlinks to /private/var; match on the basename to stay robust.
        assert tmp_path.name in result["output"]


# --------------------------------------------------------------------------- #
# 5. process — background submit/poll/wait/kill lifecycle.                       #
# --------------------------------------------------------------------------- #
class TestProcessLifecycle:
    def test_poll_wait_kill_operate_on_the_background_handle(self):
        # Start a background process, then drive it through the process node by
        # its returned session_id handle. poll/wait/kill look the process up by
        # that global handle (not task-scoped), so this path works end-to-end.
        a = make_ctx(agent_name=agent("proc-wait"))

        started = run_terminal(a, command="sleep 0.3", background=True)
        sid = started["session_id"]
        assert sid.startswith("proc_")

        polled = run_process(a, action="poll", session_id=sid)
        assert polled["status"] in {"running", "exited", "finished", "done"}

        waited = run_process(a, action="wait", session_id=sid, timeout=5)
        assert isinstance(waited, dict)

        killed = run_process(a, action="kill", session_id=sid)
        assert isinstance(killed, dict)

    def test_list_for_unknown_session_key_is_empty(self):
        # A fresh session_key with no background processes lists nothing — the
        # node correctly scopes 'list' by the forwarded task_id.
        a = make_ctx(agent_name=agent("proc-empty"))
        assert run_process(a, action="list")["processes"] == []

    def test_list_shows_the_agents_own_background_process(self):
        a = make_ctx(agent_name=agent("proc-list"))
        started = run_terminal(a, command="sleep 0.4", background=True)
        sid = started["session_id"]
        try:
            sids = {p.get("session_id") for p in run_process(a, action="list")["processes"]}
            assert sid in sids
        finally:
            run_process(a, action="kill", session_id=sid)


# --------------------------------------------------------------------------- #
# 6. process log pagination — FIX 1b: default pagination must not crash.        #
# --------------------------------------------------------------------------- #
class TestProcessLog:
    def test_log_with_default_pagination_returns_output(self):
        # FIX 1: process(action="log") without offset/limit used to forward
        # offset=None/limit=None -> read_log(None, None) -> TypeError. With the
        # None-dropping fix the upstream 0/200 defaults apply and we get output.
        a = make_ctx(agent_name=agent("proc-log"))
        started = run_terminal(a, command="echo hello-log; sleep 0.3", background=True)
        sid = started["session_id"]
        try:
            run_process(a, action="wait", session_id=sid, timeout=5)
            logged = run_process(a, action="log", session_id=sid)
            assert isinstance(logged, dict)
            assert "error" not in logged
            assert "hello-log" in logged["output"]
        finally:
            run_process(a, action="kill", session_id=sid)

    def test_log_with_explicit_pagination_works(self):
        a = make_ctx(agent_name=agent("proc-log-pag"))
        started = run_terminal(a, command="echo hello-log; sleep 0.3", background=True)
        sid = started["session_id"]
        try:
            run_process(a, action="wait", session_id=sid, timeout=5)
            logged = run_process(a, action="log", session_id=sid, offset=0, limit=50)
            assert isinstance(logged, dict)
            assert "error" not in logged
            assert "hello-log" in logged["output"]
        finally:
            run_process(a, action="kill", session_id=sid)


# --------------------------------------------------------------------------- #
# 7. Cross-tenant ownership guard — FIX 2.                                       #
#                                                                               #
# Upstream scopes only 'list' by task_id; handle actions (poll/log/wait/kill/   #
# write/close) look the process up by the global proc_* handle, so tenant B     #
# could control A's process if it learned the handle. The node verifies         #
# ownership at the public seam (a scoped 'list') before dispatching, and        #
# denies foreign handles with upstream's own not-found error shape.             #
# --------------------------------------------------------------------------- #
NOT_FOUND_STATUS = "not_found"


class TestProcessOwnershipGuard:
    def test_foreign_agent_cannot_poll_kill_or_log(self):
        owner = make_ctx(agent_name=agent("own-a"))
        intruder = make_ctx(agent_name=agent("own-b"))

        started = run_terminal(owner, command="sleep 1.0", background=True)
        sid = started["session_id"]
        try:
            for action in ("poll", "log", "kill", "wait"):
                denied = run_process(intruder, action=action, session_id=sid)
                assert denied["status"] == NOT_FOUND_STATUS
                # Deny-as-not-found: mirror upstream's unknown-handle text.
                assert sid in denied["error"]
                assert "No process with ID" in denied["error"]

            # A's process is unaffected — the owner can still poll it.
            owner_poll = run_process(owner, action="poll", session_id=sid)
            assert owner_poll["status"] in {"running", "exited", "finished", "done"}
        finally:
            run_process(owner, action="kill", session_id=sid)

    def test_owner_can_poll_log_wait_a_finished_process(self):
        # Regression for the list-includes-finished question: list_sessions()
        # unions running + finished, so the ownership guard still admits the
        # owner after the process has exited.
        owner = make_ctx(agent_name=agent("own-fin"))
        started = run_terminal(owner, command="echo bye", background=True)
        sid = started["session_id"]

        waited = run_process(owner, action="wait", session_id=sid, timeout=5)
        assert waited["status"] == "exited"

        polled = run_process(owner, action="poll", session_id=sid)
        assert polled["status"] in {"exited", "finished", "done"}

        logged = run_process(owner, action="log", session_id=sid)
        assert "error" not in logged
        assert "bye" in logged["output"]

    def test_list_is_not_blocked_by_the_guard(self, monkeypatch):
        # 'list' takes no handle and must never trigger ownership verification.
        from calfkit_tools.hermes._vendor.tools import registry as registry_mod

        real_dispatch = registry_mod.registry.dispatch
        calls = []

        def tracking_dispatch(name, args, **kwargs):
            calls.append(args.get("action"))
            return real_dispatch(name, args, **kwargs)

        a = make_ctx(agent_name=agent("guard-list"))
        # list: should not provoke an internal verification 'list' beyond its own.
        monkeypatch.setattr(registry_mod.registry, "dispatch", tracking_dispatch)
        run_process(a, action="list")
        assert calls == ["list"]

    def test_owner_submit_passes_the_guard(self):
        # 'submit' is a guarded handle action (it writes raw stdin); the guard
        # must let the OWNER's handle through to upstream.
        owner = make_ctx(agent_name=agent("own-submit"))
        started = run_terminal(owner, command="cat", background=True, pty=True)
        sid = started["session_id"]
        try:
            submitted = run_process(owner, action="submit", session_id=sid, data="hi")
            assert submitted.get("status") != "not_found"
            assert "No process with ID" not in str(submitted.get("error", ""))
        finally:
            run_process(owner, action="kill", session_id=sid)

    def test_foreign_poll_does_not_mutate_owner_state(self):
        owner = make_ctx(agent_name=agent("own-safe"))
        intruder = make_ctx(agent_name=agent("own-safe-b"))
        started = run_terminal(owner, command="sleep 1.0", background=True)
        sid = started["session_id"]
        try:
            run_process(intruder, action="poll", session_id=sid)
            # Still listed under the owner, still controllable by the owner.
            owner_sids = {
                p.get("session_id") for p in run_process(owner, action="list")["processes"]
            }
            assert sid in owner_sids
        finally:
            run_process(owner, action="kill", session_id=sid)
