"""Behavioral tests for the LOCAL terminal backend's env / temp / kill helpers.

Target: ``calfkit_tools.hermes._vendor.tools.environments.local`` — the only
supported default backend.

These pin the *currently observed* behavior of the branches that the existing
vendored suite leaves uncovered (the shared ``.coverage`` reports ~74% for this
file):

  * ``_sanitize_subprocess_env`` — only reached via process_registry (PTY /
    background spawn), so it sits near 0% in the normal ``execute()`` path:
      - the ``_HERMES_FORCE_<KEY>`` → ``<KEY>`` rewrite that re-allows a
        blocklisted var supplied through ``extra_env``;
      - the blocklist strip of provider creds (OPENAI_API_KEY, …) from the
        *base* env;
      - the ``is_env_passthrough`` re-allow escape hatch;
      - per-profile HOME injection via ``get_subprocess_home``;
      - HERMES_HOME context-override bridging.
  * ``_make_run_env`` — the ContextVar/session bridge (``_VAR_MAP``) and the
    per-profile HOME injection (both empty/inactive by default in the shim).
  * ``_resolve_shell_init_files`` — explicit list wins, missing files dropped,
    and ``expanduser``/``expandvars`` raising → candidate skipped.
  * ``_prepend_shell_init`` — single-quote-in-path escaping.
  * ``get_temp_dir`` — ``self.env`` TMPDIR override beats ``os.environ``;
    ``/tmp`` not writable → ``tempfile.gettempdir`` fallback → final ``/tmp``.
  * ``_kill_process`` — PermissionError on ``killpg`` falls through to the
    outer ``proc.kill()`` cleanup.

Lazy intra-function imports in ``local.py`` are patched at their *source*
module (``env_passthrough``, ``session_context``, ``hermes_constants``) so the
patch is in effect when ``local`` re-imports the symbol per call.

Windows-only branches (``_IS_WINDOWS``) are dormant on this POSIX target and are
intentionally not exercised here.
"""

import os
from contextvars import ContextVar
from unittest.mock import MagicMock, patch

import pytest

from calfkit_tools.hermes._vendor.tools.environments import local
from calfkit_tools.hermes._vendor.tools.environments.local import (
    LocalEnvironment,
    _HERMES_PROVIDER_ENV_BLOCKLIST,
    _HERMES_PROVIDER_ENV_FORCE_PREFIX,
)

# Path strings for patching lazy imports at their source module.
_PASSTHROUGH_SRC = (
    "calfkit_tools.hermes._vendor.tools.env_passthrough.is_env_passthrough"
)


@pytest.fixture
def local_env():
    """A LocalEnvironment that skips the real login-shell snapshot.

    ``init_session`` spawns ``bash -l`` and reads/writes temp files; suppress it
    so construction is pure and these helper-level tests stay hermetic.
    """
    with patch.object(LocalEnvironment, "init_session", return_value=None):
        env = LocalEnvironment(cwd="/tmp", timeout=10)
    yield env


def _activate_profile_home(monkeypatch, tmp_path):
    """Create ``{HERMES_HOME}/home/`` so ``get_subprocess_home()`` activates.

    Returns the absolute home-override directory the backend should inject as
    ``HOME`` into subprocess environments.
    """
    hermes_home = tmp_path / "hh"
    profile_home = hermes_home / "home"
    profile_home.mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    return str(profile_home)


# ---------------------------------------------------------------------------
# _sanitize_subprocess_env  (process_registry path — ~0% otherwise)
# ---------------------------------------------------------------------------


class TestSanitizeSubprocessEnv:
    def test_base_env_blocklisted_provider_var_is_stripped(self):
        """A Hermes provider credential in the *base* env is removed."""
        out = local._sanitize_subprocess_env(
            {"OPENAI_API_KEY": "sk-secret", "PATH": "/usr/bin:/bin"}
        )
        assert "OPENAI_API_KEY" not in out
        # Non-blocklisted base vars survive.
        assert out["PATH"] == "/usr/bin:/bin"

    def test_base_env_force_prefixed_key_is_dropped_not_rewritten(self):
        """``_HERMES_FORCE_*`` keys in the *base* env are skipped entirely.

        The force-rewrite only applies to ``extra_env`` (the explicit caller
        override). In the base env the prefixed key is just dropped — it neither
        survives verbatim nor gets un-prefixed.
        """
        forced = f"{_HERMES_PROVIDER_ENV_FORCE_PREFIX}OPENAI_API_KEY"
        out = local._sanitize_subprocess_env({forced: "sk-base", "USER": "me"})
        assert forced not in out
        assert "OPENAI_API_KEY" not in out
        assert out["USER"] == "me"

    def test_extra_env_force_prefix_reallows_blocklisted_var(self):
        """``extra_env`` ``_HERMES_FORCE_OPENAI_API_KEY`` injects OPENAI_API_KEY.

        This is the explicit opt-in escape hatch: the value lands under the
        un-prefixed real key, and the prefixed key itself never appears.
        """
        forced = f"{_HERMES_PROVIDER_ENV_FORCE_PREFIX}OPENAI_API_KEY"
        out = local._sanitize_subprocess_env(
            base_env={"PATH": "/usr/bin"},
            extra_env={forced: "sk-explicit"},
        )
        assert out["OPENAI_API_KEY"] == "sk-explicit"
        assert forced not in out

    def test_extra_env_force_prefix_overrides_base_env_block(self):
        """Force-prefixed extra var wins even when base env has the blocked one."""
        forced = f"{_HERMES_PROVIDER_ENV_FORCE_PREFIX}OPENAI_BASE_URL"
        out = local._sanitize_subprocess_env(
            base_env={"OPENAI_BASE_URL": "http://leaked/v1"},
            extra_env={forced: "http://intended/v1"},
        )
        assert out["OPENAI_BASE_URL"] == "http://intended/v1"

    def test_extra_env_blocklisted_var_without_force_is_stripped(self):
        """A blocked var passed plainly via extra_env (no force prefix) is dropped."""
        out = local._sanitize_subprocess_env(
            base_env={},
            extra_env={"ANTHROPIC_TOKEN": "ant-tok", "KEEP_ME": "yes"},
        )
        assert "ANTHROPIC_TOKEN" not in out
        assert out["KEEP_ME"] == "yes"

    def test_is_env_passthrough_reallows_blocklisted_base_var(self):
        """When ``is_env_passthrough`` says yes, the blocklist is bypassed.

        ``register_env_passthrough`` itself refuses to register Hermes provider
        credentials (GHSA-rhgp-j443-p4rf), so we patch the predicate at its
        source to exercise the ``or _is_passthrough(key)`` branch directly.
        """
        with patch(_PASSTHROUGH_SRC, return_value=True):
            out = local._sanitize_subprocess_env({"OPENAI_API_KEY": "sk-allowed"})
        assert out["OPENAI_API_KEY"] == "sk-allowed"

    def test_is_env_passthrough_reallows_blocklisted_extra_var(self):
        """The ``or _is_passthrough(key)`` branch also applies to extra_env."""
        with patch(_PASSTHROUGH_SRC, return_value=True):
            out = local._sanitize_subprocess_env(
                base_env={}, extra_env={"GROQ_API_KEY": "gk"}
            )
        assert out["GROQ_API_KEY"] == "gk"

    def test_passthrough_import_failure_degrades_to_strip(self):
        """If the env_passthrough *import* blows up, the predicate defaults to
        False (the ``except Exception`` lambda) and blocked vars stay stripped.

        The guard wraps the ``from … import is_env_passthrough`` statement, not
        the later call — so we must make the import itself fail, not the call.
        """
        import builtins

        real_import = builtins.__import__

        def _boom(name, *args, **kwargs):
            if "env_passthrough" in name:
                raise ImportError("no env_passthrough")
            return real_import(name, *args, **kwargs)

        with patch.object(builtins, "__import__", side_effect=_boom):
            out = local._sanitize_subprocess_env({"OPENAI_API_KEY": "sk"})
        assert "OPENAI_API_KEY" not in out

    def test_none_inputs_yield_empty_sanitized_env(self, monkeypatch):
        """Both args None is valid (the ``or {}`` guards) → empty result.

        Guard the profile-HOME branch off by pointing HERMES_HOME at a dir with
        no ``home/`` subdir so the result is genuinely empty.
        """
        monkeypatch.setenv("HERMES_HOME", "/nonexistent-hermes-home-xyz")
        out = local._sanitize_subprocess_env(None, None)
        assert out == {}

    def test_injects_profile_home_when_present(self, monkeypatch, tmp_path):
        """``{HERMES_HOME}/home/`` existing → HOME is overridden for subprocesses."""
        profile_home = _activate_profile_home(monkeypatch, tmp_path)
        out = local._sanitize_subprocess_env({"HOME": "/real/home", "PATH": "/usr/bin"})
        assert out["HOME"] == profile_home

    def test_no_profile_home_leaves_base_home(self, monkeypatch, tmp_path):
        """No ``home/`` subdir → the base HOME passes through unchanged."""
        hermes_home = tmp_path / "hh-empty"
        hermes_home.mkdir()
        monkeypatch.setenv("HERMES_HOME", str(hermes_home))
        out = local._sanitize_subprocess_env({"HOME": "/real/home"})
        assert out["HOME"] == "/real/home"

    def test_injects_hermes_home_from_context_override(self, monkeypatch):
        """An active context-local HERMES_HOME override is bridged into env."""
        from calfkit_tools.hermes._vendor import hermes_constants as hc

        # No profile HOME so the override is the only HERMES_HOME source here.
        monkeypatch.setenv("HERMES_HOME", "/nonexistent-hermes-home-xyz")
        token = hc._HERMES_HOME_OVERRIDE.set("/ctx/hermes")
        try:
            out = local._sanitize_subprocess_env({"PATH": "/usr/bin"})
        finally:
            hc._HERMES_HOME_OVERRIDE.reset(token)
        assert out["HERMES_HOME"] == "/ctx/hermes"


# ---------------------------------------------------------------------------
# _make_run_env  (ContextVar session bridge + profile HOME)
# ---------------------------------------------------------------------------


class TestMakeRunEnv:
    def test_session_contextvar_is_bridged_into_subprocess_env(self, monkeypatch):
        """A set session ContextVar in ``_VAR_MAP`` is injected into run_env."""
        from calfkit_tools.hermes._shims.gateway import session_context as sc

        var = ContextVar("SESSION_VAR_X")
        var.set("session-val")
        monkeypatch.setattr(sc, "_VAR_MAP", {"SESSION_VAR_X": var})
        with patch.dict(os.environ, {"PATH": "/usr/bin:/bin"}, clear=True):
            out = local._make_run_env({})
        assert out["SESSION_VAR_X"] == "session-val"

    def test_unset_session_contextvar_is_not_injected(self, monkeypatch):
        """A ContextVar left at the sentinel (_UNSET) is skipped, not injected."""
        from calfkit_tools.hermes._shims.gateway import session_context as sc

        var = ContextVar("SESSION_VAR_Y", default=sc._UNSET)
        monkeypatch.setattr(sc, "_VAR_MAP", {"SESSION_VAR_Y": var})
        with patch.dict(os.environ, {"PATH": "/usr/bin:/bin"}, clear=True):
            out = local._make_run_env({})
        assert "SESSION_VAR_Y" not in out

    def test_empty_string_session_contextvar_is_not_injected(self, monkeypatch):
        """A set-but-empty session var is falsy, so the ``and value`` guard skips it."""
        from calfkit_tools.hermes._shims.gateway import session_context as sc

        var = ContextVar("SESSION_VAR_Z")
        var.set("")
        monkeypatch.setattr(sc, "_VAR_MAP", {"SESSION_VAR_Z": var})
        with patch.dict(os.environ, {"PATH": "/usr/bin:/bin"}, clear=True):
            out = local._make_run_env({})
        assert "SESSION_VAR_Z" not in out

    def test_session_bridge_import_failure_is_swallowed(self, monkeypatch):
        """If importing the session_context shim fails, run_env is still built."""
        import builtins

        real_import = builtins.__import__

        def _boom(name, *args, **kwargs):
            if name.endswith("session_context") or "session_context" in name:
                raise ImportError("no session context")
            return real_import(name, *args, **kwargs)

        with patch.dict(os.environ, {"PATH": "/usr/bin:/bin"}, clear=True), \
             patch.object(builtins, "__import__", side_effect=_boom):
            out = local._make_run_env({})
        # The PATH still made it through despite the bridge import failing.
        assert out["PATH"] == "/usr/bin:/bin"

    def test_injects_profile_home_when_present(self, monkeypatch, tmp_path):
        """``{HERMES_HOME}/home/`` present → run_env HOME is the profile home."""
        profile_home = _activate_profile_home(monkeypatch, tmp_path)
        with patch.dict(os.environ, {"PATH": "/usr/bin:/bin", "HOME": "/real"}, clear=True):
            # Re-set HERMES_HOME inside the cleared environ patch.
            os.environ["HERMES_HOME"] = str(tmp_path / "hh")
            out = local._make_run_env({})
        assert out["HOME"] == profile_home

    def test_force_prefixed_var_in_env_is_rewritten(self, monkeypatch):
        """``_HERMES_FORCE_<KEY>`` in the merged env injects the un-prefixed key."""
        forced = f"{_HERMES_PROVIDER_ENV_FORCE_PREFIX}OPENAI_API_KEY"
        with patch.dict(os.environ, {"PATH": "/usr/bin:/bin"}, clear=True):
            out = local._make_run_env({forced: "sk-forced"})
        assert out["OPENAI_API_KEY"] == "sk-forced"
        assert forced not in out


# ---------------------------------------------------------------------------
# _resolve_shell_init_files
# ---------------------------------------------------------------------------


class TestResolveShellInitFiles:
    def test_explicit_list_wins_and_drops_missing(self, tmp_path, monkeypatch):
        """Explicit config list overrides auto-bashrc; non-existent entries drop."""
        present = tmp_path / "present.sh"
        present.write_text("export A=1\n")
        missing = tmp_path / "gone.sh"  # never created
        monkeypatch.setenv("HOME", str(tmp_path))
        # An auto candidate exists too, to prove the explicit list excludes it.
        (tmp_path / ".bashrc").write_text("export FROM_BASHRC=1\n")

        with patch.object(
            local,
            "_read_terminal_shell_init_config",
            return_value=([str(present), str(missing)], True),
        ):
            resolved = local._resolve_shell_init_files()

        assert resolved == [str(present)]
        assert str(tmp_path / ".bashrc") not in resolved

    def test_expanduser_raising_skips_candidate(self):
        """If ``expanduser`` raises, that candidate is skipped (no crash)."""
        with patch.object(
            local,
            "_read_terminal_shell_init_config",
            return_value=(["~/boom.sh"], False),
        ), patch.object(local.os.path, "expanduser", side_effect=ValueError("boom")):
            resolved = local._resolve_shell_init_files()
        assert resolved == []

    def test_expandvars_raising_skips_candidate(self):
        """If ``expandvars`` raises, that candidate is skipped too."""
        with patch.object(
            local,
            "_read_terminal_shell_init_config",
            return_value=(["${X}/boom.sh"], False),
        ), patch.object(local.os.path, "expandvars", side_effect=ValueError("boom")):
            resolved = local._resolve_shell_init_files()
        assert resolved == []

    def test_one_candidate_raising_does_not_block_others(self, tmp_path, monkeypatch):
        """A raising candidate is skipped while a valid sibling still resolves."""
        good = tmp_path / "good.sh"
        good.write_text("export OK=1\n")
        monkeypatch.setenv("HOME", str(tmp_path))

        real_expandvars = os.path.expandvars

        def _selective(p):
            if "boom" in p:
                raise ValueError("boom")
            return real_expandvars(p)

        with patch.object(
            local,
            "_read_terminal_shell_init_config",
            return_value=(["${X}/boom.sh", str(good)], False),
        ), patch.object(local.os.path, "expandvars", side_effect=_selective):
            resolved = local._resolve_shell_init_files()

        assert resolved == [str(good)]


# ---------------------------------------------------------------------------
# _prepend_shell_init
# ---------------------------------------------------------------------------


class TestPrependShellInit:
    def test_single_quote_in_path_is_escaped(self):
        """A single quote in a path is escaped as ``'\\''`` so outer quoting holds."""
        wrapped = local._prepend_shell_init("echo hi", ["/tmp/o'malley.sh"])
        # The shell-safe escape sequence for an embedded single quote.
        assert "o'\\''malley" in wrapped
        # The raw, unescaped path must NOT appear verbatim.
        assert "/tmp/o'malley.sh'" not in wrapped
        # Original command is preserved after the prelude.
        assert wrapped.endswith("echo hi")
        assert wrapped.startswith("set +e")

    def test_empty_file_list_returns_command_unchanged(self):
        assert local._prepend_shell_init("echo hi", []) == "echo hi"


# ---------------------------------------------------------------------------
# get_temp_dir  (precedence + fallbacks)
# ---------------------------------------------------------------------------


class TestGetTempDir:
    def test_self_env_tmpdir_overrides_os_environ(self, monkeypatch):
        """``self.env['TMPDIR']`` is consulted before ``os.environ`` and wins.

        The trailing slash is stripped from the returned path.
        """
        monkeypatch.delenv("TMP", raising=False)
        monkeypatch.delenv("TEMP", raising=False)
        with patch.object(LocalEnvironment, "init_session", return_value=None):
            env = LocalEnvironment(cwd="/tmp", timeout=10, env={"TMPDIR": "/data/env-tmp/"})
        monkeypatch.setenv("TMPDIR", "/should/not/win")
        assert env.get_temp_dir() == "/data/env-tmp"

    def test_non_posix_tmpdir_is_ignored(self, monkeypatch, local_env):
        """A TMPDIR that isn't an absolute POSIX path is skipped (must start ``/``)."""
        monkeypatch.delenv("TMP", raising=False)
        monkeypatch.delenv("TEMP", raising=False)
        monkeypatch.setenv("TMPDIR", "relative/path")  # no leading slash → ignored
        # /tmp is writable on the dev box, so it falls through to that.
        assert local_env.get_temp_dir() == "/tmp"

    def test_tmp_not_writable_falls_back_to_gettempdir(self, monkeypatch, local_env):
        """When ``/tmp`` isn't writable, fall back to ``tempfile.gettempdir()``."""
        monkeypatch.delenv("TMPDIR", raising=False)
        monkeypatch.delenv("TMP", raising=False)
        monkeypatch.delenv("TEMP", raising=False)
        with patch.object(local.os.path, "isdir", return_value=False), \
             patch.object(local.os, "access", return_value=False), \
             patch.object(local.tempfile, "gettempdir", return_value="/cache/tmp/"):
            assert local_env.get_temp_dir() == "/cache/tmp"

    def test_non_posix_gettempdir_falls_back_to_slash_tmp(self, monkeypatch, local_env):
        """If even ``gettempdir()`` isn't POSIX, the final fallback is ``/tmp``."""
        monkeypatch.delenv("TMPDIR", raising=False)
        monkeypatch.delenv("TMP", raising=False)
        monkeypatch.delenv("TEMP", raising=False)
        with patch.object(local.os.path, "isdir", return_value=False), \
             patch.object(local.os, "access", return_value=False), \
             patch.object(local.tempfile, "gettempdir", return_value="C:/Temp"):
            assert local_env.get_temp_dir() == "/tmp"

    def test_root_tmpdir_collapses_to_slash(self, monkeypatch, local_env):
        """A ``TMPDIR`` of exactly ``/`` survives the rstrip as ``/`` (not empty)."""
        monkeypatch.delenv("TMP", raising=False)
        monkeypatch.delenv("TEMP", raising=False)
        monkeypatch.setenv("TMPDIR", "/")
        assert local_env.get_temp_dir() == "/"


# ---------------------------------------------------------------------------
# _kill_process  (PermissionError on killpg)
# ---------------------------------------------------------------------------


class TestKillProcess:
    def test_permission_error_on_killpg_falls_back_to_proc_kill(self, local_env):
        """A PermissionError from ``killpg`` propagates to the outer handler,
        which best-effort calls ``proc.kill()``."""
        proc = MagicMock()
        proc.pid = 4242
        with patch.object(local.os, "getpgid", return_value=4242), \
             patch.object(local.os, "killpg", side_effect=PermissionError("EPERM")):
            local_env._kill_process(proc)
        assert proc.kill.called

    def test_getpgid_lookup_error_uses_cached_pgid(self, local_env):
        """When ``getpgid`` raises ProcessLookupError, the cached ``_hermes_pgid``
        is used for the SIGTERM instead."""
        proc = MagicMock()
        proc.pid = 5555
        proc._hermes_pgid = 9999
        killed_pgids = []

        def _record_killpg(pgid, sig):
            killed_pgids.append((pgid, sig))
            # Report the group as already gone so the wait loop returns fast.
            raise ProcessLookupError()

        with patch.object(local.os, "getpgid", side_effect=ProcessLookupError()), \
             patch.object(local.os, "killpg", side_effect=_record_killpg):
            local_env._kill_process(proc)

        # The cached pgid (not proc.pid) was the target of the first signal.
        assert killed_pgids
        assert killed_pgids[0][0] == 9999

    def test_getpgid_lookup_error_without_cached_pgid_falls_back(self, local_env):
        """No cached pgid and getpgid raising → the raise re-propagates into the
        outer ``except`` and we fall back to ``proc.kill()``."""
        proc = MagicMock(spec=["pid", "kill"])
        proc.pid = 6666  # no _hermes_pgid attribute (spec excludes it)
        with patch.object(local.os, "getpgid", side_effect=ProcessLookupError()):
            local_env._kill_process(proc)
        assert proc.kill.called

    def test_already_dead_group_returns_without_killing(self, local_env):
        """If the SIGTERM ``killpg`` raises ProcessLookupError (group already
        gone), ``_kill_process`` returns early and never calls ``proc.kill()``."""
        proc = MagicMock()
        proc.pid = 7777
        with patch.object(local.os, "getpgid", return_value=7777), \
             patch.object(local.os, "killpg", side_effect=ProcessLookupError()):
            local_env._kill_process(proc)
        assert not proc.kill.called
