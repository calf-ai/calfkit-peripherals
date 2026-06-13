"""Behavioral tests for tools/credential_files.py — the host->sandbox file
passthrough registry.

Focus: the security-relevant rejection paths (absolute path, ``..`` traversal,
symlink escape out of HERMES_HOME → credential exfiltration) plus malformed
skill-frontmatter handling, the symlink-sanitizing skills copy, and the
config-driven rejection logic (dormant in the node — the config shim returns
{} — but exercised here by injecting a config). No remote backend is run.
"""
import types
from pathlib import Path

import pytest

from calfkit_tools.hermes._vendor.tools import credential_files
from calfkit_tools.hermes._vendor.tools.credential_files import (
    clear_credential_files,
    get_credential_file_mounts,
    register_credential_file,
    register_credential_files,
    to_agent_visible_cache_path,
    _safe_skills_path,
)


@pytest.fixture
def cred_env(tmp_path, monkeypatch):
    hh = tmp_path / "hh"
    hh.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(hh))
    clear_credential_files()
    monkeypatch.setattr(credential_files, "_config_files", None)
    monkeypatch.setattr(credential_files, "_safe_skills_tempdir", None)
    yield types.SimpleNamespace(home=hh, tmp=tmp_path)
    clear_credential_files()


# ---------------------------------------------------------------------------
# register_credential_file — security rejections
# ---------------------------------------------------------------------------

def test_register_rejects_absolute_path(cred_env):
    assert register_credential_file("/etc/passwd") is False
    assert get_credential_file_mounts() == []


def test_register_rejects_dotdot_traversal(cred_env):
    assert register_credential_file("../../../etc/passwd") is False
    assert get_credential_file_mounts() == []


def test_register_rejects_symlink_escaping_hermes_home(cred_env):
    # A skill that declares a credential file which is actually a symlink out of
    # HERMES_HOME must NOT be mounted — that would exfiltrate the host secret
    # into the sandbox. validate_within_dir resolves the symlink before the
    # containment check.
    secret = cred_env.tmp / "host_secret.txt"
    secret.write_text("TOPSECRET")
    link = cred_env.home / "looks_innocent.json"
    link.symlink_to(secret)
    assert register_credential_file("looks_innocent.json") is False
    assert get_credential_file_mounts() == []


def test_register_skips_missing_file(cred_env):
    assert register_credential_file("does_not_exist.json") is False


def test_register_success_registers_resolved_path(cred_env):
    f = cred_env.home / "token.json"
    f.write_text("{}")
    assert register_credential_file("token.json") is True
    mounts = get_credential_file_mounts()
    assert len(mounts) == 1
    assert mounts[0]["container_path"] == "/root/.hermes/token.json"
    assert Path(mounts[0]["host_path"]).name == "token.json"


def test_register_custom_container_base(cred_env):
    f = cred_env.home / "k.json"
    f.write_text("{}")
    assert register_credential_file("k.json", container_base="/home/agent/.hermes/") is True
    assert get_credential_file_mounts()[0]["container_path"] == "/home/agent/.hermes/k.json"


# ---------------------------------------------------------------------------
# register_credential_files — malformed skill frontmatter
# ---------------------------------------------------------------------------

def test_register_files_skips_malformed_entries(cred_env):
    # dict-without-path/name, non-str, empty, whitespace are all skipped; only
    # the real (but missing) relative path is reported as missing.
    missing = register_credential_files([{"x": 1}, 123, "", "   ", "absent.json"])
    assert missing == ["absent.json"]


def test_register_files_name_fallback(cred_env):
    f = cred_env.home / "tok.json"
    f.write_text("{}")
    # Entry has no `path` key — falls back to `name`.
    assert register_credential_files([{"name": "tok.json"}]) == []
    assert get_credential_file_mounts()[0]["container_path"] == "/root/.hermes/tok.json"


# ---------------------------------------------------------------------------
# _safe_skills_path — symlink sanitization
# ---------------------------------------------------------------------------

def test_safe_skills_path_no_symlinks_returns_original(cred_env):
    skills = cred_env.home / "skills"
    (skills / "a").mkdir(parents=True)
    (skills / "a" / "f.txt").write_text("x")
    assert _safe_skills_path(skills) == str(skills)


def test_safe_skills_path_with_symlink_returns_sanitized_copy(cred_env):
    secret = cred_env.tmp / "outside_secret.txt"
    secret.write_text("SECRET")
    skills = cred_env.home / "skills"
    skills.mkdir()
    (skills / "regular.txt").write_text("ok")
    (skills / "leak").symlink_to(secret)

    safe = _safe_skills_path(skills)
    assert safe != str(skills)              # a sanitized copy, not the original
    assert (Path(safe) / "regular.txt").is_file()
    assert not (Path(safe) / "leak").exists()  # the symlink was NOT copied


# ---------------------------------------------------------------------------
# _load_config_files — config-driven rejection (dormant path, exercised here)
# ---------------------------------------------------------------------------

def test_config_files_reject_absolute_and_traversal(cred_env, monkeypatch):
    good = cred_env.home / "good.json"
    good.write_text("{}")
    config = {"terminal": {"credential_files": ["/abs/secret", "../escape", "good.json"]}}
    monkeypatch.setattr(
        "calfkit_tools.hermes._shims.hermes_cli.config.read_raw_config", lambda: config
    )
    monkeypatch.setattr(credential_files, "_config_files", None)

    entries = credential_files._load_config_files()
    # Only the valid in-HERMES_HOME relative file survives.
    assert len(entries) == 1
    assert entries[0]["container_path"] == "/root/.hermes/good.json"


# ---------------------------------------------------------------------------
# Cache path translation + registry reset
# ---------------------------------------------------------------------------

def test_cache_path_passthrough_when_not_docker(cred_env):
    # TERMINAL_ENV is scrubbed (→ "local"), so no translation happens.
    assert to_agent_visible_cache_path("/some/host/cache/x.png") == "/some/host/cache/x.png"


def test_clear_resets_registry(cred_env):
    f = cred_env.home / "z.json"
    f.write_text("{}")
    register_credential_file("z.json")
    assert get_credential_file_mounts() != []
    clear_credential_files()
    assert get_credential_file_mounts() == []
