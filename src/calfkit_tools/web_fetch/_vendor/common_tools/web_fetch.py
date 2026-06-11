"""Web fetch engine (vendored from pydantic-ai, trimmed to its reusable core).

Fetches web pages via the SSRF-protected guard and converts the body to markdown using the
`markdownify` library, with content-type gating, `<title>` extraction, JSON fencing, a 50k
char cap, and `Accept: text/markdown`.

calfkit port (§5 / §6):
  * `safe_download` repointed to the vendored sibling `_ssrf` module.
  * `is_text_like_media_type` inlined (was `pydantic_ai._utils`).
  * the 4 pydantic glue symbols dropped (`web_fetch_tool()`, `Tool`, `ModelRetry`,
    `BinaryContent`) → recorded in `NODE.md`. Binary content is returned as the neutral
    first-party `FetchedBinary`; fetch failures raise the neutral `WebFetchError` (mapped to
    the node's `error:` reply).
  * the streaming byte cap lives in the guard (`_ssrf.safe_download`); this engine's 50k char
    cap still runs downstream on the *decoded* text.
"""

from __future__ import annotations

import json
import re
from dataclasses import KW_ONLY, dataclass, field
from typing import TypedDict

import httpx

from calfkit_tools.web_fetch._vendor._ssrf import ResponseTooLargeError, safe_download
from calfkit_tools.web_fetch.results import FetchedBinary, WebFetchError

try:
    from markdownify import markdownify as md
except ImportError as _import_error:  # pragma: no cover
    raise ImportError('Please install `markdownify` to use the web fetch engine.') from _import_error

__all__ = ('WebFetchLocalTool', 'WebFetchResult')

_EXCESSIVE_NEWLINES_RE = re.compile(r'\n{3,}')


def is_text_like_media_type(media_type: str) -> bool:
    """Check if a media type represents text-like content.

    Returns True for `text/*`, JSON, XML, YAML, and their structured syntax suffixes.

    Inlined verbatim from `pydantic_ai._utils` (calfkit port, §4).
    """
    return (
        media_type.startswith('text/')
        or media_type == 'application/json'
        or media_type.endswith('+json')
        or media_type == 'application/xml'
        or media_type.endswith('+xml')
        or media_type in ('application/x-yaml', 'application/yaml')
    )


class WebFetchResult(TypedDict):
    """Result of fetching a web page."""

    url: str
    """The URL that was fetched."""
    title: str
    """The page title, or empty string if not found."""
    content: str
    """The page content converted to markdown."""


@dataclass
class WebFetchLocalTool:
    """Fetches a URL and converts the response to markdown."""

    _: KW_ONLY

    max_content_length: int | None
    """Maximum character length of returned content. None for no limit."""

    allow_local_urls: bool
    """Whether to allow fetching from private/local IP addresses."""

    timeout: int
    """Request timeout in seconds."""

    allowed_domains: list[str] | None = field(default=None)
    """Only fetch from these domains (exact hostname match). Raises `WebFetchError` on violation."""

    blocked_domains: list[str] | None = field(default=None)
    """Never fetch from these domains (exact hostname match). Raises `WebFetchError` on violation."""

    headers: dict[str, str] | None = field(default=None)
    """Additional HTTP headers to include in the request."""

    async def __call__(self, url: str) -> WebFetchResult | FetchedBinary:
        """Fetches the content of a web page at the given URL and returns it as markdown.

        For textual content (HTML, JSON, plain text), returns a `WebFetchResult`. For binary
        content (PDF, images, etc.), returns a neutral `FetchedBinary` (§6).

        Args:
            url: The URL to fetch.

        Returns:
            The fetched page content.

        Raises:
            WebFetchError: If the fetch fails (SSRF-blocked, HTTP error, bad URL, oversize, ...).
        """
        request_headers = {'Accept': 'text/markdown, text/html;q=0.9, */*;q=0.8'}
        if self.headers:
            request_headers.update(self.headers)

        try:
            response = await safe_download(
                url,
                allow_local=self.allow_local_urls,
                timeout=self.timeout,
                headers=request_headers,
                allowed_domains=self.allowed_domains,
                blocked_domains=self.blocked_domains,
            )
        except (ValueError, ResponseTooLargeError, httpx.HTTPStatusError, httpx.RequestError) as e:
            raise WebFetchError(f'Failed to fetch {url}: {e}') from e

        media_type = response.headers.get('content-type', '')
        media_type = media_type.split(';')[0].strip().lower()

        title = ''

        if not media_type or is_text_like_media_type(media_type):
            text = response.text

            if media_type in ('text/markdown', 'text/x-markdown'):
                content = text
            elif not media_type or media_type in ('text/html', 'application/xhtml+xml'):
                title = _extract_title(text)
                content = md(text, strip=['img', 'script', 'style'])
            elif media_type == 'application/json':
                try:
                    parsed = json.loads(text)
                    content = f'```json\n{json.dumps(parsed, indent=2)}\n```'
                except (json.JSONDecodeError, ValueError):
                    content = text
            else:
                content = text
        else:
            return FetchedBinary(data=response.content, media_type=media_type)

        content = _clean_whitespace(content)

        if self.max_content_length is not None and len(content) > self.max_content_length:
            content = content[: self.max_content_length] + '\n\n[Content truncated]'

        return WebFetchResult(url=url, title=title, content=content)


_TITLE_RE = re.compile(r'<title[^>]*>(.*?)</title>', re.IGNORECASE | re.DOTALL)


def _extract_title(html: str) -> str:
    """Extract the <title> from HTML."""
    match = _TITLE_RE.search(html)
    return match.group(1).strip() if match else ''


def _clean_whitespace(text: str) -> str:
    """Collapse runs of 3+ newlines into 2 newlines."""
    return _EXCESSIVE_NEWLINES_RE.sub('\n\n', text).strip()
