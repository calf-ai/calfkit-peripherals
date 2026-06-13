"""Behavioral tests for the REACHABLE atomic-write helpers in the vendored utils.

Only the five functions that the calfkit node actually exercises are covered here:
``is_truthy_value``, ``atomic_json_write``, ``atomic_replace``,
``_preserve_file_mode``, and ``_restore_file_mode``.  The remaining helpers in
``utils.py`` (yaml writers, proxy/url helpers, env readers) are dormant in the
node and intentionally left untested.

Assertions favour observable behaviour — file bytes, directory contents,
whether an exception propagates, whether a symlink survives — over exact
resolved paths, because macOS routes ``tmp_path`` through the
``/tmp -> /private/tmp`` symlink and a literal-path assertion would be
spuriously platform-dependent.
"""
import json
import os
import stat
import sys
from pathlib import Path

import pytest

from calfkit_tools.hermes._vendor import utils
from calfkit_tools.hermes._vendor.utils import (
    _preserve_file_mode,
    _restore_file_mode,
    atomic_json_write,
    atomic_replace,
    is_truthy_value,
)

POSIX_ONLY = pytest.mark.skipif(
    sys.platform == "win32",
    reason="POSIX permission bits are not meaningful on Windows",
)


def _tmp_files(directory: Path) -> list[str]:
    """Names of the leftover atomic temp files in *directory* (``.*.tmp``)."""
    return [p.name for p in directory.iterdir() if p.name.endswith(".tmp")]


class _RaisesOnSerialize:
    """A value json.dump cannot serialize even with a ``default`` callback.

    The ``default`` hook re-raises, so ``json.dump`` fails *after* it has
    already begun streaming the surrounding container to the file descriptor —
    exercising the mid-write failure path of ``atomic_json_write``.
    """

    def __repr__(self) -> str:  # pragma: no cover - only for test debugging
        return "<unserializable>"


def _boom(_obj):
    raise TypeError("cannot serialize")


# ─────────────────────────── is_truthy_value ────────────────────────────────


class TestIsTruthyValue:
    """The non-str / non-bool / non-None branch coerces via ``bool(value)``."""

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ([], False),          # empty container -> falsy
            ([0], True),          # non-empty container -> truthy
            ({}, False),          # empty mapping -> falsy
            (0, False),           # zero int -> falsy
            (0.0, False),         # zero float -> falsy
            (1, True),            # non-zero int -> truthy
            (-1, True),           # negative non-zero -> truthy
        ],
    )
    def test_other_types_use_bool_coercion(self, value, expected):
        assert is_truthy_value(value) is expected

    def test_arbitrary_object_is_truthy(self):
        # A plain object has no __bool__/__len__ -> bool() is True.
        assert is_truthy_value(object()) is True

    def test_none_returns_default(self):
        assert is_truthy_value(None) is False
        assert is_truthy_value(None, default=True) is True

    def test_bool_passthrough_ignores_default(self):
        # An explicit bool is returned verbatim, never the default.
        assert is_truthy_value(False, default=True) is False
        assert is_truthy_value(True, default=False) is True

    @pytest.mark.parametrize("text", ["1", "true", "YES", "  On  ", "TrUe"])
    def test_truthy_strings(self, text):
        assert is_truthy_value(text) is True

    @pytest.mark.parametrize("text", ["0", "false", "no", "off", "", "maybe"])
    def test_non_truthy_strings(self, text):
        # Unknown / falsy strings are False regardless of the default, because
        # the string branch never consults ``default``.
        assert is_truthy_value(text, default=True) is False


# ─────────────────────────── atomic_json_write ──────────────────────────────


class TestAtomicJsonWriteHappyPath:
    def test_writes_serialized_json(self, tmp_path):
        target = tmp_path / "data.json"
        atomic_json_write(target, {"b": 2, "a": 1})

        assert json.loads(target.read_text(encoding="utf-8")) == {"a": 1, "b": 2}
        # No temp files left behind on success.
        assert _tmp_files(tmp_path) == []

    def test_creates_missing_parent_dirs(self, tmp_path):
        target = tmp_path / "nested" / "deep" / "data.json"
        atomic_json_write(target, [1, 2, 3])

        assert target.exists()
        assert json.loads(target.read_text(encoding="utf-8")) == [1, 2, 3]

    def test_overwrites_existing_target(self, tmp_path):
        target = tmp_path / "data.json"
        target.write_text('{"old": true}', encoding="utf-8")

        atomic_json_write(target, {"new": True})

        assert json.loads(target.read_text(encoding="utf-8")) == {"new": True}

    def test_dump_kwargs_forwarded(self, tmp_path):
        # default=str lets json.dump serialize an otherwise-unserializable type.
        target = tmp_path / "data.json"
        atomic_json_write(target, {"p": Path("/x/y")}, default=str)

        assert json.loads(target.read_text(encoding="utf-8")) == {"p": "/x/y"}


class TestAtomicJsonWriteErrorPath:
    """A failing ``json.dump`` must raise, clean up its temp file, and not
    corrupt or create the target."""

    def test_raises_and_leaves_no_orphan_tmp_for_new_target(self, tmp_path):
        target = tmp_path / "data.json"

        with pytest.raises(TypeError):
            atomic_json_write(target, _RaisesOnSerialize(), default=_boom)

        # The target was never created...
        assert not target.exists()
        # ...and the temp file was unlinked by the BaseException handler.
        assert _tmp_files(tmp_path) == []

    def test_raises_and_leaves_existing_target_untouched(self, tmp_path):
        target = tmp_path / "data.json"
        original = '{"keep": "me"}'
        target.write_text(original, encoding="utf-8")

        with pytest.raises(TypeError):
            atomic_json_write(target, {"x": _RaisesOnSerialize()}, default=_boom)

        # The pre-existing file is byte-for-byte intact (os.replace never ran).
        assert target.read_text(encoding="utf-8") == original
        # Only the original file remains; no leftover ".tmp". (The autouse
        # hermetic fixture also seeds a ``hermes_test`` dir under tmp_path, so we
        # scope the assertion to the data file + any atomic temp artifacts.)
        assert _tmp_files(tmp_path) == []
        json_artifacts = sorted(
            p.name
            for p in tmp_path.iterdir()
            if p.name == "data.json" or p.name.endswith(".tmp")
        )
        assert json_artifacts == ["data.json"]

    def test_non_serializable_without_default_also_cleans_up(self, tmp_path):
        # No ``default`` hook at all -> json.dump raises TypeError on the set().
        target = tmp_path / "data.json"

        with pytest.raises(TypeError):
            atomic_json_write(target, {1, 2, 3})

        assert not target.exists()
        assert _tmp_files(tmp_path) == []

    def test_original_error_wins_even_if_temp_cleanup_fails(self, tmp_path, monkeypatch):
        # Defensive corner: if the dump fails AND the cleanup os.unlink itself
        # raises OSError, that secondary error is swallowed and the ORIGINAL
        # TypeError still propagates (utils.py L147-151). The temp file is left
        # behind because unlink failed.
        target = tmp_path / "data.json"

        def boom_unlink(_path, *a, **k):
            raise OSError("unlink denied")

        monkeypatch.setattr(os, "unlink", boom_unlink)

        with pytest.raises(TypeError):
            atomic_json_write(target, _RaisesOnSerialize(), default=_boom)

        # Target never materialized; an orphan ".tmp" remains since unlink failed.
        assert not target.exists()
        assert len(_tmp_files(tmp_path)) == 1


@POSIX_ONLY
class TestAtomicJsonWriteMode:
    def test_mode_applies_permissions(self, tmp_path):
        target = tmp_path / "secret.json"
        atomic_json_write(target, {"token": "shh"}, mode=0o640)

        actual = stat.S_IMODE(target.stat().st_mode)
        assert actual == 0o640
        assert json.loads(target.read_text(encoding="utf-8")) == {"token": "shh"}

    def test_mode_does_not_leak_owner_only_default(self, tmp_path):
        # mkstemp creates 0o600; an explicit broader mode must win, proving the
        # final file is not silently left owner-only.
        target = tmp_path / "broad.json"
        atomic_json_write(target, {"k": "v"}, mode=0o644)

        assert stat.S_IMODE(target.stat().st_mode) == 0o644

    def test_chmod_failure_after_replace_is_swallowed(self, tmp_path, monkeypatch):
        # BUG (pinned): when ``mode`` is set and the post-replace os.chmod fails,
        # atomic_json_write silently swallows the OSError (utils.py L138-141)
        # rather than surfacing it. The write itself still succeeds. We pin the
        # current behavior: no exception propagates and the data is written.
        # (On POSIX, os.fchmod already applied ``mode`` to the temp fd before the
        # write, so the resulting file is NOT necessarily left at 0o600 — hence
        # we assert the swallow, not a specific leftover mode.)
        target = tmp_path / "data.json"
        real_chmod = os.chmod
        calls = {"n": 0}

        def flaky_chmod(path, mode, *a, **k):
            # Fail only the explicit-mode os.chmod path inside atomic_json_write,
            # identified by the target file path; let fchmod/other chmods pass.
            if str(path) == os.path.realpath(str(target)):
                calls["n"] += 1
                raise OSError("chmod denied")
            return real_chmod(path, mode, *a, **k)

        monkeypatch.setattr(os, "chmod", flaky_chmod)

        # Must NOT raise despite the chmod failure.
        atomic_json_write(target, {"ok": True}, mode=0o600)

        assert calls["n"] == 1  # the swallowed chmod was actually attempted
        assert json.loads(target.read_text(encoding="utf-8")) == {"ok": True}
        assert _tmp_files(tmp_path) == []


# ─────────────────────────── atomic_replace ─────────────────────────────────


class TestAtomicReplace:
    def test_plain_replace_for_regular_target(self, tmp_path):
        src = tmp_path / "src.tmp"
        target = tmp_path / "target.txt"
        src.write_text("new-bytes", encoding="utf-8")
        target.write_text("old-bytes", encoding="utf-8")

        returned = atomic_replace(src, target)

        assert target.read_text(encoding="utf-8") == "new-bytes"
        assert not src.exists()  # source consumed by the move
        # Returned path resolves to the (regular, non-symlink) target.
        assert Path(returned).resolve() == target.resolve()

    def test_plain_replace_for_nonexistent_target(self, tmp_path):
        src = tmp_path / "src.tmp"
        target = tmp_path / "brand_new.txt"
        src.write_text("hello", encoding="utf-8")

        atomic_replace(src, target)

        assert target.read_text(encoding="utf-8") == "hello"
        assert not src.exists()

    @POSIX_ONLY
    def test_symlink_target_is_preserved_and_real_file_rewritten(self, tmp_path):
        # Deployments symlink config files into a managed dir; a naive
        # os.replace would clobber the link with a regular file (GitHub #16743).
        real = tmp_path / "real_config.yaml"
        real.write_text("original", encoding="utf-8")
        link = tmp_path / "config.yaml"
        link.symlink_to(real)

        src = tmp_path / "src.tmp"
        src.write_text("updated", encoding="utf-8")

        returned = atomic_replace(src, link)

        # The link is STILL a symlink (not replaced by a regular file)...
        assert link.is_symlink()
        # ...still points at the same real file...
        assert os.readlink(link) == str(real)
        # ...and the real file received the new contents in-place.
        assert real.read_text(encoding="utf-8") == "updated"
        # Reading through the link sees the update.
        assert link.read_text(encoding="utf-8") == "updated"
        # The returned path is the real file, not the link, so callers re-apply
        # perms to the right inode.
        assert Path(returned) == Path(os.path.realpath(str(link)))
        assert not src.exists()


# ─────────────────────────── _preserve_file_mode ────────────────────────────


class TestPreserveFileMode:
    @POSIX_ONLY
    def test_returns_mode_for_existing_file(self, tmp_path):
        target = tmp_path / "f"
        target.write_text("x", encoding="utf-8")
        os.chmod(target, 0o641)

        assert _preserve_file_mode(target) == 0o641

    def test_returns_none_for_missing_file(self, tmp_path):
        assert _preserve_file_mode(tmp_path / "missing") is None

    def test_oserror_from_stat_returns_none(self, tmp_path, monkeypatch):
        # path.exists() is True but stat() raises -> the OSError branch -> None.
        target = tmp_path / "f"
        target.write_text("x", encoding="utf-8")

        real_stat = Path.stat

        def boom_stat(self, *a, **k):
            if self == target:
                raise OSError("stat failed")
            return real_stat(self, *a, **k)

        monkeypatch.setattr(Path, "stat", boom_stat)

        assert _preserve_file_mode(target) is None


# ─────────────────────────── _restore_file_mode ─────────────────────────────


class TestRestoreFileMode:
    def test_none_mode_is_a_noop(self, tmp_path, monkeypatch):
        target = tmp_path / "f"
        target.write_text("x", encoding="utf-8")

        called = {"n": 0}

        def spy_chmod(*a, **k):
            called["n"] += 1

        monkeypatch.setattr(os, "chmod", spy_chmod)

        _restore_file_mode(target, None)
        # mode is None -> early return, chmod never invoked.
        assert called["n"] == 0

    @POSIX_ONLY
    def test_applies_mode_to_file(self, tmp_path):
        target = tmp_path / "f"
        target.write_text("x", encoding="utf-8")
        os.chmod(target, 0o600)

        _restore_file_mode(target, 0o644)

        assert stat.S_IMODE(target.stat().st_mode) == 0o644

    def test_chmod_oserror_is_swallowed(self, tmp_path, monkeypatch):
        target = tmp_path / "f"
        target.write_text("x", encoding="utf-8")

        def boom_chmod(*a, **k):
            raise OSError("chmod denied")

        monkeypatch.setattr(os, "chmod", boom_chmod)

        # A failing chmod must not propagate (best-effort restore).
        _restore_file_mode(target, 0o644)  # no raise == pass


def test_target_functions_are_importable():
    """Guard the import surface this file depends on stays public."""
    for name in (
        "is_truthy_value",
        "atomic_json_write",
        "atomic_replace",
        "_preserve_file_mode",
        "_restore_file_mode",
    ):
        assert hasattr(utils, name), name
