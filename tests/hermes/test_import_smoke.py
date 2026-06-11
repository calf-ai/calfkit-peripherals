"""Import-smoke: the rewritten vendored tree + authored shims must import cleanly,
and the self-registering tools must actually register into the vendored registry.
"""
from pathlib import Path

# sys.path bootstrap is done once in conftest.py. SRC is kept for the module glob below.
SRC = Path(__file__).resolve().parents[2] / "src"


EXPECTED_TOOLS = {
    "terminal", "process", "execute_code",          # shell
    "read_file", "write_file", "patch", "search_files",  # files
    "todo",                                          # todo (per-session task list)
}


def test_core_vendored_modules_import():
    import calfkit_tools.hermes._vendor.tools.terminal_tool  # noqa: F401
    import calfkit_tools.hermes._vendor.tools.process_registry  # noqa: F401
    import calfkit_tools.hermes._vendor.tools.code_execution_tool  # noqa: F401
    import calfkit_tools.hermes._vendor.tools.file_tools  # noqa: F401
    import calfkit_tools.hermes._vendor.tools.environments.local  # noqa: F401


def test_discover_registers_shell_and_file_tools():
    from calfkit_tools.hermes._vendor.tools.registry import registry, discover_builtin_tools

    discover_builtin_tools()
    missing = {name for name in EXPECTED_TOOLS if registry.get_entry(name) is None}
    assert not missing, f"tools failed to register: {sorted(missing)}"


def test_all_vendored_modules_import():
    import importlib

    failures = {}
    files = sorted(SRC.glob("calfkit_tools/hermes/_vendor/**/*.py"))
    assert files, f"glob found no modules under {SRC} — package layout changed?"
    for path in files:
        mod = ".".join(path.relative_to(SRC).with_suffix("").parts)
        if mod.endswith(".__init__"):
            mod = mod[: -len(".__init__")]
        try:
            importlib.import_module(mod)
        except Exception as e:  # noqa: BLE001
            failures[mod] = f"{type(e).__name__}: {e}"
    assert not failures, "modules failed to import:\n" + "\n".join(
        f"  {m}: {e}" for m, e in failures.items()
    )


def test_todo_dispatch_without_store_returns_clean_error():
    """Vendor-only contract: `todo` registers, but with no Store wired the
    dispatched tool returns a clean error instead of crashing. The durable Store
    (Faust table) is deferred — see docs/design/todo-tool-port.md.
    """
    import json
    from calfkit_tools.hermes._vendor.tools.registry import registry, discover_builtin_tools

    discover_builtin_tools()
    result = json.loads(registry.dispatch("todo", {}))
    assert result["error"] == "TodoStore not initialized"
