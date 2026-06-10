"""The ``execute_code`` tool node: a sandboxed Python REPL with an RPC tool bridge.

Design: docs/design/node-port.md §3 "execute_code specifics"; ADR-0004 (tenancy).

A 1:1 calfkit tool node over the vendored hermes ``execute_code`` tool. The model
hands it a Python script; the script runs in a sandboxed child process that can
call back into a subset of hermes tools (``from hermes_tools import ...``) over an
RPC bridge. The node drives the tool wholesale through the ``dispatch`` seam — no
reaching into vendor internals — forwarding two extra kwargs:

- ``task_id`` (= ``session_key(ctx)``): the tenancy key. In-script tool calls
  inherit this same ``task_id`` (the RPC bridge forwards it), so the sandbox
  shares the node session's shell environment and file trackers — isolation
  propagates (ADR-0004).
- ``enabled_tools``: which tool stubs the sandbox may import. The sandbox uses
  the intersection of this set with upstream's ``SANDBOX_ALLOWED_TOOLS``
  (terminal, read_file, write_file, patch, search_files, web_search,
  web_extract), so listing a non-sandboxable tool (process, todo) is harmless.

Trust model (see NODE.md): a script runs arbitrary Python on the node host — the
same trust class as ``terminal``. Deploy sandboxed or single-tenant.

Sync by design: the upstream handler blocks (it spawns and waits on the child
process); calfkit runs sync tools in its thread pool.
"""

from __future__ import annotations

import os

from calfkit import ToolContext, agent_tool

from calfkit_hermes.node._runtime import dispatch, session_key

# The hermes tool set this node exposes to the sandbox by default: every other
# hermes tool minus ``execute_code`` itself (a script can't recursively spawn
# the sandbox). Upstream intersects this with SANDBOX_ALLOWED_TOOLS, so the
# non-sandboxable members (``process``, ``todo``) simply never generate a stub.
_DEFAULT_ENABLED_TOOLS = frozenset(
    {"terminal", "process", "read_file", "write_file", "patch", "search_files", "todo"}
)

_ENABLED_TOOLS_ENV = "EXECUTE_CODE_ENABLED_TOOLS"


def _enabled_tools() -> set[str]:
    """Resolve the sandbox-exposed tool set from node config.

    ``EXECUTE_CODE_ENABLED_TOOLS`` is a comma-separated list of tool names; a
    blank/whitespace-only value falls back to the default set. Node config, not
    agent-authored — the agent only supplies ``code``.
    """
    raw = os.environ.get(_ENABLED_TOOLS_ENV)
    if raw is None:
        return set(_DEFAULT_ENABLED_TOOLS)
    names = {name.strip() for name in raw.split(",") if name.strip()}
    return names or set(_DEFAULT_ENABLED_TOOLS)


@agent_tool
def execute_code(ctx: ToolContext, code: str) -> dict | str:
    """Run a Python script that can call Hermes tools programmatically. Use this
    when you need 3+ tool calls with processing logic between them, need to
    filter/reduce large tool outputs before they enter your context, need
    conditional branching (if X then Y else Z), or need to loop (fetch N pages,
    process N files, retry on failure).

    Use normal tool calls instead when: single tool call with no processing, you
    need to see the full result and apply complex reasoning, or the task requires
    interactive user input.

    Hermes tools are imported with ``from hermes_tools import ...`` (e.g.
    terminal, read_file, write_file, patch, search_files). terminal() is
    foreground-only (no background or pty). Scripts run in the node's working
    directory with the active venv's python; in-script tool calls (terminal,
    file tools) operate in your own session's shell environment.

    Limits: 5-minute timeout, 50KB stdout cap, max 50 tool calls per script.
    Print your final result to stdout. Use Python stdlib (json, re, math, csv,
    datetime, collections, etc.) for processing between tool calls. Also built in
    (no import): json_parse(text), shell_quote(s), retry(fn, max_attempts, delay).

    Args:
        code: Python code to execute. Import tools with
            ``from hermes_tools import ...`` and print your final result to
            stdout.
    """
    from calfkit_hermes._vendor.tools.code_execution_tool import SANDBOX_ALLOWED_TOOLS

    enabled = _enabled_tools()
    # Fail closed: upstream expands an EMPTY intersection with
    # SANDBOX_ALLOWED_TOOLS back to the FULL set — a typo'd or
    # non-sandboxable-only override would silently grant every tool.
    if not (enabled & SANDBOX_ALLOWED_TOOLS):
        return {
            "error": (
                "EXECUTE_CODE_ENABLED_TOOLS resolves to no sandbox-callable tools "
                f"(got: {sorted(enabled)}; sandbox-callable: "
                f"{sorted(SANDBOX_ALLOWED_TOOLS)}). Refusing to run rather than "
                "fall back to the full tool set."
            )
        }
    return dispatch(
        "execute_code",
        {"code": code},
        session_key=session_key(ctx),
        # Upstream types this Optional[List[str]] and rebuilds a set internally;
        # pass a list to match the documented contract.
        enabled_tools=sorted(enabled),
    )
