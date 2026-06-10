"""Shell tool nodes: ``terminal`` and ``process``.

Two 1:1 calfkit tool nodes over the vendored hermes shell tools (design
docs/design/node-port.md §3). Each wrapper mirrors its upstream JSON schema
(``TERMINAL_SCHEMA`` / ``PROCESS_SCHEMA``) and drives the tool wholesale through
the ``dispatch`` seam — no reaching into vendor internals. Tenancy (ADR-0004):
``session_key(ctx)`` is fed to hermes as its ``task_id``, so the vendored
per-``task_id`` terminal-environment and process registries provide isolation.

Sync by design: hermes handlers block; calfkit runs sync tools in its thread
pool, and hermes' own locks make the seam thread-safe.
"""

from __future__ import annotations

from typing import Literal

from calfkit import ToolContext, agent_tool

from calfkit_hermes.node._runtime import dispatch, session_key


@agent_tool
def terminal(
    ctx: ToolContext,
    command: str,
    background: bool = False,
    timeout: int | None = None,
    workdir: str | None = None,
    pty: bool = False,
    notify_on_complete: bool = False,
    watch_patterns: list[str] | None = None,
) -> dict | str:
    """Execute a shell command in the session's terminal environment.

    Args:
        command: The command to execute on the VM.
        background: Run the command in the background. Almost always pair with
            notify_on_complete=true — without it, the process runs silently and
            you'll have no way to learn it finished short of calling
            process(action='poll') yourself (easy to forget, leading to silent
            blindness on long jobs). Two legitimate patterns: (1) Long-lived
            processes that never exit (servers, watchers, daemons) — these stay
            silent because there's no exit to notify on. (2) Long-running bounded
            tasks (tests, builds, deploys, CI pollers, batch jobs) — these MUST
            set notify_on_complete=true. For short commands, prefer foreground
            with a generous timeout instead.
        timeout: Max seconds to wait (default: 180; foreground has a hard cap).
            Returns INSTANTLY when the command finishes — set high for long
            tasks, you won't wait unnecessarily. A foreground timeout above the
            cap is rejected; use background=true for longer commands.
        workdir: Working directory for this command (absolute path). Defaults to
            the session working directory.
        pty: Run in pseudo-terminal (PTY) mode for interactive CLI tools like
            Codex, Claude Code, or Python REPL. Only works with local and SSH
            backends. Default: false.
        notify_on_complete: When true (and background=true), you'll be
            automatically notified exactly once when the process finishes. This
            is the right choice for almost every long-running task — tests,
            builds, deployments, multi-item batch jobs, anything that takes over
            a minute and has a defined end. Use this and keep working on other
            things; the system notifies you on exit. MUTUALLY EXCLUSIVE with
            watch_patterns — when both are set, watch_patterns is dropped.
        watch_patterns: Strings to watch for in background process output. HARD
            RATE LIMIT: at most 1 notification per 15 seconds per process —
            matches arriving inside the cooldown are dropped. After 3
            consecutive 15-second windows with dropped matches, watch_patterns
            is automatically disabled for that process and promoted to
            notify_on_complete behavior. USE ONLY for truly rare, one-shot
            mid-process signals on LONG-LIVED processes that will never exit on
            their own — e.g. ['Application startup complete'] on a server.
            MUTUALLY EXCLUSIVE with notify_on_complete — set one, not both.
    """
    return dispatch(
        "terminal",
        {
            "command": command,
            "background": background,
            "timeout": timeout,
            "workdir": workdir,
            "pty": pty,
            "notify_on_complete": notify_on_complete,
            "watch_patterns": watch_patterns,
        },
        session_key=session_key(ctx),
    )


@agent_tool
def process(
    ctx: ToolContext,
    action: Literal["list", "poll", "log", "wait", "kill", "write", "submit", "close"],
    session_id: str | None = None,
    data: str | None = None,
    timeout: int | None = None,
    offset: int | None = None,
    limit: int | None = None,
) -> dict | str:
    """Manage background processes started with terminal(background=true).

    Args:
        action: Action to perform on background processes. One of 'list' (show
            all), 'poll' (check status + new output), 'log' (full output with
            pagination), 'wait' (block until done or timeout), 'kill'
            (terminate), 'write' (send raw stdin data without newline), 'submit'
            (send data + Enter, for answering prompts), 'close' (close
            stdin/send EOF).
        session_id: Process session ID (from terminal background output).
            Required for all actions except 'list'. This is the upstream
            background-process handle — unrelated to the node's tenancy scope.
        data: Text to send to process stdin (for 'write' and 'submit' actions).
        timeout: Max seconds to block for 'wait' action. Returns partial output
            on timeout.
        offset: Line offset for 'log' action (default: last 200 lines).
        limit: Max lines to return for 'log' action.
    """
    return dispatch(
        "process",
        {
            "action": action,
            "session_id": session_id,
            "data": data,
            "timeout": timeout,
            "offset": offset,
            "limit": limit,
        },
        session_key=session_key(ctx),
    )
