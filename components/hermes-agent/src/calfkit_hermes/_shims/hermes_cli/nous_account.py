"""Shim for hermes-agent ``hermes_cli.nous_account`` (Nous-portal account info).

Irrelevant to calfkit; only referenced by the managed-tool-gateway path inside
try/except. Safe no-ops.
"""


def get_nous_portal_account_info(*args, **kwargs):
    return None


def format_nous_portal_entitlement_message(*args, **kwargs) -> str:
    return ""
