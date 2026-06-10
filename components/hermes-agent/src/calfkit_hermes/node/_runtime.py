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
    if not ctx.agent_name:
        # Fail closed: an unstamped caller would otherwise format into
        # "None:default" and silently share one tenancy bucket with every
        # other unstamped caller. agent_name is stamped from the unspoofable
        # x-calf-emitter transport header (ADR-0004); its absence is a wiring
        # bug, not a default to paper over.
        raise ValueError(
            "session_key requires a non-empty ctx.agent_name (stamped from the "
            "x-calf-emitter header); got "
            f"{ctx.agent_name!r}. Refusing to merge unstamped callers into a "
            "shared tenancy bucket."
        )
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


def _ensure_session_isolated(session_key: str) -> None:
    """Register the session_key with upstream's per-task override registry.

    ``_resolve_container_task_id`` collapses every task_id to ``"default"``
    (one shared shell environment) UNLESS the task_id is registered via
    ``register_task_env_overrides`` — upstream's documented seam for
    infrastructure that needs per-task isolated sandboxes, which is exactly
    what this node is. An empty overrides dict changes nothing else: the only
    read site falls back to ``{}`` anyway. Racing threads register the same
    empty value, so no lock is needed.
    """
    from calfkit_hermes._vendor.tools import terminal_tool

    if session_key not in terminal_tool._task_env_overrides:
        terminal_tool.register_task_env_overrides(session_key, {})


def dispatch(name: str, args: dict[str, Any], *, session_key: str, **kwargs: Any) -> Any:
    """Call a vendored tool through the upstream registry seam.

    Passes the tenancy key as hermes' ``task_id``. Upstream handlers return a
    JSON string; parse it so agents receive structured data (the raw string is
    returned untouched if it isn't valid JSON).
    """
    ensure_tools_discovered()
    from calfkit_hermes._vendor.tools.registry import registry

    _ensure_session_isolated(session_key)
    # Drop None-valued keys: the node wrappers forward every upstream param
    # explicitly (None for those the agent omitted), but upstream handlers read
    # each field via ``args.get(name, default)`` — a PRESENT key carrying None
    # overrides the default with None (e.g. process(action="log") forwards
    # offset=None/limit=None -> read_log(None, None) -> TypeError). Dropping
    # None keys restores absent-means-default semantics. Verified safe for every
    # hermes tool: all upstream handlers source params through ``args.get`` (a
    # missing key is already treated as None / its declared default), so an
    # explicit None is indistinguishable from omission downstream.
    args = {k: v for k, v in args.items() if v is not None}
    result = registry.dispatch(name, args, task_id=session_key, **kwargs)
    try:
        return json.loads(result)
    except (json.JSONDecodeError, TypeError):
        return result
