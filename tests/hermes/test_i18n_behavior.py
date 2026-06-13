"""Behavioral tests for the vendored Hermes i18n module.

These PIN the *current* observable behavior of
``calfkit_tools.hermes._vendor.agent.i18n`` -- including one genuine bug (the
placeholder leak, marked ``# BUG:``). They are NOT aspirational: where the code
does something arguably wrong, the test asserts what it *actually* does today so
that a future fix surfaces as a deliberate, reviewed change.

Coverage focus (the previously-uncovered branches):
  * ``t()`` placeholder leak when a ``{count}`` value is rendered with no kwargs.
  * ``t()`` format failure (wrong kwarg) -> caught, returns the unformatted value.
  * ``t()`` missing key -> returns the bare key string.
  * ``t()`` cross-locale English fallback (reachable only via a synthetic partial
    catalog, since the shipped en/zh catalogs are at key parity).
  * ``_normalize_lang`` region-strip / alias / non-str / empty.
  * ``_load_catalog`` missing file -> ``{}`` and malformed YAML -> ``{}`` (no crash).
  * ``_locales_dir`` when the override points at a FILE -> warns + falls back.
  * ``get_language`` env > config precedence.
  * ``reset_language_cache`` actually invalidates the caches.

Isolation: the autouse ``_hermetic_environment`` fixture (tests/hermes/conftest.py)
scrubs every ``HERMES_*`` var per test, so we set ``HERMES_LANGUAGE`` /
``HERMES_BUNDLED_LOCALES`` via ``monkeypatch`` inside each test. Any test that
mutates locale resolution also calls ``reset_language_cache()`` (and the
``lru_cache``'s ``cache_clear``) so the per-process catalog/config caches never
leak across tests. A module-level autouse fixture below enforces that for every
test as a belt-and-suspenders guarantee; run the file twice to confirm no leak.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from calfkit_tools.hermes._vendor.agent import i18n


# --------------------------------------------------------------------------- #
# Local isolation: clear i18n's process-global caches around every test in this
# file. The shared conftest does not know about i18n's _catalog_cache /
# _config_language_cached lru_cache, and _load_catalog memoizes even the empty
# ({}) result for a missing/synthetic locale dir -- which would otherwise pin a
# stale catalog into the next test (and make a second run of this file differ
# from the first).
# --------------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def _reset_i18n_caches():
    i18n.reset_language_cache()
    yield
    i18n.reset_language_cache()


def _write_locale_dir(tmp_path: Path, name: str, **files: str) -> Path:
    """Create a temp locales/ dir holding ``<lang>.yaml`` files.

    ``files`` maps a language code to raw YAML text, e.g.
    ``_write_locale_dir(tmp_path, "loc", en="approval:\\n  x: hi")``.
    """
    loc = tmp_path / name
    loc.mkdir()
    for lang, text in files.items():
        (loc / f"{lang}.yaml").write_text(text, encoding="utf-8")
    return loc


# A minimal en catalog whose one value carries a {count} placeholder, plus a
# couple of plain keys. Used to exercise format paths deterministically without
# depending on the exact contents of the shipped catalogs.
_EN_WITH_COUNT = (
    "gateway:\n"
    '  draining: "Draining {count} active agent(s)..."\n'
    '  goal_cleared: "Goal cleared."\n'
    "approval:\n"
    '  denied: "Denied"\n'
)


# =========================================================================== #
# t() -- formatting / fallback behavior
# =========================================================================== #
class TestTranslateFormatting:
    def test_placeholder_leak_when_kwarg_omitted(self, tmp_path, monkeypatch):
        # BUG: when a value contains a {placeholder} but the caller passes NO
        # format_kwargs, t() skips str.format entirely and returns the RAW
        # template -- so the user sees a literal "{count}" instead of a number.
        # A correct implementation would format even with zero kwargs (and would
        # then surface the missing arg via the existing try/except). We PIN the
        # leak here; flip this assertion only alongside a real code fix.
        loc = _write_locale_dir(tmp_path, "loc", en=_EN_WITH_COUNT)
        monkeypatch.setenv("HERMES_BUNDLED_LOCALES", str(loc))
        monkeypatch.setenv("HERMES_LANGUAGE", "en")
        i18n.reset_language_cache()

        leaked = i18n.t("gateway.draining")  # no count= passed
        assert leaked == "Draining {count} active agent(s)..."
        assert "{count}" in leaked  # the placeholder is exposed verbatim

    def test_format_substitutes_when_kwarg_present(self, tmp_path, monkeypatch):
        # Sanity counterpart to the leak: with the kwarg, formatting happens.
        loc = _write_locale_dir(tmp_path, "loc", en=_EN_WITH_COUNT)
        monkeypatch.setenv("HERMES_BUNDLED_LOCALES", str(loc))
        monkeypatch.setenv("HERMES_LANGUAGE", "en")
        i18n.reset_language_cache()

        assert i18n.t("gateway.draining", count=3) == "Draining 3 active agent(s)..."

    def test_format_failure_wrong_kwarg_returns_unformatted_and_warns(
        self, tmp_path, monkeypatch, caplog
    ):
        # A value needs {count}; we pass an unrelated kwarg. str.format raises
        # KeyError('count'), which t() catches and logs at WARNING, returning the
        # value UNFORMATTED (placeholder still present) rather than raising.
        loc = _write_locale_dir(tmp_path, "loc", en=_EN_WITH_COUNT)
        monkeypatch.setenv("HERMES_BUNDLED_LOCALES", str(loc))
        monkeypatch.setenv("HERMES_LANGUAGE", "en")
        i18n.reset_language_cache()

        with caplog.at_level(logging.WARNING, logger=i18n.logger.name):
            out = i18n.t("gateway.draining", wrong_kwarg=5)

        assert out == "Draining {count} active agent(s)..."
        assert any("i18n format failed" in r.message for r in caplog.records)

    def test_format_failure_bad_spec_value_error_is_caught(self, tmp_path, monkeypatch):
        # ValueError from a malformed format spec is in the caught tuple too.
        bad = 'gateway:\n  x: "value {count:zzz}"\n'
        loc = _write_locale_dir(tmp_path, "loc", en=bad)
        monkeypatch.setenv("HERMES_BUNDLED_LOCALES", str(loc))
        monkeypatch.setenv("HERMES_LANGUAGE", "en")
        i18n.reset_language_cache()

        # Should not raise; returns the raw value because formatting failed.
        assert i18n.t("gateway.x", count=4) == "value {count:zzz}"

    def test_missing_key_returns_bare_key(self, tmp_path, monkeypatch):
        # Key absent from target AND from English -> the dotted key path itself
        # is returned (a broken catalog must never crash the agent).
        loc = _write_locale_dir(tmp_path, "loc", en=_EN_WITH_COUNT)
        monkeypatch.setenv("HERMES_BUNDLED_LOCALES", str(loc))
        monkeypatch.setenv("HERMES_LANGUAGE", "en")
        i18n.reset_language_cache()

        assert i18n.t("does.not.exist") == "does.not.exist"

    def test_missing_key_with_kwargs_returns_bare_key_unformatted(
        self, tmp_path, monkeypatch
    ):
        # The bare-key fallback has no placeholders, so format_kwargs are a no-op
        # and the key passes through unchanged (str.format on a plain string).
        loc = _write_locale_dir(tmp_path, "loc", en=_EN_WITH_COUNT)
        monkeypatch.setenv("HERMES_BUNDLED_LOCALES", str(loc))
        monkeypatch.setenv("HERMES_LANGUAGE", "en")
        i18n.reset_language_cache()

        assert i18n.t("no.such.key", count=9) == "no.such.key"

    def test_cross_locale_english_fallback(self, tmp_path, monkeypatch):
        # The shipped en/zh catalogs are at key parity, so the en-fallback branch
        # is only reachable with a synthetic PARTIAL non-en catalog: a key present
        # in en but absent in the target falls through to the English value.
        en_text = (
            "gateway:\n"
            '  only_in_en: "English only value"\n'
            '  shared: "EN shared"\n'
        )
        zh_text = 'gateway:\n  shared: "ZH shared"\n'  # deliberately missing only_in_en
        loc = _write_locale_dir(tmp_path, "loc", en=en_text, zh=zh_text)
        monkeypatch.setenv("HERMES_BUNDLED_LOCALES", str(loc))
        monkeypatch.setenv("HERMES_LANGUAGE", "zh")
        i18n.reset_language_cache()

        # Present in zh -> zh wins.
        assert i18n.t("gateway.shared") == "ZH shared"
        # Absent in zh, present in en -> English fallback (NOT the bare key).
        assert i18n.t("gateway.only_in_en") == "English only value"

    def test_explicit_lang_arg_overrides_env(self, tmp_path, monkeypatch):
        # lang= takes precedence over HERMES_LANGUAGE.
        en_text = 'gateway:\n  shared: "EN shared"\n'
        zh_text = 'gateway:\n  shared: "ZH shared"\n'
        loc = _write_locale_dir(tmp_path, "loc", en=en_text, zh=zh_text)
        monkeypatch.setenv("HERMES_BUNDLED_LOCALES", str(loc))
        monkeypatch.setenv("HERMES_LANGUAGE", "en")
        i18n.reset_language_cache()

        assert i18n.t("gateway.shared", lang="zh") == "ZH shared"


# =========================================================================== #
# _normalize_lang
# =========================================================================== #
class TestNormalizeLang:
    def test_supported_code_passthrough(self):
        assert i18n._normalize_lang("zh") == "zh"
        assert i18n._normalize_lang("en") == "en"

    def test_case_insensitive(self):
        assert i18n._normalize_lang("ZH") == "zh"
        assert i18n._normalize_lang("Fr") == "fr"

    def test_region_strip_to_supported_base(self):
        # "zh-CN" is in the alias table; "de-XX" is not, so it falls through to
        # the region-strip branch and lands on the supported base "de".
        assert i18n._normalize_lang("zh-CN") == "zh"
        assert i18n._normalize_lang("de-XX") == "de"

    def test_alias_resolution(self):
        assert i18n._normalize_lang("pt-br") == "pt"
        assert i18n._normalize_lang("chinese") == "zh"
        assert i18n._normalize_lang("traditional-chinese") == "zh-hant"

    def test_non_string_returns_default(self):
        assert i18n._normalize_lang(123) == "en"
        assert i18n._normalize_lang(None) == "en"
        assert i18n._normalize_lang(["zh"]) == "en"

    def test_empty_or_whitespace_returns_default(self):
        assert i18n._normalize_lang("") == "en"
        assert i18n._normalize_lang("   ") == "en"

    def test_unknown_value_returns_default(self):
        assert i18n._normalize_lang("klingon") == "en"
        # Region strip of an unsupported base also falls back.
        assert i18n._normalize_lang("xx-yy") == "en"

    def test_whitespace_around_valid_code_is_stripped(self):
        assert i18n._normalize_lang("  zh  ") == "zh"


# =========================================================================== #
# _load_catalog
# =========================================================================== #
class TestLoadCatalog:
    def test_missing_file_returns_empty_dict(self, tmp_path, monkeypatch):
        # Dir exists but has no <lang>.yaml -> {} (and it's cached as {}).
        loc = tmp_path / "loc"
        loc.mkdir()
        monkeypatch.setenv("HERMES_BUNDLED_LOCALES", str(loc))
        i18n.reset_language_cache()

        assert i18n._load_catalog("en") == {}

    def test_malformed_yaml_returns_empty_dict_without_crashing(
        self, tmp_path, monkeypatch, caplog
    ):
        # Unparseable YAML must be swallowed (logged at WARNING) -> {}, never a
        # raised exception that would crash the agent.
        bad_yaml = "approval:\n  x: [unclosed list\n  : : :\n"
        loc = _write_locale_dir(tmp_path, "loc", en=bad_yaml)
        monkeypatch.setenv("HERMES_BUNDLED_LOCALES", str(loc))
        i18n.reset_language_cache()

        with caplog.at_level(logging.WARNING, logger=i18n.logger.name):
            result = i18n._load_catalog("en")

        assert result == {}
        assert any("Failed to load i18n catalog" in r.message for r in caplog.records)

    def test_empty_yaml_file_returns_empty_dict(self, tmp_path, monkeypatch):
        # yaml.safe_load("") is None; the ``or {}`` guard turns it into {}.
        loc = _write_locale_dir(tmp_path, "loc", en="")
        monkeypatch.setenv("HERMES_BUNDLED_LOCALES", str(loc))
        i18n.reset_language_cache()

        assert i18n._load_catalog("en") == {}

    def test_nested_yaml_is_flattened_to_dotted_keys(self, tmp_path, monkeypatch):
        text = (
            "gateway:\n"
            "  model:\n"
            '    switched: "Switched"\n'
            'top: "plain"\n'
        )
        loc = _write_locale_dir(tmp_path, "loc", en=text)
        monkeypatch.setenv("HERMES_BUNDLED_LOCALES", str(loc))
        i18n.reset_language_cache()

        cat = i18n._load_catalog("en")
        assert cat == {"gateway.model.switched": "Switched", "top": "plain"}

    def test_non_string_leaves_are_dropped(self, tmp_path, monkeypatch):
        # _flatten_into ignores non-str, non-dict leaves (catalogs are text-only).
        text = (
            "gateway:\n"
            '  text_key: "kept"\n'
            "  number_key: 42\n"
            "  list_key:\n"
            "    - a\n"
            "    - b\n"
        )
        loc = _write_locale_dir(tmp_path, "loc", en=text)
        monkeypatch.setenv("HERMES_BUNDLED_LOCALES", str(loc))
        i18n.reset_language_cache()

        cat = i18n._load_catalog("en")
        assert cat == {"gateway.text_key": "kept"}

    def test_catalog_is_cached_per_language(self, tmp_path, monkeypatch):
        # Second call serves from _catalog_cache: deleting the file on disk after
        # the first load does not change the result.
        loc = _write_locale_dir(tmp_path, "loc", en='a:\n  b: "v"\n')
        monkeypatch.setenv("HERMES_BUNDLED_LOCALES", str(loc))
        i18n.reset_language_cache()

        first = i18n._load_catalog("en")
        assert first == {"a.b": "v"}
        (loc / "en.yaml").unlink()  # gone from disk
        second = i18n._load_catalog("en")
        assert second == {"a.b": "v"}  # served from cache, no re-read
        assert second is first  # exact same cached object


# =========================================================================== #
# _locales_dir
# =========================================================================== #
class TestLocalesDir:
    def test_override_directory_is_used(self, tmp_path, monkeypatch):
        loc = tmp_path / "myloc"
        loc.mkdir()
        monkeypatch.setenv("HERMES_BUNDLED_LOCALES", str(loc))
        assert i18n._locales_dir() == loc

    def test_override_pointing_at_file_warns_and_falls_back(
        self, tmp_path, monkeypatch, caplog
    ):
        # When HERMES_BUNDLED_LOCALES is a FILE (not a dir), the override is
        # rejected with a WARNING and resolution falls back to the bundled/source
        # locales dir -- which, in this repo, exists and ends with "locales".
        a_file = tmp_path / "not_a_dir.txt"
        a_file.write_text("x", encoding="utf-8")
        monkeypatch.setenv("HERMES_BUNDLED_LOCALES", str(a_file))

        with caplog.at_level(logging.WARNING, logger=i18n.logger.name):
            resolved = i18n._locales_dir()

        assert resolved != a_file
        assert resolved.name == "locales"
        assert resolved.is_dir()
        assert any(
            "HERMES_BUNDLED_LOCALES points to a non-directory" in r.message
            for r in caplog.records
        )

    def test_unset_override_resolves_to_source_locales(self, monkeypatch):
        # No override -> the source-checkout locales dir next to agent/ wins, and
        # it actually contains the shipped catalogs (en/zh present).
        monkeypatch.delenv("HERMES_BUNDLED_LOCALES", raising=False)
        resolved = i18n._locales_dir()
        assert resolved.name == "locales"
        assert resolved.is_dir()
        assert (resolved / "en.yaml").is_file()
        assert (resolved / "zh.yaml").is_file()

    def test_blank_override_is_ignored(self, tmp_path, monkeypatch):
        # Whitespace-only override .strip()s to empty -> treated as unset.
        monkeypatch.setenv("HERMES_BUNDLED_LOCALES", "   ")
        resolved = i18n._locales_dir()
        assert resolved.name == "locales"
        assert resolved.is_dir()


# =========================================================================== #
# get_language -- env > config > default precedence
# =========================================================================== #
class TestGetLanguage:
    def test_env_var_wins(self, monkeypatch):
        monkeypatch.setenv("HERMES_LANGUAGE", "zh")
        i18n.reset_language_cache()
        assert i18n.get_language() == "zh"

    def test_env_var_is_normalized(self, monkeypatch):
        # Aliases / regional tags flow through _normalize_lang.
        monkeypatch.setenv("HERMES_LANGUAGE", "zh-CN")
        i18n.reset_language_cache()
        assert i18n.get_language() == "zh"

    def test_env_overrides_config(self, monkeypatch):
        # Config says "ja", env says "fr" -> env wins. We patch the config reader
        # because the shipped shim's load_config() returns {} (no language).
        monkeypatch.setattr(
            "calfkit_tools.hermes._shims.hermes_cli.config.load_config",
            lambda: {"display": {"language": "ja"}},
        )
        monkeypatch.setenv("HERMES_LANGUAGE", "fr")
        i18n.reset_language_cache()
        assert i18n.get_language() == "fr"

    def test_config_used_when_env_absent(self, monkeypatch):
        # No env var -> config's display.language is consulted (and normalized).
        monkeypatch.delenv("HERMES_LANGUAGE", raising=False)
        monkeypatch.setattr(
            "calfkit_tools.hermes._shims.hermes_cli.config.load_config",
            lambda: {"display": {"language": "german"}},  # alias -> de
        )
        i18n.reset_language_cache()
        assert i18n.get_language() == "de"

    def test_default_when_env_and_config_both_absent(self, monkeypatch):
        # No env, shim config returns {} -> baseline "en".
        monkeypatch.delenv("HERMES_LANGUAGE", raising=False)
        i18n.reset_language_cache()
        assert i18n.get_language() == "en"

    def test_blank_config_language_falls_back_to_default(self, monkeypatch):
        # display.language present but empty -> treated as no config language.
        monkeypatch.delenv("HERMES_LANGUAGE", raising=False)
        monkeypatch.setattr(
            "calfkit_tools.hermes._shims.hermes_cli.config.load_config",
            lambda: {"display": {"language": ""}},
        )
        i18n.reset_language_cache()
        assert i18n.get_language() == "en"

    def test_config_read_error_falls_back_to_default(self, monkeypatch):
        # If load_config raises, _config_language_cached swallows it -> None ->
        # get_language returns the default rather than propagating.
        def _boom():
            raise RuntimeError("config blew up")

        monkeypatch.delenv("HERMES_LANGUAGE", raising=False)
        monkeypatch.setattr(
            "calfkit_tools.hermes._shims.hermes_cli.config.load_config", _boom
        )
        i18n.reset_language_cache()
        assert i18n.get_language() == "en"


# =========================================================================== #
# reset_language_cache -- invalidation behavior
# =========================================================================== #
class TestResetLanguageCache:
    def test_reset_invalidates_catalog_cache(self, tmp_path, monkeypatch):
        # Load a catalog, change the file on disk, and confirm the *old* value is
        # cached until reset_language_cache() forces a re-read.
        loc = _write_locale_dir(tmp_path, "loc", en='k:\n  v: "first"\n')
        monkeypatch.setenv("HERMES_BUNDLED_LOCALES", str(loc))
        i18n.reset_language_cache()

        assert i18n._load_catalog("en") == {"k.v": "first"}

        (loc / "en.yaml").write_text('k:\n  v: "second"\n', encoding="utf-8")
        assert i18n._load_catalog("en") == {"k.v": "first"}  # stale (cached)

        i18n.reset_language_cache()
        assert i18n._load_catalog("en") == {"k.v": "second"}  # re-read after reset

    def test_reset_invalidates_config_language_cache(self, monkeypatch):
        # _config_language_cached is an lru_cache(maxsize=1). Resolve once, swap
        # the config, and confirm only reset_language_cache() lets the new value
        # take effect.
        monkeypatch.delenv("HERMES_LANGUAGE", raising=False)

        calls = {"n": 0}

        def _cfg_v1():
            calls["n"] += 1
            return {"display": {"language": "ja"}}

        monkeypatch.setattr(
            "calfkit_tools.hermes._shims.hermes_cli.config.load_config", _cfg_v1
        )
        i18n.reset_language_cache()
        assert i18n.get_language() == "ja"
        assert calls["n"] == 1

        # Second resolution: still cached, load_config NOT called again.
        assert i18n.get_language() == "ja"
        assert calls["n"] == 1

        # Swap config to fr; without a reset the cached "ja" persists.
        monkeypatch.setattr(
            "calfkit_tools.hermes._shims.hermes_cli.config.load_config",
            lambda: {"display": {"language": "fr"}},
        )
        assert i18n.get_language() == "ja"  # still cached

        i18n.reset_language_cache()
        assert i18n.get_language() == "fr"  # now picks up the new config

    def test_reset_clears_negative_catalog_cache_entry(self, tmp_path, monkeypatch):
        # A missing locale caches {} (a "negative" entry). After the file appears
        # and we reset, the real catalog must load -- proving {} was cleared too.
        loc = tmp_path / "loc"
        loc.mkdir()
        monkeypatch.setenv("HERMES_BUNDLED_LOCALES", str(loc))
        i18n.reset_language_cache()

        assert i18n._load_catalog("en") == {}  # negative cache entry stored

        (loc / "en.yaml").write_text('a:\n  b: "now here"\n', encoding="utf-8")
        assert i18n._load_catalog("en") == {}  # still the cached empty dict

        i18n.reset_language_cache()
        assert i18n._load_catalog("en") == {"a.b": "now here"}
