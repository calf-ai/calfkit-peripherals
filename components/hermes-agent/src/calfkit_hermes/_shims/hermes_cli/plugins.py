"""Shim for hermes-agent ``hermes_cli.plugins``.

``invoke_hook`` dispatches user/project plugin hooks; calfkit has no such plugin
system here. Returns an empty list of hook results (callers iterate the result).
"""
from typing import Any, List


def invoke_hook(hook_name: str, **kwargs: Any) -> List[Any]:
    return []
