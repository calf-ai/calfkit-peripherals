"""Shim for hermes-agent ``agent.lsp`` — LSP-diagnostics-on-edit enrichment.

LSP is a pure enrichment layer for file edits; every call site is gated and try/except'd.
The shim fails closed: ``get_service`` returns None so file edits proceed without
post-edit diagnostics. (Port the real ``agent/lsp`` later if diagnostics are wanted.)
"""


def get_service():
    return None
