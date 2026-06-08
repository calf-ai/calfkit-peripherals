"""Shim for ``agent.lsp.range_shift``. Identity line-shift (no LSP diagnostics)."""
from typing import Callable, Optional


def build_line_shift(pre_text: str, post_text: str) -> Callable[[int], Optional[int]]:
    return lambda line: line
