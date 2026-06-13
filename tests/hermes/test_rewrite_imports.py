"""TDD tests for the vendored-import rewriter.

The rewriter relocates the hermes-agent vendored tree's absolute imports into the
component namespace:
  - vendored packages (tools.* / agent.* / hermes_constants / utils) -> calfkit_tools.hermes._vendor.*
  - app-runtime edges (hermes_cli.* / gateway.* / model_tools / agent.auxiliary_client /
    agent.lsp[.*]) -> calfkit_tools.hermes._shims.*   (most-specific-prefix-wins)
It only touches import statements; identifiers/strings that merely look like module
paths are left alone.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "vendor" / "hermes" / "scripts"))

from rewrite_imports import rewrite_imports


def test_rewrites_vendored_from_import_to_vendor_namespace():
    src = "from tools.environments.base import BaseEnvironment\n"
    expected = "from calfkit_tools.hermes._vendor.tools.environments.base import BaseEnvironment\n"
    assert rewrite_imports(src) == expected


def test_rewrites_plain_import_statement_preserves_binding_with_alias():
    # `import hermes_constants` must keep the `hermes_constants` name bound (via `as`),
    # else `hermes_constants.X` references break after the dotted rewrite.
    src = "import hermes_constants\n"
    expected = "import calfkit_tools.hermes._vendor.hermes_constants as hermes_constants\n"
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
        "from calfkit_tools.hermes._shims.agent.lsp import get_service\n"
        "from calfkit_tools.hermes._vendor.agent.file_safety import is_write_denied\n"
        "from calfkit_tools.hermes._shims.hermes_cli.config import cfg_get\n"
    )
    assert rewrite_imports(src) == expected


def test_does_not_rewrite_imports_inside_docstrings():
    # An import-looking line inside a string/docstring is NOT a real import and
    # must be left byte-for-byte (preserves the "verbatim" provenance of _vendor).
    src = (
        '"""Usage:\n'
        "    from tools.web_tools import fetch\n"
        '"""\n'
        "from tools.real import thing\n"
    )
    out = rewrite_imports(src)
    assert "    from tools.web_tools import fetch\n" in out  # docstring untouched
    assert "from calfkit_tools.hermes._vendor.tools.real import thing\n" in out


def test_rewrites_import_with_as_alias():
    src = "import hermes_constants as hc\n"
    assert rewrite_imports(src) == "import calfkit_tools.hermes._vendor.hermes_constants as hc\n"


def test_leaves_relative_imports_unchanged():
    src = "from .terminal_tool import check\nfrom ..agent import x\n"
    assert rewrite_imports(src) == src


def test_rewrites_module_in_multiline_paren_import():
    src = "from tools.file_operations import (\n    a,\n    b,\n)\n"
    expected = "from calfkit_tools.hermes._vendor.tools.file_operations import (\n    a,\n    b,\n)\n"
    assert rewrite_imports(src) == expected


def test_rewrite_is_idempotent():
    src = "from tools.environments.base import X\nfrom agent.lsp import get_service\n"
    once = rewrite_imports(src)
    assert rewrite_imports(once) == once
