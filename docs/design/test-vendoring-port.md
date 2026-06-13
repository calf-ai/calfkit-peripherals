# Test-suite vendoring port plan (hermes-agent `tests/tools/` → calfkit-peripherals)

**Status:** PLAN — no test files vendored yet; implementation gated on phase decisions below.
**Upstream pin:** `NousResearch/hermes-agent @ 5a36f76a00cc448948856a5c1b52710aafec264e` (same revision + MIT
license as the already-vendored modules — no new licensing/provenance surface).
**Method:** every file referenced here was fetched at the pin and its imports/patch-targets/fixtures verified
against the vendored tree `src/calfkit_tools/hermes/_vendor/` and shims `…/_shims/`. Counts are research-pass
estimates to be confirmed as each file is actually ported and run.

---

## 1. Goal

Raise real test coverage of the vendored hermes tree (currently **~39%**, dragging the repo coverage badge to
40%/red) by vendoring upstream's existing `tests/tools/` suite, true to the repo's **vendor-over-handroll**
philosophy. Upstream ships a dedicated test file for almost every module we vendored; today we vendor only 3
of them (`test_vendored_fuzzy_match`, `test_vendored_patch_parser`, `test_vendored_todo_tool`). There are well
over **a thousand** additional upstream tests targeting modules we already ship.

## 2. Why these tests are *not* a uniform drop-in

The 3 tests vendored so far were the easy ones: pure-logic, zero `mock.patch("tools…")` string targets, no
module-global state, no shim imports. The rest hit four frictions — each a known, bounded piece of wiring:

1. **Import rewrite (free, automated).** `vendor/hermes/scripts/rewrite_imports.py` already rewrites real
   `import`/`from … import` statement lines: `tools.* / agent.* / hermes_constants / utils` →
   `calfkit_tools.hermes._vendor.*`, and app-runtime edges (`hermes_cli.* / gateway.* / model_tools /
   agent.auxiliary_client / agent.lsp.*`) → `calfkit_tools.hermes._shims.*`.

2. **String-target patch rewrite (the dominant manual burden).** The rewriter is AST-import-only by design
   (keeps provenance "verbatim + mechanical import rewrite"). It does **not** touch string literals, so
   `unittest.mock.patch("tools.file_tools._get_file_ops")`, `monkeypatch.setattr("tools.…", …)`,
   `caplog … logger="tools.…"`, `sys.modules["tools.…"]`, and `spec_from_file_location(… "tools"/…)` are left
   pointing at non-existent module paths. This is the "string-target patch rewrite" the shell/file port doc
   already named as deferred work. Across the candidate suite there are **~500 such occurrences**, but they are
   mechanical and concentrated (tirith ~224; the `file_tools._get_file_ops` family ~100; foreground-timeout 38;
   file_read_guards 31; process_registry 18; read_loop 20). See §4a for the tooling that clears them in one pass.

3. **In-process state isolation (the keystone risk).** Upstream runs **one subprocess per test file**
   (`scripts/run_tests_parallel.py`), so module-global state can leak freely within a file but never across
   files. calfkit runs a single in-process `uv run pytest`, so files that mutate module singletons contaminate
   each other. Upstream's own history records this exact failure (`test_command_guards` flaking because
   `approval._session_approved` carried across files). The globals at risk: `tools/registry.py::registry`,
   `tools/file_state.py::_registry`, `agent/web_search_registry.py::_providers`, `tools/approval.py` session
   dicts/ContextVars, and the `tool_output_limits` / `url_safety` caches. Mitigation in §4b.

4. **Behavioral divergence vs our shims + local fix-ups.** A handful of tests assert upstream app-runtime
   behavior that we intentionally changed: empty shim config readers (`{}`), disabled gateway session-env
   injection (`_VAR_MAP={}`), the `registry.py` importlib-root fix-up, and the `HERMES_HOME` tempdir prefix.
   These need small assertion reconciliations (§5), not wholesale rewrites.

## 3. Inventory & classification

~80 candidate files were triaged into four tiers (deduped across domains; each file appears once). Two files
in the original wishlist **do not exist** at this pin (`test_binary_extensions.py`, `test_thread_context.py`),
and `test_approval_heartbeat.py` is a **stub with zero `test_` methods** at this pin.

| Tier | Files | ~Tests | Per-file manual effort |
|---|---|---|---|
| **DROP_IN** — verbatim + automated import rewrite only | ~25 | ~415 | none (place file, run rewriter) |
| **EASY** — import rewrite + mechanical string-target rewrite (companion script) | ~26 | ~850 | seconds (one script pass); occasional 1 shim symbol |
| **MODERATE** — behavior reconciliation, real-subprocess, or structural loader rebuild | ~13 | ~330 | minutes–hours per file |
| **SKIP** — CUT-module deps / live services / non-existent / stub / pure-duplicate | ~15 | — | excluded |

Headline: **DROP_IN + EASY alone ≈ 1,265 tests across ~51 files** with near-mechanical effort, once the Tier-0
wiring (§4) exists.

### Tier DROP_IN (no shim/global-state friction; zero or one trivial edit)
`test_ansi_strip` (30), `test_lazy_deps` (36, pip mocked — no network), `test_accretion_caps` (9),
`test_terminal_tool` (14), `test_terminal_tool_pty_fallback` (3), `test_terminal_compound_background` (34),
`test_terminal_exit_semantics` (22), `test_terminal_none_command_guard` (2), `test_terminal_task_cwd` (10),
`test_threaded_process_handle` (12), `test_local_env_windows_msys` (10), `test_file_operations` (55),
`test_file_operations_edge_cases` (30), `test_file_ops_cwd_tracking` (5), `test_file_tools_cwd_resolution` (13),
`test_resolve_path` (6), `test_hidden_dir_filter` (12, no hermes dep), `test_symlink_prefix_confusion` (8, no
hermes dep), `test_init_session_cwd_respect` (4), `test_gateway_cwd_contract` (4, name is misleading — no
gateway dep), `test_line_ending_preservation` (12), `test_force_dangerous_override` (8, no hermes dep),
`test_skill_view_path_check` (6, no hermes dep), `test_code_execution_windows_env` (24, 3 win-only auto-skip),
`test_file_write_safety` (30, real shell in one class).

### Tier EASY (companion string-rewrite pass; ⭐ = high leverage)
⭐`test_approval` (203 tests / **3** string edits), `test_process_registry` (66/18),
`test_url_safety` (76/8 + 3 async-convert — **duplicate decision, §6**), `test_file_tools` (50/35),
`test_file_read_guards` (40/31), `test_credential_files` (33/0), `test_file_state_registry` (30/few),
`test_watch_patterns` (30/2), `test_read_loop_detection` (25/20), `test_execute_code_approval_cluster` (20/2),
`test_budget_config` (20/2), `test_write_deny` (20/0), `test_hardline_blocklist` (18/0),
`test_command_guards` (18/1), `test_env_passthrough` (17/0), `test_file_staleness` (14/10),
`test_local_shell_init` (14/9, 2 real-bash), `test_tool_output_limits` (13/11 + shim `DEFAULT_CONFIG`),
`test_search_error_guard` (13/0, live shell), `test_parse_env_var` (12/1),
`test_terminal_foreground_timeout_cap` (11/38), `test_terminal_requirements` (9/2),
`test_local_env_cwd_recovery` (9/4), `test_file_tools_container_config` (4/10),
`test_approval_plugin_hooks` (3/3), `test_local_tempdir` (3/3).

### Tier MODERATE (reconciliation / real-subprocess / structural)
- `test_tirith_security` (92) — ~224 *mechanical* string rewrites (bulk sed) + **1** assertion edit
  (`HERMES_HOME` prefix). Fully mocked — no network or real `tirith` binary.
- `test_code_execution` (67) — 14 string rewrites + drop one CUT `import tools.web_tools` line; spawns real
  child processes. PYTHONPATH/`__file__` fix-up verified benign (in-sandbox `import hermes_constants` resolves
  under `_vendor`).
- `test_code_execution_modes` (36) — 10 string rewrites; real children; 2 POSIX-only tests auto-skip on Windows.
- `test_registry` (31) — 3 `importlib` string rewrites **+ 3 discovery tests reconcile**: the `registry.py`
  importlib-root fix-up makes discovered names `calfkit_tools.hermes._vendor.tools.X` and the discovered set
  omits CUT modules (kanban/web_tools), so `TestBuiltinDiscovery` expectations must be adapted; ~28 other tests
  clean.
- `test_local_env_blocklist` (22) — reconcile ~4 assertions: empty `PROVIDER_REGISTRY`/`OPTIONAL_ENV_VARS`
  shims shrink the provider-env blocklist to its hardcoded fallback, so ~8 registry-derived API-key vars are no
  longer stripped (xfail/trim with a Stage-D note — this is by design).
- `test_file_tools_live` (45) — live integration (real shell, writes to `~`/`/tmp`); delete a dead
  `sys.path.insert` line; gate behind a marker.
- `test_cross_profile_guard` (12→7) — drop 5 tests needing CUT `skill_manager_tool` / on-disk
  `agent/system_prompt.py`.
- `test_terminal_output_transform_hook` (9→8) — drop 1 test needing the un-shimmed plugin manager.
- `test_interrupt` (7→5) — drop 2 tests importing CUT `run_agent`.
- `test_local_background_child_hang` (8), `test_local_interrupt_cleanup` (2), `test_terminal_timeout_output` (2)
  — real-shell POSIX, timing-sensitive/flaky; gate behind a marker.
- `test_managed_tool_gateway` (6) — structural: loads the module via `spec_from_file_location` from a hard-coded
  file path, bypassing both the import rewriter and the package's absolute imports. Needs the loader rebuilt as
  a normal import. Lowest value/effort ratio — **defer or SKIP**.

### Tier SKIP (with reason)
CUT-module deps: `test_threat_patterns` (`tools.threat_patterns`), `test_skill_view_traversal` /
`test_skills_guard` / `test_skills_ast_audit` (`tools.skills_*`), `test_credential_pool_env_fallback`
(`agent.credential_pool`), `test_website_policy` (`website_policy`/`browser_tool`/`web_tools`/`plugins.web`),
`test_tts_path_traversal` (`tts_tool`), `test_terminal_config_env_sync` (CUT `cli`/`gateway.run` + shim lacks
`set_config_value`), `test_terminal_tool_requirements` (shim `model_tools` lacks `get_tool_definitions`),
`test_zombie_process_cleanup` (7/9 need CUT `run_agent`/`gateway.run`/`delegate_tool`),
`test_search_hidden_dirs` (2 need CUT `skills_hub`; other 7 are pure-shell, marginal),
`test_config_null_guard` (7/10 CUT; the 3 inline MCP tests can optionally be kept).
Non-existent at pin: `test_binary_extensions`, `test_thread_context`. Stub (0 tests):
`test_approval_heartbeat`.

## 4. What must be brought in besides the test files (wiring)

### 4a. Companion string-target rewriter (highest-leverage piece)
Add `vendor/hermes/scripts/rewrite_test_imports.py` — applied **only to vendored test files**, keeping the
production `rewrite_imports.py` pure. It does the existing AST import rewrite **and** rewrites string literals
that are module paths inside call expressions (`patch(...)`, `patch.object`, `monkeypatch.setattr/delattr`,
`importlib.import_module/reload`, `sys.modules[...]`, `caplog … logger=`). Crucially it **reuses
`rewrite_imports._target()`** as the single source of truth for the mapping, so a string `"tools.file_tools.x"`
→ `"calfkit_tools.hermes._vendor.tools.file_tools.x"` and `"hermes_cli.config.load_config"` →
`"calfkit_tools.hermes._shims.hermes_cli.config.load_config"` with the same most-specific-prefix-wins rules.
- Object-target patches (`patch.object(module_obj, "attr")`, `setattr(ft, …)`) are correctly **left alone**.
- Function-local-import targets work: patching the source module (e.g.
  `calfkit_tools.hermes._vendor.utils.atomic_json_write`) binds because the function re-imports at call time.
- Ship it with its own unit test (mirrors the existing `tests/hermes/test_rewrite_imports.py`).

**Design options considered:** (A) manual `sed` per file — error-prone, not reproducible; (B) extend the
production rewriter with a string mode — pollutes the pure module rewriter; (C) **companion script reusing
`_target` — chosen** (reproducible, keeps provenance mechanical, one mapping source).

### 4b. `tests/hermes/conftest.py` augmentation
Today it only puts `src/` on `sys.path` and sets one process-wide `HERMES_HOME`. Add:

- **Hermetic keystone — port `_hermetic_environment` (adapted).** From upstream `tests/conftest.py`: scrub
  credential-shaped env vars (suffix list + explicit names) and `HERMES_*` behavioral vars (incl.
  `HERMES_SESSION_*`, `HERMES_KANBAN_*`, `TERMINAL_*`), per-test `HERMES_HOME` tempdir, pin
  `TZ/LANG/LC_ALL/PYTHONHASHSEED`, disable AWS-IMDS, `TIRITH_ENABLED=false`. **Drop the step that resets
  `hermes_cli.plugins._plugin_manager`** (our shim has no such attribute) — or add the attr to the shim. This
  fixes latent failures where a developer's real `OPENAI_API_KEY` would break provider-priority assertions.
- **Singleton reset autouse fixtures (the in-process isolation fix).** Upstream had no reset hooks for
  `registry.registry` and `file_state._registry` because subprocess-per-file made them unnecessary. Add autouse
  fixtures that, per test, reset: `…_vendor.tools.registry.registry` (fresh `ToolRegistry()`),
  `…_vendor.tools.file_state._registry` (fresh `FileStateRegistry()`), call
  `agent.web_search_registry._reset_for_tests()`, and the `tool_output_limits` / `url_safety` cache resets.
  Prefer vendoring `approval`'s **in-file** reset fixtures with their test files (they clear the full session
  state set).
- **Optional — port `_live_system_guard` + its marker.** Stdlib + `psutil` (already a dep); wraps
  `os.kill`/`subprocess`/`pty.spawn` to stop a stray test from signalling a developer's processes. Recommended
  if/when the real-subprocess shell tests are included.

### 4c. Shim gaps to fill first (else ImportError at collection)
- `_shims/hermes_cli/config.py`: add `DEFAULT_CONFIG` (needed by `test_tool_output_limits`); consider
  `set_config_value` only if `test_terminal_config_env_sync` is ever revisited (currently SKIP).
- `_shims/hermes_cli/nous_account.py`: add the `NousPaidServiceAccessInfo` and `NousPortalAccountInfo`
  dataclasses (needed by `test_tool_backend_helpers` if vendored).

### 4d. `pyproject.toml` / deps
- Register markers under `[tool.pytest.ini_options]`: `integration` (deselected by default) and a `live`/`posix`
  marker for real-shell tests.
- **Async:** the 3 `@pytest.mark.asyncio` tests in `test_url_safety` should be converted to `asyncio.run(...)`
  (the pattern the existing `test_web_url_safety.py` already uses) — **no new dependency**. Avoid adding
  `pytest-asyncio` unless a later batch needs it.
- `pytest-timeout` is **optional** (only if mirroring upstream's 30s cap; prefer `--timeout-method=thread` for
  cross-platform). No other upstream test deps exist (no freezegun/responses/respx/pyfakefs).
- **No data/golden/fixture files exist** in upstream `tests/tools/` or `tests/` — every input is inline or
  `tmp_path`. Nothing to vendor on that axis.

### 4e. Provenance
Each vendored test gets the 2-line `# Vendored from NousResearch/hermes-agent @ 5a36f76 (MIT) — tests/tools/<f>`
banner (as the existing 3 do). Record the vendored test set + the companion-rewriter local-modification in
`vendor/hermes/METADATA.yaml` `local_modifications`. Same pin/license as the modules — no THIRD_PARTY_NOTICES
change.

## 5. Behavioral reconciliations (the only non-mechanical edits)
- `test_local_env_blocklist`: ~4 assertions (empty provider-registry shim) → xfail/trim with Stage-D note.
- `test_registry::TestBuiltinDiscovery`: 3 tests → adapt expected module names/dir/set to the vendored layout.
- `test_tirith_security`: 1 assertion (`"hermes_test"` → our `HERMES_HOME` prefix), or align the conftest prefix.
- Partial-drop files (`cross_profile_guard`, `output_transform_hook`, `interrupt`): delete the CUT-dep classes.

## 6. Duplicate handling
- `test_url_safety` (76 tests) is a **superset** of the existing hand-written `test_web_url_safety.py` (8 tests),
  different filename (no collision). **Recommend** vendoring it and retiring the hand-written one.
- `test_web_providers` / `test_web_search_registry` already exist in `tests/hermes/` — **do not** re-vendor the
  upstream equivalents; keep ours.

## 7. Phased rollout

- **Phase 0 — Wiring (no new tests):** companion rewriter + its test; conftest keystone + singleton-reset
  fixtures; shim gaps (`DEFAULT_CONFIG`, nous_account dataclasses); marker registration. **Gate:** existing 511
  tests still green; the 3 vendored tests unaffected.
- **Phase 1 — DROP_IN (~25 files, ~415 tests):** place files, run the rewriter, run. **Gate:** all green run
  together (not just individually) to prove no cross-file contamination; coverage delta recorded.
- **Phase 2 — EASY (~26 files, ~850 tests):** companion rewriter clears string targets; add shim
  `DEFAULT_CONFIG`; convert the 3 url_safety async tests. Lead with ⭐`test_approval` (203 tests / 3 edits).
- **Phase 3 — MODERATE (selective):** tirith (bulk sed + 1 assert), registry (reconcile 3), code_execution ×2,
  local_env_blocklist (reconcile 4); then decide on real-shell/live and the structural `managed_tool_gateway`.
- **SKIP list** stays excluded; revisit only if the corresponding modules are ever un-CUT.

**Per-phase verification** (beyond pass/fail): run the whole `tests/hermes/` set **together** and in randomized
order to catch in-process state leakage; record the `_vendor` coverage delta each phase against the badge.

## 8. Coverage impact (qualitative)
The low-coverage modules dragging the badge are exactly the ones these tests target: `tirith_security` 9% →
covered by `test_tirith_security` (92), `lazy_deps` 14% → `test_lazy_deps` (36), `terminal_tool` 39% →
the terminal_* family, `process_registry` 40% → `test_process_registry` (66), `registry` 34% → `test_registry`,
`file_operations`/`file_tools` 51–58% → the file_* family, `approval` → `test_approval` (203). Phases 1–2 alone
should move the vendored tree well out of the red band; the remaining floor is the genuinely un-exercised
environment backends (`ssh` 11%, modal/daytona) and CUT-adjacent code, which `[tool.coverage.report] omit` could
exclude if desired (separate decision).

## 9. Risk register
| Risk | Trigger | Mitigation |
|---|---|---|
| In-process global-state leakage | files mutating `registry`/`file_state`/`approval`/web-registry singletons | autouse reset fixtures (§4b); run suite together + randomized in CI |
| Wrong patch targets after vendoring | string-literal module paths untouched by import rewriter | companion rewriter reusing `_target` (§4a) |
| Collection-time ImportError | shim missing a symbol an upstream test imports | fill `DEFAULT_CONFIG`, nous_account dataclasses first (§4c) |
| Silent behavior drift | tests asserting shim/fix-up-changed behavior | explicit reconciliations (§5); xfail with notes, never weaken silently |
| CI flakiness | real-shell timing/POSIX tests | gate behind `live`/`posix` marker; `test.yml` runs on Linux so POSIX is fine, timing asserts are the watch-item |
| Provenance erosion | hand-edited "verbatim" tests | keep edits to mechanical rewrite + documented reconciliations; banner + METADATA |

## 10. Open decisions (for the user)
1. **First-phase scope** — Phase 0+1 only (prove the harness, ~415 tests), or push straight through Phase 2
   (~1,265 tests)?
2. **Real-shell / live tests** — include the POSIX real-subprocess + `file_tools_live` tests (behind a marker),
   or hold them out of CI initially?
3. **url_safety duplicate** — replace the hand-written `test_web_url_safety.py` with the upstream superset, or
   keep both?
