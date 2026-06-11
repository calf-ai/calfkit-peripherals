"""Tests for the calfkit `web_fetch` tool node (Stage D)."""

from __future__ import annotations

import base64
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from calfkit.nodes.tool import ToolNodeDef

from calfkit_tools.web_fetch.node import _ENGINE, web_fetch
from calfkit_tools.web_fetch.results import WebFetchError

# Patch target for the guard's HTTP client factory — the vendor's documented patch point
# (see tests/test_ssrf.py `mock_ssrf_client`). Driving the node through this seam (rather than
# the engine's `safe_download`) exercises the real engine + guard end-to-end.
_CREATE_CLIENT = 'calfkit_tools.web_fetch._vendor._ssrf.create_async_http_client'
_RESOLVE = 'calfkit_tools.web_fetch._vendor._ssrf.run_in_executor'

pytestmark = [pytest.mark.anyio]

# The node-decorated function is itself the ToolNodeDef; the original coroutine is reachable
# at `_tool.function` for direct unit calls (calfkit awaits it on the node's event loop).
_fetch = web_fetch._tool.function


class _StreamResponse:
    """Minimal fake of the streamed `httpx.Response` the guard reads off `client.stream(...)`.

    Mirrors `tests/test_ssrf.py._StreamResponse`: a single, non-redirect hop whose body is
    drained via `aiter_bytes()`.
    """

    def __init__(self, *, headers: dict[str, str], body: bytes, request_url: str) -> None:
        self.is_redirect = False
        self.headers = httpx.Headers(headers)
        self.status_code = 200
        self.request = httpx.Request('GET', request_url)
        self._body = body

    async def aiter_bytes(self):
        yield self._body


class _StreamCM:
    """Async context manager returned by the mocked `client.stream(...)`."""

    def __init__(self, response: _StreamResponse) -> None:
        self._response = response

    async def __aenter__(self) -> _StreamResponse:
        return self._response

    async def __aexit__(self, *exc: object) -> None:
        return None


def _serving(headers: dict[str, str], body: bytes, *, url: str):
    """Build (resolver, client_factory) mocks serving one response with the given content.

    The resolver returns a public IP so the SSRF guard admits the request; the factory yields
    an async-CM client whose `.stream(...)` returns the fake hop.
    """
    resolve = AsyncMock(return_value=[(2, 1, 6, '', ('93.184.215.14', 0))])

    response = _StreamResponse(headers=headers, body=body, request_url=url)

    def factory(**kwargs: object) -> object:
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.stream = lambda *a, **k: _StreamCM(response)
        return client

    return resolve, factory


# ---------------------------------------------------------------------------
# 1. Schema sanity
# ---------------------------------------------------------------------------


def test_node_is_tool_node_def():
    assert isinstance(web_fetch, ToolNodeDef)


def test_node_id_and_topics():
    assert web_fetch.node_id == 'tool_web_fetch'
    assert web_fetch.subscribe_topics == ['tool.web_fetch.input']
    assert web_fetch.publish_topic == 'tool.web_fetch.output'


def test_params_schema_requires_only_url():
    schema = web_fetch.tool_schema.parameters_json_schema
    assert schema['required'] == ['url']
    assert set(schema['properties']) == {'url'}
    assert schema['properties']['url']['type'] == 'string'


# ---------------------------------------------------------------------------
# 2. Text path
# ---------------------------------------------------------------------------


async def test_text_path_returns_markdown_dict():
    html = '<html><head><title>Hello</title></head><body><h1>Hi</h1><p>World</p></body></html>'
    resolve, factory = _serving(
        {'content-type': 'text/html; charset=utf-8'},
        html.encode(),
        url='https://example.com/',
    )

    with patch(_RESOLVE, resolve), patch(_CREATE_CLIENT, factory):
        result = await _fetch('https://example.com/')

    assert isinstance(result, dict)
    assert result['url'] == 'https://example.com/'
    assert result['title'] == 'Hello'
    # markdownify output, not raw HTML.
    assert 'World' in result['content']
    assert '<h1>' not in result['content']


# ---------------------------------------------------------------------------
# 3. Binary path
# ---------------------------------------------------------------------------


async def test_binary_path_returns_base64_dict():
    png_bytes = b'\x89PNG\r\n\x1a\n binary body'
    resolve, factory = _serving(
        {'content-type': 'image/png'},
        png_bytes,
        url='https://example.com/img.png',
    )

    with patch(_RESOLVE, resolve), patch(_CREATE_CLIENT, factory):
        result = await _fetch('https://example.com/img.png')

    assert isinstance(result, dict)
    assert result.keys() == {'data_base64', 'media_type'}
    assert result['media_type'] == 'image/png'
    assert base64.b64decode(result['data_base64']) == png_bytes


# ---------------------------------------------------------------------------
# 4. Error path
# ---------------------------------------------------------------------------


async def test_ssrf_blocked_url_raises_web_fetch_error():
    # 169.254.169.254 is the cloud-metadata IP — blocked even with allow_local, and the engine
    # is configured allow_local_urls=False, so a literal-IP fetch is rejected by the guard.
    with pytest.raises(WebFetchError):
        await _fetch('http://169.254.169.254/latest/meta-data/')


async def test_http_error_propagates_as_web_fetch_error():
    with patch(
        'calfkit_tools.web_fetch._vendor.common_tools.web_fetch.safe_download',
        new_callable=AsyncMock,
        side_effect=httpx.HTTPStatusError(
            'Not Found',
            request=httpx.Request('GET', 'https://example.com/missing'),
            response=httpx.Response(404, request=httpx.Request('GET', 'https://example.com/missing')),
        ),
    ):
        with pytest.raises(WebFetchError):
            await _fetch('https://example.com/missing')


# ---------------------------------------------------------------------------
# 5. Engine config (fixed safe defaults)
# ---------------------------------------------------------------------------


def test_engine_has_fixed_safe_defaults():
    assert _ENGINE.max_content_length == 50_000
    assert _ENGINE.allow_local_urls is False
    assert _ENGINE.timeout == 30
    # The request surface is `url` only: domain allow/block lists and headers stay at defaults.
    assert _ENGINE.allowed_domains is None
    assert _ENGINE.blocked_domains is None
    assert _ENGINE.headers is None
