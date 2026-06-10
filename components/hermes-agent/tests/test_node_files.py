"""Tests for the file tool nodes: read_file, write_file, patch, search_files.

Design: docs/design/node-port.md §3, §5; ADR-0004. These wrappers are thin 1:1
adapters over the vendored hermes file tools, driven through the dispatch seam.

macOS caveat: the vendored sensitive-path guard (``_SENSITIVE_PATH_PREFIXES``)
refuses ``/private/var/...``, which is where pytest's ``tmp_path`` lives on
macOS. We use ``tempfile.mkdtemp(dir="/tmp")`` (the literal "/tmp/..." string
passes the guard) and clean up ourselves.
"""

import os
import shutil
import tempfile
import uuid

import pytest
from calfkit import ToolContext

from calfkit_hermes.node import files
from calfkit_hermes.node.files import patch, read_file, search_files, write_file


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


def unique_agent(prefix="agent"):
    """A per-test agent name so tenancy state stays order-independent."""
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def tmp_workdir():
    """A scratch dir under /tmp (passes the vendored sensitive-path guard)."""
    d = tempfile.mkdtemp(dir="/tmp")
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)


# Each node def exposes its original function at ``._tool.function``.
def fn(node_def):
    return node_def._tool.function


def params_schema(node_def):
    return node_def.tool_schema.parameters_json_schema


# ───────────────────────── 1. Schema sanity ─────────────────────────


class TestSchemaSanity:
    """Each node's LLM schema mirrors the upstream tool schema (minus task_id,
    minus the auto-hidden ctx). Catches signature drift from upstream."""

    def test_node_ids_are_tool_name_derived(self):
        assert read_file.id == "tool_read_file"
        assert write_file.id == "tool_write_file"
        assert patch.id == "tool_patch"
        assert search_files.id == "tool_search_files"

    def test_read_file_schema(self):
        schema = params_schema(read_file)
        assert set(schema["properties"]) == {"path", "offset", "limit"}
        assert schema["required"] == ["path"]
        # ctx must be hidden from the LLM-facing schema.
        assert "ctx" not in schema["properties"]

    def test_write_file_schema(self):
        schema = params_schema(write_file)
        assert set(schema["properties"]) == {"path", "content", "cross_profile"}
        assert set(schema["required"]) == {"path", "content"}

    def test_patch_schema(self):
        schema = params_schema(patch)
        assert set(schema["properties"]) == {
            "mode",
            "path",
            "old_string",
            "new_string",
            "replace_all",
            "patch",
            "cross_profile",
        }
        # Upstream's hand-written schema marks "mode" required, but upstream's
        # own ``patch_tool`` / handler default mode to "replace". We keep that
        # functional default, so calfkit derives no required params (every param
        # has a default). Faithful to upstream *behavior*; the LLM gets the same
        # "replace" fallback. No param is required.
        assert schema.get("required", []) == []

    def test_patch_mode_is_enum(self):
        schema = params_schema(patch)
        # Literal -> JSON-schema enum.
        assert schema["properties"]["mode"]["enum"] == ["replace", "patch"]

    def test_search_files_schema(self):
        schema = params_schema(search_files)
        assert set(schema["properties"]) == {
            "pattern",
            "target",
            "path",
            "file_glob",
            "limit",
            "offset",
            "output_mode",
            "context",
        }
        assert schema["required"] == ["pattern"]

    def test_search_files_enums(self):
        schema = params_schema(search_files)
        assert schema["properties"]["target"]["enum"] == ["content", "files"]
        assert schema["properties"]["output_mode"]["enum"] == [
            "content",
            "files_only",
            "count",
        ]


# ───────────────────────── 2. Wiring spy ─────────────────────────


class TestWiring:
    """Each wrapper forwards name + args + the session_key (as task_id) to the
    vendored registry. Spy the seam, not internals."""

    @pytest.fixture
    def spy(self, monkeypatch):
        from calfkit_hermes._vendor.tools import registry as registry_mod

        seen = {}

        def fake_dispatch(name, args, **kwargs):
            seen.update({"name": name, "args": args, **kwargs})
            return '{"ok": true}'

        # Ensure the registry exists before we patch over its dispatch.
        files.read_file  # noqa: B018  (import side-effect already done)
        from calfkit_hermes.node._runtime import ensure_tools_discovered

        ensure_tools_discovered()
        monkeypatch.setattr(registry_mod.registry, "dispatch", fake_dispatch)
        return seen

    def test_read_file_forwards(self, spy):
        ctx = make_ctx(agent_name="agent-a", deps={"session_id": "s1"})
        result = fn(read_file)(ctx, path="/tmp/x", offset=3, limit=10)
        assert spy["name"] == "read_file"
        assert spy["args"] == {"path": "/tmp/x", "offset": 3, "limit": 10}
        assert spy["task_id"] == "agent-a:s1"
        assert result == {"ok": True}

    def test_write_file_forwards(self, spy):
        ctx = make_ctx(agent_name="w", deps={"session_id": "s2"})
        fn(write_file)(ctx, path="/tmp/y", content="hi")
        assert spy["name"] == "write_file"
        assert spy["args"] == {
            "path": "/tmp/y",
            "content": "hi",
            "cross_profile": False,
        }
        assert spy["task_id"] == "w:s2"

    def test_patch_forwards(self, spy):
        ctx = make_ctx(agent_name="p")
        fn(patch)(ctx, mode="replace", path="/tmp/z", old_string="a", new_string="b")
        assert spy["name"] == "patch"
        assert spy["args"] == {
            "mode": "replace",
            "path": "/tmp/z",
            "old_string": "a",
            "new_string": "b",
            "replace_all": False,
            "patch": None,
            "cross_profile": False,
        }
        assert spy["task_id"] == "p:default"

    def test_search_files_forwards(self, spy):
        ctx = make_ctx(agent_name="s")
        fn(search_files)(ctx, pattern="needle", path="/tmp/d", target="files")
        assert spy["name"] == "search_files"
        assert spy["args"] == {
            "pattern": "needle",
            "target": "files",
            "path": "/tmp/d",
            "file_glob": None,
            "limit": 50,
            "offset": 0,
            "output_mode": "content",
            "context": 0,
        }
        assert spy["task_id"] == "s:default"


# ───────────────────────── 3. Real round-trip ─────────────────────────


class TestRoundTrip:
    """Real local execution through the dispatch seam (the vendored writer runs
    through the shell-fused environment layer — that's by design)."""

    def test_write_then_read(self, tmp_workdir):
        ctx = make_ctx(agent_name=unique_agent())
        path = os.path.join(tmp_workdir, "hello.txt")

        w = fn(write_file)(ctx, path=path, content="hello world\n")
        assert isinstance(w, dict)
        assert not w.get("error"), w
        assert w["bytes_written"] > 0

        r = fn(read_file)(ctx, path=path)
        assert isinstance(r, dict)
        # Upstream shape: line-numbered "LINE_NUM|CONTENT".
        assert "hello world" in r["content"]
        assert r["content"].startswith("1|")
        assert r["is_binary"] is False

    def test_search_finds_known_string(self, tmp_workdir):
        ctx = make_ctx(agent_name=unique_agent())
        path = os.path.join(tmp_workdir, "doc.txt")
        fn(write_file)(ctx, path=path, content="alpha\nNEEDLE_TOKEN\nbeta\n")

        res = fn(search_files)(ctx, pattern="NEEDLE_TOKEN", path=tmp_workdir)
        assert isinstance(res, dict)
        assert res["total_count"] >= 1
        assert any("NEEDLE_TOKEN" in m["content"] for m in res["matches"])

    def test_patch_replace_applies_edit(self, tmp_workdir):
        ctx = make_ctx(agent_name=unique_agent())
        path = os.path.join(tmp_workdir, "a.py")
        fn(write_file)(ctx, path=path, content="x = 1\n")

        res = fn(patch)(
            ctx, mode="replace", path=path, old_string="x = 1", new_string="x = 2"
        )
        assert isinstance(res, dict)
        assert res.get("success") is True, res
        assert not res.get("error")

        r = fn(read_file)(ctx, path=path)
        assert "x = 2" in r["content"]
        assert "x = 1" not in r["content"]

    def test_patch_v4a_mode_applies(self, tmp_workdir):
        """A minimal valid V4A patch (mode='patch') routed through the node."""
        ctx = make_ctx(agent_name=unique_agent())
        path = os.path.join(tmp_workdir, "v4a.py")
        fn(write_file)(ctx, path=path, content="def greet():\n    return 1\n")

        patch_text = (
            "*** Begin Patch\n"
            f"*** Update File: {path}\n"
            "@@ def greet @@\n"
            " def greet():\n"
            "-    return 1\n"
            "+    return 2\n"
            "*** End Patch"
        )
        res = fn(patch)(ctx, mode="patch", patch=patch_text)
        assert isinstance(res, dict)
        assert res.get("success") is True, res

        r = fn(read_file)(ctx, path=path)
        assert "return 2" in r["content"]


# ─────────────── 4. Read-before-write protocol (verified: NO hard gate) ───────────────


class TestStalenessWarning:
    """VERIFIED FROM SOURCE (file_tools.py / file_state.py): hermes does NOT
    hard-gate overwrites behind a prior read. ``_check_file_staleness`` and
    ``file_state.check_stale`` only emit a non-blocking ``_warning`` string
    ("Does not block — the write still proceeds"). The only hard blocks are the
    sensitive-path and cross-profile guards, neither of which is a read gate.

    So there is nothing to pin as "overwrite-without-read returns an error".
    Instead we pin the behavior that *does* exist: an overwrite-without-read of
    an existing file succeeds, and the staleness machinery surfaces through the
    node when an out-of-band edit occurs after a read.
    """

    def test_overwrite_without_read_succeeds(self, tmp_workdir):
        # Agent B overwrites a file it never read -> succeeds (no hard gate).
        path = os.path.join(tmp_workdir, "shared.txt")
        ctx_a = make_ctx(agent_name=unique_agent("a"))
        fn(write_file)(ctx_a, path=path, content="from-a\n")

        ctx_b = make_ctx(agent_name=unique_agent("b"))
        res = fn(write_file)(ctx_b, path=path, content="from-b\n")
        assert isinstance(res, dict)
        assert not res.get("error"), res

        r = fn(read_file)(ctx_b, path=path)
        assert "from-b" in r["content"]

    def test_external_edit_after_read_surfaces_warning(self, tmp_workdir):
        # Read pins the mtime; an out-of-band edit makes the next write stale.
        agent = unique_agent()
        ctx = make_ctx(agent_name=agent)
        path = os.path.join(tmp_workdir, "tracked.txt")
        fn(write_file)(ctx, path=path, content="v1\n")
        fn(read_file)(ctx, path=path)

        # Mutate the file out-of-band with a clearly different mtime.
        old = os.path.getmtime(path)
        with open(path, "w") as f:
            f.write("v2-external\n")
        os.utime(path, (old + 5, old + 5))

        res = fn(write_file)(ctx, path=path, content="v3\n")
        assert isinstance(res, dict)
        # Write still proceeds (no gate) but flags staleness.
        assert not res.get("error"), res
        assert "_warning" in res and "read" in res["_warning"].lower()


# ───────────────────────── 5. Tenancy isolation ─────────────────────────


class TestTenancy:
    """Read-tracking state is keyed per session_key: one agent's read does not
    license another agent's overwrite (and the staleness warning names the
    sibling). Unique agent names keep these order-independent."""

    def test_sibling_write_after_my_read_warns_other_agent(self, tmp_workdir):
        path = os.path.join(tmp_workdir, "race.txt")
        agent_a = unique_agent("a")
        agent_b = unique_agent("b")
        ctx_a = make_ctx(agent_name=agent_a)
        ctx_b = make_ctx(agent_name=agent_b)

        fn(write_file)(ctx_a, path=path, content="seed\n")
        # A reads; B writes; A's next write should see B as a sibling writer.
        fn(read_file)(ctx_a, path=path)
        fn(write_file)(ctx_b, path=path, content="b-edit\n")

        res = fn(write_file)(ctx_a, path=path, content="a-edit\n")
        assert not res.get("error"), res
        # A never re-read after B's write -> sibling-staleness warning names B.
        assert "_warning" in res
        assert agent_b in res["_warning"]

    def test_read_tracking_is_per_session_key(self, tmp_workdir):
        # Same agent, two session_ids -> independent read trackers.
        path = os.path.join(tmp_workdir, "perscope.txt")
        agent = unique_agent()
        ctx_s1 = make_ctx(agent_name=agent, deps={"session_id": "s1"})
        ctx_s2 = make_ctx(agent_name=agent, deps={"session_id": "s2"})

        fn(write_file)(ctx_s1, path=path, content="orig\n")
        fn(read_file)(ctx_s1, path=path)  # only s1 has a read record

        # s2 never read this file: an overwrite is a clean first-touch, no
        # stale-since-read warning for s2's own scope.
        res = fn(write_file)(ctx_s2, path=path, content="s2-write\n")
        assert not res.get("error"), res
        # s2 has no prior read of its own, so no "modified since you last read"
        # warning attributable to s2's own tracker.
        warning = res.get("_warning", "")
        assert "since you last read" not in warning
