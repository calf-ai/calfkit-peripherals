"""Adversarial behavioral tests for agent/file_safety.py.

Probes the write-deny / read-deny / cross-profile / sandbox-mirror guards with
edge and boundary cases. These are documented defense-in-depth (not hard
boundaries), so the tests pin the guard's *classification* decisions. Bugs /
by-design skirts are pinned with `# BUG:` / `# NOTE:`.

Env is fully sandboxed per test: HOME -> a tmp dir (controls ``~`` for the
home-based denylist) and HERMES_HOME -> a tmp non-profile root (controls the
profile-aware control-plane + cross-profile logic). Paths need not exist —
realpath/resolve normalize non-existent components deterministically.
"""
import os
import types
from pathlib import Path

import pytest

from calfkit_tools.hermes._vendor.agent import file_safety
from calfkit_tools.hermes._vendor.agent.file_safety import (
    classify_container_mirror_target,
    classify_cross_profile_target,
    classify_sandbox_mirror_target,
    get_read_block_error,
    is_write_denied,
)


@pytest.fixture
def fs_env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    hroot = home / "hermes_root"  # not under ~/.hermes → non-profile: HERMES_HOME == root
    hroot.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("HERMES_HOME", str(hroot))
    monkeypatch.delenv("HERMES_WRITE_SAFE_ROOT", raising=False)
    return types.SimpleNamespace(home=home, hroot=hroot, tmp=tmp_path)


# ---------------------------------------------------------------------------
# is_write_denied
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("rel", [".ssh/id_rsa", ".ssh/authorized_keys", ".netrc", ".git-credentials"])
def test_write_denied_home_secret_files(fs_env, rel):
    assert is_write_denied(str(fs_env.home / rel)) is True


@pytest.mark.parametrize("rel", [".ssh/anything", ".aws/credentials", ".gnupg/x", ".config/gh/hosts.yml"])
def test_write_denied_home_secret_prefixes(fs_env, rel):
    assert is_write_denied(str(fs_env.home / rel)) is True


@pytest.mark.parametrize("name", ["auth.json", "config.yaml", "webhook_subscriptions.json"])
def test_write_denied_control_plane_files(fs_env, name):
    # Prompt-injection overwrite of the security policy must be blocked (#15981).
    assert is_write_denied(str(fs_env.hroot / name)) is True


def test_write_denied_mcp_tokens_and_pairing(fs_env):
    assert is_write_denied(str(fs_env.hroot / "mcp-tokens" / "t.json")) is True
    assert is_write_denied(str(fs_env.hroot / "pairing" / "device.json")) is True


def test_write_denied_etc_system_files(fs_env):
    assert is_write_denied("/etc/passwd") is True
    assert is_write_denied("/etc/sudoers.d/zz") is True  # prefix


def test_write_allowed_ordinary_project_file(fs_env):
    assert is_write_denied(str(fs_env.tmp / "project" / "main.py")) is False


def test_write_denied_via_symlink_realpath_normalization(fs_env):
    # A symlink whose realpath lands on a denied file must be blocked.
    ssh = fs_env.home / ".ssh"
    ssh.mkdir()
    link = fs_env.tmp / "innocent_name"
    link.symlink_to(ssh / "id_rsa")
    assert is_write_denied(str(link)) is True


def test_write_safe_root_jail(fs_env, monkeypatch):
    safe = fs_env.tmp / "safe"
    safe.mkdir()
    monkeypatch.setenv("HERMES_WRITE_SAFE_ROOT", str(safe))
    assert is_write_denied(str(safe / "ok.txt")) is False          # inside jail
    assert is_write_denied(str(fs_env.tmp / "outside" / "x")) is True  # outside jail
    # NOTE: get_safe_write_root() returns None on a resolution error, which
    # *disables* the jail (fail-open) — a documented soft-guard property.


# ---------------------------------------------------------------------------
# get_read_block_error
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "rel", ["auth.json", "auth.lock", ".env", "webhook_subscriptions.json",
            "auth/google_oauth.json", "cache/bws_cache.json"],
)
def test_read_block_credential_stores(fs_env, rel):
    # bws_cache.json is the #31968 regression — now covered.
    msg = get_read_block_error(str(fs_env.hroot / rel))
    assert msg is not None and "Access denied" in msg


def test_read_block_skills_hub_prompt_injection(fs_env):
    msg = get_read_block_error(str(fs_env.hroot / "skills" / ".hub" / "index-cache" / "x.json"))
    assert msg is not None and ".hub" in msg


def test_read_block_mcp_tokens_dir_and_file(fs_env):
    assert get_read_block_error(str(fs_env.hroot / "mcp-tokens")) is not None
    assert get_read_block_error(str(fs_env.hroot / "mcp-tokens" / "tok.json")) is not None


@pytest.mark.parametrize("name", [".env", ".env.local", ".env.production", ".envrc"])
def test_read_block_project_env_basenames_anywhere(fs_env, name):
    assert get_read_block_error(str(fs_env.tmp / "someproj" / name)) is not None


def test_read_allows_env_example(fs_env):
    assert get_read_block_error(str(fs_env.tmp / "proj" / ".env.example")) is None


def test_read_allows_ordinary_file(fs_env):
    assert get_read_block_error(str(fs_env.hroot / "notes.md")) is None


def test_BUG_read_block_env_is_basename_only(fs_env):
    # BUG (by-design DiD, but skirtable): the project-.env block is a basename
    # ALLOWLIST, so trivially-renamed secret files are NOT blocked.
    assert get_read_block_error(str(fs_env.tmp / "p" / ".env.bak")) is None
    assert get_read_block_error(str(fs_env.tmp / "p" / "secrets.env")) is None
    assert get_read_block_error(str(fs_env.tmp / "p" / ".env.prod.local")) is None


# ---------------------------------------------------------------------------
# Cross-profile classification
# ---------------------------------------------------------------------------

def test_cross_profile_named_target_from_default(fs_env):
    info = classify_cross_profile_target(str(fs_env.hroot / "profiles" / "alpha" / "skills" / "s.md"))
    assert info is not None
    assert info["active_profile"] == "default"
    assert info["target_profile"] == "alpha"
    assert info["area"] == "skills"


def test_cross_profile_in_profile_write_is_none(fs_env):
    # default profile active, writing the default profile's own skills → not cross.
    assert classify_cross_profile_target(str(fs_env.hroot / "skills" / "s.md")) is None


def test_cross_profile_non_area_and_outside_root_are_none(fs_env):
    assert classify_cross_profile_target(str(fs_env.hroot / "random" / "x")) is None
    assert classify_cross_profile_target(str(fs_env.tmp / "totally" / "elsewhere")) is None


def test_cross_profile_named_active_editing_default(fs_env, monkeypatch):
    # The May-2026 incident: running under a named profile, editing the DEFAULT
    # profile's skills (<root>/skills/...) must be flagged cross-profile.
    alpha_home = fs_env.hroot / "profiles" / "alpha"
    alpha_home.mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(alpha_home))
    info = classify_cross_profile_target(str(fs_env.hroot / "skills" / "s.md"))
    assert info is not None
    assert info["active_profile"] == "alpha"
    assert info["target_profile"] == "default"


# ---------------------------------------------------------------------------
# Sandbox-mirror classification (path-shape only)
# ---------------------------------------------------------------------------

def test_sandbox_mirror_classifies_full_shape(fs_env):
    p = fs_env.tmp / "sandboxes" / "docker" / "t1" / "home" / ".hermes" / "SOUL.md"
    info = classify_sandbox_mirror_target(str(p))
    assert info is not None
    assert info["mirror_root"].endswith(os.path.join("home", ".hermes"))
    assert info["inner_path"] == "SOUL.md"


def test_sandbox_mirror_boundary_bare_hermes_is_none(fs_env):
    # Off-by-one boundary: a path that ends exactly at .hermes (no inner part)
    # is NOT classified (`i + 5 >= len(parts)`).
    p = fs_env.tmp / "sandboxes" / "docker" / "t1" / "home" / ".hermes"
    assert classify_sandbox_mirror_target(str(p)) is None


def test_sandbox_mirror_non_mirror_path_is_none(fs_env):
    assert classify_sandbox_mirror_target(str(fs_env.tmp / "plain" / "file.txt")) is None


# ---------------------------------------------------------------------------
# Container-mirror classification (caller supplies mirror_prefix)
# ---------------------------------------------------------------------------

def test_container_mirror_requires_prefix(fs_env):
    assert classify_container_mirror_target(str(fs_env.tmp / "x"), None) is None


def test_container_mirror_inside_and_outside_prefix(fs_env):
    prefix = fs_env.tmp / "container_home"
    prefix.mkdir()
    inside = classify_container_mirror_target(str(prefix / ".hermes" / "SOUL.md"), str(prefix))
    assert inside is not None
    assert inside["inner_path"] == os.path.join(".hermes", "SOUL.md").replace(os.sep, "/")
    # Outside the prefix → relative_to raises → None.
    assert classify_container_mirror_target(str(fs_env.tmp / "elsewhere" / "y"), str(prefix)) is None
