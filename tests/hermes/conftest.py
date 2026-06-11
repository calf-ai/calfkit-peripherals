"""Shared test setup: make the component importable and keep the vendored code's
HERMES_HOME off the real ~/.hermes (hermetic)."""
import os
import sys
import tempfile
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

os.environ.setdefault("HERMES_HOME", tempfile.mkdtemp(prefix="calfkit-hermes-test-"))
