"""Shim for hermes-agent ``hermes_cli.config`` — only the symbols the vendored tree uses.

``get_hermes_home`` re-exports the vendored real implementation. ``cfg_get`` is the pure
nested-dict helper (faithful). The config-reading functions return empty defaults: the
vendored tools all consume them inside try/except with sane fallbacks, and real
subprocess env hygiene is handled by the Stage-D allowlist (not this blocklist-feeding
config). ``OPTIONAL_ENV_VARS`` is intentionally empty here for the same reason.
"""
import os
from pathlib import Path

# Re-export the real implementation (lives in the vendored constants module).
from calfkit_hermes._vendor.hermes_constants import get_hermes_home  # noqa: F401

# Import-safe stub; real env hygiene = Stage-D allowlist (see design doc §6.1, §3.2).
OPTIONAL_ENV_VARS: dict = {}


def cfg_get(cfg, *keys, default=None):
    """Traverse nested dict keys safely, returning ``default`` on any miss (faithful)."""
    if not isinstance(cfg, dict):
        return default
    node = cfg
    for key in keys:
        if not isinstance(node, dict) or key not in node:
            return default
        node = node[key]
    return node


def read_raw_config() -> dict:
    return {}


def load_config() -> dict:
    return {}


def save_config(config: dict) -> None:
    # No-op: persisting host config is not a calfkit-node responsibility.
    return None


def load_env() -> dict:
    return {}


def get_env_value(key: str):
    return os.environ.get(key)


def get_config_path() -> Path:
    return get_hermes_home() / "config.yaml"
