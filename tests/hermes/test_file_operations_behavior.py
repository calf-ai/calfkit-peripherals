"""Behavioral tests for the low-level file engine in ``file_operations``.

Targets the UNCOVERED branches of
``calfkit_tools.hermes._vendor.tools.file_operations.ShellFileOperations`` that
the existing suites (``test_vendored_file_operations*``,
``test_vendored_search_error_guard``, ``test_vendored_line_ending_preservation``)
do not reach: search-backend fallbacks, post-write verification, raw-read
binary/image/cat-fail branches, the optional-dependency lint skips, the
in-process ``_python_delete`` body, similar-file scoring, and ``~``/``~user``
path expansion.

Two harness styles are used, matching the two patterns already in the suite:

* ``MagicMock`` env with a scripted ``_exec`` ``side_effect`` for branch-precise
  unit tests (no real shell). Lets us force exit codes and stdout that a real
  ``rg``/``grep``/``sed`` would only emit under hard-to-stage conditions
  (exit 2 + partial matches, BSD ``find`` lacking ``-printf``, a diverging
  post-write re-read, a missing optional linter dependency).
* A real-subprocess ``RealEnv`` (``subprocess.run(..., shell=True)``) on
  ``tmp_path`` for end-to-end round-trips (the ``_python_delete`` exec body,
  similar-file suggestions). These are POSIX/Linux-CI-green.

Two flagged bugs from ``docs/known-bugs.md`` are addressed:

* ``BUG-016`` (``_check_lint`` ``{file}`` substitution) is investigated and
  PINNED as a NON-reproduction — see ``TestBug016FilePlaceholderSubstitution``.
* ``BUG-017`` (BOM + patch verification asymmetry) is not cleanly reproducible
  here and is intentionally skipped (per task instructions).
"""

import builtins
import subprocess

import pytest

from calfkit_tools.hermes._vendor.tools import file_operations
from calfkit_tools.hermes._vendor.tools.file_operations import (
    LINTERS,
    ShellFileOperations,
    LintResult,
    WriteResult,
)


# ---------------------------------------------------------------------------
# Harness helpers
# ---------------------------------------------------------------------------

def _scripted_ops(handler):
    """ShellFileOperations whose ``_exec`` is driven by ``handler(command)``.

    ``handler`` returns an object with ``.exit_code`` / ``.stdout`` (a
    ``_FakeExec``). No real shell runs — every branch is reachable by
    scripting exit codes and stdout precisely.
    """
    from unittest.mock import MagicMock

    env = MagicMock()
    env.cwd = "/work"
    ops = ShellFileOperations(env, cwd="/work")
    ops._exec = lambda command, *a, **k: handler(command)  # type: ignore[assignment]
    return ops


class _FakeExec:
    """Stand-in for ``ExecuteResult`` with explicit fields."""

    def __init__(self, exit_code=0, stdout=""):
        self.exit_code = exit_code
        self.stdout = stdout


class RealEnv:
    """Terminal env backed by a real subprocess (pattern B).

    Mirrors the local backend's contract: ``execute(command, cwd, **kw)``
    returns ``{"output": ..., "returncode": ...}`` with stderr folded into
    stdout (the engine runs with ``stderr=subprocess.STDOUT`` semantics).
    """

    def __init__(self, cwd):
        self.cwd = str(cwd)

    def execute(self, command, cwd=None, **kw):
        proc = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            cwd=cwd or self.cwd,
            input=kw.get("stdin_data"),
            timeout=kw.get("timeout"),
        )
        return {"output": proc.stdout + proc.stderr, "returncode": proc.returncode}


@pytest.fixture
def real_ops(tmp_path):
    return ShellFileOperations(RealEnv(tmp_path), cwd=str(tmp_path))


def _no_import(*names):
    """Return an ``__import__`` replacement that raises ImportError for ``names``."""
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name in names:
            raise ImportError(f"forced: {name} unavailable")
        return real_import(name, *args, **kwargs)

    return fake_import


# ===========================================================================
# Search backend fallbacks (content + files)
# ===========================================================================

class TestSearchBackendFallbacks:
    def test_content_no_rg_no_grep_returns_typed_error(self):
        ops = _scripted_ops(lambda cmd: _FakeExec(0, ""))
        ops._has_command = lambda cmd: False
        res = ops._search_content("x", "/work", None, 50, 0, "content", 0)
        assert res.error is not None
        assert "ripgrep" in res.error and "grep" in res.error
        assert not res.matches

    def test_content_falls_back_to_grep_when_no_rg(self):
        seen = []

        def handler(cmd):
            seen.append(cmd)
            return _FakeExec(0, "a.py:1:hit\n")

        ops = _scripted_ops(handler)
        ops._has_command = lambda cmd: cmd == "grep"
        res = ops._search_content("hit", "/work", None, 50, 0, "content", 0)
        assert res.error is None
        assert [(m.path, m.line_number, m.content) for m in res.matches] == [("a.py", 1, "hit")]
        assert any(c.lstrip().startswith("set -o pipefail; grep") for c in seen)

    def test_files_no_rg_no_find_returns_typed_error(self):
        ops = _scripted_ops(lambda cmd: _FakeExec(0, ""))
        ops._has_command = lambda cmd: False
        res = ops._search_files("*.py", "/work", 50, 0)
        assert res.error is not None
        assert "rg" in res.error and "find" in res.error
        assert not res.files

    def test_bsd_find_printf_retry_without_printf(self):
        # BSD find (macOS) lacks -printf: the first command returns empty,
        # forcing the retry that drops -printf.
        seen = []

        def handler(cmd):
            seen.append(cmd)
            if "-printf" in cmd:
                return _FakeExec(0, "")  # BSD find: -printf unsupported -> empty
            return _FakeExec(0, "/work/x.py\n/work/y.py\n")

        ops = _scripted_ops(handler)
        ops._has_command = lambda cmd: cmd == "find"
        res = ops._search_files("*.py", "/work", 50, 0)
        assert res.files == ["/work/x.py", "/work/y.py"]
        assert any("-printf" in c for c in seen), "GNU -printf attempt expected first"
        assert any(c.startswith("find ") and "-printf" not in c for c in seen), "BSD retry expected"

    def test_rg_files_sortr_retry_falls_back_to_plain(self):
        # rg < 13 lacks --sortr=modified: first command empty -> plain retry.
        seen = []

        def handler(cmd):
            seen.append(cmd)
            if "--sortr" in cmd:
                return _FakeExec(0, "")
            return _FakeExec(0, "/work/a.py\n")

        ops = _scripted_ops(handler)
        res = ops._search_files_rg("a.py", "/work", 50, 0)
        assert res.files == ["/work/a.py"]
        assert any("--sortr" in c for c in seen)
        assert any("rg --files" in c and "--sortr" not in c for c in seen)


# ===========================================================================
# Partial-error guard + count/context parsing (rg and grep)
# ===========================================================================

class TestSearchExitCodeAndParsing:
    def test_rg_exit2_with_matches_keeps_matches(self):
        # rg exits 2 on a partial error (one unreadable file) but other files
        # matched. Those matches must survive — not be discarded as an error.
        out = (
            "rg: sub/locked.txt: Permission denied (os error 13)\n"
            "a.txt:1:needle\nb.txt:2:needle\n"
        )
        ops = _scripted_ops(lambda cmd: _FakeExec(2, out))
        ops._has_command = lambda cmd: cmd == "rg"
        res = ops._search_with_rg("needle", "/work", None, 50, 0, "content", 0)
        assert res.error is None, f"partial error wrongly surfaced: {res.error!r}"
        assert [(m.path, m.line_number) for m in res.matches] == [("a.txt", 1), ("b.txt", 2)]

    def test_grep_exit2_with_matches_keeps_matches(self):
        out = (
            "grep: sub/locked.txt: Permission denied\n"
            "a.txt:1:needle\n"
        )
        ops = _scripted_ops(lambda cmd: _FakeExec(2, out))
        ops._has_command = lambda cmd: cmd == "grep"
        res = ops._search_with_grep("needle", "/work", None, 50, 0, "content", 0)
        assert res.error is None
        assert [(m.path, m.line_number) for m in res.matches] == [("a.txt", 1)]

    def test_rg_exit2_pure_error_is_surfaced(self):
        # exit 2 with only diagnostics (no usable payload) -> surface error.
        out = "rg: regex parse error:\n    (?:[)\n       ^\nerror: unclosed character class\n"
        ops = _scripted_ops(lambda cmd: _FakeExec(2, out))
        ops._has_command = lambda cmd: cmd == "rg"
        res = ops._search_with_rg("[", "/work", None, 50, 0, "content", 0)
        assert res.error is not None
        assert "Search failed" in res.error
        assert not res.matches

    def test_rg_count_mode_swallows_non_integer_count(self):
        # A malformed count line ("path:notanint") must be skipped, not crash.
        out = "a.py:3\nb.py:notanint\nc.py:5\n"
        ops = _scripted_ops(lambda cmd: _FakeExec(0, out))
        ops._has_command = lambda cmd: cmd == "rg"
        res = ops._search_with_rg("x", "/work", None, 50, 0, "count", 0)
        assert res.counts == {"a.py": 3, "c.py": 5}
        assert res.total_count == 8

    def test_grep_count_mode_swallows_non_integer_count(self):
        out = "a.py:2\nb.py:xx\n"
        ops = _scripted_ops(lambda cmd: _FakeExec(0, out))
        ops._has_command = lambda cmd: cmd == "grep"
        res = ops._search_with_grep("x", "/work", None, 50, 0, "count", 0)
        assert res.counts == {"a.py": 2}
        assert res.total_count == 2

    def test_rg_context_lines_are_parsed(self):
        # Dash-separated context line "a.py-6-after" is parsed as a match when
        # context was requested.
        out = "a.py:5:hit\na.py-6-after\n--\n"
        ops = _scripted_ops(lambda cmd: _FakeExec(0, out))
        ops._has_command = lambda cmd: cmd == "rg"
        res = ops._search_with_rg("hit", "/work", None, 50, 0, "content", 2)
        assert [(m.path, m.line_number, m.content) for m in res.matches] == [
            ("a.py", 5, "hit"),
            ("a.py", 6, "after"),
        ]

    def test_grep_context_lines_are_parsed(self):
        out = "a.py:5:hit\na.py-6-after\n--\n"
        ops = _scripted_ops(lambda cmd: _FakeExec(0, out))
        ops._has_command = lambda cmd: cmd == "grep"
        res = ops._search_with_grep("hit", "/work", None, 50, 0, "content", 2)
        assert [(m.path, m.line_number, m.content) for m in res.matches] == [
            ("a.py", 5, "hit"),
            ("a.py", 6, "after"),
        ]

    def test_grep_files_only_mode(self):
        out = "a.py\nb.py\n"
        ops = _scripted_ops(lambda cmd: _FakeExec(0, out))
        ops._has_command = lambda cmd: cmd == "grep"
        res = ops._search_with_grep("x", "/work", None, 50, 0, "files_only", 0)
        assert res.files == ["a.py", "b.py"]
        assert res.total_count == 2


# ===========================================================================
# patch_replace post-write verification
# ===========================================================================

class TestPatchPostWriteVerification:
    def _verify_ops(self, initial, reread_exit, reread_stdout):
        """Ops whose patch_replace re-read diverges from the intended write.

        ``write_file`` and ``_check_lint_delta`` are stubbed to isolate the
        verification branch: the first ``cat`` returns ``initial`` (the
        pre-patch read), the verify ``cat`` returns ``reread_*``.
        """
        state = {"cats": 0}

        def handler(cmd):
            if cmd.startswith("cat "):
                state["cats"] += 1
                if state["cats"] == 1:
                    return _FakeExec(0, initial)
                return _FakeExec(reread_exit, reread_stdout)
            return _FakeExec(0, "")

        ops = _scripted_ops(handler)
        ops.write_file = lambda path, content: WriteResult(bytes_written=len(content))
        ops._check_lint_delta = lambda *a, **k: LintResult(success=True)
        return ops

    def test_diverging_reread_reports_verification_failure(self):
        ops = self._verify_ops("hello world\n", 0, "TOTALLY DIFFERENT CONTENT\n")
        res = ops.patch_replace("/work/f.txt", "hello", "goodbye")
        assert res.success is False
        assert res.error is not None
        assert "Post-write verification failed" in res.error
        assert "did not persist" in res.error

    def test_unreadable_reread_reports_verification_failure(self):
        ops = self._verify_ops("hello world\n", 1, "")
        res = ops.patch_replace("/work/f.txt", "hello", "goodbye")
        assert res.success is False
        assert res.error is not None
        assert "Post-write verification failed: could not re-read" in res.error

    def test_matching_reread_succeeds(self):
        # Sanity counterpart: when the re-read matches, the patch succeeds and
        # emits a diff. (Guards the verification branch isn't a false-positive.)
        def handler(cmd):
            if cmd.startswith("cat "):
                return _FakeExec(0, "hello world\n")
            return _FakeExec(0, "")

        ops = _scripted_ops(handler)
        ops._check_lint_delta = lambda *a, **k: LintResult(success=True)
        # write_file must produce "goodbye world\n" on disk; fake the reread to
        # echo exactly what patch intends to write.
        intended = {}

        def real_write(path, content):
            intended["content"] = content
            return WriteResult(bytes_written=len(content))

        ops.write_file = real_write

        def handler2(cmd):
            if cmd.startswith("cat "):
                if "content" in intended:
                    return _FakeExec(0, intended["content"])
                return _FakeExec(0, "hello world\n")
            return _FakeExec(0, "")

        ops._exec = lambda command, *a, **k: handler2(command)
        res = ops.patch_replace("/work/f.txt", "hello", "goodbye")
        assert res.success is True
        assert res.error is None
        assert res.files_modified == ["/work/f.txt"]
        assert res.diff


# ===========================================================================
# read_file_raw branches (image / binary / cat-fail / not-found)
# ===========================================================================

class TestReadFileRaw:
    def test_image_extension_short_circuits(self):
        def handler(cmd):
            if cmd.startswith("wc -c"):
                return _FakeExec(0, "2048")
            return _FakeExec(0, "")

        ops = _scripted_ops(handler)
        res = ops.read_file_raw("/work/pic.png")
        assert res.is_image is True
        assert res.is_binary is True
        assert res.file_size == 2048
        assert res.content == ""

    def test_binary_content_returns_error(self):
        def handler(cmd):
            if cmd.startswith("wc -c"):
                return _FakeExec(0, "512")
            if cmd.startswith("head -c"):
                return _FakeExec(0, "\x00\x01\x02binary\x00")
            return _FakeExec(0, "")

        ops = _scripted_ops(handler)
        res = ops.read_file_raw("/work/blob.dat")
        assert res.is_binary is True
        assert res.error is not None and "Binary file" in res.error

    def test_cat_failure_returns_error(self):
        def handler(cmd):
            if cmd.startswith("wc -c"):
                return _FakeExec(0, "100")
            if cmd.startswith("head -c"):
                return _FakeExec(0, "plain text")
            if cmd.startswith("cat "):
                return _FakeExec(1, "cat: permission denied")
            return _FakeExec(0, "")

        ops = _scripted_ops(handler)
        res = ops.read_file_raw("/work/x.txt")
        assert res.error is not None
        assert "Failed to read file" in res.error

    def test_not_found_suggests_similar(self):
        # wc -c fails (file missing) -> falls through to _suggest_similar_files.
        def handler(cmd):
            if cmd.startswith("wc -c"):
                return _FakeExec(1, "")
            if cmd.startswith("ls -1"):
                return _FakeExec(0, "config.yaml\nother.txt\n")
            return _FakeExec(0, "")

        ops = _scripted_ops(handler)
        res = ops.read_file_raw("/work/config.yml")
        assert res.error is not None and "File not found" in res.error
        assert any(s.endswith("config.yaml") for s in res.similar_files)


# ===========================================================================
# read_file branches (image / binary / read-fail)
# ===========================================================================

class TestReadFileBranches:
    def test_image_redirects_to_vision(self):
        def handler(cmd):
            if cmd.startswith("wc -c"):
                return _FakeExec(0, "9999")
            return _FakeExec(0, "")

        ops = _scripted_ops(handler)
        res = ops.read_file("/work/p.png")
        assert res.is_image is True
        assert res.hint is not None and "vision_analyze" in res.hint

    def test_binary_extension_returns_error(self):
        def handler(cmd):
            if cmd.startswith("wc -c"):
                return _FakeExec(0, "500")
            if cmd.startswith("head -c"):
                return _FakeExec(0, "\x00\x01\x02")
            return _FakeExec(0, "")

        ops = _scripted_ops(handler)
        res = ops.read_file("/work/a.bin")
        assert res.is_binary is True
        assert res.error is not None and "Binary file" in res.error

    def test_read_command_failure_returns_error(self):
        def handler(cmd):
            if cmd.startswith("wc -c"):
                return _FakeExec(0, "50")
            if cmd.startswith("head -c"):
                return _FakeExec(0, "text content")
            if cmd.startswith("sed -n"):
                return _FakeExec(1, "sed: read error")
            return _FakeExec(0, "")

        ops = _scripted_ops(handler)
        res = ops.read_file("/work/a.txt")
        assert res.error is not None
        assert "Failed to read file" in res.error


# ===========================================================================
# Optional-dependency lint skips (__SKIP__) and shell-linter selection
# ===========================================================================

class TestLintOptionalDependencies:
    def test_yaml_missing_dependency_is_skipped(self, monkeypatch):
        ops = _scripted_ops(lambda cmd: _FakeExec(0, "a: 1\n"))
        monkeypatch.setattr(builtins, "__import__", _no_import("yaml"))
        res = ops._check_lint("/work/x.yaml", content="a: 1\n")
        assert res.skipped is True
        assert "missing dependency" in res.message

    def test_toml_missing_dependency_is_skipped(self, monkeypatch):
        ops = _scripted_ops(lambda cmd: _FakeExec(0, "x = 1\n"))
        # Force both stdlib tomllib and the tomli fallback to be unavailable.
        monkeypatch.setattr(builtins, "__import__", _no_import("tomllib", "tomli"))
        res = ops._check_lint("/work/x.toml", content="x = 1\n")
        assert res.skipped is True
        assert "missing dependency" in res.message

    def test_shell_linter_base_command_missing_is_skipped(self):
        # .js uses a shell linter (node --check). With node absent, skip.
        ops = _scripted_ops(lambda cmd: _FakeExec(0, ""))
        ops._has_command = lambda cmd: False
        res = ops._check_lint("/work/app.js")
        assert res.skipped is True
        assert "not available" in res.message

    def test_unknown_extension_is_skipped(self):
        ops = _scripted_ops(lambda cmd: _FakeExec(0, ""))
        res = ops._check_lint("/work/notes.unknownext")
        assert res.skipped is True
        assert "No linter" in res.message

    def test_inproc_linter_reads_from_disk_when_no_content(self):
        # JSON linter with content=None reads via cat; malformed JSON -> error.
        def handler(cmd):
            if cmd.startswith("cat "):
                return _FakeExec(0, "{not valid json")
            return _FakeExec(0, "")

        ops = _scripted_ops(handler)
        res = ops._check_lint("/work/data.json")
        assert res.success is False
        assert "JSON" in res.output


# ===========================================================================
# _python_delete exec body (real subprocess round-trips)
# ===========================================================================

class TestPythonDelete:
    def test_deletes_existing_file(self, real_ops, tmp_path):
        target = tmp_path / "gone.txt"
        target.write_text("bye")
        res = real_ops.delete_file(str(target))
        assert res.error is None
        assert not target.exists()

    def test_missing_file_is_a_noop(self, real_ops, tmp_path):
        # FileNotFoundError is swallowed inside the snippet -> clean WriteResult.
        res = real_ops.delete_file(str(tmp_path / "never_existed.txt"))
        assert res.error is None

    def test_directory_rejected_without_recursive(self, real_ops, tmp_path):
        d = tmp_path / "adir"
        d.mkdir()
        res = real_ops.delete_file(str(d))
        assert res.error is not None
        assert "is a directory" in res.error
        assert d.exists()

    def test_recursive_delete_removes_tree(self, real_ops, tmp_path):
        d = tmp_path / "tree"
        (d / "nested").mkdir(parents=True)
        (d / "nested" / "f.txt").write_text("x")
        res = real_ops.delete_path(str(d), recursive=True)
        assert res.error is None
        assert not d.exists()

    def test_delete_denied_for_protected_path(self, monkeypatch):
        ops = _scripted_ops(lambda cmd: _FakeExec(0, ""))
        monkeypatch.setattr(file_operations, "_is_write_denied", lambda path: True)
        res = ops.delete_file("/work/secret")
        assert res.error is not None
        assert "protected path" in res.error

    def test_falls_back_to_python_when_python3_missing(self, monkeypatch):
        # When `python3 -c` fails AND its output mentions python3, retry with
        # `python -c` (Windows / older systems without the python3 symlink).
        seen = []

        def handler(cmd):
            seen.append(cmd)
            if "python3 -c" in cmd:
                return _FakeExec(127, "python3: command not found")
            if cmd.startswith("python -c"):
                return _FakeExec(0, "")
            return _FakeExec(0, "")

        ops = _scripted_ops(handler)
        monkeypatch.setattr(file_operations, "_is_write_denied", lambda path: False)
        res = ops.delete_file("/work/x.txt")
        assert res.error is None
        assert any(c.startswith("python -c") for c in seen), "python fallback expected"


# ===========================================================================
# move_file guards
# ===========================================================================

class TestMoveFile:
    def test_move_denied_for_protected_destination(self, monkeypatch):
        ops = _scripted_ops(lambda cmd: _FakeExec(0, ""))
        monkeypatch.setattr(
            file_operations, "_is_write_denied", lambda p: p == "/work/secret"
        )
        res = ops.move_file("/work/a", "/work/secret")
        assert res.error is not None and "protected path" in res.error

    def test_move_failure_surfaces_error(self, monkeypatch):
        ops = _scripted_ops(lambda cmd: _FakeExec(1, "mv: cannot stat 'a'"))
        monkeypatch.setattr(file_operations, "_is_write_denied", lambda p: False)
        res = ops.move_file("/work/a", "/work/b")
        assert res.error is not None and "Failed to move" in res.error


# ===========================================================================
# _suggest_similar_files scoring (real subprocess)
# ===========================================================================

class TestSuggestSimilarFiles:
    def test_same_basename_different_extension_scores_high(self, real_ops, tmp_path):
        (tmp_path / "config.yaml").write_text("a: 1\n")
        res = real_ops.read_file(str(tmp_path / "config.yml"))
        assert res.error is not None and "File not found" in res.error
        # config.yaml shares the basename -> top suggestion.
        assert res.similar_files
        assert res.similar_files[0].endswith("config.yaml")

    def test_unrelated_files_not_suggested(self, real_ops, tmp_path):
        (tmp_path / "zzz_unrelated.bin").write_text("x")
        res = real_ops.read_file(str(tmp_path / "myquery.py"))
        assert res.error is not None
        assert all("zzz_unrelated" not in s for s in res.similar_files)

    def test_scoring_prefers_substring_over_extension_only(self):
        # Drive _suggest_similar_files directly with a scripted ls listing so
        # the scoring ladder (substring 60 vs same-ext-overlap 30) is exercised
        # deterministically without filesystem noise.
        def handler(cmd):
            if cmd.startswith("ls -1"):
                return _FakeExec(0, "myhandler_old.py\nunrelated.py\n")
            return _FakeExec(0, "")

        ops = _scripted_ops(handler)
        res = ops._suggest_similar_files("/work/myhandler.py")
        assert res.similar_files
        # "myhandler" is a substring of "myhandler_old.py" (score 70/60),
        # which must rank above the same-extension-only "unrelated.py".
        assert res.similar_files[0].endswith("myhandler_old.py")


# ===========================================================================
# _expand_path (~ and ~user)
# ===========================================================================

class TestExpandPath:
    def test_bare_tilde_expands_to_home(self):
        def handler(cmd):
            if cmd == "echo $HOME":
                return _FakeExec(0, "/home/me\n")
            return _FakeExec(0, "")

        ops = _scripted_ops(handler)
        assert ops._expand_path("~") == "/home/me"

    def test_tilde_slash_prefix_expands(self):
        def handler(cmd):
            if cmd == "echo $HOME":
                return _FakeExec(0, "/home/me\n")
            return _FakeExec(0, "")

        ops = _scripted_ops(handler)
        assert ops._expand_path("~/docs/file.txt") == "/home/me/docs/file.txt"

    def test_tilde_user_expands_via_echo(self):
        def handler(cmd):
            if cmd == "echo $HOME":
                return _FakeExec(0, "/home/me\n")
            if cmd.startswith("echo ~bob"):
                return _FakeExec(0, "/home/bob\n")
            return _FakeExec(0, "")

        ops = _scripted_ops(handler)
        assert ops._expand_path("~bob/docs/x") == "/home/bob/docs/x"

    def test_empty_home_leaves_path_unexpanded(self):
        # echo $HOME returns empty -> the ~ branch can't resolve, path is
        # returned unchanged (no crash, no bogus prefix).
        def handler(cmd):
            if cmd == "echo $HOME":
                return _FakeExec(0, "")
            return _FakeExec(0, "")

        ops = _scripted_ops(handler)
        assert ops._expand_path("~/docs") == "~/docs"

    def test_empty_path_returned_as_is(self):
        ops = _scripted_ops(lambda cmd: _FakeExec(0, ""))
        assert ops._expand_path("") == ""

    def test_invalid_username_not_shell_expanded(self):
        # A username with shell-injection chars fails the validation regex, so
        # `echo ~user` is never run and the path is returned unchanged.
        calls = []

        def handler(cmd):
            calls.append(cmd)
            if cmd == "echo $HOME":
                return _FakeExec(0, "/home/me\n")
            return _FakeExec(0, "")

        ops = _scripted_ops(handler)
        result = ops._expand_path("~ev;il/x")
        assert result == "~ev;il/x"
        assert not any(c.startswith("echo ~ev") for c in calls)


# ===========================================================================
# patch_v4a dispatch
# ===========================================================================

class TestPatchV4ADispatch:
    def test_parse_error_is_surfaced(self):
        # A patch header with no hunks triggers a parser error before any
        # filesystem op — exercises the parse-error early return.
        ops = _scripted_ops(lambda cmd: _FakeExec(0, ""))
        res = ops.patch_v4a("*** Begin Patch\n*** Update File: x.py\n")
        assert res.success is False
        assert res.error is not None
        assert "Failed to parse patch" in res.error

    def test_valid_patch_dispatches_to_apply(self, real_ops, tmp_path):
        # End-to-end: a well-formed V4A update patch is parsed AND applied,
        # mutating the real file on disk.
        target = tmp_path / "greet.py"
        target.write_text("print('hi')\n")
        patch = (
            "*** Begin Patch\n"
            "*** Update File: greet.py\n"
            "@@\n"
            "-print('hi')\n"
            "+print('hello')\n"
            "*** End Patch\n"
        )
        res = real_ops.patch_v4a(patch)
        assert res.error is None, f"v4a apply failed: {res.error!r}"
        assert res.success is True
        assert "hello" in target.read_text()


# ===========================================================================
# BUG-016 — _check_lint {file} substitution (PINNED as non-reproduction)
# ===========================================================================

class TestBug016FilePlaceholderSubstitution:
    """BUG-016 (docs/known-bugs.md): the concern is that
    ``linter_cmd.replace("{file}", path)`` in ``_check_lint`` could
    *double-substitute* a path that literally contains the token ``{file}``.

    Investigated and PINNED as a NON-reproduction: Python's ``str.replace``
    replaces every occurrence of the FIRST argument in the ORIGINAL string and
    does NOT re-scan the replacement text. The linter templates each contain
    exactly one ``{file}`` placeholder, so a path whose own bytes contain the
    literal ``{file}`` is substituted exactly once and survives intact. These
    tests pin the CURRENT (correct) behavior so a future regression that
    introduced a re-scanning substitution would fail here.
    """

    def test_literal_file_token_in_path_is_not_double_substituted(self):
        # .js is a shell-linter extension, so _check_lint actually runs the
        # `linter_cmd.replace("{file}", escaped)` line (.py/.json/.yaml/.toml
        # short-circuit to in-process linters and never reach it).
        captured = {}

        def handler(cmd):
            captured["cmd"] = cmd
            return _FakeExec(0, "")

        ops = _scripted_ops(handler)
        ops._has_command = lambda cmd: True

        path = "/work/weird{file}name.js"
        escaped = ops._escape_shell_arg(path)
        ops._check_lint(path)

        final = captured["cmd"]
        # BUG-016 non-reproduction: the escaped path appears exactly once...
        assert final.count(escaped) == 1, final
        # ...and the path's literal {file} token is preserved verbatim (NOT
        # re-expanded into another copy of the escaped path).
        assert "weird{file}name.js" in final, final
        # Concretely, the command is the template with a single substitution.
        assert final == LINTERS[".js"].replace("{file}", escaped)

    def test_normal_path_substitutes_once(self):
        # Control: an ordinary path also substitutes exactly once.
        captured = {}

        def handler(cmd):
            captured["cmd"] = cmd
            return _FakeExec(0, "")

        ops = _scripted_ops(handler)
        ops._has_command = lambda cmd: True
        path = "/work/app.js"
        ops._check_lint(path)
        escaped = ops._escape_shell_arg(path)
        assert captured["cmd"].count(escaped) == 1
        assert "{file}" not in captured["cmd"]  # placeholder fully consumed
