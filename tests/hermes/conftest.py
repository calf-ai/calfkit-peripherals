"""Shared test setup for the vendored hermes tree.

Two concerns the vendored upstream tests assume but calfkit must provide itself:

1. **Hermeticity** (``_hermetic_environment``). Upstream's root conftest scrubs
   credential/behavioral env vars, pins a per-test ``HERMES_HOME`` and a
   deterministic locale/TZ. Ported (adapted) here so a developer's real API keys
   or shell ``HERMES_*`` vars can't leak into assertions.

2. **In-process isolation** (``_reset_vendored_global_state``). Upstream runs one
   subprocess per test FILE, so module-global singletons never leak across files.
   calfkit runs a single in-process ``pytest``, so we snapshot/restore the vendored
   global registries around every test. Without this, e.g. ``registry.registry`` or
   ``file_state._registry`` mutated by one file would contaminate the next.
"""
import os
import sys
import tempfile
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

# Import-time fallback so any module read of HERMES_HOME before a test's fixture
# runs still lands off the real ~/.hermes. The autouse fixture overrides per test.
os.environ.setdefault("HERMES_HOME", tempfile.mkdtemp(prefix="calfkit-hermes-test-"))

# Credential-shaped suffixes scrubbed so a real key in the dev's shell can't satisfy
# a "provider auto-detected when key present" assertion.
_CREDENTIAL_SUFFIXES = (
    "_API_KEY",
    "_TOKEN",
    "_SECRET",
    "_PASSWORD",
    "_CREDENTIALS",
    "_ACCESS_KEY",
    "_PRIVATE_KEY",
)
# Behavioral prefixes that must not bleed in from the dev's shell or an active
# gateway session (HERMES_SESSION_*, TERMINAL_CWD/ENV, TIRITH_*, ...).
_BEHAVIORAL_PREFIXES = ("HERMES_", "TERMINAL_", "TIRITH_")


@pytest.fixture(autouse=True)
def _hermetic_environment(tmp_path, monkeypatch):
    """Per-test hermetic env: scrub leaky vars, isolate HERMES_HOME, pin runtime."""
    for name in list(os.environ):
        if name.endswith(_CREDENTIAL_SUFFIXES) or name.startswith(_BEHAVIORAL_PREFIXES):
            monkeypatch.delenv(name, raising=False)

    # Named "hermes_test" so upstream tests that assert this token in HERMES_HOME hold.
    home = tmp_path / "hermes_test"
    for sub in ("sessions", "cron", "memories", "skills"):
        (home / sub).mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HERMES_HOME", str(home))

    monkeypatch.setenv("TZ", "UTC")
    monkeypatch.setenv("LANG", "C.UTF-8")
    monkeypatch.setenv("LC_ALL", "C.UTF-8")
    monkeypatch.setenv("PYTHONHASHSEED", "0")
    # Keep security/metadata probes from reaching the network in CI.
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    monkeypatch.setenv("TIRITH_ENABLED", "false")
    yield


@pytest.fixture(autouse=True)
def _reset_vendored_global_state():
    """Snapshot/restore vendored module-global state that tests mutate.

    Pure undo of per-test mutations, mirroring the isolation upstream got from
    subprocess-per-file. Deliberately does NOT touch ``tools.registry.registry``:
    its builtin tools are registered by one-time import side-effects, so clearing
    it permanently (modules won't re-register on cached re-import) and breaks
    ``discover_builtin_tools`` + node dispatch. Tests needing a clean registry use
    a local ``ToolRegistry()`` instance, as upstream does.
    """
    from calfkit_tools.hermes._vendor.agent import web_search_registry as wsr
    from calfkit_tools.hermes._vendor.tools import (
        file_tools,
        terminal_tool,
        tool_output_limits,
        url_safety,
    )

    def _clear_per_task_state():
        # Per-task caches/registries upstream got fresh per subprocess. They key on
        # task_id, so a stale entry (e.g. a previous test's live-tracking cwd in
        # terminal_tool._active_environments) leaks into the next test's path
        # resolution. Clear all of them between tests.
        with terminal_tool._env_lock:
            terminal_tool._active_environments.clear()
            terminal_tool._task_env_overrides.clear()
        file_tools.clear_file_ops_cache()
        file_tools.reset_file_dedup()

    with wsr._lock:
        snap_providers = dict(wsr._providers)
    _clear_per_task_state()
    tool_output_limits._reset_tool_output_limits_cache()
    url_safety._reset_allow_private_cache()

    try:
        yield
    finally:
        with wsr._lock:
            wsr._providers.clear(); wsr._providers.update(snap_providers)
        _clear_per_task_state()
        tool_output_limits._reset_tool_output_limits_cache()
        url_safety._reset_allow_private_cache()
