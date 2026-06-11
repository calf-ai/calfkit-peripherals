"""Shim for hermes-agent ``gateway.session_context``.

Provides the symbols the vendored tree imports: ``_UNSET``, ``_VAR_MAP``,
``get_session_env``. Upstream backs these with per-request ContextVars to inject
session-scoped env vars into subprocesses.

NOTE (Stage D): the real per-tenant isolation lives here. For the adapt stage this is a
minimal env-backed version so the tree imports and single-tenant behaviour is correct;
``_VAR_MAP`` is empty so no session vars are injected. Stage D must replace this with the
real ContextVar mechanism (re-keyed on the calfkit session_key) — see design doc §6, §12.
"""
import os
from typing import Any

_UNSET: Any = object()

# Maps session-var name -> ContextVar upstream; empty here (no injection until Stage D).
_VAR_MAP: dict = {}


def get_session_env(name: str, default: str = "") -> str:
    """Return a session-scoped env value, falling back to the process environment."""
    value = os.environ.get(name)
    return value if value is not None else default
