"""Behavioral tests for the reachable path resolvers in ``hermes_constants``.

Scope is deliberately narrow: only the four functions that calfkit actually
reaches at runtime —

    * ``get_hermes_home``        (incl. the one-shot profile-fallback warning)
    * ``get_default_hermes_root``
    * ``get_subprocess_home``
    * ``display_hermes_home``

The remaining helpers (``is_wsl`` / ``is_container`` / ``is_termux`` /
``apply_ipv4_preference`` / the packaged-data discovery) are dormant
platform/config detectors in this vendor and are intentionally not exercised.

Test strategy
-------------
* These functions resolve the platform default via
  ``_get_platform_default_hermes_home`` → ``Path.home() / ".hermes"`` on POSIX,
  and ``Path.home()`` reads ``$HOME``. So setting ``HOME`` to ``tmp_path`` gives
  us a fully behavioral, side-effect-free way to pin the "native home" without
  monkeypatching the ``Path`` class globally. (Verified on the macOS dev box and
  Linux CI; if a future port runs on native Windows this assumption changes —
  the platform branch there is ``%LOCALAPPDATA%\\hermes``.)
* The autouse ``_hermetic_environment`` fixture in this package's conftest pins a
  per-test ``HERMES_HOME`` and scrubs ``HERMES_*``; to reach the "unset" branches
  we must ``delenv('HERMES_HOME')`` explicitly inside the test.
* macOS resolves ``/tmp`` through a ``/private/tmp`` symlink, so we assert on
  behavior / substrings rather than exact resolved string equality.
"""

import sys
from pathlib import Path

import pytest

from calfkit_tools.hermes._vendor import hermes_constants


# Native-Windows uses an entirely different platform-default branch
# (%LOCALAPPDATA%\hermes); these tests pin the POSIX ``~/.hermes`` behavior.
pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason="POSIX ~/.hermes platform-default behavior; native Windows differs",
)


@pytest.fixture(autouse=True)
def _no_context_override():
    """Ensure no in-process ContextVar override leaks into/out of a test.

    ``get_hermes_home`` and ``get_subprocess_home`` consult the context-local
    override *before* the env var, so a stray override would mask the env-driven
    branches we are pinning. Reset it to the unset sentinel around every test.
    """
    token = hermes_constants.set_hermes_home_override(None)
    try:
        yield
    finally:
        hermes_constants.reset_hermes_home_override(token)


# --------------------------------------------------------------------------- #
# get_hermes_home
# --------------------------------------------------------------------------- #
class TestGetHermesHome:
    def test_env_var_wins(self, tmp_path, monkeypatch):
        """An explicit HERMES_HOME is returned verbatim as a Path."""
        target = tmp_path / "explicit_home"
        monkeypatch.setenv("HERMES_HOME", str(target))

        assert hermes_constants.get_hermes_home() == target

    def test_context_override_beats_env(self, tmp_path, monkeypatch):
        """The context-local override takes precedence over HERMES_HOME."""
        env_home = tmp_path / "env_home"
        override_home = tmp_path / "override_home"
        monkeypatch.setenv("HERMES_HOME", str(env_home))

        token = hermes_constants.set_hermes_home_override(override_home)
        try:
            assert hermes_constants.get_hermes_home() == override_home
        finally:
            hermes_constants.reset_hermes_home_override(token)

    def test_falls_back_to_platform_default_when_unset(self, tmp_path, monkeypatch):
        """With no override and no HERMES_HOME, returns ``$HOME/.hermes``."""
        monkeypatch.delenv("HERMES_HOME", raising=False)
        monkeypatch.setenv("HOME", str(tmp_path))
        # Keep the one-shot warning suppressed for this purely-default case.
        monkeypatch.setattr(hermes_constants, "_profile_fallback_warned", True)

        assert hermes_constants.get_hermes_home() == tmp_path / ".hermes"

    def test_blank_env_var_is_treated_as_unset(self, tmp_path, monkeypatch):
        """A whitespace-only HERMES_HOME is stripped and falls through to default."""
        monkeypatch.setenv("HERMES_HOME", "   ")
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setattr(hermes_constants, "_profile_fallback_warned", True)

        assert hermes_constants.get_hermes_home() == tmp_path / ".hermes"

    def test_profile_fallback_warns_once_then_silent(self, tmp_path, monkeypatch, capsys):
        """Unset HERMES_HOME + a non-default active_profile => loud one-shot stderr warning.

        Pins the documented one-shot semantics: the module global
        ``_profile_fallback_warned`` gates the message, so it fires on the first
        offending call and is silent thereafter (within the process).
        """
        monkeypatch.delenv("HERMES_HOME", raising=False)
        monkeypatch.setenv("HOME", str(tmp_path))
        # active_profile lives at ``$HOME/.hermes/active_profile``.
        default_home = tmp_path / ".hermes"
        default_home.mkdir(parents=True, exist_ok=True)
        (default_home / "active_profile").write_text("coder\n")
        # Reset the process-global one-shot latch so THIS test is the "first offender".
        monkeypatch.setattr(hermes_constants, "_profile_fallback_warned", False)

        # First call: warns, and still returns the platform default.
        result = hermes_constants.get_hermes_home()
        assert result == default_home
        first = capsys.readouterr()
        assert "HERMES_HOME fallback" in first.err
        assert "coder" in first.err
        # The latch is now set as a side effect.
        assert hermes_constants._profile_fallback_warned is True

        # Second call: latch is set => silent (one-shot), same return value.
        result2 = hermes_constants.get_hermes_home()
        assert result2 == default_home
        second = capsys.readouterr()
        assert second.err == ""

    def test_default_active_profile_does_not_warn(self, tmp_path, monkeypatch, capsys):
        """An active_profile of exactly ``default`` is the no-op case — no warning."""
        monkeypatch.delenv("HERMES_HOME", raising=False)
        monkeypatch.setenv("HOME", str(tmp_path))
        default_home = tmp_path / ".hermes"
        default_home.mkdir(parents=True, exist_ok=True)
        (default_home / "active_profile").write_text("default\n")
        monkeypatch.setattr(hermes_constants, "_profile_fallback_warned", False)

        hermes_constants.get_hermes_home()

        out = capsys.readouterr()
        assert out.err == ""
        # Latch stays unset because nothing warned.
        assert hermes_constants._profile_fallback_warned is False

    def test_missing_active_profile_does_not_warn(self, tmp_path, monkeypatch, capsys):
        """No active_profile file at all => the guard reads "" and stays quiet."""
        monkeypatch.delenv("HERMES_HOME", raising=False)
        monkeypatch.setenv("HOME", str(tmp_path))
        # Note: we deliberately do NOT create ~/.hermes/active_profile.
        monkeypatch.setattr(hermes_constants, "_profile_fallback_warned", False)

        result = hermes_constants.get_hermes_home()

        assert result == tmp_path / ".hermes"
        out = capsys.readouterr()
        assert out.err == ""
        assert hermes_constants._profile_fallback_warned is False

    def test_unreadable_active_profile_is_swallowed_no_warning(
        self, tmp_path, monkeypatch, capsys
    ):
        """If reading active_profile raises OSError, the guard swallows it (active="").

        Pins the ``except (UnicodeDecodeError, OSError)`` branch: making
        ``active_profile`` a *directory* turns ``read_text()`` into an
        ``IsADirectoryError`` (an ``OSError``), so the guard treats the profile
        as empty and stays quiet instead of crashing import-time callers.
        """
        monkeypatch.delenv("HERMES_HOME", raising=False)
        monkeypatch.setenv("HOME", str(tmp_path))
        default_home = tmp_path / ".hermes"
        # active_profile as a DIRECTORY => read_text() raises IsADirectoryError.
        (default_home / "active_profile").mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(hermes_constants, "_profile_fallback_warned", False)

        result = hermes_constants.get_hermes_home()

        assert result == default_home
        out = capsys.readouterr()
        assert out.err == ""
        # Nothing warned, so the latch is untouched.
        assert hermes_constants._profile_fallback_warned is False

    def test_warning_latch_already_set_suppresses_even_non_default(
        self, tmp_path, monkeypatch, capsys
    ):
        """If the process latch is already True, a non-default profile is NOT re-warned.

        This pins the cross-test/process global one-shot: once *any* prior caller
        has warned, later offenders are silent. (The analysis flagged this as a
        process-global one-shot — only the first offender warns. Pinned, not
        "fixed".)
        """
        monkeypatch.delenv("HERMES_HOME", raising=False)
        monkeypatch.setenv("HOME", str(tmp_path))
        default_home = tmp_path / ".hermes"
        default_home.mkdir(parents=True, exist_ok=True)
        (default_home / "active_profile").write_text("coder\n")
        # Pretend an earlier (unrelated) caller already tripped the latch.
        monkeypatch.setattr(hermes_constants, "_profile_fallback_warned", True)

        hermes_constants.get_hermes_home()

        out = capsys.readouterr()
        assert out.err == ""


# --------------------------------------------------------------------------- #
# get_default_hermes_root
# --------------------------------------------------------------------------- #
class TestGetDefaultHermesRoot:
    def test_unset_returns_native_home(self, tmp_path, monkeypatch):
        """No HERMES_HOME => the platform-native ``$HOME/.hermes`` is the root."""
        monkeypatch.delenv("HERMES_HOME", raising=False)
        monkeypatch.setenv("HOME", str(tmp_path))

        assert hermes_constants.get_default_hermes_root() == tmp_path / ".hermes"

    def test_blank_env_returns_native_home(self, tmp_path, monkeypatch):
        """A blank HERMES_HOME is falsy here and yields the native home.

        NOTE: ``get_default_hermes_root`` checks ``if not env_home`` against the
        RAW value (no ``.strip()``), so a whitespace-only string like ``"   "``
        is truthy and would fall through to the path branches — only the empty
        string hits this early-return. We pin the empty-string behavior.
        """
        monkeypatch.setenv("HERMES_HOME", "")
        monkeypatch.setenv("HOME", str(tmp_path))

        assert hermes_constants.get_default_hermes_root() == tmp_path / ".hermes"

    def test_home_under_native_returns_native_home(self, tmp_path, monkeypatch):
        """HERMES_HOME nested under ``~/.hermes`` (profile mode) => native home.

        Exercises the ``relative_to`` success branch: a profile path like
        ``~/.hermes/profiles/coder`` resolves *under* the native home, so the
        root is the native home itself.
        """
        monkeypatch.setenv("HOME", str(tmp_path))
        native = tmp_path / ".hermes"
        profile = native / "profiles" / "coder"
        profile.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv("HERMES_HOME", str(profile))

        # resolve() both sides => robust against the /tmp->/private/tmp symlink.
        assert hermes_constants.get_default_hermes_root() == native

    def test_docker_profile_path_returns_grandparent(self, tmp_path, monkeypatch):
        """A custom ``<root>/profiles/<name>`` outside ~/.hermes => grandparent root.

        Exercises the ValueError branch (not under native) + the
        ``parent.name == "profiles"`` case: HERMES_HOME=/opt/data/profiles/coder
        should resolve the root to /opt/data.
        """
        # Put HOME somewhere unrelated so the docker dir is NOT under ~/.hermes.
        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        root = tmp_path / "opt" / "data"
        profile = root / "profiles" / "coder"
        profile.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv("HERMES_HOME", str(profile))

        result = hermes_constants.get_default_hermes_root()
        assert result == root

    def test_custom_non_profile_path_returns_itself(self, tmp_path, monkeypatch):
        """A custom HERMES_HOME outside ~/.hermes and not a profile path => itself.

        Exercises the ValueError branch + the final fall-through: HERMES_HOME
        itself IS the root (e.g. a Docker ``/opt/data`` mount).
        """
        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        custom = tmp_path / "opt" / "hermes-custom"
        custom.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv("HERMES_HOME", str(custom))

        # Note: returned as the un-resolved ``Path(env_home)`` (no .resolve()).
        assert hermes_constants.get_default_hermes_root() == custom


# --------------------------------------------------------------------------- #
# get_subprocess_home
# --------------------------------------------------------------------------- #
class TestGetSubprocessHome:
    def test_returns_home_subdir_when_present(self, tmp_path, monkeypatch):
        """``{HERMES_HOME}/home`` existing => returned as the subprocess HOME string."""
        hermes_home = tmp_path / "hh"
        (hermes_home / "home").mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv("HERMES_HOME", str(hermes_home))

        result = hermes_constants.get_subprocess_home()
        assert result == str(hermes_home / "home")
        assert isinstance(result, str)

    def test_returns_none_when_home_subdir_absent(self, tmp_path, monkeypatch):
        """No ``home/`` subdir => activation is opt-in, so returns None."""
        hermes_home = tmp_path / "hh"
        hermes_home.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv("HERMES_HOME", str(hermes_home))

        assert hermes_constants.get_subprocess_home() is None

    def test_returns_none_when_hermes_home_unset(self, monkeypatch):
        """No override and no HERMES_HOME => None (no default subprocess HOME)."""
        monkeypatch.delenv("HERMES_HOME", raising=False)

        assert hermes_constants.get_subprocess_home() is None

    def test_override_supplies_hermes_home(self, tmp_path, monkeypatch):
        """The context override is consulted even though the env var is unset."""
        monkeypatch.delenv("HERMES_HOME", raising=False)
        hermes_home = tmp_path / "hh"
        (hermes_home / "home").mkdir(parents=True, exist_ok=True)

        token = hermes_constants.set_hermes_home_override(hermes_home)
        try:
            assert hermes_constants.get_subprocess_home() == str(hermes_home / "home")
        finally:
            hermes_constants.reset_hermes_home_override(token)

    def test_home_path_that_is_a_file_returns_none(self, tmp_path, monkeypatch):
        """``home`` existing as a *file* (not a dir) => ``isdir`` False => None."""
        hermes_home = tmp_path / "hh"
        hermes_home.mkdir(parents=True, exist_ok=True)
        (hermes_home / "home").write_text("not a dir")
        monkeypatch.setenv("HERMES_HOME", str(hermes_home))

        assert hermes_constants.get_subprocess_home() is None


# --------------------------------------------------------------------------- #
# display_hermes_home
# --------------------------------------------------------------------------- #
class TestDisplayHermesHome:
    def test_under_home_uses_tilde_shorthand(self, tmp_path, monkeypatch):
        """A HERMES_HOME under ``$HOME`` is displayed with the ``~/`` shorthand."""
        monkeypatch.setenv("HOME", str(tmp_path))
        home = tmp_path / ".hermes" / "profiles" / "coder"
        monkeypatch.setenv("HERMES_HOME", str(home))

        # relative_to is computed against Path.home() (== $HOME == tmp_path).
        assert hermes_constants.display_hermes_home() == "~/.hermes/profiles/coder"

    def test_outside_home_returns_raw_abs_path(self, tmp_path, monkeypatch):
        """A HERMES_HOME NOT under ``$HOME`` => raw absolute path (ValueError branch)."""
        # HOME and the custom dir are siblings, so the custom dir is not relative
        # to home and ``relative_to`` raises ValueError.
        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        custom = tmp_path / "opt" / "hermes-custom"
        custom.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv("HERMES_HOME", str(custom))

        result = hermes_constants.display_hermes_home()
        assert result == str(custom)
        assert not result.startswith("~/")

    def test_display_default_home_is_tilde_dot_hermes(self, tmp_path, monkeypatch):
        """With no HERMES_HOME, the default ``$HOME/.hermes`` displays as ``~/.hermes``."""
        monkeypatch.delenv("HERMES_HOME", raising=False)
        monkeypatch.setenv("HOME", str(tmp_path))
        # Suppress the unrelated profile-fallback warning path.
        monkeypatch.setattr(hermes_constants, "_profile_fallback_warned", True)

        assert hermes_constants.display_hermes_home() == "~/.hermes"
