"""The calfkit `web_fetch` tool node.

A thin, stateless calfkit wrapper over the vendored pydantic-ai SSRF-safe fetch engine
(`WebFetchLocalTool`). The node owns one module-level engine constructed with the fixed safe
defaults from `NODE.md` (§7) — the request surface is `url` only, NOT caller-overridable.

See `docs/design/node-port.md` (§3 "web_fetch specifics") and ADR-0001.
"""

from __future__ import annotations

import base64

from calfkit import agent_tool

from calfkit_tools.web_fetch._vendor.common_tools.web_fetch import WebFetchLocalTool
from calfkit_tools.web_fetch.results import FetchedBinary

__all__ = ["web_fetch"]

# One stateless engine for the node's lifetime. The SSRF guard's arguments are fixed at safe
# defaults (§7): no private/local URLs, a 30s timeout, and a 50k-char content cap. Domain
# allow/block lists and extra headers stay at their defaults — the LLM only supplies `url`.
_ENGINE = WebFetchLocalTool(
    max_content_length=50_000,
    allow_local_urls=False,
    timeout=30,
)


@agent_tool
async def web_fetch(url: str) -> dict:
    """Fetch a URL and return its content as markdown.

    Retrieves the page at the given URL through an SSRF-protected fetcher and converts the
    response body to clean markdown. HTML titles are extracted, JSON is pretty-printed, and
    other text formats are returned as-is. Long pages are truncated to ~50,000 characters and
    marked with a trailing `[Content truncated]`. Binary responses (PDFs, images, etc.) are
    returned base64-encoded with their media type.

    Args:
        url: The HTTP or HTTPS URL to fetch.

    Returns:
        For textual content, a dict with the fetched `url`, the page `title` (empty if none),
        and the `content` as markdown. For binary content, a dict with `data_base64` (the
        base64-encoded body) and its `media_type`.
    """
    result = await _ENGINE(url)

    if isinstance(result, FetchedBinary):
        return {
            "data_base64": base64.b64encode(result.data).decode("ascii"),
            "media_type": result.media_type,
        }

    # `WebFetchResult` is a TypedDict (a plain dict at runtime) — already JSON-serializable.
    return dict(result)
