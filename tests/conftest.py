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

# macOS resolves the default tmp dir under ``/var/folders/...`` (a sensitive system
# prefix), which the vendored path-security write-guard refuses to write to — so the
# many vendored file-write tests fail locally though they pass on Linux CI (whose
# ``/tmp`` is fine). Point ALL temp allocation at a non-sensitive HOME dir: both pytest's
# ``tmp_path`` factory and the raw ``tempfile.mkdtemp()`` calls vendored tests make
# (which ``--basetemp`` would not reach). Done at import — before any tempfile use here —
# and a no-op on Linux. Resetting ``tempfile.tempdir`` clears the cached default so
# ``gettempdir()`` re-reads ``TMPDIR``.
if tempfile.gettempdir().startswith(("/var", "/private")):
    _tmp_root = Path.home() / ".cache" / "calfkit-pytest-tmp"
    _tmp_root.mkdir(parents=True, exist_ok=True)
    os.environ["TMPDIR"] = str(_tmp_root)
    tempfile.tempdir = None

os.environ.setdefault("HERMES_HOME", tempfile.mkdtemp(prefix="calfkit-tools-test-"))
