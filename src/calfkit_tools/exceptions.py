"""Public error types for calfkit-tools tool nodes.

A tool node signals a *recoverable* failure — one the model can react to (retry, try another
source, tell the user) rather than a fault that kills the agent run — by raising ``ModelRetry``.
calfkit's tool dispatch recognises this type and surfaces its message to the model as a retry;
any other exception escapes to the fault rail and terminates the turn.

``ModelRetry`` is re-exported here so node authors depend on a first-party, public name instead
of reaching into calfkit's private ``_vendor`` tree. This module is the *single*
seam binding calfkit-tools to whatever type calfkit treats as recoverable: when calfkit moves off
the vendored pydantic-ai, only this import changes. The contract — that this object is exactly the
type calfkit's dispatch catches — is pinned by ``tests/test_exceptions.py`` so a drift fails loudly.
"""

from __future__ import annotations

from calfkit._vendor.pydantic_ai.exceptions import ModelRetry

__all__ = ["ModelRetry"]
