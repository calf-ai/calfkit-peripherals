"""Shim for hermes-agent ``hermes_cli.auth``.

``PROVIDER_REGISTRY`` feeds local.py's subprocess secret *blocklist*. Empty here is
deliberate: Stage D replaces the blocklist with an allowlist env model (design doc
§6.1), so the blocklist data is moot for the calfkit node. ``resolve_nous_access_token``
is a Nous-portal token resolver, irrelevant to calfkit — returns None (callers wrap it
in try/except and fail closed).
"""

# Import-safe stub; real subprocess env hygiene = Stage-D allowlist.
PROVIDER_REGISTRY: dict = {}


def resolve_nous_access_token(*args, **kwargs):
    return None
