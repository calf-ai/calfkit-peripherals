"""Shim for hermes-agent ``hermes_cli.profiles``.

Only ``get_active_profile_name`` is used (a Docker container label, inside try/except).
"""


def get_active_profile_name() -> str:
    return "default"
