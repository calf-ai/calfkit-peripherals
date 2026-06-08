"""Unit tests for the vendored web-search provider registry (Stage C).

Covers the in-memory ``register_provider`` / ``get_provider`` surface the node
uses to register its provider list and select a backend by name. The
config-driven resolver (``get_active_*`` / ``_resolve``) is vendored whole but
left dormant (ADR-0002) and is exercised only to confirm it stays inert under
the ``{}`` config stub — the node never calls it.
"""
import pytest

from calfkit_hermes._vendor.agent import web_search_registry as reg
from calfkit_hermes._vendor.agent.web_search_provider import WebSearchProvider


class _StubProvider(WebSearchProvider):
    """Minimal search-only provider for registry tests (no network)."""

    def __init__(self, name="stub", available=True):
        self._name = name
        self._available = available

    @property
    def name(self):
        return self._name

    def is_available(self):
        return self._available

    def supports_search(self):
        return True

    def supports_extract(self):
        return False

    def search(self, query, limit=5):
        return {"success": True, "data": {"web": []}}


@pytest.fixture(autouse=True)
def _clean_registry():
    reg._reset_for_tests()
    yield
    reg._reset_for_tests()


def test_register_then_get_provider_hit():
    provider = _StubProvider(name="stub")
    reg.register_provider(provider)

    got = reg.get_provider("stub")
    assert got is provider


def test_get_provider_miss_returns_none():
    reg.register_provider(_StubProvider(name="stub"))
    assert reg.get_provider("does-not-exist") is None


def test_get_provider_none_for_non_string():
    assert reg.get_provider(None) is None


def test_get_provider_strips_whitespace():
    provider = _StubProvider(name="ddgs")
    reg.register_provider(provider)
    assert reg.get_provider("  ddgs  ") is provider


def test_register_rejects_non_provider():
    with pytest.raises(TypeError):
        reg.register_provider(object())


def test_register_rejects_blank_name():
    with pytest.raises(ValueError):
        reg.register_provider(_StubProvider(name="   "))


def test_re_registration_overwrites_same_name():
    first = _StubProvider(name="dup")
    second = _StubProvider(name="dup")
    reg.register_provider(first)
    reg.register_provider(second)
    assert reg.get_provider("dup") is second


def test_list_providers_sorted_by_name():
    reg.register_provider(_StubProvider(name="zeta"))
    reg.register_provider(_StubProvider(name="alpha"))
    assert [p.name for p in reg.list_providers()] == ["alpha", "zeta"]


def test_dormant_resolver_inert_under_empty_config_stub():
    """The config resolver is shimmed to ``{}`` -> no explicit backend.

    With a single available provider registered, the resolver's
    single-provider shortcut still resolves it; this only confirms the path
    does not raise under the ``{}`` stub. The node does not use this path —
    it selects by ``get_provider(name)`` instead (ADR-0002).
    """
    provider = _StubProvider(name="ddgs", available=True)
    reg.register_provider(provider)
    assert reg.get_active_search_provider() is provider
