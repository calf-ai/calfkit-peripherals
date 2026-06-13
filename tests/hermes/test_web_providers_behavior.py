"""Behavioral tests for the web providers' HTTP/error branches.

``test_web_providers.py`` covers the success contract + the missing-credential
early-returns. This file targets the *uncovered* code: the whole ``search()``
HTTP body of searxng/brave_free (non-200, connect/timeout, malformed JSON,
score-sort, count/limit clamping, trailing-slash URL), tavily's interrupt and
httpx-raise and ``failed_urls`` branches, and ddgs's mid-iteration raise +
``limit<1`` clamp. All network is mocked — no real HTTP.
"""
import sys
import types

import httpx
import pytest

from calfkit_tools.hermes._vendor.tools.web_providers.brave_free import (
    BraveFreeWebSearchProvider,
)
from calfkit_tools.hermes._vendor.tools.web_providers.ddgs import DDGSWebSearchProvider
from calfkit_tools.hermes._vendor.tools.web_providers.searxng import SearXNGWebSearchProvider
from calfkit_tools.hermes._vendor.tools.web_providers.tavily import TavilyWebSearchProvider


class _Resp:
    """Fake httpx.Response for the providers' get()/post() call sites."""

    def __init__(self, *, status_code=200, json_data=None, json_exc=None, status_exc=None):
        self.status_code = status_code
        self._json_data = {} if json_data is None else json_data
        self._json_exc = json_exc
        self._status_exc = status_exc

    def raise_for_status(self):
        if self._status_exc is not None:
            raise self._status_exc

    def json(self):
        if self._json_exc is not None:
            raise self._json_exc
        return self._json_data


def _status_error(status):
    req = httpx.Request("GET", "https://test.local")
    return httpx.HTTPStatusError(
        f"HTTP {status}", request=req, response=httpx.Response(status, request=req)
    )


def _patch_get(monkeypatch, result, capture=None):
    """Patch httpx.get to return ``result`` (a _Resp) or raise it (an Exception)."""

    def fake_get(url, params=None, timeout=None, headers=None, **kwargs):
        if capture is not None:
            capture["url"] = url
            capture["params"] = params
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(httpx, "get", fake_get)


def _patch_post(monkeypatch, result, capture=None):
    def fake_post(url, json=None, timeout=None, **kwargs):
        if capture is not None:
            capture["url"] = url
            capture["json"] = json
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(httpx, "post", fake_post)


# ---------------------------------------------------------------------------
# SearXNG search() HTTP body
# ---------------------------------------------------------------------------

@pytest.fixture
def searxng_env(monkeypatch):
    monkeypatch.setenv("SEARXNG_URL", "http://localhost:8080")


def test_searxng_non_200_is_typed_error(monkeypatch, searxng_env):
    _patch_get(monkeypatch, _Resp(status_exc=_status_error(503)))
    result = SearXNGWebSearchProvider().search("q")
    assert result == {"success": False, "error": "SearXNG returned HTTP 503"}


def test_searxng_connect_error_names_the_instance(monkeypatch, searxng_env):
    _patch_get(monkeypatch, httpx.ConnectError("refused"))
    result = SearXNGWebSearchProvider().search("q")
    assert result["success"] is False
    assert "Could not reach SearXNG" in result["error"]
    assert "http://localhost:8080" in result["error"]


def test_searxng_malformed_json_is_typed_error(monkeypatch, searxng_env):
    _patch_get(monkeypatch, _Resp(json_exc=ValueError("not json")))
    result = SearXNGWebSearchProvider().search("q")
    assert result == {"success": False, "error": "Could not parse SearXNG response as JSON"}


def test_searxng_sorts_by_score_desc_and_caps_to_limit(monkeypatch, searxng_env):
    payload = {
        "results": [
            {"title": "low", "url": "u1", "content": "c1", "score": 0.1},
            {"title": "high", "url": "u2", "content": "c2", "score": 0.9},
            {"title": "mid", "url": "u3", "content": "c3", "score": 0.5},
        ]
    }
    _patch_get(monkeypatch, _Resp(json_data=payload))
    web = SearXNGWebSearchProvider().search("q", limit=2)["data"]["web"]
    assert [r["title"] for r in web] == ["high", "mid"]  # 0.9, 0.5 — low dropped by limit
    assert [r["position"] for r in web] == [1, 2]
    assert web[0]["description"] == "c2"  # content -> description


def test_searxng_missing_results_key_yields_empty(monkeypatch, searxng_env):
    _patch_get(monkeypatch, _Resp(json_data={}))
    result = SearXNGWebSearchProvider().search("q")
    assert result == {"success": True, "data": {"web": []}}


def test_searxng_trailing_slash_url_not_doubled(monkeypatch):
    monkeypatch.setenv("SEARXNG_URL", "http://x:8080/")
    capture = {}
    _patch_get(monkeypatch, _Resp(json_data={"results": []}), capture)
    SearXNGWebSearchProvider().search("q")
    assert capture["url"] == "http://x:8080/search"


# ---------------------------------------------------------------------------
# Brave Free search() HTTP body
# ---------------------------------------------------------------------------

@pytest.fixture
def brave_env(monkeypatch):
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "brave-key")


def test_brave_non_200_is_typed_error(monkeypatch, brave_env):
    _patch_get(monkeypatch, _Resp(status_exc=_status_error(429)))
    result = BraveFreeWebSearchProvider().search("q")
    assert result == {"success": False, "error": "Brave Search returned HTTP 429"}


def test_brave_request_error_is_typed_error(monkeypatch, brave_env):
    _patch_get(monkeypatch, httpx.ConnectTimeout("timed out"))
    result = BraveFreeWebSearchProvider().search("q")
    assert result["success"] is False
    assert "Could not reach Brave Search" in result["error"]


def test_brave_malformed_json_is_typed_error(monkeypatch, brave_env):
    _patch_get(monkeypatch, _Resp(json_exc=ValueError("nope")))
    result = BraveFreeWebSearchProvider().search("q")
    assert result == {"success": False, "error": "Could not parse Brave Search response as JSON"}


def test_brave_clamps_count_to_20(monkeypatch, brave_env):
    capture = {}
    _patch_get(monkeypatch, _Resp(json_data={"web": {"results": []}}), capture)
    BraveFreeWebSearchProvider().search("q", limit=99)
    assert capture["params"]["count"] == 20


def test_brave_count_floor_is_1(monkeypatch, brave_env):
    capture = {}
    _patch_get(monkeypatch, _Resp(json_data={"web": {"results": []}}), capture)
    BraveFreeWebSearchProvider().search("q", limit=0)
    assert capture["params"]["count"] == 1


@pytest.mark.parametrize("payload", [{}, {"web": None}, {"web": {}}])
def test_brave_missing_nested_results_yields_empty(monkeypatch, brave_env, payload):
    _patch_get(monkeypatch, _Resp(json_data=payload))
    result = BraveFreeWebSearchProvider().search("q")
    assert result == {"success": True, "data": {"web": []}}


def test_brave_truncates_results_to_limit(monkeypatch, brave_env):
    payload = {"web": {"results": [{"title": f"t{i}", "url": f"u{i}", "description": "d"} for i in range(10)]}}
    _patch_get(monkeypatch, _Resp(json_data=payload))
    web = BraveFreeWebSearchProvider().search("q", limit=3)["data"]["web"]
    assert len(web) == 3
    assert [r["position"] for r in web] == [1, 2, 3]


# ---------------------------------------------------------------------------
# Tavily interrupt / httpx-raise / failed_urls branches
# ---------------------------------------------------------------------------

_INTERRUPT = "calfkit_tools.hermes._vendor.tools.interrupt.is_interrupted"


@pytest.fixture
def tavily_env(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "tav-key")
    monkeypatch.delenv("TAVILY_BASE_URL", raising=False)
    monkeypatch.setattr(_INTERRUPT, lambda: False)


def test_tavily_search_interrupt_short_circuits(monkeypatch, tavily_env):
    monkeypatch.setattr(_INTERRUPT, lambda: True)
    _patch_post(monkeypatch, AssertionError("network must not be hit when interrupted"))
    result = TavilyWebSearchProvider().search("q")
    assert result == {"success": False, "error": "Interrupted"}


def test_tavily_search_httpx_raise_is_typed_error(monkeypatch, tavily_env):
    _patch_post(monkeypatch, _Resp(status_exc=_status_error(500)))
    result = TavilyWebSearchProvider().search("q")
    assert result["success"] is False
    assert result["error"].startswith("Tavily search failed:")


def test_tavily_extract_interrupt_returns_per_url_errors(monkeypatch, tavily_env):
    monkeypatch.setattr(_INTERRUPT, lambda: True)
    _patch_post(monkeypatch, AssertionError("network must not be hit when interrupted"))
    docs = TavilyWebSearchProvider().extract(["https://a.test", "https://b.test"])
    assert [d["error"] for d in docs] == ["Interrupted", "Interrupted"]
    assert [d["url"] for d in docs] == ["https://a.test", "https://b.test"]


def test_tavily_extract_httpx_raise_returns_per_url_errors(monkeypatch, tavily_env):
    _patch_post(monkeypatch, httpx.ConnectError("boom"))
    docs = TavilyWebSearchProvider().extract(["https://a.test"])
    assert len(docs) == 1
    assert docs[0]["error"].startswith("Tavily extract failed:")


def test_tavily_extract_normalizes_failed_urls(monkeypatch, tavily_env):
    payload = {"results": [], "failed_urls": ["https://broken.test"]}
    _patch_post(monkeypatch, _Resp(json_data=payload))
    docs = TavilyWebSearchProvider().extract(["https://a.test"])
    assert len(docs) == 1
    assert docs[0]["url"] == "https://broken.test"
    assert docs[0]["error"] == "extraction failed"
    assert docs[0]["metadata"]["sourceURL"] == "https://broken.test"


def test_tavily_extract_empty_urls_uses_blank_fallback(monkeypatch, tavily_env):
    payload = {"results": [{"title": "x", "raw_content": "y"}]}  # result has no url
    _patch_post(monkeypatch, _Resp(json_data=payload))
    docs = TavilyWebSearchProvider().extract([])
    assert docs[0]["url"] == ""
    assert docs[0]["content"] == "y"


# ---------------------------------------------------------------------------
# ddgs mid-iteration raise + limit<1 clamp
# ---------------------------------------------------------------------------

def _inject_ddgs(monkeypatch, ddgs_cls):
    fake_mod = types.ModuleType("ddgs")
    fake_mod.DDGS = ddgs_cls
    monkeypatch.setitem(sys.modules, "ddgs", fake_mod)


def test_ddgs_provider_error_is_typed_error(monkeypatch):
    class _RaisingDDGS:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def text(self, query, max_results=5):
            raise RuntimeError("rate limited")

    _inject_ddgs(monkeypatch, _RaisingDDGS)
    result = DDGSWebSearchProvider().search("q")
    assert result["success"] is False
    assert "DuckDuckGo search failed:" in result["error"]
    assert "rate limited" in result["error"]


def test_ddgs_limit_below_one_is_clamped_to_one(monkeypatch):
    class _TwoHitDDGS:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def text(self, query, max_results=5):
            for hit in [
                {"title": "a", "href": "https://a", "body": "ba"},
                {"title": "b", "href": "https://b", "body": "bb"},
            ][:max_results]:
                yield hit

    _inject_ddgs(monkeypatch, _TwoHitDDGS)
    result = DDGSWebSearchProvider().search("q", limit=0)
    assert result["success"] is True
    assert len(result["data"]["web"]) == 1
