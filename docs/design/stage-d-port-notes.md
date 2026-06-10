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
