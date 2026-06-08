# Search tools research log

Realtime log for the grep/glob tooling research. Append-only.

## Step 1 — Verification of OUR search tools (source-level)

### Environment facts
- `openhands-tools >= 1.23.1` is the dep (`pyproject.toml:16`, `uv.lock:572`). Installed version: **1.23.1**.
- openhands-tools does **NOT** bundle ripgrep. Backend selection is purely `shutil.which("rg")` /
  `shutil.which("grep")` at executor **construction** time
  (`.venv/.../openhands/tools/utils/__init__.py:13-45`). So the live backend is whatever the host image
  happens to ship → the silent fallback chain that underlies SEARCH-3/4.
- This dev box has `rg 15.1.0` (`/opt/homebrew/bin/rg`). Note: the interactive shell's `grep` is shimmed to
  `ugrep` by Claude Code, but openhands shells out via `subprocess` to the real binaries, unaffected.

### Our wrapper (`src/calfcord/tools/builtin/search.py`)
- Two module-global lazy singletons `_grep_executor` / `_glob_executor`, both built with
  `working_dir=str(get_workspace_root())` (lines 44-61). One shared `calfkit-tools` worker, `max_workers=1`.
- `_resolve_search_path` (64-76): `None` → workspace root; relative → resolved under workspace root;
  **absolute → passed straight through** (no containment) → confirms **XC-2** (`../` / absolute / symlink escape;
  abs path string is LLM-influenced from untrusted Discord chat).
- `grep()` (104-142): builds `GrepAction`, calls executor, surfaces `is_error` via `flatten_observation_text`,
  else `_format_files("grep", obs.matches, …, obs.truncated)`.
- `glob()` (145-168): same shape with `GlobAction` / `obs.files`.
- The wrapper **trusts `obs.truncated` blindly** → inherits the glob off-by-one (SEARCH-6).
- Docstrings: grep promises "Full regex syntax" (SEARCH-3 mismatch), never mentions case-insensitivity
  (SEARCH-8) or gitignore behavior (SEARCH-1/2/5).

### Upstream openhands grep (`.venv/.../openhands/tools/grep/impl.py`)
- Backend chain: ripgrep → system grep → pure-Python (`_select_search_backend`, 52-57). Chosen once at
  construction; a fallback warning is logged but **not surfaced to the LLM**.
- **SEARCH-1 (verified):** ripgrep path is `["rg","-l","-i",pattern,path,"--sortr=modified"]` (230-237). No
  `--no-ignore`/`--hidden`/`-u`. So inside a git repo, `.gitignore`/`.ignore`/`.git/info/exclude` subtrees are
  silently skipped → false "no files matched". No injection point on the executor to change this.
- **SEARCH-2 (verified):** `if action.include: cmd += ["-g", action.include]` (238-239). ripgrep `-g` is a
  whitelist that *overrides* ignore rules → `include="*.log"` *finds* a gitignored `*.log` that bare grep
  misses. `include` widens ignore scope instead of only narrowing.
- **SEARCH-3 (verified):** three dialects — ripgrep (Rust regex: `\d\w\s` ok, **no backreferences/lookaround**),
  system grep is `["grep","-R","-I","-l","-i",pattern,path]` = **BRE** (`\d` literal, `\w+`/`(...)` not ERE)
  (256-267), Python `re.compile(pattern, re.IGNORECASE)` (81, 288). Same pattern → contradictory results across
  hosts. Docstring promises "Full regex syntax".
- **SEARCH-4 (verified):** Python fallback `compiled_regex.search(content)` per file, full contents, **no
  per-file timeout** (281-306). rg/grep subprocesses have `timeout=30`; the Python path has none. A
  catastrophic pattern (`(a+)+$`) on a long `a`-run hangs; with `max_workers=1` it wedges grep fleet-wide.
- **SEARCH-8 (verified):** forced case-insensitive everywhere — `re.IGNORECASE` (81, 288), `rg -i` (233),
  `grep -i` (261). No override; undocumented.
- Note: this version added `_path_matches_filters` (147-165) which **drops dotfiles/dot-dirs** at the wrapper
  level (any leading-dot path part except the basename for include) — an extra, undocumented hidden-file
  filter layered on top of ripgrep. `_finalize_matches` dedups by resolved path and truncates with
  `len > _MAX_MATCHES` (correct, 196).

### Upstream openhands glob (`.venv/.../openhands/tools/glob/impl.py`)
- Backend: ripgrep → Python `glob` fallback (`_check_ripgrep_available`, 39-49). Decided at construction.
- **SEARCH-5 (verified):** glob ripgrep path is `["rg","--files",path,"-g",pattern,"--sortr=modified"]`
  (152-159). `-g` again overrides gitignore → `glob("*.log")` **finds** the gitignored `debug.log` that bare
  `grep` does **not**. Two "find files" tools, opposite gitignore visibility. Undocumented.
- **SEARCH-6 (verified):** ripgrep glob path `if len(file_paths) >= 100: break` then
  `truncated = len(file_paths) >= 100` (178, 181) → **exactly 100** files reports `truncated=True` though
  nothing dropped. Python fallback uses `> 100` (227); grep uses `> _MAX_MATCHES` (196). Internally
  inconsistent + inconsistent with grep.
- **SEARCH-7 (verified):** Python fallback `glob.glob(pattern, recursive=True)` then
  `(search_path/match).absolute()` (213, 219) — `.absolute()` not `.resolve()`, so symlink-loop paths are kept
  as distinct strings, no dedup → duplicate physical files consume the 100-cap. (Note the ripgrep glob path
  *does* `Path(line).resolve()` at 176, so the loop dedups there.) Python fallback also `os.chdir`s
  (process-global; flagged not-parallel-safe at 43-49 — moot under max_workers=1 but a sharp edge).
- **XC-2** also applies here (`action.path` resolved and used directly).

### Net: which issues are upstream-openhands vs our wrapper
- Upstream openhands (no injection point to fix from our side without forking): SEARCH-1, -2, -3, -4, -5, -6,
  -7, -8. The executors hardcode rg flags; there is no config surface for ignore/case/backend/timeout.
- Our wrapper: XC-2 (containment), trusting `truncated`, docstring accuracy, single shared singleton under
  max_workers=1 (XC-3 amplifies SEARCH-4).

This frames the candidate bar: we need either (a) a tool with a real config surface for
backend/ignore/case/limit/timeout, or (b) a thin design we port ourselves over a pinned ripgrep.

---

## Step 2 — Candidate framework survey


### Candidate: OpenCode (sst/opencode) — TypeScript/Bun, MIT
Two implementations present in the repo (mid-migration to a "v2" core):
- Stable tool layer: `packages/opencode/src/tool/grep.ts`, `.../glob.ts` (calls `@opencode-ai/core` Search service).
- v2 core: `packages/core/src/filesystem/ripgrep.ts` (binary mgmt + exec), `packages/core/src/ripgrep.ts`
  (adapter), `packages/core/src/tool/grep.ts` (leaf tool).

**Backend determinism — BEST IN CLASS.** `filesystem/ripgrep.ts:18` pins `VERSION="15.1.0"` and a `PLATFORM`
map; `filepath` (292-327): prefer system `rg` if present, else a cached `~/.../bin/rg`, else **download the exact
pinned release from BurntSushi GitHub and extract** (tar/zip per-platform), chmod 0755. No system-grep / Python
fallback ever — single backend, single regex dialect (ripgrep Rust regex). → directly fixes SEARCH-3, SEARCH-4
root (no Python ReDoS path), and the "silent fallback" core of our problem.

**Deterministic flags.** `--no-config` + `delete env.RIPGREP_CONFIG_PATH` (148-152) → user rc can't change
behavior. Hidden/ignore semantics are explicit and consistent across grep & files:
- search (grep): `["--no-config","--json","--hidden","--glob=!.git/*","--no-messages", ...glob, --max-count=limit, --, pattern, ...files]` (213-222).
- files (glob): `["--no-config","--files","--glob=!.git/*", (--hidden|--glob=!.*), ...glob, "."]` (200-211).
  → SEARCH-5: grep and files use the **same** `--hidden`/`!.git` policy → consistent visibility. (NB: ripgrep
  `--files`/search still honor `.gitignore` by default unless `--no-ignore` is added; opencode keeps gitignore
  ON but applies it identically to both tools and documents the `.git` exclusion in a TODO.)

**Truncation — correct.** v2 adapter streams `take(limit+1)` then `truncated = rows.length > limit` (ripgrep.ts
v2 115-116); stable glob passes `--max-count`/limit and reports `files.truncated`. No off-by-one (fixes SEARCH-6
shape). Also surfaces `partial` (exit code 2 = some paths inaccessible) and `regexFallbackError`.

**Regex safety.** Invalid pattern detected via exit code 2 + stderr parse → typed `InvalidPatternError`
surfaced to the model (ripgrep.ts v2 80-81,120-122). No Python regex engine → no catastrophic-backtracking DoS.
AbortSignal threaded everywhere (`raceAbort`, `waitForAbort`) → cancelable / timeout-able (fixes SEARCH-4).

**Containment.** Stable tools call `assertExternalDirectoryEffect(ctx, path, {...})` + a `Reference.contains`
allowlist and a permission `ctx.ask` before running (grep.ts 44-64, glob.ts 31-51) → real workspace containment
with explicit external-dir consent (addresses XC-2 with a permission model).

**Case sensitivity.** ripgrep default = case-sensitive (smart-case off); opencode does NOT force `-i`. So unlike
openhands it is case-sensitive by default (SEARCH-8 non-issue; though no per-call toggle exposed in these files).

Integration for calfcord: **PORT (design-to-port)**, not a drop-in dep (TS/Bun + Effect runtime). The valuable,
portable ideas: pin+auto-download ripgrep, `--no-config`/env scrub, identical hidden/ignore flags for grep&glob,
`take(limit+1)` truncation, JSON streaming with typed invalid-pattern + partial signals, AbortSignal timeout.
License MIT — clean to reimplement in Python.

### Candidate: Gemini CLI (google-gemini/gemini-cli) — TypeScript, Apache-2.0
Files: `packages/core/src/tools/ripGrep.ts` (grep), `.../glob.ts`, `.../grep-utils.ts`,
`scripts/download-ripgrep-binaries.ts`, `third_party/get-ripgrep/`. Most **feature-complete** reference.

**Backend.** `resolveRipgrepPath()` (ripGrep.ts:53-95): prefer **bundled per-platform binary**
(`rg-${platform}-${arch}`), else system `rg` **but only if `isTrustedSystemPath(realPath)`** (RCE / search-path
-interruption hardening). Hard-fails if no binary ("Cannot find bundled ripgrep binary", 489-491). Single
backend → fixes SEARCH-3/4 root. (Binaries are downloaded at build time by `scripts/download-ripgrep-binaries.ts`.)

**Configurable ignore semantics (fixes SEARCH-1/2/5).** `no_ignore` param (default false) → `--no-ignore`
(449-451). Honors `getFileFilteringRespectGitIgnore()`; if off adds `--no-ignore-vcs --no-ignore-exclude`
(461-463). Adds explicit glob excludes (`COMMON_DIRECTORY_EXCLUDES`, `*.log`, `*.tmp`) and `.geminiignore` via
`--ignore-file` (466-481). All documented in the param schema ("If true, does not respect .gitignore or default
ignores").

**Case sensitivity (fixes SEARCH-8).** `case_sensitive` param (default false → `--ignore-case`, 430-432).
Documented. Also `fixed_strings` (literal), `context/before/after`, `exclude_pattern`.

**Timeout / ReDoS (fixes SEARCH-4).** Hard search timeout: `AbortController` + `DEFAULT_SEARCH_TIMEOUT_MS`
(or larger configured) → aborts the rg subprocess; emits a "narrow your search" message (238-287). ripgrep's
linear regex engine means no catastrophic backtracking anyway.

**Truncation / limits (fixes SEARCH-6).** `total_max_matches` (default `DEFAULT_TOTAL_MAX_MATCHES`) +
`max_matches_per_file` → `--max-count`; counts non-context matches and breaks at limit (514-522). Validated `>=1`.

**Containment (fixes XC-2).** `config.validatePathAccess(searchDirAbs, 'read')` → `PATH_NOT_IN_WORKSPACE`
(189-202); plus per-match `path.relative` `..`/absolute rejection in JSON parse (544-551). Strong, multi-layer.

**Regex.** Validates `new RegExp(pattern)` up front (642-647) — caveat: validates against **JS** dialect, then
runs **ripgrep** (Rust) dialect; not identical, but at least one consistent runtime dialect (Rust) for actual
matching. `--regexp` used (so leading-dash patterns are safe).

Integration: **PORT** (TypeScript, Apache-2.0). Richest param surface to copy: no_ignore / case_sensitive /
fixed_strings / context / max-count / timeout / containment. The `download-ripgrep-binaries.ts` approach (vendor
at build) is an alternative to opencode's runtime download.


### Candidate: Gemini glob (`gemini-cli/.../tools/glob.ts`) — Apache-2.0
Glob uses the npm `glob` package (NOT ripgrep). `follow: false` (114-187) → **no symlink-loop recursion**
(fixes SEARCH-7); `nocase: !case_sensitive` (configurable → SEARCH-8); `dot: true`; `signal` (timeout);
`ignore: getFileExclusions().getGlobExcludes()`. Then post-filters through `FileDiscoveryService` with
`respectGitIgnore`/`respectGeminiIgnore` **params** and reports `ignoredCount` (224-256) → SEARCH-1/5 fixed at a
**shared filter layer** so grep (rg) and glob (node-glob) agree on ignore semantics despite different backends.
Containment via `validatePathAccess` (XC-2). Recency-then-alpha sort (deterministic ordering).
`grep-utils.ts formatGrepResults`: `wasTruncated = matchCount >= totalMaxMatches` (177) — honest limit reporting,
plus per-line `MAX_LINE_LENGTH_TEXT_FILE` truncation and auto-context enrichment.

### Candidate: Cline (cline/cline) — TypeScript, Apache-2.0
Two relevant paths:
- VSCode product grep: `apps/vscode/src/services/ripgrep/index.ts`. Uses VSCode's **bundled** rg via
  `getBinaryLocation("rg")` (61) — pinned/deterministic, no fallback chain. Args
  `["--json","-e",regex,"--glob",filePattern,"--context","1",dir]` (109): `-e` (safe leading-dash patterns),
  Rust regex documented in header (18), **case-sensitive** (no `-i`). Respects `.gitignore` by rg default +
  `.clineignore` post-filter via `ClineIgnoreController.validateAccess` (154-156). Bounded output: cap 300
  results AND a **0.25MB byte budget** (161-281) with an explicit truncation message. No explicit timeout but
  caps stdout lines and kills the process. Solid; addresses SEARCH-1(partial)/3/6/8; weaker on SEARCH-4 (no
  hard timeout — but rg's linear engine = no ReDoS).
- SDK search executor: `sdk/packages/core/src/extensions/tools/executors/search.ts`. rg-if-available else a
  **JS regex fallback** — but the fallback matches **line-by-line** with a per-loop `maxResults` break and a
  5s rg timeout + AbortSignal (124-266). Still a silent fallback chain (like openhands), but the line-scoped
  fallback + JS engine (no catastrophic *whole-file* scan) is materially safer than openhands' whole-file
  Python scan. Hardcoded extension allow/deny lists rather than gitignore — coarser.

### Candidate: Codex CLI (openai/codex) — Rust, Apache-2.0
**No dedicated content-grep tool**: codex lets the model run `rg`/`grep` in the sandboxed shell. It DOES ship a
first-class **fuzzy file finder** (glob analog): `codex-rs/file-search/src/lib.rs`, built on the `ignore` crate
(ripgrep's own gitignore engine) + `nucleo` (fuzzy matcher). Best-in-class *file-find* semantics:
- `respect_gitignore` toggle (explicit, documented; 105-113) → SEARCH-1 analog.
- **`require_git(true)`** (402-433): only honor `.gitignore` when inside a git repo — matches git's own behavior
  and prevents a parent `~/.gitignore` (`*`) from silently hiding everything. Has a regression test (#3493,
  1041-1102). This is the *correct* gitignore-scope fix for our SEARCH-1/9 anxiety.
- `hidden(false)` (show hidden), `follow_links(true)` but the `ignore` crate has **built-in symlink loop
  detection** → SEARCH-7 non-issue.
- `limit` + `total_match_count` → `matches_truncated = total > shown` (honest truncation; 277).
- `cancel_flag` cancellation; deterministic tie-break (score desc, path asc; 320-333).
Note: this is FUZZY matching, not literal glob — a different UX from our glob. Rust → port-the-design only.

### Candidate: hermes-agent (NousResearch/hermes-agent) — **Python**, check LICENSE
Most directly portable: `tools/file_operations.py` (`FileOperations.search`, 1864+) + `tools/file_tools.py`
(tool schema, 1460+). One unified `search` tool: `target="content"` (grep) or `target="files"` (glob-ish via
`rg --files -g`).
- Backend: **rg-first, then grep** for content (2055-2067); **rg-first, then find** for files (1944-2007). No
  pure-Python regex path → no whole-file ReDoS (mitigates SEARCH-4). If neither present → explicit error telling
  the user to install ripgrep (no silent wrong answer).
- Content rg args: `["rg","--line-number","--no-heading","--with-filename", (-C ctx), (--glob file_glob),
  (-l|-c), pattern, path]` (2072-2090). **No `-i`** → case-sensitive (SEARCH-8 fixed; no per-call toggle here).
- `--exclude-dir='.*'` in the grep fallback mirrors rg's hidden-dir default → consistent hidden handling.
- gitignore: relies on rg default (respects) — same as openhands; no explicit toggle, but the file-search docs
  note "respects .gitignore" and prefers rg *because* it does. (No SEARCH-1 fix, but documented.)
- **Diagnostics split** (`_split_tool_diagnostics`, 289-341): separates `rg:`/`grep:` error lines from real
  match lines so (a) errors never parse as matches and (b) a hard failure surfaces a clean message. Directly
  relevant to our `is_error` problem.
- `set -o pipefail` so rg exit 2 (error) isn't masked by `| head` 0; distinguishes exit-2-with-matches (partial,
  keep) from exit-2-empty (real error). 60s timeout. `limit`/`offset` pagination; `truncated = total >
  offset+limit` (correct → SEARCH-6 fixed). Windows drive-letter-aware parsing.
- Caveat: builds **shell strings** (`shell=True`-style with `_escape_shell_arg`), not argv lists — we'd port the
  logic but switch to argv subprocess for safety/determinism.

### Candidate: openclaw (openclaw/openclaw) — TypeScript, check LICENSE
`src/agents/sessions/tools/grep.ts` + `src/agents/utils/tools-manager.ts`.
- **Pinned-binary manager** like opencode: `ensureTool("rg")` (grep.ts:195) → `tools-manager.ts` locates system
  rg/fd else **downloads the release from GitHub** (SSRF-guarded fetch, tar/zip extract, chmod 0755). Hard error
  if rg unavailable ("ripgrep (rg) is not available and could not be downloaded"). No silent degradation.
- Args `["--json","--line-number","--color=never","--hidden"]` + `--ignore-case` (param `ignoreCase`, **default
  false** → SEARCH-8 fixed), `--fixed-strings` (literal), `--glob` (include) (240-250). `--hidden` consistent.
  Respects `.gitignore` (stated in description).
- Streaming JSON; **match limit** (kills child at limit), **byte limit** (DEFAULT_MAX_BYTES), **per-line
  truncation** — each with an actionable notice ("Use limit=N*2 for more") → honest truncation (SEARCH-6 fixed).
- AbortSignal; distinguishes abort vs rg error (exit not in {0,1}). File finding handled by a separate fd-backed
  `find` tool. Pluggable `operations` (remote/SSH backends) — nice for distributed calfcord.

### Negative results (no portable LOCAL search tool)
- **OpenAI Agents SDK (openai/openai-agents-python)**: `FileSearchTool` (`src/agents/tool.py:556`, type
  `"file_search"`) is a **hosted** tool over OpenAI vector stores — NOT a local fs grep/glob. Local code search
  happens only inside the hosted Codex/sandbox extensions (which shell out to rg/grep). Nothing to port.
- **Claude Agent SDK Python (anthropics/claude-agent-sdk-python)**: no grep/glob implementation in the SDK.
  `"Grep"`/`"Glob"` are tool *names* forwarded to the `claude-code` CLI subprocess; the impl is server-side in
  the bundled `@anthropic-ai/claude-code` JS, not the SDK. Design known only from docs (below).

### Claude Code Grep/Glob design (from docs + public reporting — design reference, not source)
- Grep is **ripgrep-backed**; ships a bundled rg via `@vscode/ripgrep`; `USE_BUILTIN_RIPGREP=0` switches to the
  faster system rg. Respects `.gitignore`/`.ignore`/`.rgignore` (rg default). **Case-sensitive by default** with
  an explicit `-i` boolean param (option to override) and output modes (content/files_with_matches/count),
  `glob`/`type`/`-A/-B/-C`/`multiline` params. Known limitation tracked upstream (anthropics/claude-code #9411):
  rg only honors `.gitignore` when a `.git` dir is present → for non-git dirs they want `--no-require-git`
  (mirrors codex's `require_git` consideration). v2.1.117 reportedly swapped to **ugrep** for regex
  compatibility + compressed-file support. Takeaway: pinned/bundled rg + explicit case toggle + documented
  ignore semantics + rich param surface — same shape as gemini/opencode/openclaw.

### Licenses (all permissive — safe to port designs)
opencode MIT · gemini-cli Apache-2.0 · cline Apache-2.0 · codex Apache-2.0 · hermes-agent MIT ·
openclaw MIT · openai-agents-python MIT (but no local search tool) · claude-agent-sdk-python MIT (no local impl).

### Claude Code Grep params (confirmed via tools reference + issue #9411)
Grep: pattern, path, output_mode {content|files_with_matches|count}, glob, type, -A/-B/-C, **-i (case toggle,
default sensitive)**, -n, multiline, head_limit. ripgrep-backed. Glob: pattern, path; sorted by mtime.
Issue #9411 (closed not-planned): rg only honors .gitignore when a .git dir is present → `--no-require-git`
requested (same design point codex solved via `require_git(true)`).

### Baseline confirmation
Reviewed `openhands-tools 1.23.1` directly from `.venv` — that IS the published, canonical source we ship
(the All-Hands-AI/OpenHands monorepo houses the app; the tools ship from the agent-sdk as `openhands-tools`).
