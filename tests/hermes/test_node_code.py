"""Tests for the ``execute_code`` tool node: PTC sandbox over the dispatch seam.

Design: docs/design/node-port.md §3 (wrapper shape), §3 "execute_code specifics",
§5 (testing); ADR-0004 (tenancy key).

``execute_code`` runs a Python script in a sandboxed child process that can call
back into a subset of hermes tools over an RPC bridge. The node forwards two
extra kwargs through the dispatch seam: the tenancy ``task_id`` (our
``session_key``) and ``enabled_tools`` (which tool stubs the sandbox may import).
Per-session isolation propagates: in-script tool calls inherit the script's
``task_id``, so the sandbox shares the node session's shell environment.
"""

import os
import shutil
import tempfile
import uuid

from calfkit import ToolContext

from calfkit_tools.hermes._vendor.tools.code_execution_tool import EXECUTE_CODE_SCHEMA
from calfkit_tools.hermes.node.code import _DEFAULT_ENABLED_TOOLS, _enabled_tools, execute_code


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


def run_execute_code(ctx, **kwargs):
    # ToolNodeDef is not directly callable; the wrapper fn is at node._tool.function.
    return execute_code._tool.function(ctx, **kwargs)


def schema_props(node_def):
    return node_def.tool_schema.parameters_json_schema["properties"]


def schema_required(node_def):
    return set(node_def.tool_schema.parameters_json_schema.get("required", []))


# --------------------------------------------------------------------------- #
# 1. Schema sanity — node def mirrors the upstream registered schema.           #
# --------------------------------------------------------------------------- #
class TestSchemaSanity:
    def test_param_names_match_upstream(self):
        upstream = set(EXECUTE_CODE_SCHEMA["parameters"]["properties"])
        assert set(schema_props(execute_code)) == upstream
        assert upstream == {"code"}

    def test_required_matches_upstream(self):
        assert schema_required(execute_code) == set(EXECUTE_CODE_SCHEMA["parameters"]["required"])
        assert "code" in schema_required(execute_code)

    def test_ctx_is_hidden_from_schema(self):
        assert "ctx" not in schema_props(execute_code)

    def test_node_id_is_tool_name_derived(self):
        # calfkit derives the node_id from the tool name; 0.12 dropped the
        # historical ``tool_`` prefix (topics stay ``tool.<name>.*``). Accept
        # both so the supported range (calfkit >=0.9,<0.13) stays green.
        assert execute_code.node_id in ("execute_code", "tool_execute_code")


# --------------------------------------------------------------------------- #
# 2. Wiring spy — name/args/task_id/enabled_tools forwarded through dispatch.    #
# --------------------------------------------------------------------------- #
class TestWiring:
    def test_forwards_name_args_task_id_and_enabled_tools(self, monkeypatch):
        from calfkit_tools.hermes._vendor.tools import registry as registry_mod

        seen = {}

        def fake_dispatch(name, args, **kwargs):
            seen.update({"name": name, "args": args, **kwargs})
            return '{"ok": true}'

        monkeypatch.setattr(registry_mod.registry, "dispatch", fake_dispatch)

        ctx = make_ctx(agent_name="agent-x", deps={"session_id": "s1"})
        result = run_execute_code(ctx, code="print('hi')")

        assert seen["name"] == "execute_code"
        assert seen["args"] == {"code": "print('hi')"}
        assert seen["task_id"] == "agent-x:s1"
        assert result == {"ok": True}

    def test_enabled_tools_is_a_list_of_default_names(self, monkeypatch):
        # Upstream execute_code is typed enabled_tools: Optional[List[str]] and
        # builds set(enabled_tools) itself — pass a list to match the contract.
        from calfkit_tools.hermes._vendor.tools import registry as registry_mod

        seen = {}
        monkeypatch.setattr(
            registry_mod.registry,
            "dispatch",
            lambda name, args, **kw: seen.update(kw) or "{}",
        )

        run_execute_code(make_ctx(agent_name="agent-d"), code="pass")

        assert isinstance(seen["enabled_tools"], list)
        assert set(seen["enabled_tools"]) == _DEFAULT_ENABLED_TOOLS
        # Defaults are the hermes set minus execute_code itself.
        assert "execute_code" not in seen["enabled_tools"]
        assert {"terminal", "read_file", "write_file", "patch", "search_files"} <= set(
            seen["enabled_tools"]
        )

    def test_env_override_replaces_enabled_tools(self, monkeypatch):
        from calfkit_tools.hermes._vendor.tools import registry as registry_mod

        seen = {}
        monkeypatch.setattr(
            registry_mod.registry,
            "dispatch",
            lambda name, args, **kw: seen.update(kw) or "{}",
        )
        monkeypatch.setenv("EXECUTE_CODE_ENABLED_TOOLS", "terminal, read_file ,write_file")

        run_execute_code(make_ctx(agent_name="agent-e"), code="pass")

        assert set(seen["enabled_tools"]) == {"terminal", "read_file", "write_file"}


# --------------------------------------------------------------------------- #
# 3. _enabled_tools — env parsing in isolation.                                 #
# --------------------------------------------------------------------------- #
class TestEnabledTools:
    def test_default_set_when_env_absent(self, monkeypatch):
        monkeypatch.delenv("EXECUTE_CODE_ENABLED_TOOLS", raising=False)
        assert _enabled_tools() == _DEFAULT_ENABLED_TOOLS

    def test_env_override_parsed_and_deduped(self, monkeypatch):
        monkeypatch.setenv("EXECUTE_CODE_ENABLED_TOOLS", "terminal,terminal, patch ,")
        assert _enabled_tools() == {"terminal", "patch"}

    def test_blank_env_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("EXECUTE_CODE_ENABLED_TOOLS", "  , ,")
        assert _enabled_tools() == _DEFAULT_ENABLED_TOOLS


# --------------------------------------------------------------------------- #
# 3b. Fail-closed guard — a restrictive override must never EXPAND the sandbox. #
#                                                                               #
# Upstream falls back to the FULL SANDBOX_ALLOWED_TOOLS set when the enabled    #
# set's intersection with it is empty (code_execution_tool: "if not             #
# sandbox_tools: sandbox_tools = SANDBOX_ALLOWED_TOOLS"). A typo'd or           #
# non-sandboxable-only override would silently grant every tool. The node       #
# fails closed instead: error dict, no dispatch.                                #
# --------------------------------------------------------------------------- #
class TestFailClosedOverride:
    def _spy(self, monkeypatch):
        from calfkit_tools.hermes._vendor.tools import registry as registry_mod

        calls = []
        monkeypatch.setattr(
            registry_mod.registry,
            "dispatch",
            lambda name, args, **kw: calls.append(name) or "{}",
        )
        return calls

    def test_all_unknown_override_fails_closed(self, monkeypatch):
        calls = self._spy(monkeypatch)
        monkeypatch.setenv("EXECUTE_CODE_ENABLED_TOOLS", "terminl,bogus")

        result = run_execute_code(make_ctx(agent_name="agent-fc1"), code="pass")

        assert "error" in result
        assert "EXECUTE_CODE_ENABLED_TOOLS" in result["error"]
        assert calls == []  # never reached the vendored tool

    def test_non_sandboxable_only_override_fails_closed(self, monkeypatch):
        # process/todo are valid hermes tools but generate no sandbox stub;
        # upstream would expand the empty intersection to ALL tools.
        calls = self._spy(monkeypatch)
        monkeypatch.setenv("EXECUTE_CODE_ENABLED_TOOLS", "process,todo")

        result = run_execute_code(make_ctx(agent_name="agent-fc2"), code="pass")

        assert "error" in result
        assert calls == []

    def test_default_set_still_dispatches(self, monkeypatch):
        calls = self._spy(monkeypatch)
        monkeypatch.delenv("EXECUTE_CODE_ENABLED_TOOLS", raising=False)

        result = run_execute_code(make_ctx(agent_name="agent-fc3"), code="pass")

        assert result == {}
        assert calls == ["execute_code"]


# --------------------------------------------------------------------------- #
# 4. Functional — a real PTC run that proves session isolation propagates.      #
#                                                                               #
# The node's terminal env and the in-script terminal call share one session_key #
# (forwarded as task_id), so an env var exported by a prior node terminal call  #
# is visible to the in-script terminal call. This is the evidence that the PTC  #
# bridge preserves the session's isolation (ADR-0004).                          #
# --------------------------------------------------------------------------- #
class TestFunctionalIsolation:
    def test_in_script_tool_call_runs_under_the_node_session(self):
        # tempfile.mkdtemp(dir="/tmp"): pytest's tmp_path lives under
        # /private/var/... on macOS, which the vendored sensitive-path guard
        # refuses; and /tmp keeps AF_UNIX socket paths under the 104-byte limit.
        tmp = tempfile.mkdtemp(dir="/tmp", prefix="hermes-code-node-")
        try:
            from calfkit_tools.hermes.node.shell import terminal

            ctx = make_ctx(agent_name=agent("code-iso"))
            marker = os.path.join(tmp, "marker.txt")

            # 1. Set a shell env var via the NODE's terminal (session-scoped).
            terminal._tool.function(ctx, command="export NODE_MARKER=propagated")

            # 2. From inside the sandbox, read that env var via the bridged
            #    terminal and write it to the marker file. If the in-script call
            #    did NOT inherit our session_key, $NODE_MARKER would be empty.
            code = (
                "from hermes_tools import terminal\n"
                f"terminal(command='echo [$NODE_MARKER] > {marker}')\n"
                "print('script-done')\n"
            )
            out = run_execute_code(ctx, code=code)

            assert isinstance(out, dict)
            # Upstream returns the script's stdout under "output".
            assert "script-done" in out["output"]
            assert os.path.exists(marker)
            with open(marker) as f:
                contents = f.read()
            # Evidence: the in-script terminal call saw the node session's env.
            assert "propagated" in contents
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
