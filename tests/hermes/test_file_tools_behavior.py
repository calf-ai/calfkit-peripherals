#!/usr/bin/env python3
"""Behavioral tests for the file_tools handler layer.

These pin behavior on the UNCOVERED handler-layer paths in
``calfkit_tools.hermes._vendor.tools.file_tools`` that the existing vendored
suites (``test_vendored_file_read_guards``, ``test_vendored_file_staleness``,
``test_vendored_read_loop_detection``, ``test_vendored_accretion_caps``,
``test_vendored_file_tools*``) do not reach:

  * dedup hard-block escalation thresholds + stat-OSError fall-through
  * staleness external-edit warning surfaced through write_file_tool
  * patch-failure escalation (_record_patch_failure 64-cap eviction; the
    failure #3 _hint on the third consecutive replace failure)
  * _cap_read_tracker_data eviction loops (set + both dicts)
  * device / sensitive-path guards (symlink to /dev/zero, /proc gated by
    sys.platform, realpath-OSError -> False, exact /run/docker.sock)
  * write_file_tool legacy resolve-fail fallback (the ``_resolved is None``
    block when _resolve_path_for_task raises)
  * a handful of fail-open / except branches (config load failure,
    hermes-config resolve failure, internal-status non-str, expected
    write exceptions, file_state.record_read failure)

Style: real files on ``tmp_path``; ``MagicMock`` for the shell file-ops
(patched at ``_get_file_ops``); ``monkeypatch`` to force the rare except
branches.  Assertions are behavioral (observable JSON / return values /
tracker state), and every test pins CURRENT behavior.

The autouse hermes conftest fixtures scrub env and reset the file_tools /
file_state / terminal_tool / approval module globals between tests, so these
tests don't need their own global teardown — but several still clear
``_read_tracker`` / ``_patch_failure_tracker`` explicitly for locality.
"""

import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

from calfkit_tools.hermes._vendor.tools import file_state, file_tools


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class _FakeReadResult:
    def __init__(self, content="line1\nline2\n", total_lines=2, file_size=100,
                 truncated=False):
        self.content = content
        self._total_lines = total_lines
        self._file_size = file_size
        self._truncated = truncated

    def to_dict(self):
        return {
            "content": self.content,
            "total_lines": self._total_lines,
            "file_size": self._file_size,
            "truncated": self._truncated,
        }


def _read_ops(content="line one\nline two\n", file_size=20, truncated=False):
    fake = MagicMock()
    fake.read_file = lambda path, offset=1, limit=500: _FakeReadResult(
        content=content, total_lines=2, file_size=file_size, truncated=truncated,
    )
    return fake


def _write_ops():
    fake = MagicMock()
    fake.write_file = lambda path, content: MagicMock(
        to_dict=lambda: {"success": True, "path": str(path)}
    )
    return fake


def _patch_replace_ops(result_dict):
    """A fake whose patch_replace always returns ``result_dict``."""
    fake = MagicMock()
    robj = MagicMock()
    robj.to_dict.return_value = result_dict
    fake.patch_replace.return_value = robj
    return fake


@pytest.fixture(autouse=True)
def _clear_trackers():
    """Local belt-and-suspenders clear of the per-task trackers."""
    with file_tools._read_tracker_lock:
        file_tools._read_tracker.clear()
    with file_tools._patch_failure_lock:
        file_tools._patch_failure_tracker.clear()
    file_state.get_registry().clear()
    yield
    with file_tools._read_tracker_lock:
        file_tools._read_tracker.clear()
    with file_tools._patch_failure_lock:
        file_tools._patch_failure_tracker.clear()
    file_state.get_registry().clear()


# ===========================================================================
# Dedup -> hard-block thresholds  (read_file_tool lines 762-788)
# ===========================================================================

class TestDedupHardBlockThresholds:
    """Same (path, offset, limit) with unchanged mtime: read -> stub -> BLOCKED.

    Complements the existing TestDedupStubLoopGuard by asserting the exact
    sequence and the escalation payload shape on a real tmp_path file.
    """

    @patch("calfkit_tools.hermes._vendor.tools.file_tools._get_file_ops")
    def test_read_then_unchanged_then_blocked(self, mock_ops, tmp_path):
        f = tmp_path / "dedup.txt"
        f.write_text("line one\nline two\n")
        mock_ops.return_value = _read_ops()

        # 1st: real read, full content.
        r1 = json.loads(file_tools.read_file_tool(str(f), task_id="t"))
        assert r1.get("dedup") is not True
        assert "content" in r1

        # 2nd: dedup stub (first hit), no error.
        r2 = json.loads(file_tools.read_file_tool(str(f), task_id="t"))
        assert r2.get("dedup") is True
        assert r2.get("status") == "unchanged"
        assert r2.get("content_returned") is False
        assert "error" not in r2

        # 3rd: second stub hit escalates to a hard BLOCKED error.
        r3 = json.loads(file_tools.read_file_tool(str(f), task_id="t"))
        assert "BLOCKED" in r3["error"]
        assert r3["already_read"] == 3
        assert "content" not in r3
        assert "dedup" not in r3

    @patch("calfkit_tools.hermes._vendor.tools.file_tools._get_file_ops")
    def test_dedup_stat_oserror_falls_through_to_full_read(self, mock_ops, tmp_path):
        """If the file is deleted between reads, the dedup mtime stat raises
        OSError and the handler falls through to a fresh full read (lines
        789-790) rather than returning a stale stub."""
        f = tmp_path / "vanishing.txt"
        f.write_text("line one\nline two\n")
        mock_ops.return_value = _read_ops()

        # 1st read populates the dedup mtime for this key.
        r1 = json.loads(file_tools.read_file_tool(str(f), task_id="t"))
        assert r1.get("dedup") is not True

        # Delete the file: os.path.getmtime in the dedup branch now raises
        # OSError -> the `except OSError: pass` fall-through path runs.
        os.unlink(f)

        r2 = json.loads(file_tools.read_file_tool(str(f), task_id="t"))
        # Fell through to the real (mocked) read instead of a dedup stub.
        assert r2.get("dedup") is not True
        assert "content" in r2


# ===========================================================================
# Staleness external-edit warning  (write_file_tool happy + _warning path)
# ===========================================================================

class TestStalenessExternalEditWarning:
    """read -> os.utime bump -> write surfaces the per-task staleness warning.

    This exercises the resolved write path (lines ~1087-1109) and the
    _check_file_staleness mtime-mismatch branch (line 1034) with utime
    instead of a real-content rewrite, so it's deterministic without sleeps.
    """

    @patch("calfkit_tools.hermes._vendor.tools.file_tools._get_file_ops")
    def test_utime_bump_between_read_and_write_warns(self, mock_ops, tmp_path):
        f = tmp_path / "stale.txt"
        f.write_text("original\n")
        mock_ops.return_value = _read_ops(content="original\n", file_size=9)

        file_tools.read_file_tool(str(f), task_id="t")

        # Bump mtime forward deterministically (simulated external edit).
        st = f.stat()
        os.utime(f, (st.st_atime, st.st_mtime + 10))

        mock_ops.return_value = _write_ops()
        result = json.loads(file_tools.write_file_tool(str(f), "new", task_id="t"))
        assert "_warning" in result
        assert "modified since you last read" in result["_warning"]
        # Resolved absolute path is always reported on the write response.
        assert result.get("resolved_path") == str(f.resolve())

    @patch("calfkit_tools.hermes._vendor.tools.file_tools._get_file_ops")
    def test_no_warning_when_mtime_unchanged(self, mock_ops, tmp_path):
        f = tmp_path / "fresh.txt"
        f.write_text("original\n")
        mock_ops.return_value = _read_ops(content="original\n", file_size=9)
        file_tools.read_file_tool(str(f), task_id="t")

        mock_ops.return_value = _write_ops()
        result = json.loads(file_tools.write_file_tool(str(f), "new", task_id="t"))
        assert "_warning" not in result


# ===========================================================================
# write_file_tool legacy resolve-fail fallback  (lines 1071-1082)
# ===========================================================================

class TestWriteLegacyResolveFallback:
    """When _resolve_path_for_task raises, write still proceeds via the
    ``_resolved is None`` legacy block (no per-path lock / registry)."""

    @patch("calfkit_tools.hermes._vendor.tools.file_tools._get_file_ops")
    def test_resolve_raises_write_still_proceeds(self, mock_ops, tmp_path, monkeypatch):
        f = tmp_path / "legacy.txt"
        f.write_text("x\n")
        written = {}

        fake = MagicMock()

        def _capture_write(path, content):
            written["path"] = path
            written["content"] = content
            return MagicMock(to_dict=lambda: {"success": True, "path": str(path)})

        fake.write_file = _capture_write
        mock_ops.return_value = fake

        # _check_sensitive_path also calls _resolve_path_for_task; it swallows
        # OSError/ValueError internally, so raising one of those keeps the
        # sensitive-path guard pass-through intact while still driving
        # write_file_tool's own try/except (which catches *any* Exception)
        # into the _resolved is None branch.
        real_resolve = file_tools._resolve_path_for_task

        def _boom(filepath, task_id="default"):
            if filepath == str(f):
                raise OSError("resolve boom")
            return real_resolve(filepath, task_id)

        monkeypatch.setattr(file_tools, "_resolve_path_for_task", _boom)

        result = json.loads(
            file_tools.write_file_tool(str(f), "payload", task_id="t")
        )
        # Write proceeded through the legacy block.
        assert result.get("success") is True
        # Legacy block passes the ORIGINAL (unresolved) path to the shell layer
        # and does NOT attach a resolved_path key.
        assert written["path"] == str(f)
        assert written["content"] == "payload"
        assert "resolved_path" not in result

    @patch("calfkit_tools.hermes._vendor.tools.file_tools._get_file_ops")
    def test_expected_write_denial_is_quiet_error(self, mock_ops, tmp_path):
        """A PermissionError from the shell layer is treated as an expected
        denial (lines 1110-1118 / _is_expected_write_exception) and returned
        as a tool_error rather than crashing."""
        f = tmp_path / "perm.txt"
        f.write_text("x\n")

        fake = MagicMock()

        def _denied(path, content):
            raise PermissionError("nope")

        fake.write_file = _denied
        mock_ops.return_value = fake

        result = json.loads(file_tools.write_file_tool(str(f), "data", task_id="t"))
        assert "error" in result


# ===========================================================================
# Patch-failure escalation  (_record_patch_failure + failure #3 _hint)
# ===========================================================================

class TestPatchFailureEscalation:
    """Three consecutive 'Could not find' replace failures on the same path
    escalate from the generic hint to the failure-count _hint (lines
    1262-1281)."""

    @patch("calfkit_tools.hermes._vendor.tools.file_tools._get_file_ops")
    def test_third_consecutive_failure_escalates_hint(self, mock_ops, tmp_path):
        f = tmp_path / "patchme.txt"
        f.write_text("alpha\n")
        # patch_replace always fails with a generic 'Could not find' (no
        # "Did you mean" snippet), so the hint logic runs.
        mock_ops.return_value = _patch_replace_ops(
            {"error": "Could not find match for old_string in patchme.txt"}
        )

        # Failures 1 and 2: generic hint, not the escalated one.
        for _ in range(2):
            r = json.loads(file_tools.patch_tool(
                mode="replace", path=str(f), old_string="zzz", new_string="q",
                task_id="t",
            ))
            assert "failure #" not in r.get("_hint", "")
            assert "old_string not found" in r["_hint"]

        # Failure 3: escalated hint naming the failure count + the path.
        r3 = json.loads(file_tools.patch_tool(
            mode="replace", path=str(f), old_string="zzz", new_string="q",
            task_id="t",
        ))
        assert "failure #3" in r3["_hint"]
        assert "write_file" in r3["_hint"]

    @patch("calfkit_tools.hermes._vendor.tools.file_tools._get_file_ops")
    def test_successful_patch_resets_failure_counter(self, mock_ops, tmp_path):
        f = tmp_path / "reset.txt"
        f.write_text("alpha\n")
        resolved = str(f.resolve())

        # Two failures.
        mock_ops.return_value = _patch_replace_ops(
            {"error": "Could not find match for old_string in reset.txt"}
        )
        for _ in range(2):
            file_tools.patch_tool(mode="replace", path=str(f),
                                  old_string="zzz", new_string="q", task_id="t")
        with file_tools._patch_failure_lock:
            assert file_tools._patch_failure_tracker["t"][resolved] == 2

        # A successful patch clears the counter for that path (line 1249).
        mock_ops.return_value = _patch_replace_ops(
            {"success": True, "diff": "--- a\n+++ b\n"}
        )
        file_tools.patch_tool(mode="replace", path=str(f),
                              old_string="alpha", new_string="beta", task_id="t")
        with file_tools._patch_failure_lock:
            assert resolved not in file_tools._patch_failure_tracker.get("t", {})

    @patch("calfkit_tools.hermes._vendor.tools.file_tools._get_file_ops")
    def test_did_you_mean_snippet_suppresses_generic_hint(self, mock_ops, tmp_path):
        """When patch_replace already attached a 'Did you mean one of these
        sections?' snippet, the generic hint is suppressed (line 1282 branch)
        — but the per-path failure counter still increments."""
        f = tmp_path / "snippet.txt"
        f.write_text("alpha\n")
        mock_ops.return_value = _patch_replace_ops({
            "error": (
                "Could not find match for old_string. "
                "Did you mean one of these sections?\n  alpha"
            )
        })
        r = json.loads(file_tools.patch_tool(
            mode="replace", path=str(f), old_string="zzz", new_string="q",
            task_id="t",
        ))
        # Only the first failure: generic hint suppressed, escalated not yet hit.
        assert "_hint" not in r
        with file_tools._patch_failure_lock:
            assert file_tools._patch_failure_tracker["t"][str(f.resolve())] == 1


class TestRecordPatchFailureEviction:
    """_record_patch_failure caps the per-task dict at 64 distinct paths,
    evicting the oldest insertion-order entry (lines 432-438)."""

    def test_sixty_fifth_distinct_path_evicts_oldest(self):
        tid = "evict"
        # Insert 64 distinct failing paths.
        for i in range(64):
            file_tools._record_patch_failure(tid, f"/p/{i}")
        with file_tools._patch_failure_lock:
            assert len(file_tools._patch_failure_tracker[tid]) == 64
            assert "/p/0" in file_tools._patch_failure_tracker[tid]

        # 65th distinct path triggers eviction of the oldest (/p/0).
        file_tools._record_patch_failure(tid, "/p/64")
        with file_tools._patch_failure_lock:
            failures = file_tools._patch_failure_tracker[tid]
            assert len(failures) == 64
            assert "/p/0" not in failures   # oldest evicted
            assert "/p/64" in failures      # newest inserted

    def test_repeat_same_path_when_full_does_not_evict(self):
        """Incrementing an existing path at capacity must not evict (the
        ``resolved_path not in task_failures`` guard)."""
        tid = "full"
        for i in range(64):
            file_tools._record_patch_failure(tid, f"/q/{i}")
        # Re-hit an already-tracked path: count rises, size stays 64, nothing
        # evicted.
        count = file_tools._record_patch_failure(tid, "/q/0")
        with file_tools._patch_failure_lock:
            failures = file_tools._patch_failure_tracker[tid]
            assert count == 2
            assert len(failures) == 64
            assert "/q/0" in failures

    def test_reset_patch_failures_empty_paths_is_noop(self):
        file_tools._record_patch_failure("z", "/a")
        file_tools._reset_patch_failures("z", [])  # early return, line 444
        with file_tools._patch_failure_lock:
            assert file_tools._patch_failure_tracker["z"]["/a"] == 1

    def test_reset_patch_failures_unknown_task_is_noop(self):
        # No tracker entry for this task -> the `if not task_failures` guard
        # short-circuits without error (line 448).
        file_tools._reset_patch_failures("never-seen", ["/a"])


# ===========================================================================
# _cap_read_tracker_data eviction loops  (lines 489-517)
# ===========================================================================

class TestCapReadTrackerEvictionLoops:
    """Drive each eviction loop in _cap_read_tracker_data past its cap,
    including the dedup_hits dict the accretion-cap suite doesn't cover."""

    def test_all_four_containers_trimmed_to_cap(self, monkeypatch):
        monkeypatch.setattr(file_tools, "_READ_HISTORY_CAP", 2)
        monkeypatch.setattr(file_tools, "_DEDUP_CAP", 2)
        monkeypatch.setattr(file_tools, "_READ_TIMESTAMPS_CAP", 2)
        task_data = {
            "read_history": {(f"/h{i}", 0, 500) for i in range(6)},
            "dedup": {(f"/d{i}", 0, 500): float(i) for i in range(6)},
            "dedup_hits": {(f"/d{i}", 0, 500): i for i in range(6)},
            "read_timestamps": {f"/t{i}": float(i) for i in range(6)},
        }
        file_tools._cap_read_tracker_data(task_data)
        assert len(task_data["read_history"]) == 2
        assert len(task_data["dedup"]) == 2
        # dedup_hits shares _DEDUP_CAP (lines 501-508).
        assert len(task_data["dedup_hits"]) == 2
        assert len(task_data["read_timestamps"]) == 2

    def test_dedup_hits_evicts_oldest_first(self, monkeypatch):
        monkeypatch.setattr(file_tools, "_DEDUP_CAP", 3)
        task_data = {
            "dedup_hits": {(f"/k{i}", 0, 500): i for i in range(8)},
        }
        file_tools._cap_read_tracker_data(task_data)
        assert len(task_data["dedup_hits"]) == 3
        assert ("/k7", 0, 500) in task_data["dedup_hits"]   # newest kept
        assert ("/k0", 0, 500) not in task_data["dedup_hits"]  # oldest gone

    def test_missing_dedup_hits_container_is_safe(self, monkeypatch):
        monkeypatch.setattr(file_tools, "_DEDUP_CAP", 1)
        # No dedup_hits key at all -> the `is not None` guard skips it.
        file_tools._cap_read_tracker_data({"dedup": {("/a", 0, 500): 1.0}})


# ===========================================================================
# _is_blocked_device / _is_blocked_device_path  (lines 195-230)
# ===========================================================================

class TestBlockedDevice:
    def test_symlink_to_dev_zero_is_blocked(self, tmp_path):
        if sys.platform == "win32":
            pytest.skip("POSIX device paths")
        link = tmp_path / "zero-link"
        try:
            os.symlink("/dev/zero", link)
        except OSError as exc:
            pytest.skip(f"symlink unavailable: {exc}")
        # Realpath resolves the symlink to /dev/zero -> blocked (line 228-229).
        assert file_tools._is_blocked_device(str(link)) is True

    def test_read_file_tool_rejects_dev_zero_symlink(self, tmp_path):
        if sys.platform == "win32":
            pytest.skip("POSIX device paths")
        link = tmp_path / "zero-link2"
        try:
            os.symlink("/dev/zero", link)
        except OSError as exc:
            pytest.skip(f"symlink unavailable: {exc}")
        with patch(
            "calfkit_tools.hermes._vendor.tools.file_tools._get_file_ops"
        ) as mock_ops:
            result = json.loads(file_tools.read_file_tool(str(link), task_id="t"))
        assert "device file" in result["error"]
        mock_ops.assert_not_called()  # blocked before any I/O

    def test_realpath_oserror_returns_false(self, monkeypatch):
        """If os.path.realpath raises (OSError/ValueError), _is_blocked_device
        returns False (lines 226-227) — it does not crash the read."""
        monkeypatch.setattr(
            file_tools.os.path, "realpath",
            MagicMock(side_effect=OSError("loop")),
        )
        # A non-literal-blocked path: literal check passes, realpath raises,
        # function returns False.
        assert file_tools._is_blocked_device("/tmp/whatever.txt") is False

    def test_safe_device_not_blocked(self):
        assert file_tools._is_blocked_device("/dev/null") is False

    def test_proc_environ_blocked_on_linux(self):
        if sys.platform != "linux":
            pytest.skip("/proc sensitive-pseudo-file blocklist is Linux-shaped")
        assert file_tools._is_blocked_device("/proc/self/environ") is True
        assert file_tools._is_blocked_device_path("/proc/1234/cmdline") is True

    def test_proc_environ_path_check_is_platform_agnostic(self):
        # _is_blocked_device_path is a pure string check, so the /proc rule
        # fires regardless of host platform (the device blocklist itself is
        # path-pattern based). Asserted separately from the realpath-driven
        # _is_blocked_device so it's stable on macOS dev boxes too.
        assert file_tools._is_blocked_device_path("/proc/self/environ") is True


# ===========================================================================
# _check_sensitive_path  (lines 262-289)
# ===========================================================================

class TestCheckSensitivePath:
    def test_etc_prefix_blocked(self):
        err = file_tools._check_sensitive_path("/etc/passwd")
        assert err is not None
        assert "sensitive system path" in err

    def test_exact_docker_sock_blocked(self):
        # Exact-match branch (line 276-277): /run/docker.sock is absolute so
        # it resolves to itself and matches _SENSITIVE_EXACT_PATHS.
        err = file_tools._check_sensitive_path("/run/docker.sock")
        assert err is not None
        assert "sensitive system path" in err

    def test_normal_path_allowed(self, tmp_path):
        assert file_tools._check_sensitive_path(str(tmp_path / "ok.txt")) is None

    def test_resolve_oserror_falls_back_to_raw_path(self, monkeypatch):
        """If _resolve_path_for_task raises OSError/ValueError, the guard
        falls back to the normalized raw path (lines 266-267) and STILL
        catches a sensitive prefix."""
        monkeypatch.setattr(
            file_tools, "_resolve_path_for_task",
            MagicMock(side_effect=OSError("boom")),
        )
        err = file_tools._check_sensitive_path("/etc/shadow")
        assert err is not None
        assert "sensitive system path" in err

    def test_hermes_config_path_blocked(self, monkeypatch):
        """Writing the resolved Hermes config path is refused with the
        config-specific message (lines 282-288)."""
        monkeypatch.setattr(
            file_tools, "_get_hermes_config_resolved",
            lambda: "/home/u/.hermes/config.yaml",
        )
        # Absolute input resolves to itself, matching the cached config path.
        err = file_tools._check_sensitive_path("/home/u/.hermes/config.yaml")
        assert err is not None
        assert "Hermes config file" in err


# ===========================================================================
# _get_hermes_config_resolved  (lines 245-259, cached)
# ===========================================================================

class TestGetHermesConfigResolved:
    def setup_method(self):
        # Reset the module cache so each test re-evaluates.
        file_tools._hermes_config_resolved = None
        file_tools._hermes_config_resolved_loaded = False

    def teardown_method(self):
        file_tools._hermes_config_resolved = None
        file_tools._hermes_config_resolved_loaded = False

    def test_uses_shim_get_config_path(self, monkeypatch):
        monkeypatch.setattr(
            "calfkit_tools.hermes._shims.hermes_cli.config.get_config_path",
            lambda: __import__("pathlib").Path("/x/y/config.yaml"),
        )
        out = file_tools._get_hermes_config_resolved()
        assert out == str(__import__("pathlib").Path("/x/y/config.yaml").resolve())

    def test_falls_back_when_get_config_path_raises(self, monkeypatch):
        """When get_config_path raises, fall back to ~/.hermes/config.yaml
        (lines 254-257)."""
        monkeypatch.setattr(
            "calfkit_tools.hermes._shims.hermes_cli.config.get_config_path",
            MagicMock(side_effect=RuntimeError("no config")),
        )
        out = file_tools._get_hermes_config_resolved()
        assert out is not None
        assert out.endswith("config.yaml")

    def test_result_is_cached(self, monkeypatch):
        calls = {"n": 0}

        def _cfg():
            calls["n"] += 1
            return __import__("pathlib").Path("/z/config.yaml")

        monkeypatch.setattr(
            "calfkit_tools.hermes._shims.hermes_cli.config.get_config_path", _cfg,
        )
        first = file_tools._get_hermes_config_resolved()
        second = file_tools._get_hermes_config_resolved()
        assert first == second
        assert calls["n"] == 1  # second call hit the cache (line 248-249)


# ===========================================================================
# _is_internal_file_status_text  (line 539 non-str guard)
# ===========================================================================

class TestInternalStatusTextGuard:
    def test_non_string_returns_false(self):
        # Line 539: not isinstance(content, str) -> False.
        assert file_tools._is_internal_file_status_text(None) is False
        assert file_tools._is_internal_file_status_text(b"bytes") is False
        assert file_tools._is_internal_file_status_text(123) is False

    def test_empty_string_returns_false(self):
        assert file_tools._is_internal_file_status_text("   ") is False

    def test_exact_status_message_returns_true(self):
        assert file_tools._is_internal_file_status_text(
            file_tools._READ_DEDUP_STATUS_MESSAGE
        ) is True

    @patch("calfkit_tools.hermes._vendor.tools.file_tools._get_file_ops")
    def test_write_non_string_content_path_not_internal(self, mock_ops, tmp_path):
        """write_file_tool with the verbatim status string is rejected; a
        legitimately different string is not (sanity around the guard)."""
        f = tmp_path / "g.txt"
        f.write_text("x\n")
        fake = MagicMock()
        fake.write_file = MagicMock()
        mock_ops.return_value = fake
        result = json.loads(file_tools.write_file_tool(
            str(f), file_tools._READ_DEDUP_STATUS_MESSAGE, task_id="t",
        ))
        assert "internal read_file status text" in result["error"]
        fake.write_file.assert_not_called()


# ===========================================================================
# _is_expected_write_exception  (line 391 OSError errno branch)
# ===========================================================================

class TestExpectedWriteException:
    def test_permission_error_is_expected(self):
        assert file_tools._is_expected_write_exception(PermissionError()) is True

    def test_oserror_with_eacces_is_expected(self):
        import errno
        exc = OSError(errno.EACCES, "perm")
        assert file_tools._is_expected_write_exception(exc) is True

    def test_oserror_with_erofs_is_expected(self):
        import errno
        exc = OSError(errno.EROFS, "read only fs")
        assert file_tools._is_expected_write_exception(exc) is True

    def test_oserror_with_other_errno_not_expected(self):
        import errno
        exc = OSError(errno.ENOENT, "missing")
        assert file_tools._is_expected_write_exception(exc) is False

    def test_value_error_not_expected(self):
        assert file_tools._is_expected_write_exception(ValueError()) is False


# ===========================================================================
# file_state.record_read failure is swallowed  (lines 883-884)
# ===========================================================================

class TestRecordReadFailureSwallowed:
    @patch("calfkit_tools.hermes._vendor.tools.file_tools._get_file_ops")
    def test_record_read_exception_does_not_break_read(self, mock_ops, tmp_path,
                                                       monkeypatch):
        f = tmp_path / "rec.txt"
        f.write_text("line one\nline two\n")
        mock_ops.return_value = _read_ops()
        # Force file_state.record_read to raise; read_file_tool must still
        # return content (the except logs at debug and continues).
        monkeypatch.setattr(
            file_tools.file_state, "record_read",
            MagicMock(side_effect=RuntimeError("registry down")),
        )
        result = json.loads(file_tools.read_file_tool(str(f), task_id="t"))
        assert "content" in result
        assert "error" not in result


# ===========================================================================
# reset_file_dedup single-task branches  (lines 922-935)
# ===========================================================================

class TestResetFileDedupBranches:
    @patch("calfkit_tools.hermes._vendor.tools.file_tools._get_file_ops")
    def test_reset_single_task_clears_dedup_and_hits(self, mock_ops, tmp_path):
        f = tmp_path / "rd.txt"
        f.write_text("line one\nline two\n")
        mock_ops.return_value = _read_ops()
        # read -> stub populates dedup + dedup_hits for "t".
        file_tools.read_file_tool(str(f), task_id="t")
        file_tools.read_file_tool(str(f), task_id="t")
        with file_tools._read_tracker_lock:
            assert file_tools._read_tracker["t"]["dedup"]
            assert file_tools._read_tracker["t"]["dedup_hits"]

        file_tools.reset_file_dedup("t")
        with file_tools._read_tracker_lock:
            assert file_tools._read_tracker["t"]["dedup"] == {}
            assert file_tools._read_tracker["t"]["dedup_hits"] == {}

    def test_reset_unknown_task_is_noop(self):
        # task_id given but no tracker entry -> the `if task_data:` guard
        # short-circuits (line 925) without error.
        file_tools.reset_file_dedup("does-not-exist")


# ===========================================================================
# _invalidate_dedup_for_path / _check_file_staleness resolve-fail
# (lines 973-974, 1021-1022)
# ===========================================================================

class TestResolveFailureGuards:
    def test_invalidate_dedup_resolve_failure_returns_quietly(self, monkeypatch):
        monkeypatch.setattr(
            file_tools, "_resolve_path",
            MagicMock(side_effect=ValueError("bad path")),
        )
        # Should not raise (lines 973-974 early return).
        file_tools._invalidate_dedup_for_path("\x00bad", "t")

    def test_check_staleness_resolve_failure_returns_none(self, monkeypatch):
        monkeypatch.setattr(
            file_tools, "_resolve_path_for_task",
            MagicMock(side_effect=OSError("bad")),
        )
        assert file_tools._check_file_staleness("/whatever", "t") is None
