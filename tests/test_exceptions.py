"""Contract tests for the public ``calfkit_tools.exceptions`` surface."""

from __future__ import annotations

from calfkit.nodes import tool as calfkit_tool_dispatch

from calfkit_tools.exceptions import ModelRetry


def test_model_retry_is_exactly_the_type_calfkit_dispatch_catches():
    # calfkit's tool dispatch decides "model-visible recoverable" purely by ``except ModelRetry``
    # (class identity — see calfkit/nodes/tool.py). Pin that our public re-export IS that exact
    # object, so if calfkit ever relocates the type it recognises (e.g. when it drops the vendored
    # pydantic-ai), this fails loudly instead of silently turning every tool error back into a
    # run-killing fault.
    assert ModelRetry is calfkit_tool_dispatch.ModelRetry


def test_model_retry_carries_message():
    # calfkit renders the retry from ``.message``; make sure the constructor populates it.
    exc = ModelRetry("boom")
    assert exc.message == "boom"
    assert str(exc) == "boom"
