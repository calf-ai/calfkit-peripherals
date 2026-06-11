# Test configuration for the web_fetch source (part of the calfkit-tools distribution).
#
# The vendored regression suites (tests/web_fetch/test_ssrf.py, test_web_fetch.py) are async
# and marked `pytest.mark.anyio`. We pin the anyio backend to asyncio so the suites run with
# only `pytest` + `anyio` installed (no trio dependency). See ../../vendor/web_fetch/METADATA.yaml
# and ../../docs/design/web-fetch-tool-port.md.

import pytest


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
