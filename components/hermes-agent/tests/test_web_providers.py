"""Unit tests for the Tier-0 web providers (Stage C).

Each provider:
  - instantiates and reports a stable ``name`` + capability flags;
  - ``search()`` returns the ``{success, data: {web: [...]}}`` contract;
  - tavily ``extract()`` returns the bare list shape ``[{url, title,
    content, raw_content, metadata, error?}]`` (the dispatcher's ``{success,
    data}`` wrap is added by the node, not the provider — design doc §7).

ALL network is mocked — no real HTTP. ddgs is mocked by injecting a fake
``ddgs`` module into ``sys.modules`` (the provider does ``from ddgs import
DDGS`` lazily inside ``search``); tavily by patching ``httpx.post``.
"""
import sys
import types

import pytest

from calfkit_hermes._vendor.tools.web_providers.ddgs import DDGSWebSearchProvider
from calfkit_hermes._vendor.tools.web_providers.brave_free import (
    BraveFreeWebSearchProvider,
)
from calfkit_hermes._vendor.tools.web_providers.searxng import SearXNGWebSearchProvider
from calfkit_hermes._vendor.tools.web_providers.tavily import TavilyWebSearchProvider


# ---------------------------------------------------------------------------
# Instantiation + capability reporting
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "cls, name, search, extract",
    [
        (DDGSWebSearchProvider, "ddgs", True, False),
        (BraveFreeWebSearchProvider, "brave-free", True, False),
        (SearXNGWebSearchProvider, "searxng", True, False),
        (TavilyWebSearchProvider, "tavily", True, True),
    ],
)
def test_provider_instantiates_and_reports_capabilities(cls, name, search, extract):
    provider = cls()
    assert provider.name == name
    assert provider.supports_search() is search
    assert provider.supports_extract() is extract
    # display_name + setup schema are cheap, side-effect-free metadata.
    assert isinstance(provider.display_name, str) and provider.display_name
    assert isinstance(provider.get_setup_schema(), dict)
    # is_available() must not do network I/O — just confirm it returns a bool.
    assert isinstance(provider.is_available(), bool)


# ---------------------------------------------------------------------------
# ddgs.search() — fake `ddgs` module injected into sys.modules
# ---------------------------------------------------------------------------

class _FakeDDGS:
    """Stand-in for ``ddgs.DDGS`` context manager yielding text() hits."""

    _hits = [
        {"title": "First", "href": "https://example.com/1", "body": "first body"},
        {"title": "Second", "url": "https://example.com/2", "body": "second body"},
    ]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def text(self, query, max_results=5):
        for hit in self._hits[:max_results]:
            yield hit


@pytest.fixture
def fake_ddgs(monkeypatch):
    fake_mod = types.ModuleType("ddgs")
    fake_mod.DDGS = _FakeDDGS
    monkeypatch.setitem(sys.modules, "ddgs", fake_mod)
    return fake_mod


def test_ddgs_search_returns_contract(fake_ddgs):
    result = DDGSWebSearchProvider().search("python", limit=5)
    assert result["success"] is True
    web = result["data"]["web"]
    assert len(web) == 2
    assert web[0] == {
        "title": "First",
        "url": "https://example.com/1",
        "description": "first body",
        "position": 1,
    }
    # Second hit uses the `url` key (not `href`) — both are accepted.
    assert web[1]["url"] == "https://example.com/2"
    assert web[1]["position"] == 2


def test_ddgs_search_respects_limit(fake_ddgs):
    result = DDGSWebSearchProvider().search("python", limit=1)
    assert result["success"] is True
    assert len(result["data"]["web"]) == 1


def test_ddgs_search_missing_package_is_typed_error(monkeypatch):
    """When ``ddgs`` is not importable, search() returns a typed error,
    not a raise (the contract's failure shape)."""
    # Ensure the import fails regardless of whether ddgs is installed.
    monkeypatch.setitem(sys.modules, "ddgs", None)
    result = DDGSWebSearchProvider().search("python")
    assert result["success"] is False
    assert "ddgs" in result["error"].lower()


# ---------------------------------------------------------------------------
# tavily.search() / tavily.extract() — httpx.post patched
# ---------------------------------------------------------------------------

class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


@pytest.fixture
def tavily_env(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "test-key")
    monkeypatch.delenv("TAVILY_BASE_URL", raising=False)


def _patch_httpx_post(monkeypatch, payload, captured=None):
    import httpx

    def fake_post(url, json=None, timeout=None, **kwargs):
        if captured is not None:
            captured["url"] = url
            captured["json"] = json
        return _FakeResponse(payload)

    monkeypatch.setattr(httpx, "post", fake_post)


def test_tavily_search_returns_contract(monkeypatch, tavily_env):
    payload = {
        "results": [
            {"title": "T1", "url": "https://a.test", "content": "c1"},
            {"title": "T2", "url": "https://b.test", "content": "c2"},
        ]
    }
    captured = {}
    _patch_httpx_post(monkeypatch, payload, captured)

    result = TavilyWebSearchProvider().search("hello", limit=5)

    assert result["success"] is True
    web = result["data"]["web"]
    assert web[0] == {
        "title": "T1",
        "url": "https://a.test",
        "description": "c1",
        "position": 1,
    }
    assert web[1]["position"] == 2
    # Hit the /search endpoint with the api_key injected.
    assert captured["url"].endswith("/search")
    assert captured["json"]["api_key"] == "test-key"


def test_tavily_search_missing_key_is_typed_error(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    # httpx.post must never be reached; patch it to fail loudly if it is.
    import httpx

    def explode(*a, **k):  # pragma: no cover - should not be called
        raise AssertionError("network must not be hit when key is missing")

    monkeypatch.setattr(httpx, "post", explode)

    result = TavilyWebSearchProvider().search("hello")
    assert result["success"] is False
    assert "TAVILY_API_KEY" in result["error"]


def test_tavily_extract_returns_raw_list_shape(monkeypatch, tavily_env):
    payload = {
        "results": [
            {
                "url": "https://a.test",
                "title": "Doc A",
                "raw_content": "raw A",
                "content": "content A",
            }
        ],
        "failed_results": [
            {"url": "https://b.test", "error": "boom"},
        ],
    }
    captured = {}
    _patch_httpx_post(monkeypatch, payload, captured)

    docs = TavilyWebSearchProvider().extract(["https://a.test", "https://b.test"])

    # Bare list (NOT wrapped in {success, data}) — the node owns the wrap.
    assert isinstance(docs, list)
    assert len(docs) == 2

    ok = docs[0]
    assert ok["url"] == "https://a.test"
    assert ok["title"] == "Doc A"
    assert ok["content"] == "raw A"
    assert ok["raw_content"] == "raw A"
    assert ok["metadata"]["sourceURL"] == "https://a.test"
    assert "error" not in ok

    failed = docs[1]
    assert failed["url"] == "https://b.test"
    assert failed["error"] == "boom"

    assert captured["url"].endswith("/extract")


def test_tavily_extract_missing_key_returns_per_url_errors(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    docs = TavilyWebSearchProvider().extract(["https://a.test", "https://b.test"])
    assert isinstance(docs, list)
    assert len(docs) == 2
    assert all("error" in d and d["url"] for d in docs)


# ---------------------------------------------------------------------------
# brave-free / searxng — typed-error path when their config env is absent
# ---------------------------------------------------------------------------

def test_brave_search_without_key_is_typed_error(monkeypatch):
    monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)
    result = BraveFreeWebSearchProvider().search("hi")
    assert result["success"] is False
    assert "BRAVE_SEARCH_API_KEY" in result["error"]


def test_searxng_search_without_url_is_typed_error(monkeypatch):
    monkeypatch.delenv("SEARXNG_URL", raising=False)
    result = SearXNGWebSearchProvider().search("hi")
    assert result["success"] is False
    assert "SEARXNG_URL" in result["error"]
