"""Tests for the ``web_search`` and ``web_extract`` tool nodes (Stage D).

Design: docs/design/node-port.md §3/§5; docs/design/web-search-tool-port.md §4/§7;
ADR-0002 (env-var provider selection; the config resolver stays dormant).

Stateless nodes — no ctx, no tenancy. Provider chosen per call from an env var
via ``registry.get_provider(name)``; the dormant config resolver
(``get_active_*``) must never be called. ``web_extract`` runs the vendored
``async_is_safe_url`` over every URL before dispatch (fail-closed SSRF guard).

Fake providers (no network) drive selection and contract tests.
"""

import asyncio

import pytest

from calfkit_hermes._vendor.agent.web_search_provider import WebSearchProvider
from calfkit_hermes._vendor.agent.web_search_registry import (
    _reset_for_tests,
    register_provider,
)
from calfkit_hermes.node import web
from calfkit_hermes.node.web import web_extract as web_extract_node
from calfkit_hermes.node.web import web_search as web_search_node

# The node defs are ToolNodeDef (not callable); exercise the underlying funcs.
web_search = web_search_node._tool.function
web_extract = web_extract_node._tool.function


@pytest.fixture(autouse=True)
def clean_registry():
    """Start each test with an empty provider registry."""
    _reset_for_tests()
    yield
    _reset_for_tests()


# ---------------------------------------------------------------------------
# Fake providers (no network)
# ---------------------------------------------------------------------------


class FakeSearchProvider(WebSearchProvider):
    def __init__(self, name="ddgs", available=True):
        self._name = name
        self._available = available
        self.search_calls = []

    @property
    def name(self) -> str:
        return self._name

    def is_available(self) -> bool:
        return self._available

    def supports_search(self) -> bool:
        return True

    def search(self, query: str, limit: int = 5):
        self.search_calls.append((query, limit))
        return {
            "success": True,
            "data": {"web": [{"title": "t", "url": "https://x", "description": "d", "position": 1}]},
        }


class FakeExtractProvider(WebSearchProvider):
    def __init__(self, name="tavily", available=True, supports_extract=True):
        self._name = name
        self._available = available
        self._supports_extract = supports_extract
        self.extract_calls = []

    @property
    def name(self) -> str:
        return self._name

    def is_available(self) -> bool:
        return self._available

    def supports_search(self) -> bool:
        return False

    def supports_extract(self) -> bool:
        return self._supports_extract

    def extract(self, urls, **kwargs):
        self.extract_calls.append(list(urls))
        return [
            {"url": u, "title": "T", "content": "C", "raw_content": "C", "metadata": {}}
            for u in urls
        ]


# ===========================================================================
# web_search
# ===========================================================================


class TestWebSearch:
    def test_default_backend_is_ddgs(self, monkeypatch):
        monkeypatch.delenv("WEB_SEARCH_BACKEND", raising=False)
        provider = FakeSearchProvider(name="ddgs")
        register_provider(provider)
        result = web_search("hello")
        assert result["success"] is True
        assert provider.search_calls == [("hello", 5)]

    def test_env_selection_picks_provider(self, monkeypatch):
        monkeypatch.setenv("WEB_SEARCH_BACKEND", "brave-free")
        chosen = FakeSearchProvider(name="brave-free")
        register_provider(FakeSearchProvider(name="ddgs"))
        register_provider(chosen)
        web_search("q", limit=3)
        assert chosen.search_calls == [("q", 3)]

    def test_unknown_provider_returns_error_dict(self, monkeypatch):
        monkeypatch.setenv("WEB_SEARCH_BACKEND", "does-not-exist")
        result = web_search("q")
        assert "error" in result
        assert result.get("success") is False

    def test_unavailable_provider_returns_error_dict(self, monkeypatch):
        monkeypatch.setenv("WEB_SEARCH_BACKEND", "ddgs")
        register_provider(FakeSearchProvider(name="ddgs", available=False))
        result = web_search("q")
        assert "error" in result
        assert result.get("success") is False

    def test_returns_provider_contract_as_is(self, monkeypatch):
        monkeypatch.setenv("WEB_SEARCH_BACKEND", "ddgs")
        register_provider(FakeSearchProvider(name="ddgs"))
        result = web_search("q")
        assert result["data"]["web"][0]["url"] == "https://x"


# ===========================================================================
# web_extract
# ===========================================================================


def run_extract(urls):
    return asyncio.run(web_extract(urls))


class TestWebExtract:
    def test_default_backend_is_tavily(self, monkeypatch):
        monkeypatch.delenv("WEB_EXTRACT_BACKEND", raising=False)
        provider = FakeExtractProvider(name="tavily")
        register_provider(provider)
        result = run_extract(["https://example.com"])
        assert result["success"] is True
        assert provider.extract_calls == [["https://example.com"]]

    def test_env_selection_picks_provider(self, monkeypatch):
        monkeypatch.setenv("WEB_EXTRACT_BACKEND", "myextract")
        provider = FakeExtractProvider(name="myextract")
        register_provider(provider)
        run_extract(["https://example.com"])
        assert provider.extract_calls == [["https://example.com"]]

    def test_unknown_provider_returns_error_dict(self, monkeypatch):
        monkeypatch.setenv("WEB_EXTRACT_BACKEND", "nope")
        result = run_extract(["https://example.com"])
        assert "error" in result
        assert result.get("success") is False

    def test_provider_without_extract_returns_error(self, monkeypatch):
        monkeypatch.setenv("WEB_EXTRACT_BACKEND", "tavily")
        register_provider(FakeExtractProvider(name="tavily", supports_extract=False))
        result = run_extract(["https://example.com"])
        assert "error" in result
        assert result.get("success") is False

    def test_rewrap_shape_success_with_data_list(self, monkeypatch):
        monkeypatch.setenv("WEB_EXTRACT_BACKEND", "tavily")
        register_provider(FakeExtractProvider(name="tavily"))
        result = run_extract(["https://example.com", "https://example.org"])
        assert result["success"] is True
        assert isinstance(result["data"], list)
        assert len(result["data"]) == 2
        assert {d["url"] for d in result["data"]} == {
            "https://example.com",
            "https://example.org",
        }

    def test_ssrf_guard_blocks_metadata_endpoint(self, monkeypatch):
        monkeypatch.setenv("WEB_EXTRACT_BACKEND", "tavily")
        provider = FakeExtractProvider(name="tavily")
        register_provider(provider)
        result = run_extract(["http://169.254.169.254/"])
        # The provider never sees the unsafe URL.
        assert provider.extract_calls == []
        # It becomes a per-URL error entry in the data list.
        assert result["data"][0]["url"] == "http://169.254.169.254/"
        assert "error" in result["data"][0]

    def test_ssrf_guard_blocks_loopback(self, monkeypatch):
        monkeypatch.setenv("WEB_EXTRACT_BACKEND", "tavily")
        provider = FakeExtractProvider(name="tavily")
        register_provider(provider)
        result = run_extract(["http://127.0.0.1/"])
        assert provider.extract_calls == []
        assert "error" in result["data"][0]

    def test_mixed_safe_and_unsafe(self, monkeypatch):
        monkeypatch.setenv("WEB_EXTRACT_BACKEND", "tavily")
        provider = FakeExtractProvider(name="tavily")
        register_provider(provider)
        result = run_extract(["https://example.com", "http://127.0.0.1/"])
        # Only the safe URL reaches the provider.
        assert provider.extract_calls == [["https://example.com"]]
        urls = {d["url"] for d in result["data"]}
        assert urls == {"https://example.com", "http://127.0.0.1/"}
        # Unsafe entry carries an error; safe entry has content.
        by_url = {d["url"]: d for d in result["data"]}
        assert "error" in by_url["http://127.0.0.1/"]
        assert by_url["https://example.com"].get("content") == "C"


# ===========================================================================
# ADR-0002 — the dormant config resolver is never called
# ===========================================================================


class TestDormantResolverNeverCalled:
    def test_neither_node_calls_active_resolvers(self, monkeypatch):
        import calfkit_hermes._vendor.agent.web_search_registry as reg

        def boom(*a, **k):
            raise AssertionError("dormant config resolver was called")

        monkeypatch.setattr(reg, "get_active_search_provider", boom)
        monkeypatch.setattr(reg, "get_active_extract_provider", boom)

        monkeypatch.setenv("WEB_SEARCH_BACKEND", "ddgs")
        register_provider(FakeSearchProvider(name="ddgs"))
        web_search("q")  # must not raise

        monkeypatch.setenv("WEB_EXTRACT_BACKEND", "tavily")
        register_provider(FakeExtractProvider(name="tavily"))
        run_extract(["https://example.com"])  # must not raise


# ===========================================================================
# Provider registration at import + schema sanity
# ===========================================================================


class TestModuleImport:
    def test_providers_registered_at_import(self):
        # The node registers its name->class list at module import (guarded by
        # ImportError for extras). ddgs has no hard dep beyond the package
        # probe, so at minimum the import must not have raised.
        assert hasattr(web, "_register_builtin_providers")


class TestSchema:
    def test_web_search_node_id_and_params(self):
        assert web_search_node.node_id == "tool_web_search"
        props = web_search_node.tool_schema.parameters_json_schema["properties"]
        assert "query" in props
        assert "limit" in props

    def test_web_extract_node_id_and_params(self):
        assert web_extract_node.node_id == "tool_web_extract"
        props = web_extract_node.tool_schema.parameters_json_schema["properties"]
        assert "urls" in props
