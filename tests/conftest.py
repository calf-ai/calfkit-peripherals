"""Root test setup.

Keep hermes' ``HERMES_HOME`` off the real ``~/.hermes`` for any test under
``tests/`` that imports ``calfkit_tools.hermes.*`` — including the unified-surface
tests at this root level, which are owned by neither per-source sub-suite. The
per-source ``tests/hermes/conftest.py`` sets the same var; ``setdefault`` makes
this idempotent.
"""

import os
import tempfile

os.environ.setdefault("HERMES_HOME", tempfile.mkdtemp(prefix="calfkit-tools-test-"))
