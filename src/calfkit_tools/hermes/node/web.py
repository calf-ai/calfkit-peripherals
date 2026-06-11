"""The ``web_search`` and ``web_extract`` tool nodes.

Design: docs/design/node-port.md §3; docs/design/web-search-tool-port.md §4/§7;
ADR-0002 (env-var provider selection; the config resolver stays dormant).

Both nodes are **stateless** — no ctx, no tenancy. At import the node registers
its own name->class provider list (replacing the dropped plugin hooks), each
guarded by ``ImportError`` because the backends are extras-dependent. Per call
the active backend is chosen from an env var via ``registry.get_provider`` — the
vendored config resolver (``get_active_*``) is never called (ADR-0002).

- ``web_search`` (sync): ``WEB_SEARCH_BACKEND`` (default ``ddgs``). Returns the
  provider's ``search()`` contract dict as-is; clean ``error`` dict on an
  unknown/unavailable backend.
- ``web_extract`` (async): ``WEB_EXTRACT_BACKEND`` (default ``tavily``). Every
  URL is run through the vendored ``async_is_safe_url`` (fail-closed SSRF guard)
  before dispatch; unsafe URLs become per-URL error entries and never reach the
  provider. Safe URLs go to ``provider.extract`` (sync -> ``asyncio.to_thread``).
  Results are re-wrapped into ``{"success": True, "data": [...]}`` (the wrapping
  the dropped dispatcher used to do; web-search-tool-port.md §7).

Provider exceptions PROPAGATE: neither node wraps ``provider.search`` /
``provider.extract`` in a ``try``/``except`` — an unexpected raise bubbles up so
calfkit converts it into a ``FailedToolCall``. (The clean ``error`` dicts these
nodes return cover only *known* conditions — unknown/unavailable backend,
unsupported capability, blocked URL. In practice the shipped providers
self-handle network errors and return an error dict of their own.)
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

from calfkit import agent_tool

from calfkit_tools.hermes._vendor.agent.web_search_registry import get_provider, register_provider
from calfkit_tools.hermes._vendor.tools.url_safety import async_is_safe_url

DEFAULT_SEARCH_BACKEND = "ddgs"
DEFAULT_EXTRACT_BACKEND = "tavily"


def _register_builtin_providers() -> None:
    """Register the node-owned provider name->class list (web-search §4).

    Each registration is guarded by ``ImportError``: the backends are
    extras-dependent (``ddgs`` for ddgs; ``httpx`` for the rest). A missing
    extra simply leaves that provider unregistered — ``get_provider`` then
    returns ``None`` and the node surfaces a clean error.
    """
    try:
        import ddgs  # noqa: F401

        from calfkit_tools.hermes._vendor.tools.web_providers.ddgs import DDGSWebSearchProvider

        register_provider(DDGSWebSearchProvider())
    except ImportError:
        pass

    try:
        import httpx  # noqa: F401

        from calfkit_tools.hermes._vendor.tools.web_providers.brave_free import (
            BraveFreeWebSearchProvider,
        )
        from calfkit_tools.hermes._vendor.tools.web_providers.searxng import SearXNGWebSearchProvider
        from calfkit_tools.hermes._vendor.tools.web_providers.tavily import TavilyWebSearchProvider

        register_provider(BraveFreeWebSearchProvider())
        register_provider(SearXNGWebSearchProvider())
        register_provider(TavilyWebSearchProvider())
    except ImportError:
        pass


_register_builtin_providers()


@agent_tool
def web_search(query: str, limit: int = 5) -> dict:
    """Search the web and return ranked links.

    Args:
        query: The search query.
        limit: Maximum number of results to return (default 5).
    """
    # Deliberately sync: calfkit runs sync tools in a thread pool, which already
    # satisfies the design's "offload the blocking provider call off the event
    # loop" requirement — do NOT "fix" this to a bare async def (that would run
    # the blocking provider.search ON the event loop, without offload).
    name = os.getenv("WEB_SEARCH_BACKEND", DEFAULT_SEARCH_BACKEND)
    provider = get_provider(name)
    if provider is None:
        return {"success": False, "error": f"Unknown web search backend '{name}'"}
    if not provider.is_available():
        return {
            "success": False,
            "error": f"Web search backend '{name}' is not available (missing API key or dependency)",
        }
    return provider.search(query, limit=limit)


@agent_tool
async def web_extract(urls: list[str]) -> dict:
    """Extract readable content from one or more web pages.

    Result order is NOT guaranteed to match the input ``urls`` order: the
    returned ``data`` list is the provider-extracted (safe-URL) entries followed
    by the per-URL SSRF-blocked entries. Correlate results by their ``url``
    field, not by position.

    Args:
        urls: The page URLs to extract content from.
    """
    name = os.getenv("WEB_EXTRACT_BACKEND", DEFAULT_EXTRACT_BACKEND)
    provider = get_provider(name)
    if provider is None:
        return {"success": False, "error": f"Unknown web extract backend '{name}'"}
    if not provider.supports_extract():
        return {
            "success": False,
            "error": f"Web extract backend '{name}' does not support extract",
        }
    if not provider.is_available():
        return {
            "success": False,
            "error": f"Web extract backend '{name}' is not available (missing API key or dependency)",
        }

    # SSRF guard every URL before dispatch; unsafe URLs become per-URL error
    # entries and are never handed to the provider (fail-closed).
    safe_urls: list[str] = []
    blocked: list[dict[str, Any]] = []
    for url in urls:
        if await async_is_safe_url(url):
            safe_urls.append(url)
        else:
            blocked.append(
                {
                    "url": url,
                    "title": "",
                    "content": "",
                    "error": "URL blocked: private, loopback, or cloud-metadata target",
                }
            )

    extracted: list[dict[str, Any]] = []
    if safe_urls:
        # provider.extract is sync (httpx.post) -> offload off the event loop.
        extracted = await asyncio.to_thread(provider.extract, safe_urls)

    # Re-wrap into the documented success envelope (web-search-tool-port.md §7);
    # the node owns the wrapping the dropped dispatcher used to do.
    return {"success": True, "data": [*extracted, *blocked]}
