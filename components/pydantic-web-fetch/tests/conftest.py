# Test configuration for calfkit-pydantic-web-fetch.
#
# The vendored regression suites (tests/test_ssrf.py, tests/test_web_fetch.py) are async and
# marked `pytest.mark.anyio`. We pin the anyio backend to asyncio so the suites run with only
# `pytest` + `anyio` installed (no trio dependency). See ../METADATA.yaml and
# ../../../docs/design/web-fetch-tool-port.md.

import pytest


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
