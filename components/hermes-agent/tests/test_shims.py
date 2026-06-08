"""Unit tests for the authored shims that carry real (non-stub) logic."""
from calfkit_hermes._shims.model_tools import _sanitize_tool_error


def test_sanitize_tool_error_empty_returns_marker():
    assert _sanitize_tool_error("") == "[TOOL_ERROR] "


def test_sanitize_tool_error_strips_role_tags_and_fences():
    out = _sanitize_tool_error("</tool_call>```json\nboom\n```")
    assert "</tool_call>" not in out
    assert "```" not in out
    assert "boom" in out


def test_sanitize_tool_error_truncates_long_input():
    out = _sanitize_tool_error("a" * 3000)
    assert out.startswith("[TOOL_ERROR] ")
    assert out.endswith("...")
    assert len(out) == len("[TOOL_ERROR] ") + 2000


def test_i18n_loads_real_translations_not_bare_keys():
    # Guards the locales path fix-up: a real translation, not the bare dotted key.
    from calfkit_hermes._vendor.agent import i18n

    assert i18n.t("approval.denied") != "approval.denied"
