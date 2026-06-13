"""TDD tests for the test-file rewriter (companion to rewrite_imports).

Vendored upstream test files need two rewrites:
  1. their real import statements (delegated to rewrite_imports), and
  2. the *string-literal* module paths the import rewriter deliberately leaves
     alone -- ``patch("tools.x.y")``, ``monkeypatch.setattr("tools.x", ...)``,
     ``importlib.import_module("tools.x")``, ``sys.modules["tools.x"]`` and
     ``logger="tools.x"`` -- which otherwise point at non-existent module paths.

Both use the SAME mapping (rewrite_imports._target), so vendor/shim routing is
identical to the production import rewriter. Strings NOT in those call positions
(assertion expected-values, ``patch.dict("os.environ")``, ``patch.object`` attr
names) must be left byte-for-byte.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "vendor" / "hermes" / "scripts"))

from rewrite_test_imports import rewrite_test_source


def test_rewrites_patch_string_target_to_vendor():
    src = 'with patch("tools.file_tools._get_file_ops") as m:\n    pass\n'
    out = rewrite_test_source(src)
    assert 'patch("calfkit_tools.hermes._vendor.tools.file_tools._get_file_ops")' in out


def test_rewrites_patch_string_target_to_shim():
    src = 'patch("hermes_cli.config.load_config")\n'
    out = rewrite_test_source(src)
    assert out == 'patch("calfkit_tools.hermes._shims.hermes_cli.config.load_config")\n'


def test_rewrites_model_tools_string_to_shim():
    src = 'patch("model_tools.handle_function_call")\n'
    assert rewrite_test_source(src) == 'patch("calfkit_tools.hermes._shims.model_tools.handle_function_call")\n'


def test_rewrites_monkeypatch_setattr_string_first_arg():
    src = 'monkeypatch.setattr("tools.terminal_tool._interrupt_event", obj)\n'
    out = rewrite_test_source(src)
    assert '"calfkit_tools.hermes._vendor.tools.terminal_tool._interrupt_event"' in out


def test_rewrites_importlib_import_module_string():
    src = 'importlib.import_module("tools.terminal_tool")\n'
    out = rewrite_test_source(src)
    assert out == 'importlib.import_module("calfkit_tools.hermes._vendor.tools.terminal_tool")\n'


def test_rewrites_sys_modules_subscript_key():
    src = 'sys.modules["tools.terminal_tool"]\n'
    out = rewrite_test_source(src)
    assert out == 'sys.modules["calfkit_tools.hermes._vendor.tools.terminal_tool"]\n'


def test_rewrites_logger_kwarg_in_caplog():
    src = 'with caplog.at_level(logging.INFO, logger="tools.code_execution_tool"):\n    pass\n'
    out = rewrite_test_source(src)
    assert 'logger="calfkit_tools.hermes._vendor.tools.code_execution_tool"' in out


def test_leaves_patch_object_attr_name_untouched():
    # patch.object(module_obj, "attr") -- the attr string is not a module path
    # and the func is `.object`, so nothing is rewritten.
    src = 'patch.object(terminal_tool, "subprocess")\n'
    assert rewrite_test_source(src) == src


def test_leaves_patch_dict_os_environ_untouched():
    src = 'patch.dict("os.environ", {"X": "1"})\n'
    assert rewrite_test_source(src) == src


def test_leaves_assertion_expected_value_strings_untouched():
    # "tools.alpha" here is an EXPECTED value, not a patch/setattr target.
    src = 'assert imported == ["tools.alpha"]\nmock.assert_called_once_with("tools.alpha")\n'
    assert rewrite_test_source(src) == src


def test_also_applies_import_statement_rewrite():
    src = "from tools.file_operations import read\n"
    assert rewrite_test_source(src) == "from calfkit_tools.hermes._vendor.tools.file_operations import read\n"


def test_preserves_single_quote_style_and_rest_of_line():
    src = "m = patch('tools.approval.detect_dangerous_command')  # comment\n"
    out = rewrite_test_source(src)
    assert out == "m = patch('calfkit_tools.hermes._vendor.tools.approval.detect_dangerous_command')  # comment\n"


def test_rewrites_sys_modules_get_and_pop_methods():
    # The save/restore dict-method forms must use the SAME rewritten key as the
    # subscript set, or save/restore desync and leak a broken module.
    src = (
        'orig = sys.modules.get("tools.tirith_security")\n'
        'sys.modules["tools.tirith_security"] = None\n'
        'sys.modules.pop("tools.tirith_security", None)\n'
    )
    out = rewrite_test_source(src)
    assert out.count('"calfkit_tools.hermes._vendor.tools.tirith_security"') == 3
    assert '"tools.tirith_security"' not in out


def test_rewrites_aliased_patch_import():
    # `from unittest.mock import patch as mock_patch` then `mock_patch("tools.x.y")`.
    src = (
        "from unittest.mock import patch as mock_patch\n"
        'mock_patch("hermes_cli.config.load_config")\n'
    )
    out = rewrite_test_source(src)
    assert 'mock_patch("calfkit_tools.hermes._shims.hermes_cli.config.load_config")' in out


def test_is_idempotent():
    src = 'patch("tools.file_tools._get_file_ops")\nfrom tools.registry import registry\n'
    once = rewrite_test_source(src)
    assert rewrite_test_source(once) == once
