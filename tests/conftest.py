"""Root test setup.

Keep hermes' ``HERMES_HOME`` off the real ``~/.hermes`` for any test under
``tests/`` that imports ``calfkit_tools.hermes.*`` — including the unified-surface
tests at this root level, which are owned by neither per-source sub-suite. The
per-source ``tests/hermes/conftest.py`` sets the same var; ``setdefault`` makes
this idempotent.
"""

import os
import tempfile
from pathlib import Path

os.environ.setdefault("HERMES_HOME", tempfile.mkdtemp(prefix="calfkit-tools-test-"))


def pytest_configure(config):
    """Keep the pytest tmp dir off a path the vendored write-guard treats as sensitive.

    macOS resolves the default tmp dir under ``/var/folders/...`` (a sensitive system
    prefix), so file-write tests that go through the path-security guard fail locally
    although they pass on Linux CI, whose ``/tmp`` is fine. When the user hasn't set
    ``--basetemp`` and the default tmp is sensitive, redirect to a HOME-based dir. No-op
    on Linux. This lives at the tests/ root so it runs as an initial conftest regardless
    of how pytest is invoked (a subdir conftest's hook runs too late for the tmp factory).
    """
    if config.option.basetemp:
        return
    if tempfile.gettempdir().startswith(("/var", "/private")):
        base = Path.home() / ".cache" / "calfkit-pytest-tmp"
        base.mkdir(parents=True, exist_ok=True)
        config.option.basetemp = str(base / "bt")
