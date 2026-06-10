"""File tool nodes: read_file, write_file, patch, search_files.

Each node is a thin 1:1 wrapper over a vendored hermes file tool, driven
wholesale through the ``registry.dispatch`` seam (``node/_runtime.py``) — never
through vendor internals. Parameters mirror the upstream JSON schemas the tools
registered for the LLM (``file_tools.py``: ``READ_FILE_SCHEMA``,
``WRITE_FILE_SCHEMA``, ``PATCH_SCHEMA``, ``SEARCH_FILES_SCHEMA``), minus the
injected ``task_id``, with a leading ``ToolContext``. Tenancy (ADR-0004): the
per-caller ``session_key`` is fed to hermes as its ``task_id``, so the vendored
per-``task_id`` file trackers provide isolation. See docs/design/node-port.md §3.
"""

from __future__ import annotations

from typing import Literal

from calfkit import ToolContext, agent_tool

from ._runtime import dispatch, session_key


@agent_tool
def read_file(ctx: ToolContext, path: str, offset: int = 1, limit: int = 500) -> dict:
    """Read a text file with line numbers and pagination.

    Use this instead of cat/head/tail in terminal. Output format:
    'LINE_NUM|CONTENT'. Suggests similar filenames if not found. Use offset and
    limit for large files. Reads exceeding ~100K characters are rejected; use
    offset and limit to read specific sections of large files. NOTE: Cannot read
    images or binary files — use vision_analyze for images.

    Args:
        path: Path to the file to read (absolute, relative, or ~/path).
        offset: Line number to start reading from (1-indexed, default: 1).
        limit: Maximum number of lines to read (default: 500, max: 2000).
    """
    return dispatch(
        "read_file",
        {"path": path, "offset": offset, "limit": limit},
        session_key=session_key(ctx),
    )


@agent_tool
def write_file(
    ctx: ToolContext, path: str, content: str, cross_profile: bool = False
) -> dict:
    """Write content to a file, completely replacing existing content.

    Use this instead of echo/cat heredoc in terminal. Creates parent directories
    automatically. OVERWRITES the entire file — use 'patch' for targeted edits.
    Auto-runs syntax checks on .py/.json/.yaml/.toml and other linted languages;
    only NEW errors introduced by this write are surfaced (pre-existing errors
    are filtered out).

    Args:
        path: Path to the file to write (will be created if it doesn't exist,
            overwritten if it does).
        content: Complete content to write to the file.
        cross_profile: Opt out of the cross-profile soft guard. Defaults to
            false. Set true ONLY after explicit user direction to edit another
            Hermes profile's skills/plugins/cron/memories — by default these
            writes are blocked with a warning because they affect a different
            profile than the one this session is running under.
    """
    return dispatch(
        "write_file",
        {"path": path, "content": content, "cross_profile": cross_profile},
        session_key=session_key(ctx),
    )


@agent_tool
def patch(
    ctx: ToolContext,
    mode: Literal["replace", "patch"] = "replace",
    path: str | None = None,
    old_string: str | None = None,
    new_string: str | None = None,
    replace_all: bool = False,
    patch: str | None = None,
    cross_profile: bool = False,
) -> dict:
    """Targeted find-and-replace edits in files.

    Use this instead of sed/awk in terminal. Uses fuzzy matching (9 strategies)
    so minor whitespace/indentation differences won't break it. Returns a
    unified diff. Auto-runs syntax checks after editing.

    REPLACE MODE (mode='replace', default): find a unique string and replace it.
    REQUIRED PARAMETERS: mode, path, old_string, new_string.
    PATCH MODE (mode='patch'): apply V4A multi-file patches for bulk changes.
    REQUIRED PARAMETERS: mode, patch.

    Args:
        mode: Edit mode. 'replace' (default): requires path + old_string +
            new_string. 'patch': requires patch content only.
        path: REQUIRED when mode='replace'. File path to edit.
        old_string: REQUIRED when mode='replace'. Exact text to find and replace.
            Must be unique in the file unless replace_all=true. Include
            surrounding context lines to ensure uniqueness.
        new_string: REQUIRED when mode='replace'. Replacement text. Pass empty
            string '' to delete the matched text.
        replace_all: Replace all occurrences instead of requiring a unique match
            (default: false).
        patch: REQUIRED when mode='patch'. V4A format patch content. Format:
            *** Begin Patch
            *** Update File: path/to/file
            @@ context hint @@
             context line
            -removed line
            +added line
            *** End Patch
        cross_profile: Opt out of the cross-profile soft guard. Defaults to
            false. Set true ONLY after explicit user direction to edit another
            Hermes profile's skills/plugins/cron/memories.
    """
    return dispatch(
        "patch",
        {
            "mode": mode,
            "path": path,
            "old_string": old_string,
            "new_string": new_string,
            "replace_all": replace_all,
            "patch": patch,
            "cross_profile": cross_profile,
        },
        session_key=session_key(ctx),
    )


@agent_tool
def search_files(
    ctx: ToolContext,
    pattern: str,
    target: Literal["content", "files"] = "content",
    path: str = ".",
    file_glob: str | None = None,
    limit: int = 50,
    offset: int = 0,
    output_mode: Literal["content", "files_only", "count"] = "content",
    context: int = 0,
) -> dict:
    """Search file contents or find files by name.

    Use this instead of grep/rg/find/ls in terminal. Ripgrep-backed, faster than
    shell equivalents.

    Content search (target='content'): Regex search inside files. Output modes:
    full matches with line numbers, file paths only, or match counts.

    File search (target='files'): Find files by glob pattern (e.g., '*.py',
    '*config*'). Also use this instead of ls — results sorted by modification
    time.

    Args:
        pattern: Regex pattern for content search, or glob pattern (e.g., '*.py')
            for file search.
        target: 'content' searches inside file contents, 'files' searches for
            files by name. One of: 'content', 'files'.
        path: Directory or file to search in (default: current working
            directory).
        file_glob: Filter files by pattern in grep mode (e.g., '*.py' to only
            search Python files).
        limit: Maximum number of results to return (default: 50).
        offset: Skip first N results for pagination (default: 0).
        output_mode: Output format for grep mode: 'content' shows matching lines
            with line numbers, 'files_only' lists file paths, 'count' shows match
            counts per file. One of: 'content', 'files_only', 'count'.
        context: Number of context lines before and after each match (grep mode
            only).
    """
    return dispatch(
        "search_files",
        {
            "pattern": pattern,
            "target": target,
            "path": path,
            "file_glob": file_glob,
            "limit": limit,
            "offset": offset,
            "output_mode": output_mode,
            "context": context,
        },
        session_key=session_key(ctx),
    )
