"""Shim for hermes-agent ``model_tools`` — only the symbols the vendored tree uses.

- ``_run_async`` / ``_sanitize_tool_error``: faithful pure helpers used by the vendored
  ``registry.dispatch`` (the shell/file tools register no async handlers, so ``_run_async``
  is off the hot path here, but it stays behaviour-correct).
- ``handle_function_call``: the Programmatic-Tool-Calling (PTC) dispatcher used by
  ``code_execution_tool``'s RPC bridge. The full upstream version layers a tool-search
  bridge + hooks + guardrails; here it is a thin delegate to the vendored registry's own
  ``dispatch`` (lookup + async-bridge + error-sanitize), scoped to this node's tools.
"""
import asyncio
import concurrent.futures
import re

# --- _sanitize_tool_error (constants + body copied verbatim from upstream) ---------
_TOOL_ERROR_ROLE_TAG_RE = re.compile(
    r'</?(?:tool_call|function_call|result|response|output|input|system|assistant|user)>',
    re.IGNORECASE,
)
_TOOL_ERROR_FENCE_OPEN_RE = re.compile(r'^\s*```(?:json|xml|html|markdown)?\s*', re.MULTILINE)
_TOOL_ERROR_FENCE_CLOSE_RE = re.compile(r'\s*```\s*$', re.MULTILINE)
_TOOL_ERROR_CDATA_RE = re.compile(r'<!\[CDATA\[.*?\]\]>', re.DOTALL)
_TOOL_ERROR_MAX_LEN = 2000


def _sanitize_tool_error(error_msg: str) -> str:
    """Strip structural framing tokens from a tool error before showing it to the model."""
    if not error_msg:
        return "[TOOL_ERROR] "
    sanitized = _TOOL_ERROR_ROLE_TAG_RE.sub("", error_msg)
    sanitized = _TOOL_ERROR_FENCE_OPEN_RE.sub("", sanitized)
    sanitized = _TOOL_ERROR_FENCE_CLOSE_RE.sub("", sanitized)
    sanitized = _TOOL_ERROR_CDATA_RE.sub("", sanitized)
    if len(sanitized) > _TOOL_ERROR_MAX_LEN:
        sanitized = sanitized[: _TOOL_ERROR_MAX_LEN - 3] + "..."
    return f"[TOOL_ERROR] {sanitized}"


def _run_async(coro):
    """Run a coroutine from sync code (sync->async bridge for async tool handlers).

    Simplified vs. upstream's persistent-loop optimization (which keeps cached async
    clients alive). The shell/file toolset registers no async handlers, so this is not
    exercised on the hot path; it stays correct for any future async handler.
    """
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    if running and running.is_running():
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(lambda: asyncio.run(coro)).result()
    return asyncio.run(coro)


def handle_function_call(function_name, function_args, task_id=None, **kwargs):
    """PTC dispatcher: route an in-sandbox tool call into the vendored registry.

    Thin delegate to ``registry.dispatch`` (this node's own tools). Imported lazily to
    avoid an import cycle (registry imports this module for ``_run_async``).
    """
    from calfkit_tools.hermes._vendor.tools.registry import registry

    return registry.dispatch(function_name, function_args, task_id=task_id)
