"""First-party result types at the engine/node boundary.

These are neutral, dependency-free stand-ins for the pydantic-ai glue types the upstream
engine returned (see ``NODE.md``). They live OUTSIDE ``_vendor/`` so the vendored engine
stays free of any pydantic-ai import; the calfkit node re-wraps ``FetchedBinary`` into
pydantic-ai's ``BinaryContent`` at port time.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["FetchedBinary", "WebFetchError"]


class WebFetchError(Exception):
    """Raised when a fetch fails (SSRF-blocked, HTTP error, bad URL, oversize, ...).

    A neutral first-party stand-in for upstream ``ModelRetry`` (§4/§5). The vendored engine
    raises it instead of ``pydantic_ai.exceptions.ModelRetry``; the calfkit node re-raises it as
    ``ModelRetry`` at the boundary, which calfkit surfaces to the model as a recoverable retry.
    """


@dataclass(frozen=True, slots=True)
class FetchedBinary:
    """Binary (non-text) fetch result.

    A plain stdlib dataclass — no pydantic import. Replaces upstream ``BinaryContent`` (§6);
    the node maps ``FetchedBinary(data, media_type)`` → ``BinaryContent(data=..., media_type=...)``.
    """

    data: bytes
    """The raw response body."""

    media_type: str = "application/octet-stream"
    """The response media type (e.g. ``application/pdf``); defaults to ``application/octet-stream``."""
