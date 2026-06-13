"""Behavioral tests for the V4A patch parser's validate/apply FAILURE paths.

These complement ``test_vendored_patch_parser.py`` (which covers parse + the
happy-path apply) by exercising the *uncovered* failure and edge branches of
the two-phase ``apply_v4a_operations`` (validate-then-apply) and its helpers:

  * UPDATE context mismatch -> validation fails, **no** file is written
    (the two-phase guarantee).
  * MOVE whose destination already exists / whose source is missing -> error.
  * DELETE of a missing file -> error.
  * addition-only context hint that is ambiguous (2x) or absent (0x) -> error.
  * partial multi-op apply: op #1 succeeds, op #2 fails *during the apply
    phase* (it passes validation because ADD ops are not pre-checked). We pin
    the reported state — ``files_modified`` already lists op #1 — because the
    apply-phase docstring admits "state may be inconsistent".
  * the context-hint window-retry splice and the addition-only-ambiguous
    branch inside ``_apply_update`` — both unreachable through the public
    two-phase wrapper (validation gates them first), so driven directly.

Most tests drive a **real** ``ShellFileOperations`` over a ``LocalEnvironment``
rooted in ``tmp_path`` with real files on disk, so the assertions observe
genuine filesystem state (was the file written? did op #1's edit land?) rather
than a mock's recorded call. ``tests/conftest.py`` redirects ``TMPDIR`` off the
macOS sensitive prefix so the write-guard permits these temp writes.
"""

from types import SimpleNamespace

import pytest

from calfkit_tools.hermes._vendor.tools import patch_parser
from calfkit_tools.hermes._vendor.tools.patch_parser import (
    Hunk,
    HunkLine,
    OperationType,
    PatchOperation,
    apply_v4a_operations,
    parse_v4a_patch,
)
from calfkit_tools.hermes._vendor.tools.environments.local import LocalEnvironment
from calfkit_tools.hermes._vendor.tools.file_operations import ShellFileOperations


# --------------------------------------------------------------------------- #
# Fixtures: a real local file-ops backend rooted in tmp_path.                  #
# --------------------------------------------------------------------------- #


@pytest.fixture
def workdir(tmp_path):
    """A real working directory for on-disk file operations."""
    return tmp_path


@pytest.fixture
def ops(workdir):
    """ShellFileOperations wired to a real LocalEnvironment in tmp_path.

    Exercises the real read_file_raw / write_file / delete_file / move_file
    (and their real error reporting) against actual files — no SimpleNamespace
    stand-ins — so the two-phase guarantee is observed against the filesystem.
    """
    env = LocalEnvironment(cwd=str(workdir), timeout=30)
    return ShellFileOperations(env, cwd=str(workdir))


def _apply(patch_text, ops):
    """Parse + apply a V4A patch, asserting the parse itself succeeded."""
    operations, err = parse_v4a_patch(patch_text)
    assert err is None, f"unexpected parse error: {err}"
    return apply_v4a_operations(operations, ops)


# --------------------------------------------------------------------------- #
# UPDATE context mismatch -> validation fails, nothing written.               #
# --------------------------------------------------------------------------- #


class TestUpdateContextMismatchIsTwoPhaseSafe:
    """An UPDATE whose context can't be matched must fail in phase 1 (validate)
    and leave the target file byte-for-byte unchanged."""

    def test_unmatched_context_writes_nothing(self, ops, workdir):
        original = "def foo():\n    return 1\n"
        target = workdir / "m.py"
        target.write_text(original)

        # The removed (`-`) line is the strongest anchor in the search
        # pattern; make it (and the context) genuinely absent so no fuzzy
        # strategy — including the 50%-similarity context-aware one — can
        # match. (A near-miss like keeping `return 1` as the `-` line would
        # let context_aware splice, which is a *match*, not a mismatch.)
        patch = """\
*** Begin Patch
*** Update File: m.py
 def completely_unrelated_function_xyz():
-    raise NeverHeardOfThisError("nope nope nope")
+    return 2
*** End Patch"""
        result = _apply(patch, ops)

        assert result.success is False
        assert "validation failed" in result.error.lower()
        assert "no files were modified" in result.error.lower()
        # Two-phase guarantee: the apply phase never ran, file untouched.
        assert target.read_text() == original
        assert result.files_modified == []

    def test_second_op_failure_rolls_back_whole_patch(self, ops, workdir):
        """If op #2 fails validation, op #1 (which would have succeeded) must
        NOT have been written — validation is all-or-nothing before apply."""
        good = workdir / "good.py"
        bad = workdir / "bad.py"
        good.write_text("def good():\n    return 1\n")
        bad.write_text("totally unrelated content\n")

        patch = """\
*** Begin Patch
*** Update File: good.py
 def good():
-    return 1
+    return 2
*** Update File: bad.py
 some context that is not present anywhere
-raise NeverHeardOfThisError("absent removed line")
+new
*** End Patch"""
        result = _apply(patch, ops)

        assert result.success is False
        assert "validation failed" in result.error.lower()
        # The *valid* op #1 must not have been applied.
        assert good.read_text() == "def good():\n    return 1\n"
        assert bad.read_text() == "totally unrelated content\n"


# --------------------------------------------------------------------------- #
# Context-hint window-retry splice (driven directly).                         #
# --------------------------------------------------------------------------- #


class TestContextHintWindowRetry:
    """``_apply_update`` falls back to a ±window splice around the context hint
    when the global fuzzy match is ambiguous.

    This branch is unreachable through ``apply_v4a_operations``: ``_validate_
    operations`` runs the same *global* ``fuzzy_find_and_replace``, so an
    ambiguous hunk fails validation and the apply phase (where the window-retry
    lives) never executes. We therefore drive ``_apply_update`` directly with
    a content shaped so the search pattern is globally ambiguous (appears
    twice) but the hint window contains exactly one occurrence.
    """

    def test_ambiguous_global_match_resolved_within_hint_window(self):
        # Two identical "    TARGET = 1" lines. They are >2000 chars apart so
        # the ±(−500, +2000) window around 'def alpha' captures only the first.
        filler = "\n".join(f"filler_line_number_{i:04d}" for i in range(300))
        content = (
            "def alpha():\n"
            "    TARGET = 1\n"
            "    return TARGET\n"
            + filler + "\n"
            "def beta():\n"
            "    TARGET = 1\n"
            "    return TARGET\n"
        )

        op = PatchOperation(
            operation=OperationType.UPDATE,
            file_path="m.py",
            hunks=[
                Hunk(
                    context_hint="def alpha",
                    lines=[
                        HunkLine("-", "    TARGET = 1"),
                        HunkLine("+", "    TARGET = 2"),
                    ],
                )
            ],
        )

        written = {}

        class Ops:
            def read_file_raw(self, path):
                return SimpleNamespace(content=content, error=None)

            def write_file(self, path, value):
                written["content"] = value
                return SimpleNamespace(error=None, lsp_diagnostics=None)

        ok, diff, _lsp = patch_parser._apply_update(op, Ops())

        assert ok is True, f"window-retry should have spliced the hit: {diff}"
        new_content = written["content"]
        # Only the occurrence inside the hint window was replaced.
        assert new_content.count("TARGET = 2") == 1
        assert new_content.count("TARGET = 1") == 1
        # And it was specifically the one under 'def alpha'.
        assert "def alpha():\n    TARGET = 2\n    return TARGET" in new_content


# --------------------------------------------------------------------------- #
# Addition-only context hint: ambiguous / missing.                            #
# --------------------------------------------------------------------------- #


class TestAdditionOnlyHintValidation:
    """An addition-only hunk (only ``+`` lines) carries no removed/context
    lines to anchor on, so it relies entirely on the context hint. Validation
    rejects a hint that is ambiguous (>1) or absent (0)."""

    def test_ambiguous_hint_fails_validation_no_write(self, ops, workdir):
        original = "MARK\nx = 1\nMARK\ny = 2\n"
        target = workdir / "amb.py"
        target.write_text(original)

        patch = """\
*** Begin Patch
*** Update File: amb.py
@@ MARK @@
+inserted = True
*** End Patch"""
        result = _apply(patch, ops)

        assert result.success is False
        assert "ambiguous" in result.error.lower()
        assert "2 occurrences" in result.error
        assert target.read_text() == original  # nothing written

    def test_missing_hint_fails_validation_no_write(self, ops, workdir):
        original = "nothing relevant here\n"
        target = workdir / "miss.py"
        target.write_text(original)

        patch = """\
*** Begin Patch
*** Update File: miss.py
@@ NONEXISTENT_HINT @@
+added = 1
*** End Patch"""
        result = _apply(patch, ops)

        assert result.success is False
        assert "not found" in result.error.lower()
        assert "NONEXISTENT_HINT" in result.error
        assert target.read_text() == original  # nothing written

    def test_apply_update_ambiguous_hint_direct(self):
        """The ambiguous-hint branch *inside* ``_apply_update`` is gated out by
        validation (which fails ambiguous hints first), so drive it directly to
        pin the in-apply error message and the no-write behavior."""
        op = PatchOperation(
            operation=OperationType.UPDATE,
            file_path="x.py",
            hunks=[Hunk(context_hint="MARK", lines=[HunkLine("+", "ins = 1")])],
        )

        write_calls = []

        class Ops:
            def read_file_raw(self, path):
                return SimpleNamespace(content="MARK\na\nMARK\nb\n", error=None)

            def write_file(self, path, value):
                write_calls.append(value)
                return SimpleNamespace(error=None)

        ok, msg, _lsp = patch_parser._apply_update(op, Ops())

        assert ok is False
        assert "ambiguous" in msg.lower()
        assert "2 occurrences" in msg
        # On a failed hunk, _apply_update returns before write_file.
        assert write_calls == []


# --------------------------------------------------------------------------- #
# MOVE failures.                                                              #
# --------------------------------------------------------------------------- #


class TestMoveValidation:
    def test_move_onto_existing_destination_errors(self, ops, workdir):
        src = workdir / "src.py"
        dst = workdir / "dst.py"
        src.write_text("source body\n")
        dst.write_text("EXISTING destination\n")

        patch = """\
*** Begin Patch
*** Move File: src.py -> dst.py
*** End Patch"""
        result = _apply(patch, ops)

        assert result.success is False
        assert "already exists" in result.error.lower()
        # Validation blocks the move: source stays, destination is untouched.
        assert src.exists()
        assert src.read_text() == "source body\n"
        assert dst.read_text() == "EXISTING destination\n"

    def test_move_missing_source_errors(self, ops, workdir):
        patch = """\
*** Begin Patch
*** Move File: ghost.py -> out.py
*** End Patch"""
        result = _apply(patch, ops)

        assert result.success is False
        assert "source file not found" in result.error.lower()
        assert not (workdir / "out.py").exists()


# --------------------------------------------------------------------------- #
# DELETE of a missing file.                                                   #
# --------------------------------------------------------------------------- #


class TestDeleteValidation:
    def test_delete_missing_file_errors(self, ops, workdir):
        patch = """\
*** Begin Patch
*** Delete File: not_here.py
*** End Patch"""
        result = _apply(patch, ops)

        assert result.success is False
        assert "not found for deletion" in result.error.lower()
        assert result.files_deleted == []


# --------------------------------------------------------------------------- #
# UPDATE on a missing file.                                                   #
# --------------------------------------------------------------------------- #


class TestUpdateMissingFile:
    def test_update_missing_file_fails_validation(self, ops, workdir):
        patch = """\
*** Begin Patch
*** Update File: absent.py
 ctx
-old
+new
*** End Patch"""
        result = _apply(patch, ops)

        assert result.success is False
        assert "validation failed" in result.error.lower()
        assert "absent.py" in result.error
        assert not (workdir / "absent.py").exists()


# --------------------------------------------------------------------------- #
# Partial multi-op apply: op #1 lands, op #2 fails during the apply phase.    #
# --------------------------------------------------------------------------- #


class TestPartialApplyPhaseFailure:
    """ADD operations are not pre-checked in phase 1 (parent dirs are created
    at write time), so an ADD that fails *at write time* slips past validation
    and fails during the apply phase — after an earlier op has already been
    written. We pin the reported state: the apply-phase error is surfaced and
    ``files_modified`` reflects the partial change, matching the docstring's
    "state may be inconsistent" admission.
    """

    def test_op1_applies_then_op2_add_fails_at_write(self, ops, workdir):
        existing = workdir / "existing.py"
        existing.write_text("def foo():\n    return 1\n")
        # 'blocker' is a regular FILE; writing 'blocker/child.py' must fail
        # because a directory cannot be created under an existing file. This
        # keeps the failing write entirely inside tmp_path (no sensitive path).
        (workdir / "blocker").write_text("i am a file, not a directory\n")

        patch = """\
*** Begin Patch
*** Update File: existing.py
 def foo():
-    return 1
+    return 2
*** Add File: blocker/child.py
+payload = True
*** End Patch"""
        result = _apply(patch, ops)

        assert result.success is False
        # Distinct from validation failure: this is the apply-phase branch.
        assert "apply phase failed" in result.error.lower()
        assert "git diff" in result.error.lower()
        assert "blocker/child.py" in result.error

        # Pin the inconsistent state: op #1 DID apply and is reported.
        assert "existing.py" in result.files_modified
        assert existing.read_text() == "def foo():\n    return 2\n"
        # op #2 produced nothing on disk.
        assert not (workdir / "blocker" / "child.py").exists()
        assert (workdir / "blocker").is_file()
        assert "blocker/child.py" not in result.files_created
