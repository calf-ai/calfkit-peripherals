"""TDD tests for the vendored-import rewriter.

The rewriter relocates the hermes-agent vendored tree's absolute imports into the
component namespace:
  - vendored packages (tools.* / agent.* / hermes_constants / utils) -> calfkit_hermes._vendor.*
  - app-runtime edges (hermes_cli.* / gateway.* / model_tools / agent.auxiliary_client /
    agent.lsp[.*]) -> calfkit_hermes._shims.*   (most-specific-prefix-wins)
It only touches import statements; identifiers/strings that merely look like module
paths are left alone.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from rewrite_imports import rewrite_imports


def test_rewrites_vendored_from_import_to_vendor_namespace():
    src = "from tools.environments.base import BaseEnvironment\n"
    expected = "from calfkit_hermes._vendor.tools.environments.base import BaseEnvironment\n"
    assert rewrite_imports(src) == expected


def test_rewrites_plain_import_statement():
    src = "import hermes_constants\n"
    expected = "import calfkit_hermes._vendor.hermes_constants\n"
    assert rewrite_imports(src) == expected


def test_leaves_lookalike_identifiers_and_strings_untouched():
    # Regression guard for the anchored design: identifiers / strings that merely
    # contain a dotted module-like name must NOT be rewritten (e.g. `toolset`,
    # `tools_dir`, a docstring mentioning `tools.environments`).
    src = (
        "toolset = 1\n"
        "tools_dir = get_path()\n"
        'msg = "see tools.environments for details"\n'
    )
    assert rewrite_imports(src) == src


def test_app_runtime_edges_go_to_shims_most_specific_wins():
    # agent.lsp / agent.auxiliary_client are shims; OTHER agent.* are vendored.
    src = (
        "from agent.lsp import get_service\n"
        "from agent.file_safety import is_write_denied\n"
        "from hermes_cli.config import cfg_get\n"
    )
    expected = (
        "from calfkit_hermes._shims.agent.lsp import get_service\n"
        "from calfkit_hermes._vendor.agent.file_safety import is_write_denied\n"
        "from calfkit_hermes._shims.hermes_cli.config import cfg_get\n"
    )
    assert rewrite_imports(src) == expected
