"""Behavioral tests for ``tools.tool_backend_helpers``.

This file PINS the *current* behavior of the backend-selection helpers, covering
the branches the existing suite leaves uncovered: the pure normalizers
(``coerce_modal_mode`` / ``normalize_browser_cloud_provider`` / the OpenAI-audio
key precedence), the ``resolve_modal_backend_state`` decision matrix driven via
the explicit ``managed_enabled`` arg (so the shim is bypassed), and the three
shim-backed fail-closed paths (``managed_nous_tools_enabled``,
``prefers_gateway``, ``fal_key_is_configured``).

Shim symbols are lazy-imported *inside* each helper (``from
calfkit_tools.hermes._shims.hermes_cli... import name``), so every call re-binds
the name from the shim module. Monkeypatching therefore targets the attribute on
the shim module itself (e.g. ``..._shims.hermes_cli.config.load_config``), not a
reference on ``tool_backend_helpers``.

The autouse hermetic fixture (conftest) scrubs ``*_API_KEY`` / ``*_KEY`` /
``HERMES_*`` env vars before each test, so credential env vars start UNSET and are
set explicitly via ``monkeypatch.setenv`` where a test needs them.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from calfkit_tools.hermes._shims.hermes_cli import config as shim_config
from calfkit_tools.hermes._shims.hermes_cli import nous_account as shim_nous
from calfkit_tools.hermes._vendor.tools import tool_backend_helpers


# --------------------------------------------------------------------------- #
# coerce_modal_mode / normalize_modal_mode
# --------------------------------------------------------------------------- #
class TestCoerceModalMode:
    @pytest.mark.parametrize("valid", ["auto", "direct", "managed"])
    def test_valid_modes_pass_through(self, valid):
        assert tool_backend_helpers.coerce_modal_mode(valid) == valid

    def test_case_insensitive_uppercase(self):
        assert tool_backend_helpers.coerce_modal_mode("MANAGED") == "managed"

    def test_case_insensitive_mixed_with_surrounding_whitespace(self):
        # str().strip().lower() normalizes case AND trims surrounding space.
        assert tool_backend_helpers.coerce_modal_mode("  DiReCt  ") == "direct"

    def test_invalid_value_falls_back_to_default_auto(self):
        assert tool_backend_helpers.coerce_modal_mode("bogus") == "auto"

    def test_none_falls_back_to_default_auto(self):
        # `value or _DEFAULT_MODAL_MODE` => None is falsy => "auto".
        assert tool_backend_helpers.coerce_modal_mode(None) == "auto"

    def test_empty_string_falls_back_to_default_auto(self):
        # "" is falsy => the `or` picks the default before any validation.
        assert tool_backend_helpers.coerce_modal_mode("") == "auto"

    def test_whitespace_only_falls_back_to_default_auto(self):
        # "   " is truthy so it is kept by `or`, then strip() -> "" -> not in the
        # valid set -> default. (A distinct path from the empty-string case.)
        assert tool_backend_helpers.coerce_modal_mode("   ") == "auto"

    def test_falsy_zero_falls_back_to_default_auto(self):
        # 0 is falsy => `value or default` short-circuits to "auto".
        assert tool_backend_helpers.coerce_modal_mode(0) == "auto"

    def test_normalize_modal_mode_is_alias_of_coerce(self):
        # normalize_modal_mode just delegates to coerce_modal_mode.
        assert tool_backend_helpers.normalize_modal_mode("MANAGED") == "managed"
        assert tool_backend_helpers.normalize_modal_mode("nope") == "auto"


# --------------------------------------------------------------------------- #
# normalize_browser_cloud_provider
# --------------------------------------------------------------------------- #
class TestNormalizeBrowserCloudProvider:
    def test_none_returns_default_local(self):
        assert tool_backend_helpers.normalize_browser_cloud_provider(None) == "local"

    def test_empty_string_returns_default_local(self):
        assert tool_backend_helpers.normalize_browser_cloud_provider("") == "local"

    def test_whitespace_only_returns_default_local(self):
        # "  " is truthy (kept by `or`), strip() -> "" -> the trailing `or default`
        # restores "local".
        assert tool_backend_helpers.normalize_browser_cloud_provider("   ") == "local"

    def test_mixed_case_is_lowercased(self):
        assert (
            tool_backend_helpers.normalize_browser_cloud_provider("BrowserBase")
            == "browserbase"
        )

    def test_surrounding_whitespace_is_trimmed_and_lowered(self):
        assert (
            tool_backend_helpers.normalize_browser_cloud_provider("  AnchorBrowser  ")
            == "anchorbrowser"
        )

    def test_already_normalized_value_unchanged(self):
        assert tool_backend_helpers.normalize_browser_cloud_provider("local") == "local"


# --------------------------------------------------------------------------- #
# resolve_openai_audio_api_key  (precedence: VOICE_TOOLS_OPENAI_KEY > OPENAI_API_KEY)
# --------------------------------------------------------------------------- #
class TestResolveOpenAIAudioApiKey:
    def test_both_unset_returns_empty_string(self):
        # Hermetic fixture leaves both unset; defaults ("") feed the strip().
        assert tool_backend_helpers.resolve_openai_audio_api_key() == ""

    def test_voice_tools_key_wins_over_openai_key(self, monkeypatch):
        monkeypatch.setenv("VOICE_TOOLS_OPENAI_KEY", "voice-key")
        monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
        assert tool_backend_helpers.resolve_openai_audio_api_key() == "voice-key"

    def test_falls_back_to_openai_key_when_voice_unset(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
        assert tool_backend_helpers.resolve_openai_audio_api_key() == "openai-key"

    def test_falls_back_to_openai_key_when_voice_blank(self, monkeypatch):
        # An empty (falsy) voice key lets the `or` fall through to the OpenAI key.
        monkeypatch.setenv("VOICE_TOOLS_OPENAI_KEY", "")
        monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
        assert tool_backend_helpers.resolve_openai_audio_api_key() == "openai-key"

    def test_result_is_stripped(self, monkeypatch):
        monkeypatch.setenv("VOICE_TOOLS_OPENAI_KEY", "  spaced-key  ")
        assert tool_backend_helpers.resolve_openai_audio_api_key() == "spaced-key"

    def test_whitespace_voice_key_does_not_fall_through_and_returns_empty(
        self, monkeypatch
    ):
        # PIN: strip() is applied to the RESULT of the `or`, not each operand. A
        # whitespace-only voice key is truthy, so the `or` short-circuits to it;
        # strip() then yields "" and the OpenAI key is NEVER consulted.
        monkeypatch.setenv("VOICE_TOOLS_OPENAI_KEY", "   ")
        monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
        assert tool_backend_helpers.resolve_openai_audio_api_key() == ""


# --------------------------------------------------------------------------- #
# resolve_modal_backend_state  (explicit managed_enabled bypasses the shim)
# --------------------------------------------------------------------------- #
class TestResolveModalBackendState:
    def test_managed_requested_but_not_enabled_is_blocked(self):
        state = tool_backend_helpers.resolve_modal_backend_state(
            "managed",
            has_direct=True,
            managed_ready=True,
            managed_enabled=False,
        )
        assert state["requested_mode"] == "managed"
        assert state["mode"] == "managed"
        assert state["managed_mode_blocked"] is True
        # managed-only + not enabled => no backend, even though direct exists.
        assert state["selected_backend"] is None

    def test_managed_enabled_and_ready_selects_managed(self):
        state = tool_backend_helpers.resolve_modal_backend_state(
            "managed",
            has_direct=False,
            managed_ready=True,
            managed_enabled=True,
        )
        assert state["selected_backend"] == "managed"
        assert state["managed_mode_blocked"] is False

    def test_managed_enabled_but_not_ready_selects_nothing(self):
        # PIN: managed mode needs BOTH enabled and ready; not blocked (it IS
        # enabled) but still yields no backend.
        state = tool_backend_helpers.resolve_modal_backend_state(
            "managed",
            has_direct=True,
            managed_ready=False,
            managed_enabled=True,
        )
        assert state["managed_mode_blocked"] is False
        assert state["selected_backend"] is None

    def test_direct_mode_with_credentials_selects_direct(self):
        state = tool_backend_helpers.resolve_modal_backend_state(
            "direct",
            has_direct=True,
            managed_ready=True,
            managed_enabled=True,
        )
        assert state["selected_backend"] == "direct"
        # requested_mode != "managed" => never blocked.
        assert state["managed_mode_blocked"] is False

    def test_direct_mode_without_credentials_selects_nothing(self):
        state = tool_backend_helpers.resolve_modal_backend_state(
            "direct",
            has_direct=False,
            managed_ready=True,
            managed_enabled=True,
        )
        assert state["selected_backend"] is None

    def test_auto_prefers_managed_when_enabled_and_ready(self):
        state = tool_backend_helpers.resolve_modal_backend_state(
            "auto",
            has_direct=True,
            managed_ready=True,
            managed_enabled=True,
        )
        assert state["selected_backend"] == "managed"

    def test_auto_falls_back_to_direct_when_managed_not_enabled(self):
        # The prompt's "auto -> direct fallback": managed unavailable, direct creds
        # present => direct, and NOT blocked (auto is not a managed-only request).
        state = tool_backend_helpers.resolve_modal_backend_state(
            "auto",
            has_direct=True,
            managed_ready=True,
            managed_enabled=False,
        )
        assert state["selected_backend"] == "direct"
        assert state["managed_mode_blocked"] is False

    def test_auto_falls_back_to_direct_when_managed_enabled_but_not_ready(self):
        state = tool_backend_helpers.resolve_modal_backend_state(
            "auto",
            has_direct=True,
            managed_ready=False,
            managed_enabled=True,
        )
        assert state["selected_backend"] == "direct"

    def test_auto_with_nothing_available_selects_nothing(self):
        state = tool_backend_helpers.resolve_modal_backend_state(
            "auto",
            has_direct=False,
            managed_ready=False,
            managed_enabled=False,
        )
        assert state["selected_backend"] is None
        assert state["managed_mode_blocked"] is False

    def test_invalid_mode_is_coerced_to_auto_in_returned_state(self):
        # Both requested_mode and mode reflect the coerced "auto".
        state = tool_backend_helpers.resolve_modal_backend_state(
            "garbage",
            has_direct=True,
            managed_ready=False,
            managed_enabled=False,
        )
        assert state["requested_mode"] == "auto"
        assert state["mode"] == "auto"
        assert state["selected_backend"] == "direct"

    def test_state_echoes_back_has_direct_and_managed_ready_inputs(self):
        state = tool_backend_helpers.resolve_modal_backend_state(
            "auto",
            has_direct=True,
            managed_ready=False,
            managed_enabled=True,
        )
        assert state["has_direct"] is True
        assert state["managed_ready"] is False

    def test_managed_enabled_none_consults_shim_and_fails_closed(self, monkeypatch):
        # When managed_enabled is omitted (None), it is resolved via
        # managed_nous_tools_enabled(); the default shim returns None, whose
        # `.logged_in` raises -> caught -> False. So managed-only is blocked.
        monkeypatch.setattr(
            shim_nous, "get_nous_portal_account_info", lambda *a, **k: None
        )
        state = tool_backend_helpers.resolve_modal_backend_state(
            "managed",
            has_direct=True,
            managed_ready=True,
        )
        assert state["managed_mode_blocked"] is True
        assert state["selected_backend"] is None


# --------------------------------------------------------------------------- #
# fal_key_is_configured
# --------------------------------------------------------------------------- #
class TestFalKeyIsConfigured:
    def test_env_set_to_real_value_is_true(self, monkeypatch):
        monkeypatch.setenv("FAL_KEY", "fal-secret")
        assert tool_backend_helpers.fal_key_is_configured() is True

    def test_env_whitespace_only_is_false_without_consulting_shim(self, monkeypatch):
        # PIN: a whitespace FAL_KEY in os.environ is NOT None, so the shim
        # (get_env_value) is never consulted; bool("  ".strip()) -> False.
        sentinel = {"called": False}

        def _boom(*_a, **_k):
            sentinel["called"] = True
            raise AssertionError("shim must not be consulted when env is set")

        monkeypatch.setattr(shim_config, "get_env_value", _boom)
        monkeypatch.setenv("FAL_KEY", "   ")
        assert tool_backend_helpers.fal_key_is_configured() is False
        assert sentinel["called"] is False

    def test_unset_falls_back_to_shim_real_value_true(self, monkeypatch):
        # FAL_KEY unset (hermetic fixture scrubbed it) => the .env shim is queried.
        monkeypatch.setattr(
            shim_config, "get_env_value", lambda key: "dotenv-fal" if key == "FAL_KEY" else None
        )
        assert tool_backend_helpers.fal_key_is_configured() is True

    def test_unset_with_whitespace_only_shim_value_is_false(self, monkeypatch):
        monkeypatch.setattr(shim_config, "get_env_value", lambda key: "   ")
        assert tool_backend_helpers.fal_key_is_configured() is False

    def test_unset_with_shim_returning_none_is_false(self, monkeypatch):
        monkeypatch.setattr(shim_config, "get_env_value", lambda key: None)
        assert tool_backend_helpers.fal_key_is_configured() is False

    def test_unset_with_shim_raising_fails_closed_false(self, monkeypatch):
        # PIN: get_env_value raising is swallowed -> value reset to None -> False.
        def _raise(_key):
            raise RuntimeError("dotenv read blew up")

        monkeypatch.setattr(shim_config, "get_env_value", _raise)
        assert tool_backend_helpers.fal_key_is_configured() is False


# --------------------------------------------------------------------------- #
# prefers_gateway  (shim-backed; never raises -> fail-closed False)
# --------------------------------------------------------------------------- #
class TestPrefersGateway:
    def test_load_config_raising_returns_false(self, monkeypatch):
        def _raise():
            raise RuntimeError("config blew up")

        monkeypatch.setattr(shim_config, "load_config", _raise)
        assert tool_backend_helpers.prefers_gateway("image_tool") is False

    def test_section_with_use_gateway_true_returns_true(self, monkeypatch):
        monkeypatch.setattr(
            shim_config, "load_config", lambda: {"image_tool": {"use_gateway": True}}
        )
        assert tool_backend_helpers.prefers_gateway("image_tool") is True

    def test_truthy_string_use_gateway_returns_true(self, monkeypatch):
        # is_truthy_value coerces "yes"/"true"/etc. via the shared truthy set.
        monkeypatch.setattr(
            shim_config, "load_config", lambda: {"image_tool": {"use_gateway": "yes"}}
        )
        assert tool_backend_helpers.prefers_gateway("image_tool") is True

    def test_section_with_use_gateway_false_returns_false(self, monkeypatch):
        monkeypatch.setattr(
            shim_config, "load_config", lambda: {"image_tool": {"use_gateway": False}}
        )
        assert tool_backend_helpers.prefers_gateway("image_tool") is False

    def test_missing_use_gateway_key_defaults_false(self, monkeypatch):
        monkeypatch.setattr(
            shim_config, "load_config", lambda: {"image_tool": {"other": 1}}
        )
        assert tool_backend_helpers.prefers_gateway("image_tool") is False

    def test_section_absent_returns_false(self, monkeypatch):
        monkeypatch.setattr(shim_config, "load_config", lambda: {"other_tool": {}})
        assert tool_backend_helpers.prefers_gateway("image_tool") is False

    def test_section_not_a_dict_returns_false(self, monkeypatch):
        # PIN: a non-dict section value skips the isinstance branch -> False.
        monkeypatch.setattr(
            shim_config, "load_config", lambda: {"image_tool": "use_gateway"}
        )
        assert tool_backend_helpers.prefers_gateway("image_tool") is False

    def test_load_config_returns_none_returns_false(self, monkeypatch):
        # `(load_config() or {})` tolerates a None config.
        monkeypatch.setattr(shim_config, "load_config", lambda: None)
        assert tool_backend_helpers.prefers_gateway("image_tool") is False


# --------------------------------------------------------------------------- #
# managed_nous_tools_enabled  (account shim; fail-closed False)
# --------------------------------------------------------------------------- #
class TestManagedNousToolsEnabled:
    def test_not_logged_in_returns_false(self, monkeypatch):
        monkeypatch.setattr(
            shim_nous,
            "get_nous_portal_account_info",
            lambda *a, **k: SimpleNamespace(logged_in=False, tool_gateway_entitled=True),
        )
        assert tool_backend_helpers.managed_nous_tools_enabled() is False

    def test_logged_in_and_entitled_returns_true(self, monkeypatch):
        monkeypatch.setattr(
            shim_nous,
            "get_nous_portal_account_info",
            lambda *a, **k: SimpleNamespace(logged_in=True, tool_gateway_entitled=True),
        )
        assert tool_backend_helpers.managed_nous_tools_enabled() is True

    def test_logged_in_but_not_entitled_returns_false(self, monkeypatch):
        monkeypatch.setattr(
            shim_nous,
            "get_nous_portal_account_info",
            lambda *a, **k: SimpleNamespace(logged_in=True, tool_gateway_entitled=False),
        )
        assert tool_backend_helpers.managed_nous_tools_enabled() is False

    def test_account_lookup_raising_fails_closed_false(self, monkeypatch):
        def _raise(*_a, **_k):
            raise RuntimeError("portal unreachable")

        monkeypatch.setattr(shim_nous, "get_nous_portal_account_info", _raise)
        assert tool_backend_helpers.managed_nous_tools_enabled() is False

    def test_default_shim_returns_none_fails_closed_false(self, monkeypatch):
        # The real shim returns None; `None.logged_in` raises AttributeError ->
        # caught -> False. Pin this (it is the production default path).
        monkeypatch.setattr(
            shim_nous, "get_nous_portal_account_info", lambda *a, **k: None
        )
        assert tool_backend_helpers.managed_nous_tools_enabled() is False

    def test_force_fresh_true_is_passed_through_to_account_lookup(self, monkeypatch):
        captured = {}

        def _capture(*args, **kwargs):
            captured["force_fresh"] = kwargs.get("force_fresh")
            return SimpleNamespace(logged_in=True, tool_gateway_entitled=True)

        monkeypatch.setattr(shim_nous, "get_nous_portal_account_info", _capture)
        assert tool_backend_helpers.managed_nous_tools_enabled(force_fresh=True) is True
        assert captured["force_fresh"] is True

    def test_default_force_fresh_false_omits_kwarg(self, monkeypatch):
        # PIN: the non-fresh branch calls the lookup with NO keyword args (the
        # source has a distinct call site for force_fresh=False).
        captured = {}

        def _capture(*args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs
            return SimpleNamespace(logged_in=True, tool_gateway_entitled=True)

        monkeypatch.setattr(shim_nous, "get_nous_portal_account_info", _capture)
        assert tool_backend_helpers.managed_nous_tools_enabled() is True
        assert captured["args"] == ()
        assert captured["kwargs"] == {}
