"""Behavioral tests for the tirith security subprocess wrapper.

Targets the UNCOVERED branches of ``tirith_security.py`` that the vendored
``test_vendored_tirith_security.py`` does not exercise:

  * ``check_command_security`` exit-code -> action mapping, focusing on the
    JSON-degrades-but-verdict-survives path and the ``.app`` TLD warn
    suppression boundary.
  * ``_resolve_tirith_path`` cross-device move fallback (``shutil.move``
    OSError -> ``shutil.copy`` -> chmod, and copy-also-fails -> cleanup) and
    the explicit-path-on-PATH branch.
  * ``_background_install`` double-check-after-lock short-circuit and local
    re-checks.
  * ``ensure_installed`` explicit-path branches and the cosign-missing
    in-memory retry reset.
  * BUG-009: the ``check_command_security`` ``tirith_path is None``
    fail-closed branch, only reachable by monkeypatch because the real
    ``_resolve_tirith_path`` always returns a ``str``.

Everything is driven through mocks (``subprocess.run`` / ``shutil`` /
``shutil.which`` / ``os.access``) — no real network and no real tirith
binary. Mocking shapes mirror ``test_vendored_tirith_security.py``.
"""

import os
import subprocess
from unittest.mock import MagicMock, patch

import pytest

import calfkit_tools.hermes._vendor.tools.tirith_security as tirith_security
from calfkit_tools.hermes._vendor.tools import tirith_security as _ts

_MOD = "calfkit_tools.hermes._vendor.tools.tirith_security"

# Config that enables tirith and uses the bare-default path so the verdict is
# not gated by the conftest's ``TIRITH_ENABLED=false``. Patched onto
# ``_load_security_config`` wherever the verdict matters.
_CFG_FAIL_OPEN = {
    "tirith_enabled": True,
    "tirith_path": "tirith",
    "tirith_timeout": 5,
    "tirith_fail_open": True,
}
_CFG_FAIL_CLOSED = {**_CFG_FAIL_OPEN, "tirith_fail_open": False}


@pytest.fixture(autouse=True)
def _reset_install_state():
    """Reset the process-global install singletons around every test.

    The conftest scrubs env vars and isolates HERMES_HOME, but does NOT touch
    these module globals. Pre-set ``_resolved_path`` to a sentinel string so a
    test that exercises ``check_command_security`` doesn't accidentally trigger
    a real auto-install; tests that drive the resolver/installer reset it to
    ``None`` themselves.
    """
    _ts._resolved_path = "tirith"
    _ts._install_thread = None
    _ts._install_failure_reason = ""
    _ts._reset_spawn_warning_state()
    yield
    _ts._resolved_path = None
    _ts._install_thread = None
    _ts._install_failure_reason = ""
    _ts._reset_spawn_warning_state()


def _mock_run(returncode=0, stdout="", stderr=""):
    """Build a mock ``subprocess.CompletedProcess`` (mirrors the vendored helper)."""
    cp = MagicMock(spec=subprocess.CompletedProcess)
    cp.returncode = returncode
    cp.stdout = stdout
    cp.stderr = stderr
    return cp


# ---------------------------------------------------------------------------
# check_command_security: exit-code -> action mapping (the actual mapping)
# ---------------------------------------------------------------------------

class TestVerdictMapping:
    """Pin the exit-code -> action contract: 0=allow, 1=block, 2=warn.

    These assert the verdict comes from the exit code regardless of stdout
    payload, so they pass an empty JSON body (no findings) to isolate the
    mapping from the enrichment/suppression logic.
    """

    @patch(f"{_MOD}.subprocess.run")
    @patch(f"{_MOD}._load_security_config")
    def test_exit_0_maps_to_allow(self, mock_cfg, mock_run):
        mock_cfg.return_value = _CFG_FAIL_OPEN
        mock_run.return_value = _mock_run(0, "{}")
        result = tirith_security.check_command_security("echo hi")
        assert result["action"] == "allow"

    @patch(f"{_MOD}.subprocess.run")
    @patch(f"{_MOD}._load_security_config")
    def test_exit_1_maps_to_block(self, mock_cfg, mock_run):
        mock_cfg.return_value = _CFG_FAIL_OPEN
        mock_run.return_value = _mock_run(1, "{}")
        result = tirith_security.check_command_security("curl http://evil")
        assert result["action"] == "block"

    @patch(f"{_MOD}.subprocess.run")
    @patch(f"{_MOD}._load_security_config")
    def test_exit_2_maps_to_warn(self, mock_cfg, mock_run):
        mock_cfg.return_value = _CFG_FAIL_OPEN
        mock_run.return_value = _mock_run(2, "{}")
        result = tirith_security.check_command_security("curl https://bit.ly/x")
        assert result["action"] == "warn"

    @patch(f"{_MOD}.subprocess.run")
    @patch(f"{_MOD}._load_security_config")
    def test_verdict_is_independent_of_findings_payload(self, mock_cfg, mock_run):
        """A block exit code with an empty findings list still blocks."""
        mock_cfg.return_value = _CFG_FAIL_OPEN
        mock_run.return_value = _mock_run(1, '{"findings": [], "summary": "blocked"}')
        result = tirith_security.check_command_security("cmd")
        assert result["action"] == "block"
        assert result["findings"] == []
        assert result["summary"] == "blocked"


# ---------------------------------------------------------------------------
# JSON enrichment degrades, verdict survives
# ---------------------------------------------------------------------------

class TestBadJsonDegradesEnrichmentNotVerdict:
    """Malformed stdout must degrade the summary/findings but never flip the
    exit-code verdict. The vendored suite covers the simple ``"NOT JSON"``
    case; here we exercise empty/whitespace stdout and a top-level JSON array
    (``AttributeError`` on ``.get``) on the block path."""

    @patch(f"{_MOD}.subprocess.run")
    @patch(f"{_MOD}._load_security_config")
    def test_block_with_malformed_json_still_blocks(self, mock_cfg, mock_run):
        mock_cfg.return_value = _CFG_FAIL_OPEN
        mock_run.return_value = _mock_run(1, "}{ this is not json")
        result = tirith_security.check_command_security("rm -rf /")
        assert result["action"] == "block"
        assert result["findings"] == []
        assert "details unavailable" in result["summary"]

    @patch(f"{_MOD}.subprocess.run")
    @patch(f"{_MOD}._load_security_config")
    def test_block_with_json_array_stdout_degrades_via_attributeerror(self, mock_cfg, mock_run):
        """A top-level JSON array parses fine but ``.get`` raises
        AttributeError, which the wrapper catches and degrades the summary."""
        mock_cfg.return_value = _CFG_FAIL_OPEN
        mock_run.return_value = _mock_run(1, '["unexpected", "shape"]')
        result = tirith_security.check_command_security("cmd")
        assert result["action"] == "block"
        assert "details unavailable" in result["summary"]

    @patch(f"{_MOD}.subprocess.run")
    @patch(f"{_MOD}._load_security_config")
    def test_warn_with_empty_stdout_stays_warn_without_summary(self, mock_cfg, mock_run):
        """Empty stdout short-circuits to ``{}`` (the ``strip()`` guard), so no
        JSONDecodeError fires; findings/summary are simply empty and warn
        survives. No findings means the .app suppression block is skipped."""
        mock_cfg.return_value = _CFG_FAIL_OPEN
        mock_run.return_value = _mock_run(2, "   ")
        result = tirith_security.check_command_security("cmd")
        assert result["action"] == "warn"
        assert result["findings"] == []
        # Empty body is not a parse *failure*, so the degraded-summary branch
        # is not taken — summary stays "".
        assert result["summary"] == ""

    @patch(f"{_MOD}.subprocess.run")
    @patch(f"{_MOD}._load_security_config")
    def test_allow_with_malformed_json_stays_allow_no_degraded_summary(self, mock_cfg, mock_run):
        """On the allow path, a parse failure leaves the summary empty: the
        degraded-summary branch only fires for block/warn."""
        mock_cfg.return_value = _CFG_FAIL_OPEN
        mock_run.return_value = _mock_run(0, "garbage}{")
        result = tirith_security.check_command_security("cmd")
        assert result["action"] == "allow"
        assert result["summary"] == ""


# ---------------------------------------------------------------------------
# BUG-009: tirith_path is None fail-open / fail-closed branch
# ---------------------------------------------------------------------------

class TestBug009PathNoneBranch:
    """BUG-009: ``check_command_security``'s ``if tirith_path is None`` block is
    unreachable through the real ``_resolve_tirith_path`` (which returns a
    ``str`` on every code path — verified by inspection: it returns
    ``expanded`` / ``found`` / ``hermes_bin`` / ``installed``, never ``None``).
    The vendored suite pins the fail-OPEN side (line 727); these pin the
    fail-CLOSED side (line 728) and re-confirm fail-open, both only reachable
    by monkeypatching the resolver to return ``None``.
    """

    @patch(f"{_MOD}._resolve_tirith_path", return_value=None)
    @patch(f"{_MOD}.subprocess.run")
    @patch(f"{_MOD}._load_security_config")
    def test_path_none_fail_closed_blocks(self, mock_cfg, mock_run, mock_resolve):
        mock_cfg.return_value = _CFG_FAIL_CLOSED
        result = tirith_security.check_command_security("echo hi")
        assert result["action"] == "block"
        assert result["findings"] == []
        assert "fail-closed" in result["summary"]
        assert "unavailable" in result["summary"]
        # We never reached the spawn — the None guard returns first.
        mock_run.assert_not_called()

    @patch(f"{_MOD}._resolve_tirith_path", return_value=None)
    @patch(f"{_MOD}.subprocess.run")
    @patch(f"{_MOD}._load_security_config")
    def test_path_none_fail_open_allows(self, mock_cfg, mock_run, mock_resolve):
        mock_cfg.return_value = _CFG_FAIL_OPEN
        result = tirith_security.check_command_security("echo hi")
        assert result["action"] == "allow"
        assert result["summary"] == "tirith path unavailable"
        mock_run.assert_not_called()

    def test_real_resolver_never_returns_none_for_explicit_missing_path(self):
        """Confirm the BUG-009 premise: even the explicit-path-missing branch
        returns a ``str`` (the expanded path), never ``None``."""
        _ts._resolved_path = None
        with patch(f"{_MOD}.shutil.which", return_value=None), \
             patch("os.path.isfile", return_value=False):
            result = _ts._resolve_tirith_path("/opt/custom/tirith")
        assert isinstance(result, str)
        assert result is not None
        assert _ts._resolved_path is _ts._INSTALL_FAILED


# ---------------------------------------------------------------------------
# .app TLD warn suppression boundary
# ---------------------------------------------------------------------------

class TestAppTldSuppressionBoundary:
    """The vendored suite covers the headline cases; these pin the precise
    boundary the prompt calls out — sole .app finding suppressed, mixed set
    preserved — plus a non-dict finding inside the suppression scan."""

    @patch(f"{_MOD}.subprocess.run")
    @patch(f"{_MOD}._load_security_config")
    def test_sole_app_tld_finding_suppressed_to_allow(self, mock_cfg, mock_run):
        mock_cfg.return_value = _CFG_FAIL_OPEN
        mock_run.return_value = _mock_run(
            2, '{"findings": [{"rule_id": "lookalike_tld", "tld": ".app"}], "summary": "tld"}'
        )
        result = tirith_security.check_command_security("curl https://my.app")
        assert result["action"] == "allow"
        assert result["findings"] == []
        assert result["summary"] == ""

    @patch(f"{_MOD}.subprocess.run")
    @patch(f"{_MOD}._load_security_config")
    def test_app_tld_mixed_with_real_finding_stays_warn(self, mock_cfg, mock_run):
        mock_cfg.return_value = _CFG_FAIL_OPEN
        mock_run.return_value = _mock_run(
            2,
            '{"findings": ['
            '{"rule_id": "lookalike_tld", "value": ".app"}, '
            '{"rule_id": "homograph_url", "severity": "high"}'
            '], "summary": "mixed"}',
        )
        result = tirith_security.check_command_security("curl https://my.app")
        assert result["action"] == "warn"
        assert len(result["findings"]) == 2
        assert result["summary"] == "mixed"

    @patch(f"{_MOD}.subprocess.run")
    @patch(f"{_MOD}._load_security_config")
    def test_non_dict_finding_blocks_suppression(self, mock_cfg, mock_run):
        """A non-dict finding is treated as non-suppressible (``_is_app_tld_finding``
        returns False for it), so the warn verdict is preserved even though the
        only dict finding is a .app TLD."""
        mock_cfg.return_value = _CFG_FAIL_OPEN
        mock_run.return_value = _mock_run(
            2,
            '{"findings": ['
            '{"rule_id": "lookalike_tld", "value": ".app"}, '
            '"a-bare-string-finding"'
            '], "summary": "weird"}',
        )
        result = tirith_security.check_command_security("cmd")
        assert result["action"] == "warn"
        assert len(result["findings"]) == 2


class TestIsAppTldFindingNegatives:
    """``_is_app_tld_finding`` falsey branches the vendored suite doesn't all
    hit: explicit non-dict types, wrong rule_id, and a .app substring under a
    field that is *not* in the inspected set."""

    def test_non_dict_list_returns_false(self):
        assert _ts._is_app_tld_finding(["rule_id", "lookalike_tld"]) is False

    def test_non_dict_none_returns_false(self):
        assert _ts._is_app_tld_finding(None) is False

    def test_non_dict_int_returns_false(self):
        assert _ts._is_app_tld_finding(42) is False

    def test_wrong_rule_id_returns_false(self):
        assert _ts._is_app_tld_finding({"rule_id": "homograph_url", "value": ".app"}) is False

    def test_missing_rule_id_returns_false(self):
        assert _ts._is_app_tld_finding({"value": ".app"}) is False

    def test_app_substring_in_uninspected_field_returns_false(self):
        """``.app`` only counts when carried by one of the inspected fields
        (value/tld/detail/description/message). An unrelated ``rule_id``-only
        finding with .app elsewhere (e.g. ``severity``) is not suppressible."""
        finding = {"rule_id": "lookalike_tld", "severity": "low .app note"}
        assert _ts._is_app_tld_finding(finding) is False

    def test_correct_rule_id_but_no_app_value_returns_false(self):
        assert _ts._is_app_tld_finding({"rule_id": "lookalike_tld", "value": ".zip"}) is False


# ---------------------------------------------------------------------------
# _install_tirith: cross-device move fallback
# ---------------------------------------------------------------------------

class TestInstallCrossDeviceMoveFallback:
    """Lines 430-443: ``shutil.move`` raising OSError (cross-device, NFS,
    Docker overlay) falls back to ``shutil.copy`` + manual chmod; if the copy
    also fails, the partial dest is unlinked and a failure reason returned.

    We drive ``_install_tirith`` past download/cosign/checksum/extract via
    patches, then control only the move/copy/chmod trio.
    """

    @patch(f"{_MOD}.shutil.rmtree")
    @patch(f"{_MOD}.os.chmod")
    @patch(f"{_MOD}.os.stat")
    @patch(f"{_MOD}.shutil.copy")
    @patch(f"{_MOD}.shutil.move", side_effect=OSError("Invalid cross-device link"))
    @patch(f"{_MOD}._extract_tirith_binary", return_value=("/tmp/src/tirith", ""))
    @patch(f"{_MOD}.tarfile.open")
    @patch(f"{_MOD}._verify_checksum", return_value=True)
    @patch(f"{_MOD}.shutil.which", return_value=None)
    @patch(f"{_MOD}._download_file")
    @patch(f"{_MOD}._detect_target", return_value="aarch64-apple-darwin")
    @patch(f"{_MOD}._hermes_bin_dir", return_value="/hermes/bin")
    def test_move_oserror_falls_back_to_copy_and_chmod(
        self, mock_bindir, mock_target, mock_dl, mock_which, mock_checksum,
        mock_taropen, mock_extract, mock_move, mock_copy, mock_stat, mock_chmod,
        mock_rmtree,
    ):
        _ts._resolved_path = None
        mock_tar = MagicMock()
        mock_tar.__enter__.return_value = mock_tar
        mock_tar.__exit__.return_value = False
        mock_taropen.return_value = mock_tar

        stat_result = MagicMock()
        stat_result.st_mode = 0o644
        mock_stat.return_value = stat_result

        path, reason = _ts._install_tirith()

        assert reason == ""
        assert path == "/hermes/bin/tirith"
        mock_move.assert_called_once()
        # Fell back to copy after move raised.
        mock_copy.assert_called_once_with("/tmp/src/tirith", "/hermes/bin/tirith")
        # chmod added the executable bits on the copied dest.
        mock_chmod.assert_called_once()
        mode_arg = mock_chmod.call_args[0][1]
        import stat as _stat
        assert mode_arg & _stat.S_IXUSR
        assert mode_arg & _stat.S_IXGRP
        assert mode_arg & _stat.S_IXOTH

    @patch(f"{_MOD}.shutil.rmtree")
    @patch(f"{_MOD}.os.chmod")
    @patch(f"{_MOD}.os.unlink")
    @patch(f"{_MOD}.shutil.copy", side_effect=OSError("Read-only file system"))
    @patch(f"{_MOD}.shutil.move", side_effect=OSError("Invalid cross-device link"))
    @patch(f"{_MOD}._extract_tirith_binary", return_value=("/tmp/src/tirith", ""))
    @patch(f"{_MOD}.tarfile.open")
    @patch(f"{_MOD}._verify_checksum", return_value=True)
    @patch(f"{_MOD}.shutil.which", return_value=None)
    @patch(f"{_MOD}._download_file")
    @patch(f"{_MOD}._detect_target", return_value="aarch64-apple-darwin")
    @patch(f"{_MOD}._hermes_bin_dir", return_value="/hermes/bin")
    def test_move_and_copy_both_fail_cleans_up_and_returns_reason(
        self, mock_bindir, mock_target, mock_dl, mock_which, mock_checksum,
        mock_taropen, mock_extract, mock_move, mock_copy, mock_unlink, mock_chmod,
        mock_rmtree,
    ):
        _ts._resolved_path = None
        mock_tar = MagicMock()
        mock_tar.__enter__.return_value = mock_tar
        mock_tar.__exit__.return_value = False
        mock_taropen.return_value = mock_tar

        path, reason = _ts._install_tirith()

        assert path is None
        assert reason == "cross_device_copy_failed"
        # Partial dest cleaned up so a non-executable file can't poison a retry.
        mock_unlink.assert_called_once_with("/hermes/bin/tirith")
        # chmod never ran because the copy failed before it.
        mock_chmod.assert_not_called()

    @patch(f"{_MOD}.shutil.rmtree")
    @patch(f"{_MOD}.os.chmod")
    @patch(f"{_MOD}.os.unlink", side_effect=OSError("dest already gone"))
    @patch(f"{_MOD}.shutil.copy", side_effect=OSError("Read-only file system"))
    @patch(f"{_MOD}.shutil.move", side_effect=OSError("Invalid cross-device link"))
    @patch(f"{_MOD}._extract_tirith_binary", return_value=("/tmp/src/tirith", ""))
    @patch(f"{_MOD}.tarfile.open")
    @patch(f"{_MOD}._verify_checksum", return_value=True)
    @patch(f"{_MOD}.shutil.which", return_value=None)
    @patch(f"{_MOD}._download_file")
    @patch(f"{_MOD}._detect_target", return_value="aarch64-apple-darwin")
    @patch(f"{_MOD}._hermes_bin_dir", return_value="/hermes/bin")
    def test_cleanup_unlink_oserror_is_swallowed(
        self, mock_bindir, mock_target, mock_dl, mock_which, mock_checksum,
        mock_taropen, mock_extract, mock_move, mock_copy, mock_unlink, mock_chmod,
        mock_rmtree,
    ):
        """Even when the cleanup unlink itself raises (dest never created), the
        installer still returns the failure reason rather than propagating."""
        _ts._resolved_path = None
        mock_tar = MagicMock()
        mock_tar.__enter__.return_value = mock_tar
        mock_tar.__exit__.return_value = False
        mock_taropen.return_value = mock_tar

        path, reason = _ts._install_tirith()

        assert path is None
        assert reason == "cross_device_copy_failed"


# ---------------------------------------------------------------------------
# _resolve_tirith_path: explicit path found on PATH (lines 500-501)
# ---------------------------------------------------------------------------

class TestResolveExplicitPathOnPath:
    """An explicit (non-default) path that isn't a file but resolves via
    ``shutil.which`` (a bare program name on PATH) is honored — lines 498-501."""

    def test_explicit_bare_name_resolved_via_which(self):
        _ts._resolved_path = None
        with patch("os.path.isfile", return_value=False), \
             patch(f"{_MOD}.shutil.which", return_value="/usr/local/bin/my-tirith") as mock_which:
            result = _ts._resolve_tirith_path("my-tirith")
        assert result == "/usr/local/bin/my-tirith"
        assert _ts._resolved_path == "/usr/local/bin/my-tirith"
        mock_which.assert_called_once_with("my-tirith")

    def test_explicit_path_isfile_but_not_executable_falls_to_which(self):
        """An explicit path that exists but isn't executable is rejected by the
        ``os.access(..., X_OK)`` guard, then ``which`` is consulted as a
        fallback (here it also misses -> explicit_path_missing)."""
        _ts._resolved_path = None
        with patch("os.path.isfile", return_value=True), \
             patch("os.access", return_value=False), \
             patch(f"{_MOD}.shutil.which", return_value=None):
            result = _ts._resolve_tirith_path("/opt/custom/tirith")
        assert result == "/opt/custom/tirith"
        assert _ts._resolved_path is _ts._INSTALL_FAILED
        assert _ts._install_failure_reason == "explicit_path_missing"


# ---------------------------------------------------------------------------
# _background_install: double-check-after-lock + local re-checks
# ---------------------------------------------------------------------------

class TestBackgroundInstall:
    """Lines 569-595. The thread target re-validates state under the lock so a
    race that resolved the path (another thread/process) short-circuits the
    network download."""

    def test_double_check_after_lock_short_circuits(self):
        """If ``_resolved_path`` is already set when the lock is acquired, the
        thread returns immediately without probing PATH or installing."""
        _ts._resolved_path = "/already/resolved/tirith"
        with patch(f"{_MOD}.shutil.which") as mock_which, \
             patch(f"{_MOD}._install_tirith") as mock_install:
            _ts._background_install()
        mock_which.assert_not_called()
        mock_install.assert_not_called()
        # Untouched.
        assert _ts._resolved_path == "/already/resolved/tirith"

    def test_double_check_short_circuits_even_on_install_failed_sentinel(self):
        """``_INSTALL_FAILED`` is *not* ``None``, so the post-lock guard
        (``if _resolved_path is not None``) also short-circuits on it."""
        _ts._resolved_path = _ts._INSTALL_FAILED
        with patch(f"{_MOD}.shutil.which") as mock_which, \
             patch(f"{_MOD}._install_tirith") as mock_install:
            _ts._background_install()
        mock_which.assert_not_called()
        mock_install.assert_not_called()
        assert _ts._resolved_path is _ts._INSTALL_FAILED

    def test_picks_up_tirith_appearing_on_path_under_lock(self):
        """Race recovery: another process dropped tirith on PATH between the
        ensure_installed check and the thread running — the re-check wins and
        no download happens."""
        _ts._resolved_path = None
        with patch(f"{_MOD}.shutil.which", return_value="/usr/bin/tirith"), \
             patch(f"{_MOD}._install_tirith") as mock_install:
            _ts._background_install()
        assert _ts._resolved_path == "/usr/bin/tirith"
        assert _ts._install_failure_reason == ""
        mock_install.assert_not_called()

    def test_picks_up_tirith_in_hermes_bin_under_lock(self):
        _ts._resolved_path = None
        with patch(f"{_MOD}.shutil.which", return_value=None), \
             patch(f"{_MOD}._hermes_bin_dir", return_value="/hermes/bin"), \
             patch("os.path.isfile", return_value=True), \
             patch("os.access", return_value=True), \
             patch(f"{_MOD}._install_tirith") as mock_install:
            _ts._background_install()
        assert _ts._resolved_path == "/hermes/bin/tirith"
        assert _ts._install_failure_reason == ""
        mock_install.assert_not_called()

    def test_downloads_when_no_local_copy_and_persists_success(self):
        _ts._resolved_path = None
        with patch(f"{_MOD}.shutil.which", return_value=None), \
             patch(f"{_MOD}._hermes_bin_dir", return_value="/hermes/bin"), \
             patch("os.path.isfile", return_value=False), \
             patch(f"{_MOD}._install_tirith", return_value=("/dl/tirith", "")) as mock_install, \
             patch(f"{_MOD}._clear_install_failed") as mock_clear:
            _ts._background_install()
        mock_install.assert_called_once()
        assert _ts._resolved_path == "/dl/tirith"
        mock_clear.assert_called_once()

    def test_download_failure_marks_and_persists_reason(self):
        _ts._resolved_path = None
        with patch(f"{_MOD}.shutil.which", return_value=None), \
             patch(f"{_MOD}._hermes_bin_dir", return_value="/hermes/bin"), \
             patch("os.path.isfile", return_value=False), \
             patch(f"{_MOD}._install_tirith", return_value=(None, "download_failed")), \
             patch(f"{_MOD}._mark_install_failed") as mock_mark:
            _ts._background_install()
        assert _ts._resolved_path is _ts._INSTALL_FAILED
        assert _ts._install_failure_reason == "download_failed"
        mock_mark.assert_called_once_with("download_failed")

    def test_log_failures_flag_threaded_through_to_installer(self):
        """``log_failures=False`` (startup prefetch) is forwarded to
        ``_install_tirith`` so a quiet probe stays quiet."""
        _ts._resolved_path = None
        with patch(f"{_MOD}.shutil.which", return_value=None), \
             patch(f"{_MOD}._hermes_bin_dir", return_value="/hermes/bin"), \
             patch("os.path.isfile", return_value=False), \
             patch(f"{_MOD}._install_tirith", return_value=(None, "download_failed")) as mock_install, \
             patch(f"{_MOD}._mark_install_failed"):
            _ts._background_install(log_failures=False)
        assert mock_install.call_args.kwargs == {"log_failures": False}


# ---------------------------------------------------------------------------
# ensure_installed: explicit-path branches + cosign-missing in-mem retry reset
# ---------------------------------------------------------------------------

class TestEnsureInstalledExplicitPath:
    """Lines 631-641. Explicit path is a synchronous check only — never a
    download — and resolves via isfile+X_OK or ``which``, else caches a miss."""

    @patch(f"{_MOD}._load_security_config")
    def test_explicit_path_isfile_executable_returns_it(self, mock_cfg):
        mock_cfg.return_value = {**_CFG_FAIL_OPEN, "tirith_path": "/opt/custom/tirith"}
        _ts._resolved_path = None
        with patch("os.path.isfile", return_value=True), \
             patch("os.access", return_value=True), \
             patch(f"{_MOD}.threading.Thread") as mock_thread:
            result = tirith_security.ensure_installed()
        assert result == "/opt/custom/tirith"
        assert _ts._resolved_path == "/opt/custom/tirith"
        mock_thread.assert_not_called()

    @patch(f"{_MOD}._load_security_config")
    def test_explicit_path_resolved_via_which(self, mock_cfg):
        mock_cfg.return_value = {**_CFG_FAIL_OPEN, "tirith_path": "my-tirith"}
        _ts._resolved_path = None
        with patch("os.path.isfile", return_value=False), \
             patch(f"{_MOD}.shutil.which", return_value="/usr/bin/my-tirith"), \
             patch(f"{_MOD}.threading.Thread") as mock_thread:
            result = tirith_security.ensure_installed()
        assert result == "/usr/bin/my-tirith"
        mock_thread.assert_not_called()

    @patch(f"{_MOD}._load_security_config")
    def test_explicit_path_missing_caches_failure_no_thread(self, mock_cfg):
        mock_cfg.return_value = {**_CFG_FAIL_OPEN, "tirith_path": "/opt/custom/tirith"}
        _ts._resolved_path = None
        with patch("os.path.isfile", return_value=False), \
             patch(f"{_MOD}.shutil.which", return_value=None), \
             patch(f"{_MOD}.threading.Thread") as mock_thread:
            result = tirith_security.ensure_installed()
        assert result is None
        assert _ts._resolved_path is _ts._INSTALL_FAILED
        assert _ts._install_failure_reason == "explicit_path_missing"
        mock_thread.assert_not_called()


class TestEnsureInstalledHermesBinRecovery:
    """Lines 651-656. Default path: a previously auto-installed binary in
    ``$HERMES_HOME/bin`` is picked up synchronously, clearing any stale marker
    and starting no download thread."""

    @patch(f"{_MOD}._load_security_config")
    def test_hermes_bin_executable_returned_and_marker_cleared(self, mock_cfg):
        mock_cfg.return_value = _CFG_FAIL_OPEN
        _ts._resolved_path = None
        with patch(f"{_MOD}.shutil.which", return_value=None), \
             patch(f"{_MOD}._hermes_bin_dir", return_value="/hermes/bin"), \
             patch("os.path.isfile", return_value=True), \
             patch("os.access", return_value=True), \
             patch(f"{_MOD}._clear_install_failed") as mock_clear, \
             patch(f"{_MOD}.threading.Thread") as mock_thread:
            result = tirith_security.ensure_installed()
        assert result == "/hermes/bin/tirith"
        assert _ts._install_failure_reason == ""
        mock_clear.assert_called_once()
        mock_thread.assert_not_called()


class TestEnsureInstalledCosignMissingRetryReset:
    """Lines 658-665. When the in-memory sentinel is ``_INSTALL_FAILED`` with
    reason ``cosign_missing`` and cosign is now on PATH, ``ensure_installed``
    clears the sentinel and proceeds to (re-)launch the background install.
    A different failure reason, by contrast, returns ``None`` immediately."""

    @patch(f"{_MOD}._load_security_config")
    def test_cosign_reappeared_resets_sentinel_and_launches_thread(self, mock_cfg):
        mock_cfg.return_value = _CFG_FAIL_OPEN
        _ts._resolved_path = _ts._INSTALL_FAILED
        _ts._install_failure_reason = "cosign_missing"

        def _which(name):
            # tirith missing (forces the local checks to fail through to the
            # sentinel branch), cosign now present (the retryable condition).
            return "/usr/bin/cosign" if name == "cosign" else None

        with patch(f"{_MOD}.shutil.which", side_effect=_which), \
             patch(f"{_MOD}._hermes_bin_dir", return_value="/nonexistent"), \
             patch("os.path.isfile", return_value=False), \
             patch(f"{_MOD}._read_failure_reason", return_value=None), \
             patch(f"{_MOD}._is_install_failed_on_disk", return_value=False), \
             patch(f"{_MOD}._clear_install_failed") as mock_clear, \
             patch(f"{_MOD}.threading.Thread") as MockThread:
            mock_thread = MagicMock()
            mock_thread.is_alive.return_value = False
            MockThread.return_value = mock_thread
            result = tirith_security.ensure_installed()

        assert result is None  # download deferred to the thread
        mock_clear.assert_called_once()
        assert _ts._install_failure_reason == ""
        MockThread.assert_called_once()
        mock_thread.start.assert_called_once()

    @patch(f"{_MOD}._load_security_config")
    def test_cosign_still_absent_keeps_sentinel_returns_none(self, mock_cfg):
        mock_cfg.return_value = _CFG_FAIL_OPEN
        _ts._resolved_path = _ts._INSTALL_FAILED
        _ts._install_failure_reason = "cosign_missing"

        with patch(f"{_MOD}.shutil.which", return_value=None), \
             patch(f"{_MOD}._hermes_bin_dir", return_value="/nonexistent"), \
             patch("os.path.isfile", return_value=False), \
             patch(f"{_MOD}.threading.Thread") as MockThread:
            result = tirith_security.ensure_installed()

        assert result is None
        assert _ts._resolved_path is _ts._INSTALL_FAILED
        # Sentinel preserved; no download thread spawned.
        MockThread.assert_not_called()

    @patch(f"{_MOD}._load_security_config")
    def test_non_cosign_failure_reason_returns_none_without_retry(self, mock_cfg):
        mock_cfg.return_value = _CFG_FAIL_OPEN
        _ts._resolved_path = _ts._INSTALL_FAILED
        _ts._install_failure_reason = "download_failed"

        with patch(f"{_MOD}.shutil.which", return_value=None), \
             patch(f"{_MOD}._hermes_bin_dir", return_value="/nonexistent"), \
             patch("os.path.isfile", return_value=False), \
             patch(f"{_MOD}.threading.Thread") as MockThread:
            result = tirith_security.ensure_installed()

        assert result is None
        assert _ts._resolved_path is _ts._INSTALL_FAILED
        MockThread.assert_not_called()


# ---------------------------------------------------------------------------
# ensure_installed: cached-but-vanished resolved path
# ---------------------------------------------------------------------------

class TestEnsureInstalledCachedPathReVerified:
    """Lines 612-616. A previously cached ``_resolved_path`` is re-verified on
    every call; if the binary vanished (no longer a file / not executable),
    ``ensure_installed`` returns ``None`` rather than a stale path."""

    @patch(f"{_MOD}._load_security_config")
    def test_cached_path_still_valid_returned(self, mock_cfg):
        mock_cfg.return_value = _CFG_FAIL_OPEN
        _ts._resolved_path = "/cached/tirith"
        with patch("os.path.isfile", return_value=True), \
             patch("os.access", return_value=True):
            result = tirith_security.ensure_installed()
        assert result == "/cached/tirith"

    @patch(f"{_MOD}._load_security_config")
    def test_cached_path_vanished_returns_none(self, mock_cfg):
        mock_cfg.return_value = _CFG_FAIL_OPEN
        _ts._resolved_path = "/cached/tirith"
        with patch("os.path.isfile", return_value=False), \
             patch("os.access", return_value=False):
            result = tirith_security.ensure_installed()
        assert result is None
