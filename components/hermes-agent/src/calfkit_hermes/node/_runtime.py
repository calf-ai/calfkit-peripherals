"""Shared runtime for the hermes tool nodes: tenancy keying + the dispatch seam.

Every node in this package drives the vendored hermes tools wholesale through
``registry.dispatch`` — the upstream public seam — never through tool internals.
Tenancy (ADR-0004): the per-caller isolation key is fed to hermes as its
``task_id``, so the vendored per-``task_id`` registries (terminal environments,
file trackers, process registry) provide the isolation themselves.
"""

from __future__ import annotations

import json
import threading
from typing import Any

from calfkit import ToolContext

DEFAULT_SESSION_ID = "default"

_discover_lock = threading.Lock()
_discovered = False


def session_key(ctx: ToolContext) -> str:
    """Derive the tenancy key: agent identity + optional client-wired session id.

    Neither half is agent-authored: ``agent_name`` is stamped from the calfkit
    emitter header, ``session_id`` is wired by the invoking client via deps
    (absent -> agent-lifetime scope). See ADR-0004 and CONTEXT.md *Session*.
    """
    deps = ctx.deps or {}
    raw = deps.get("session_id")
    session_id = str(raw) if raw is not None and str(raw) else DEFAULT_SESSION_ID
    return f"{ctx.agent_name}:{session_id}"


def ensure_tools_discovered() -> None:
    """Populate the vendored tool registry exactly once per process.

    Without this the registry is silently empty — tools self-register only
    when their modules are imported by ``discover_builtin_tools()``.
    """
    global _discovered
    if _discovered:
        return
    with _discover_lock:
        if _discovered:
            return
        from calfkit_hermes._vendor.tools.registry import discover_builtin_tools

        discover_builtin_tools()
        _discovered = True


def dispatch(name: str, args: dict[str, Any], *, session_key: str, **kwargs: Any) -> Any:
    """Call a vendored tool through the upstream registry seam.

    Passes the tenancy key as hermes' ``task_id``. Upstream handlers return a
    JSON string; parse it so agents receive structured data (the raw string is
    returned untouched if it isn't valid JSON).
    """
    ensure_tools_discovered()
    from calfkit_hermes._vendor.tools.registry import registry

    result = registry.dispatch(name, args, task_id=session_key, **kwargs)
    try:
        return json.loads(result)
    except (json.JSONDecodeError, TypeError):
        return result
