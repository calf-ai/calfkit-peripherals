"""Unit tests for the extract-side SSRF guard ``async_is_safe_url`` (Stage C).

The node runs this over every URL before dispatching to an extract provider.
Under the ``{}`` config stub the guard is fail-closed: ``allow_private_urls``
stays False, so private/metadata targets stay blocked. DNS is mocked — no real
resolution.
"""
import asyncio
import socket

import pytest

from calfkit_hermes._vendor.tools import url_safety


def _fake_getaddrinfo_factory(ip_str):
    """Build a ``socket.getaddrinfo`` replacement resolving every host to *ip_str*."""

    def _fake(host, port, family=0, type=0, proto=0, flags=0):
        family = socket.AF_INET6 if ":" in ip_str else socket.AF_INET
        return [(family, socket.SOCK_STREAM, 0, "", (ip_str, 0))]

    return _fake


@pytest.fixture(autouse=True)
def _hermetic_toggle(monkeypatch):
    """Keep the SSRF guard fail-closed and reset its cached toggle.

    The toggle is cached on first call (security-frozen-at-import), so reset it
    before AND after each test, and make sure no env override leaks in.
    """
    monkeypatch.delenv("HERMES_ALLOW_PRIVATE_URLS", raising=False)
    url_safety._reset_allow_private_cache()
    yield
    url_safety._reset_allow_private_cache()


def _run(coro):
    return asyncio.run(coro)


def test_blocks_private_rfc1918_target(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo_factory("10.0.0.5"))
    assert _run(url_safety.async_is_safe_url("http://internal.test/")) is False


def test_blocks_loopback_target(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo_factory("127.0.0.1"))
    assert _run(url_safety.async_is_safe_url("http://localhost.test/")) is False


def test_blocks_cloud_metadata_ip_literal(monkeypatch):
    # Literal metadata IP — blocked regardless of DNS / toggle.
    assert _run(url_safety.async_is_safe_url("http://169.254.169.254/")) is False


def test_blocks_metadata_hostname_always(monkeypatch):
    # metadata.google.internal is in the always-blocked floor — never resolves.
    def _boom(*a, **k):  # pragma: no cover - should not be reached
        raise AssertionError("hostname must be blocked before DNS")

    monkeypatch.setattr(socket, "getaddrinfo", _boom)
    assert (
        _run(url_safety.async_is_safe_url("https://metadata.google.internal/"))
        is False
    )


def test_allows_public_target(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo_factory("93.184.216.34"))
    assert _run(url_safety.async_is_safe_url("https://example.com/")) is True


def test_blocks_non_http_scheme():
    assert _run(url_safety.async_is_safe_url("ftp://example.com/")) is False


def test_dns_failure_fails_closed(monkeypatch):
    def _gaierror(*a, **k):
        raise socket.gaierror("no such host")

    monkeypatch.setattr(socket, "getaddrinfo", _gaierror)
    assert _run(url_safety.async_is_safe_url("https://nope.invalid/")) is False


def test_fail_closed_under_empty_config_stub(monkeypatch):
    """The shimmed ``read_raw_config`` returns ``{}`` -> toggle stays False.

    Even with no env override and the ``{}`` config, a private target is
    blocked (the safe default the node relies on).
    """
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo_factory("192.168.1.10"))
    # Sanity: the shim really does return {} (the dormant config path).
    from calfkit_hermes._shims.hermes_cli.config import read_raw_config

    assert read_raw_config() == {}
    assert _run(url_safety.async_is_safe_url("http://router.test/")) is False
