"""Import-smoke for the web_search/web_extract vendor closure (Stage C).

The 7 rewritten modules (ABC, registry, url_safety, and the four Tier-0
providers) must import cleanly under the ``calfkit_tools.hermes`` namespace — i.e.
the mechanical import-rewrite resolved every cross-package edge to a vendored
or shimmed target, with no bare ``agent.`` / ``tools.`` / ``hermes_cli.`` /
``utils`` imports left behind.

Also asserts the providers self-register into the *vendored* registry via the
public ``register_provider`` API (the node owns this registration list — see
design doc §4; the dropped plugin ``register(ctx)`` hooks are replaced by it).
"""
import importlib

import pytest

WEB_MODULES = [
    "calfkit_tools.hermes._vendor.agent.web_search_provider",
    "calfkit_tools.hermes._vendor.agent.web_search_registry",
    "calfkit_tools.hermes._vendor.tools.url_safety",
    "calfkit_tools.hermes._vendor.tools.web_providers.ddgs",
    "calfkit_tools.hermes._vendor.tools.web_providers.brave_free",
    "calfkit_tools.hermes._vendor.tools.web_providers.searxng",
    "calfkit_tools.hermes._vendor.tools.web_providers.tavily",
]


@pytest.mark.parametrize("module", WEB_MODULES)
def test_web_module_imports_cleanly(module):
    importlib.import_module(module)


def test_provider_modules_have_no_bare_vendored_imports():
    """Every cross-package import in the rewritten files is namespaced.

    Guards the rewrite: a bare ``from agent.`` / ``from tools.`` /
    ``from hermes_cli.`` / ``from utils`` would mean an edge slipped past the
    rewriter and would only blow up at runtime (e.g. the lazy config import).
    """
    import re
    from pathlib import Path

    src = Path(__file__).resolve().parents[2] / "src"
    bare = re.compile(
        r"^\s*(from|import)\s+(agent|tools|hermes_cli|utils|hermes_constants)([. ]|$)"
    )
    offenders = []
    for module in WEB_MODULES:
        rel = module.removeprefix("calfkit_tools.hermes.").replace(".", "/") + ".py"
        path = src / "calfkit_tools" / "hermes" / rel
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            if bare.match(line):
                offenders.append(f"{rel}:{lineno}: {line.strip()}")
    assert not offenders, "bare un-namespaced imports remain:\n" + "\n".join(offenders)


def test_providers_self_register_into_vendored_registry():
    from calfkit_tools.hermes._vendor.agent import web_search_registry as reg
    from calfkit_tools.hermes._vendor.tools.web_providers.ddgs import DDGSWebSearchProvider
    from calfkit_tools.hermes._vendor.tools.web_providers.brave_free import (
        BraveFreeWebSearchProvider,
    )
    from calfkit_tools.hermes._vendor.tools.web_providers.searxng import (
        SearXNGWebSearchProvider,
    )
    from calfkit_tools.hermes._vendor.tools.web_providers.tavily import (
        TavilyWebSearchProvider,
    )

    reg._reset_for_tests()
    try:
        # The node's registration list (design doc §4) — name -> class.
        classes = [
            DDGSWebSearchProvider,
            BraveFreeWebSearchProvider,
            SearXNGWebSearchProvider,
            TavilyWebSearchProvider,
        ]
        for cls in classes:
            reg.register_provider(cls())

        names = {p.name for p in reg.list_providers()}
        assert names == {"ddgs", "brave-free", "searxng", "tavily"}
    finally:
        reg._reset_for_tests()
