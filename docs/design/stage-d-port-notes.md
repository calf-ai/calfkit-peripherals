# Stage D port — handoff notes for the maintainer

Running log of issues found and decisions taken during the autonomous Stage-D
implementation (2026-06-10). Posted as a PR comment at the end per request.

## Found & fixed during the port

1. **Shell/process isolation gap (the big one).** ADR-0004's premise — "hermes'
   per-`task_id` registries provide the isolation" — was false for the local
   backend: `_resolve_container_task_id` deliberately collapses every
   unregistered `task_id` to `"default"`, so all sessions shared one
   `LocalEnvironment` and `process(action="list")` could never see the calling
   agent's own background processes. Surfaced by strict-xfail TDD tests, fixed
   with **upstream's own seam** (`register_task_env_overrides(session_key, {})`
   — documented for infrastructure needing per-task isolated sandboxes), no
   vendor patch. ADR-0004 carries the amendment; `_runtime._ensure_session_isolated`
   is the fix; the former xfails now pass as regular tenancy tests.

2. **Pre-existing macOS test failure fixed.**
   `test_functional_smoke.py::test_file_tools_write_read_patch_round_trip`
   failed on macOS only: pytest's `tmp_path` lives under `/private/var/...`,
   which the vendored sensitive-path guard refuses to write to. The test now
   uses `tempfile.mkdtemp(dir="/tmp")`. This was failing on `main` before the
   port; it is unrelated to the node layer.

## Deliberate divergences / decisions to be aware of

3. **`patch` node schema:** upstream's hand-written `PATCH_SCHEMA` lists
   `required: ["mode"]`, but upstream's own handler defaults `mode` to
   `"replace"`. The node mirrors the *behavior* (default `"replace"`), so the
   derived schema has no required params — the one schema field that isn't a
   verbatim mirror. Pinned + explained in `tests/test_node_files.py`.

4. **`CONTEXT.md` is locally git-excluded** (`.git/info/exclude`), so the
   glossary updates made during the design grill (Session entry rewritten to
   agent-lifetime-by-default; new *Deps (wiring)* / *Resource (wiring)* terms)
   exist only in the local working copy and cannot ride the PR. Full updated
   text reproduced in the PR comment so it isn't lost.

5. **Naming collision documented, kept:** the `process` tool's `session_id`
   argument is upstream's background-process handle (`proc_*`), unrelated to
   the tenancy `deps["session_id"]`. Kept the upstream name (schema fidelity);
   both the glossary and NODE.md call it out.

6. **Result contract:** hermes handlers return JSON *strings*; the node layer
   parses them so agents receive structured dicts (raw string passthrough if
   unparseable). Content is byte-identical to upstream's intent, framing is
   calfkit-native.

## Review rounds (per-tool, converged) — what they found and what was done

10. **Round 1 (5 parallel scoped reviews):** web_fetch / files / todo / web
    CONVERGED on round 1; shell NEEDS-ROUND-2. The shell review found one
    CRITICAL the TDD suite had missed: wrappers forwarded explicit `None` for
    omitted params, and upstream's `args.get("offset", 0)`-style defaults were
    clobbered by present-`None` keys — `process(action="log")` with default
    pagination crashed with a TypeError. Fixed at the seam: `dispatch()` now
    drops `None`-valued args (absent ≡ None for every upstream handler), with
    runtime + functional `log` tests.

11. **Cross-tenant process-handle gap (review MAJOR, fixed):** upstream scopes
    only `process list` by task_id; poll/log/wait/kill/write/submit/close look
    up by the globally-unique `proc_*` handle, so a tenant who learned another
    tenant's handle could control its process. The node now runs an ownership
    guard (a `list` scoped to the caller's `session_key` — verified upstream
    includes finished processes) before any handle action and denies foreign
    handles with upstream's own not-found shape. The 64-entry global
    `MAX_PROCESSES` LRU remains an accepted, documented limitation.

12. **Fail-closed hardening from reviews:** `session_key()` now raises on a
    missing `agent_name` instead of silently merging unstamped callers into
    one bucket; `execute_code` refuses (error reply) when
    `EXECUTE_CODE_ENABLED_TOOLS` resolves to zero sandbox-callable tools —
    upstream's behavior for an empty intersection is to fall back to the FULL
    tool set, so a typo'd restrictive override would have silently expanded
    the sandbox.

13. **Todo strictness decision (recorded):** the node's `TodoItem` schema makes
    calfkit reject partial items (missing id/content/status) that upstream's
    `_validate` would have salvaged (`id="?"`, `status="pending"`, …). Decided
    to KEEP the strict schema: it mirrors upstream's *advertised* schema
    (TODO_SCHEMA marks all three required), and calfkit's `FailedToolCall`
    gives the model a structured retry. Pinned with tests + docstring note.

14. **Schema-bounds fidelity restored:** upstream's hand-written numeric bounds
    (`terminal.timeout ge 1`, `process.timeout/limit ge 1`, `read_file.offset
    ge 1` / `limit le 2000`) are now mirrored via `Annotated[..., Field(...)]`
    — verified calfkit propagates them into the LLM-facing JSON schema. Only
    bounds upstream actually declares were added.

15. **execute_code review extras:** in-script tool-call isolation re-verified
    end-to-end (task_id is server-side in the RPC loop — scripts cannot inject
    a foreign session); the in-script allow-list is enforced server-side (not
    just stub generation); `web_search`/`web_extract` in the env override are
    stub-generatable but dispatch-dead (not registry-bridged) — documented in
    NODE.md. Script-body cwd is the node's, not the session's (docstring
    corrected); in-script *tool calls* are session-scoped.

## Known limitations (accepted, by your in-memory decision)

7. **Stateful nodes are single-process.** calfkit partitions by correlation id
   (not session), so horizontally scaling a stateful node would scatter
   sessions across processes. One process per stateful node is the supported
   deployment; stateless nodes (`web_fetch`, `web_search`, `web_extract`)
   scale freely.

8. **Unbounded (tiny) in-memory growth per session key:** the override
   registry (`_task_env_overrides`: one `key: {}` entry per session) and the
   todo store grow monotonically with distinct session keys; shell
   environments themselves ARE evicted after `TERMINAL_LIFETIME_SECONDS`
   (default 300s) idle. Bytes-per-session is trivial; eviction policy deferred
   with the durable Store work (ADR-0003/0004).

9. **Security hardening deferred** (per shell-file design doc §6.1, unchanged):
   no env allowlist scrubbing of the node process env into shells, no sandbox
   enforcement, no per-tenant FS confinement. `terminal`/`execute_code` mean
   arbitrary code execution on the node host — deploy trusted/sandboxed.
   NODE.md documents the trust model.
