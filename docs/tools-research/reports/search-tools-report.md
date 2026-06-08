# Search tools (grep / glob) — deep research report

**Scope.** Evaluate how calfcord's `grep`/`glob` builtin tools (thin wrappers over `openhands-tools`'
`GrepExecutor`/`GlobExecutor`) should evolve, by reviewing how leading open-source agent frameworks implement
content-search and file-find tools, graded issue-by-issue against the calfcord audit findings
`SEARCH-1…SEARCH-8` and `XC-2`.

**Method.** Source-level verification of our tools and the upstream openhands executors (read in
`.venv/.../openhands/tools/{grep,glob}/`), then per-framework review of the *actual* tool source (cloned/read,
not blogs). Realtime notes in `docs/tools-research/logs/search-tools-log.md`.

---

## 1. Executive summary + top recommendation

calfcord's search tools inherit a pile of *silent-wrong-answer* bugs from `openhands-tools`, all rooted in one
design choice: **a silent backend-fallback chain (ripgrep → system grep → pure-Python) with hardcoded flags and
no config surface.** Different deploy images silently get different regex dialects, ignore-file behavior, case
sensitivity, truncation math, and (on the Python path) an unbounded ReDoS that — under our `max_workers=1`
tools host — becomes a fleet-wide outage. The wrapper adds no containment (XC-2) and trusts upstream's buggy
`truncated` flag.

Every serious agent framework converged on the **same answer**, and it is the opposite of openhands':

> **Pin a single deterministic backend — ripgrep — and remove the fallback chain.** Ship/bundle/auto-download a
> known rg version; hard-fail (don't silently degrade) if it's missing. Pass explicit, documented flags for
> hidden-file and ignore-file handling that are *identical for grep and glob*. Expose case-sensitivity as a
> param (default sensitive). Use a hard subprocess timeout + `AbortSignal`. Fetch `limit+1` and report
> truncation honestly. Distinguish "invalid pattern" and "partial/inaccessible" from "no matches".

The cleanest references for this design are **opencode**, **openclaw**, **gemini-cli**, and (for the gitignore-
scope subtlety) **codex**. The best directly-portable *Python* reference is **hermes-agent**.

**Top recommendation: PORT — build a small calfcord-owned `RipgrepSearch` core in Python**, modeled on
opencode's `filesystem/ripgrep.ts` adapter (pinned binary, `--no-config`, JSON streaming, `limit+1`, typed
invalid-pattern/partial signals, cancelation) with hermes-agent's pragmatic Python execution patterns
(diagnostics-split, `pipefail`-equivalent exit-code handling, pagination) and gemini-cli's *parameter surface*
(`case_sensitive`, `no_ignore`, `fixed_strings`, `context`, `max-count`, workspace containment). Do **not** keep
the openhands executors — there is no injection point to fix SEARCH-1/2/3/4/8 without forking them, and the
fallback chain itself is the core defect. (A short-term "stay-and-fix" band-aid is described in §6.)

Ranked candidates: **1) opencode, 2) hermes-agent (Python, easiest port), 3) gemini-cli**, then openclaw, codex
(file-find only), cline. OpenAI Agents SDK and Claude Agent SDK Python have no local search tool to port.

---

## 2. Verified recap of our search tools' issues and true causes

Files: `src/calfcord/tools/builtin/search.py` (wrapper), `.../workspace.py`, `.../_observation.py`; upstream
`.venv/.../openhands/tools/grep/impl.py`, `.../glob/impl.py`, `.../tools/utils/__init__.py`.

**Root cause of most issues — silent backend selection.** `openhands-tools` does *not* bundle ripgrep. Each
executor picks a backend once at construction via `shutil.which("rg")` / `shutil.which("grep")`
(`utils/__init__.py:36-45`); a fallback warning is logged but never reaches the LLM. The live backend is
whatever the host image happens to ship.

| ID | Verified cause (source) |
|----|--------------------------|
| **SEARCH-1** | grep ripgrep path = `["rg","-l","-i",pattern,path,"--sortr=modified"]` (`grep/impl.py:230-237`). No `--no-ignore`/`--hidden`. Inside a git repo, `.gitignore`/`.ignore`/`.git/info/exclude` subtrees are silently skipped → false "no files matched". No flag injection point on the executor. |
| **SEARCH-2** | `if action.include: cmd += ["-g", action.include]` (`grep/impl.py:238-239`). ripgrep `-g` is a *whitelist that overrides ignore rules* → `include="*.log"` finds a gitignored `*.log` that bare grep misses. `include` widens ignore scope instead of only narrowing. |
| **SEARCH-3** | Three regex dialects: ripgrep (Rust regex; `\d\w\s` ok, **no backreferences/lookaround**), system grep = BRE (`grep -R -I -l -i`, `:256-267`; `\d` literal, `\w+`/`(...)` not ERE), Python `re.compile(pattern, re.IGNORECASE)` (`:81,:288`). Same pattern → contradictory results per host. Docstring promises "Full regex syntax." |
| **SEARCH-4** | Python fallback `compiled_regex.search(content)` per file on full contents, **no per-file timeout** (`:281-306`); rg/grep subprocess paths have `timeout=30`, the Python path has none. `(a+)+$` on a long `a`-run hangs; under `max_workers=1` (XC-3) it wedges grep for every agent. |
| **SEARCH-5** | grep respects gitignore (`rg -l`) but glob *overrides* it (`rg --files -g pattern`, `glob/impl.py:152-159`). `glob("*.log")` finds a gitignored `debug.log` that `grep` won't. Two "find files" tools, opposite visibility, undocumented. |
| **SEARCH-6** | glob ripgrep path: `if len>=100: break` then `truncated = len>=100` (`glob/impl.py:178,181`) → **exactly 100** reports `truncated=True` though nothing was dropped. Python glob uses `>100` (`:227`); grep uses `>_MAX_MATCHES` (`grep/impl.py:196`). Internally inconsistent and inconsistent with grep. Our wrapper trusts `obs.truncated` blindly (`search.py:168`). |
| **SEARCH-7** | glob Python fallback: `glob.glob(pattern, recursive=True)` then `(search_path/match).absolute()` (`glob/impl.py:213,219`) — `.absolute()` not `.resolve()`, no symlink-loop dedup → duplicate physical files consume the 100-cap. (The ripgrep glob path *does* `Path(line).resolve()` at `:176`.) Also `os.chdir` (process-global). |
| **SEARCH-8** | Forced case-insensitive everywhere: `re.IGNORECASE` (`:81,:288`), `rg -i` (`:233`), `grep -i` (`:261`). No override; undocumented. |
| **XC-2** | `_resolve_search_path` (`search.py:64-76`): absolute paths pass straight through; relative resolve under workspace but no containment check → `../`/absolute/symlink escape. The path string is LLM-influenced from untrusted Discord chat. |

**Net:** SEARCH-1…-8 are all *upstream openhands* defects with no config surface to fix from our side without
forking. XC-2 and "trusting `truncated`" are ours. This sets the bar: a candidate must offer a real config
surface for backend/ignore/case/limit/timeout, or be a thin design we own over a pinned ripgrep.

---

## 3. Per-candidate deep review

### 3.1 OpenHands — incumbent baseline (All-Hands-AI/OpenHands; tools shipped as `openhands-tools`)
- **Repo/path/lang/license:** `openhands-tools` 1.23.1, `openhands/tools/{grep,glob}/impl.py`, Python, MIT (core).
- **Overview:** backend chain rg → system grep → pure-Python, chosen at construction; hardcoded flags; no config.
- **Issue assessment:** this *is* the baseline — fails SEARCH-1…-8 as verified in §2.
- **Pros:** already a dep; readable; recent versions added dotfile filtering + dedup-by-resolved-path in grep.
- **Cons:** the fallback chain is the root defect; no injection point; forced `-i`; ReDoS on the Python path.
- **Integration:** already integrated; **not fixable in place** without forking the executors.

### 3.2 OpenCode (sst/opencode) — TypeScript/Bun, Effect; **MIT** — *best overall design*
- **Paths:** `packages/core/src/filesystem/ripgrep.ts` (binary mgmt + exec), `packages/core/src/ripgrep.ts`
  (adapter), `packages/core/src/tool/grep.ts`, `packages/opencode/src/tool/{grep,glob}.ts` (stable tools).
- **Overview:** pins ripgrep `15.1.0` and a per-platform `PLATFORM` map; `filepath` prefers system `rg`, else a
  cached binary, else **downloads the exact pinned release from BurntSushi and extracts** (`ripgrep.ts:18,
  292-327`). Single backend, single regex dialect. Runs with `--no-config` and deletes `RIPGREP_CONFIG_PATH`
  (`:148-152`) so user rc can't perturb results. JSON streaming with `Stream.take(limit+1)`.
- **Issue-by-issue:**
  - SEARCH-1: gitignore honored by rg default but applied **identically to grep and files**; `--glob=!.git/*`
    explicit; a `--no-ignore` toggle is trivial to add (flags are centralized). ✅ (consistent + fixable)
  - SEARCH-2: include passed as another `--glob=`; because hidden/ignore policy is uniform, include only
    narrows. ✅
  - SEARCH-3: single Rust-regex dialect; no system-grep/Python path. ✅
  - SEARCH-4: no Python regex engine (no catastrophic backtracking) + `AbortSignal` (`raceAbort`/`waitForAbort`)
    → cancelable/timeout-able. ✅
  - SEARCH-5: grep and files use the **same** `--hidden`/`!.git` policy → consistent visibility. ✅
  - SEARCH-6: `take(limit+1)` then `truncated = rows.length > limit` (`ripgrep.ts` v2 `:115-116`). ✅
  - SEARCH-7: rg only (no symlink-loop Python walk); `--follow` is opt-in. ✅
  - SEARCH-8: rg default case-sensitive; not forced `-i`. ✅ (no per-call toggle in these files, but trivial)
  - XC-2: stable tools assert containment via `assertExternalDirectoryEffect` + a `Reference` allowlist +
    permission `ctx.ask` (`tool/grep.ts:44-64`, `glob.ts:31-51`). ✅
- **Also:** typed `InvalidPatternError` (exit 2 + stderr parse, `ripgrep.ts:80-81,120-122`) and a `partial`
  flag for inaccessible paths surfaced to the model.
- **Code quality:** excellent — clean separation (binary mgmt / adapter / leaf tool), streaming, typed errors.
- **Pros:** the textbook deterministic design; everything we need is here.
- **Cons:** TypeScript + Effect runtime → not a drop-in dep; auto-download adds a network dependency at first
  use (mitigated by preferring system rg / pre-vendoring).
- **Integration:** **design-to-port** (MIT). Port the *ideas*: pinned/auto-download rg, `--no-config`/env scrub,
  uniform hidden/ignore flags, `limit+1` truncation, JSON streaming, typed invalid-pattern/partial, cancelation.

### 3.3 hermes-agent (NousResearch/hermes-agent) — **Python**, MIT — *easiest port*
- **Paths:** `tools/file_operations.py` (`FileOperations.search`, `:1864+`), `tools/file_tools.py` (schema,
  `:1460+`). One unified `search` tool: `target="content"` (grep) or `target="files"` (`rg --files -g`).
- **Overview:** rg-first → grep fallback for content (`:2055-2067`); rg-first → find fallback for files
  (`:1944-2007`). **No pure-Python regex path** → no whole-file ReDoS. If neither binary present → explicit
  "install ripgrep" error (never a silent wrong answer).
- **Issue-by-issue:**
  - SEARCH-1: relies on rg default (respects gitignore); documented; no toggle. ⚠️ (same as ours but honest)
  - SEARCH-2: include via `--glob`; same override caveat as rg generally. ⚠️
  - SEARCH-3: two dialects only (rg Rust, grep BRE) and grep is a *fallback*; better than openhands' three.
    Would be ✅ if grep fallback is dropped.
  - SEARCH-4: rg/grep subprocess only, 60s timeout; no Python whole-file scan. ✅ (mitigated)
  - SEARCH-5: rg for both content and `--files`; grep fallback uses `--exclude-dir='.*'` to mirror rg hidden
    handling → broadly consistent. ✅-ish
  - SEARCH-6: `truncated = total > offset+limit`; fetches `limit+offset(+200 for context)`. ✅
  - SEARCH-7: rg/find, no symlink-loop Python walk. ✅
  - SEARCH-8: **no `-i`** → case-sensitive. ✅ (no per-call toggle in this version)
  - XC-2: not enforced here (path validated for existence, not containment). ❌ (we'd add it)
- **Also:** `_split_tool_diagnostics` (`:289-341`) separates `rg:`/`grep:` error lines from match lines — so an
  error never parses as a match and a hard failure has a clean message (directly relevant to our `is_error`
  handling). `set -o pipefail` so rg exit 2 isn't masked by `| head` (distinguishes partial-with-matches from
  real error). `limit`/`offset` pagination. Windows drive-letter-aware parsing.
- **Code quality:** very good and battle-tested; the one wart is building **shell command strings**
  (`shell=True`-style with `_escape_shell_arg`) rather than argv lists.
- **Pros:** Python; idioms we can lift almost verbatim (diagnostics-split, exit-code handling, pagination,
  output modes content/files_only/count).
- **Cons:** shell-string construction (swap for argv on port); no containment; gitignore not toggleable.
- **Integration:** **PORT (Python)** — lowest-friction source for the execution/parsing layer.

### 3.4 Gemini CLI (google-gemini/gemini-cli) — TypeScript, **Apache-2.0** — *richest param surface*
- **Paths:** `packages/core/src/tools/ripGrep.ts` (grep), `.../glob.ts`, `.../grep-utils.ts`,
  `scripts/download-ripgrep-binaries.ts`.
- **Overview:** grep = ripgrep; glob = npm `glob` package; **both** funnel through one `FileDiscoveryService`
  for ignore semantics, so the two backends agree.
- **Backend:** `resolveRipgrepPath` prefers a **bundled per-platform binary**, else system `rg` *only if*
  `isTrustedSystemPath` (RCE / search-path-interruption hardening), else hard-fails (`ripGrep.ts:53-95,489-491`).
- **Issue-by-issue:**
  - SEARCH-1: `no_ignore` param (`--no-ignore`); honors `getFileFilteringRespectGitIgnore()`; `.geminiignore`
    via `--ignore-file`; documented in schema. ✅ (configurable + documented)
  - SEARCH-2: include via `--glob`; ignore policy is explicit and uniform. ✅
  - SEARCH-3: single Rust dialect at runtime. ✅ (validates with JS `new RegExp` up front — dialect mismatch is
    cosmetic; the *match* runtime is one dialect)
  - SEARCH-4: hard `AbortController` timeout (`DEFAULT_SEARCH_TIMEOUT_MS`) aborts the rg subprocess
    (`:238-287`); rg's linear engine = no ReDoS. ✅
  - SEARCH-5: grep (rg) and glob (node-glob) both post-filter through `FileDiscoveryService` with the same
    `respectGitIgnore`/`respectGeminiIgnore` → consistent despite different backends; reports `ignoredCount`. ✅
  - SEARCH-6: `wasTruncated = matchCount >= totalMaxMatches` (`grep-utils.ts:177`); honest. ✅
  - SEARCH-7: glob uses `follow: false` → no symlink-loop recursion. ✅
  - SEARCH-8: `case_sensitive` param (default false → `--ignore-case`), documented. ✅ (best-documented toggle)
  - XC-2: `validatePathAccess(dir,'read')` → `PATH_NOT_IN_WORKSPACE`, plus per-match `path.relative` `..`/abs
    rejection (`ripGrep.ts:189-202,544-551`). ✅ (strongest, multi-layer)
- **Also:** `fixed_strings`, `exclude_pattern`, `context/before/after`, `max_matches_per_file`,
  `total_max_matches`; auto-context enrichment to save agent turns.
- **Code quality:** excellent; the most complete feature set; trusted-path hardening is a standout.
- **Pros:** the parameter surface to copy wholesale; explicit, documented ignore + case toggles; strong
  containment; bundles rg at build (`download-ripgrep-binaries.ts`) as an alternative to runtime download.
- **Cons:** large/feature-heavy; TypeScript; two backends (rg + node-glob) to keep aligned.
- **Integration:** **design-to-port** (Apache-2.0) — primary source for the *param schema* and containment.

### 3.5 openclaw (openclaw/openclaw) — TypeScript, **MIT**
- **Paths:** `src/agents/sessions/tools/grep.ts`, `src/agents/utils/tools-manager.ts` (file-find via a separate
  fd-backed `find` tool).
- **Overview:** like opencode — `ensureTool("rg")` locates system rg/fd else **downloads the GitHub release**
  (SSRF-guarded fetch, tar/zip extract, chmod 0755); hard-errors if rg unavailable. No silent degradation.
- **Issue-by-issue:** SEARCH-1 respects gitignore (documented), no toggle in this file ⚠️; SEARCH-3 single Rust
  dialect ✅; SEARCH-4 `AbortSignal` + kills child, no Python path ✅; SEARCH-5 `--hidden` consistent + file-find
  is a separate tool; SEARCH-6 match-limit + byte-limit + per-line truncation, each with an actionable notice
  ("Use limit=N*2 for more") ✅; SEARCH-7 rg only ✅; **SEARCH-8 `ignoreCase` param, default false** ✅; XC-2 not
  enforced here ❌.
- **Code quality:** very good; pluggable `operations` (remote/SSH backends) — nice fit for distributed calfcord;
  SSRF-guarded downloader is a thoughtful touch.
- **Pros:** clean pinned-binary manager (alt to opencode); honest, actionable truncation; case toggle.
- **Cons:** TS; file-find is fd-based (different tool); no containment in the grep tool itself.
- **Integration:** **design-to-port** (MIT) — secondary reference for the binary manager + truncation UX.

### 3.6 Codex CLI (openai/codex) — Rust, **Apache-2.0** — *file-find only*
- **Path:** `codex-rs/file-search/src/lib.rs` (fuzzy file finder, glob analog). No dedicated content-grep tool —
  the model runs `rg`/`grep` in the sandboxed shell.
- **Overview:** built on the `ignore` crate (ripgrep's gitignore engine) + `nucleo` (fuzzy matcher).
- **Issue-by-issue (file-find):** SEARCH-1 `respect_gitignore` toggle (documented, `:105-113`) ✅;
  **`require_git(true)`** (`:402-433`) — only honor `.gitignore` inside a git repo (matches git's own behavior;
  prevents a parent `~/.gitignore *` from hiding everything; regression test #3493 at `:1041-1102`) — this is
  the *correct* gitignore-scope fix for our SEARCH-1/9 concern, and exactly the design point Claude Code debates
  in issue #9411; SEARCH-7 `follow_links(true)` but the `ignore` crate has built-in loop detection ✅;
  SEARCH-6 `matches_truncated = total > shown` (`:277`) ✅; cancelation via `cancel_flag`; deterministic
  tie-break (score desc, path asc, `:320-333`).
- **Code quality:** excellent, but it's *fuzzy* matching, not literal glob — a different UX.
- **Pros:** the canonical gitignore-scope (`require_git`) reference; loop-safe; correct truncation.
- **Cons:** Rust; fuzzy (not glob); no content-grep tool.
- **Integration:** **design-to-port** (Apache-2.0) — adopt the `require_git`-style gitignore scoping idea;
  optionally the `ignore`-crate engine *concept* (Python analog: rely on rg, or `pathspec` for our own walks).

### 3.7 Cline (cline/cline) — TypeScript, **Apache-2.0**
- **Paths:** `apps/vscode/src/services/ripgrep/index.ts` (product grep);
  `sdk/packages/core/src/extensions/tools/executors/search.ts` (SDK search).
- **Overview (product):** VSCode's **bundled** rg via `getBinaryLocation("rg")` — pinned, no fallback chain.
  Args `["--json","-e",regex,"--glob",pat,"--context","1",dir]` (`-e` safe for leading-dash patterns; Rust
  regex; **case-sensitive**). Respects gitignore (rg default) + `.clineignore` post-filter. Bounded output:
  300-result cap **and** a 0.25MB byte budget with an explicit truncation message.
- **Issue-by-issue:** SEARCH-1 rg default + `.clineignore` (no toggle) ⚠️; SEARCH-3 single Rust dialect ✅;
  SEARCH-4 caps stdout lines + kills process (no hard timeout in product path; SDK path has a 5s rg timeout) ⚠️;
  SEARCH-6 byte+count caps with honest message ✅; SEARCH-8 case-sensitive (no toggle) ✅/⚠️; XC-2 N/A (VSCode
  workspace). The **SDK** path still has a silent rg→JS-regex fallback, but the JS fallback matches
  **line-by-line** with per-loop limit + AbortSignal — materially safer than openhands' whole-file Python scan.
- **Code quality:** good; the byte-budget truncation is a nice idea for Discord-sized outputs.
- **Pros:** bundled-rg determinism; byte-budget output bounding (useful given Discord limits).
- **Cons:** product path has no hard timeout; SDK path keeps a (safer) fallback chain; no case toggle.
- **Integration:** **design-to-port** (Apache-2.0) — borrow the byte-budget output bounding.

### 3.8 OpenAI Agents SDK (openai/openai-agents-python) — Python, MIT — *not applicable*
- `FileSearchTool` (`src/agents/tool.py:556`, type `"file_search"`) is a **hosted** tool over OpenAI vector
  stores, not a local fs grep/glob. Local code search exists only inside hosted Codex/sandbox extensions
  (which shell out to rg/grep). **Nothing to port.**

### 3.9 Claude Agent SDK Python (anthropics/claude-agent-sdk-python) — Python, MIT — *design reference only*
- No grep/glob implementation in the SDK; `"Grep"`/`"Glob"` are tool *names* forwarded to the `claude-code`
  CLI subprocess (impl is server-side in bundled JS). **Design (from docs + issue #9411):** ripgrep-backed
  (bundled `@vscode/ripgrep`; `USE_BUILTIN_RIPGREP=0` → system rg), respects `.gitignore`/`.ignore`/`.rgignore`,
  **case-sensitive by default with explicit `-i`**, output modes (content/files_with_matches/count),
  glob/type/`-A/-B/-C`/multiline/head_limit; Glob sorts by mtime. The `require_git` gitignore nuance is a known
  limitation (#9411). Same shape as opencode/gemini/openclaw — validates the recommended design.

---

## 4. Comparison matrix (candidates × issues / themes)

Legend: ✅ solved/strong · ➖ partial/mitigated · ❌ not addressed · n/a not applicable. "(cfg)" = behavior is a
documented parameter.

| | SEARCH-1 ignore | SEARCH-2 include | SEARCH-3 dialect | SEARCH-4 ReDoS/timeout | SEARCH-5 grep≡glob | SEARCH-6 truncation | SEARCH-7 symlink | SEARCH-8 case | XC-2 containment | Backend determinism |
|---|---|---|---|---|---|---|---|---|---|---|
| **openhands (ours)** | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ silent chain |
| **opencode** | ➖→✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ pinned+download |
| **gemini-cli** | ✅(cfg) | ✅ | ✅ | ✅(cfg) | ✅ | ✅ | ✅ | ✅(cfg) | ✅ | ✅ bundled+trusted |
| **openclaw** | ➖ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅(cfg) | ❌ | ✅ pinned+download |
| **hermes-agent** | ➖ | ➖ | ➖→✅ | ✅ | ➖ | ✅ | ✅ | ✅ | ❌ | ➖ rg→grep fallback |
| **codex (file-find)** | ✅(cfg) | n/a | n/a | ✅ | n/a | ✅ | ✅ | ✅(fuzzy) | ➖ | ✅ `ignore` crate |
| **cline** | ➖ | ✅ | ✅ | ➖ | ✅ | ✅ | ✅ | ➖ | n/a | ✅ bundled (SDK: ➖) |
| **Claude Code** | ✅(cfg) | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅(cfg) | n/a | ✅ bundled |

Cross-cutting themes (deterministic backend, consistent+documented ignore, single regex dialect, regex
safety/timeout, symlink-loop safety, honest truncation, ordering, case control, large-repo perf): the
ripgrep-pinned candidates (opencode/openclaw/gemini/cline/Claude Code) satisfy all of them; openhands fails
nearly all; hermes satisfies most with rg-only and would satisfy all if the grep fallback is dropped and
containment + an ignore toggle are added.

---

## 5. Ranking with rationale

1. **opencode** — the most complete, cleanest *deterministic* design: pinned+auto-download rg, `--no-config`/env
   scrub, uniform hidden/ignore flags for grep & files, `limit+1` truncation, JSON streaming, typed
   invalid-pattern + partial signals, cancelation, containment. Everything we need, expressed minimally.
2. **hermes-agent** — best *Python* source. rg-first/no-Python-ReDoS, diagnostics-split, exit-code handling,
   pagination, output modes. Lowest-friction execution/parsing layer to lift; needs containment + (optional)
   ignore toggle and a swap from shell-strings to argv.
3. **gemini-cli** — best *parameter surface* and containment (`no_ignore`, `case_sensitive`, `fixed_strings`,
   context, `total_max_matches`, `validatePathAccess`, trusted-system-binary check). Slightly heavier; two
   backends to align.
4. **openclaw** — clean alternate binary-manager + actionable truncation UX + pluggable remote `operations`
   (interesting for distributed calfcord); no containment in the grep tool.
5. **codex** — authoritative for the gitignore *scope* fix (`require_git`) and loop-safety; but file-find only
   and Rust.
6. **cline** — useful byte-budget output bounding; product path lacks a hard timeout; SDK keeps a fallback.

(OpenAI Agents SDK / Claude Agent SDK: no local tool to adopt; Claude Code = design validation only.)

---

## 6. Final recommendation

### Adopt: **PORT** a calfcord-owned ripgrep-only search core (do not keep the openhands executors)

Because SEARCH-1…-8 are all upstream openhands defects with no config surface, and the silent fallback chain is
itself the core defect, wrapping or monkeypatching cannot make our tools deterministic. Build a small,
calfcord-owned core and drop the `GrepExecutor`/`GlobExecutor` dependency for these two tools. Concrete design,
synthesizing the candidates:

1. **Single pinned backend (fixes SEARCH-3, SEARCH-4 root, the fallback chain).** Require ripgrep. Resolve in
   this order, like opencode/openclaw/gemini: pinned vendored binary → trusted system `rg` (validate the
   resolved real path is in a trusted dir, per gemini's `isTrustedSystemPath`, to avoid search-path hijack) →
   *optional* auto-download of the pinned release. If unavailable, **hard-fail with a clear message** — never
   fall back to system grep or a Python regex walk. Run with `--no-config` and scrub `RIPGREP_CONFIG_PATH`
   (opencode) so behavior is host-independent. This eliminates the three dialects, the BRE surprises, and the
   unbounded Python ReDoS in one move.
2. **Explicit, uniform, documented ignore + hidden semantics (fixes SEARCH-1, -2, -5).** Use the *same* glob/
   ignore flags for both grep and file-find. Default to honoring `.gitignore` **but** add a documented
   `no_ignore` param (gemini) and apply codex's `require_git`-style scoping consideration so a stray parent
   `.gitignore` can't silently blank results (and to behave sanely in non-git workspaces — the #9411 problem).
   Make `include` a pure narrowing filter on top of the chosen ignore policy (no override surprise).
3. **Regex safety (fixes SEARCH-4).** Hard subprocess timeout (gemini/hermes: ~30-60s) + cancelation
   (`AbortSignal`/process kill on limit, per opencode/openclaw). With rg's linear engine there is no
   catastrophic backtracking; surface invalid patterns as a typed error (opencode's exit-2 + stderr parse).
4. **Case control (fixes SEARCH-8).** Add a `case_sensitive` param, **default true** (rg default), documented
   (gemini/openclaw/Claude Code). Update the docstring to stop promising "full regex syntax" without the rg
   caveat (no backreferences/lookaround).
5. **Honest truncation + bounded output (fixes SEARCH-6).** Fetch `limit+1` (or `--max-count`) and compute
   `truncated` from the real overflow (opencode/hermes/gemini). Add a byte budget for Discord-sized outputs
   (cline's 0.25MB idea) with an actionable "narrow your search / use limit=N*2" notice (openclaw).
6. **Symlink-loop safety (fixes SEARCH-7).** rg `--files`/walk dedups by resolved path and doesn't follow
   symlinks unless asked; if we ever add a Python file-find path, dedup by `.resolve()` and don't follow loops
   (codex/gemini `follow:false`).
7. **Workspace containment (fixes XC-2).** Resolve the search path and reject (or require explicit consent for)
   escapes outside the workspace root (gemini `validatePathAccess` + per-match `..`/abs rejection; opencode's
   `assertExternalDirectory` consent model). Important because the path comes from LLM output influenced by
   untrusted Discord chat.
8. **Error/diagnostics discrimination.** Split rg diagnostics from match output and distinguish *partial*
   (exit 2 with matches) from *real error* (exit 2, empty) from *no matches* (exit 1) — hermes' `pipefail` +
   `_split_tool_diagnostics`, opencode's `partial` flag — so the LLM gets `is_error`/partial signals instead of
   a misleading clean "no matches."

**Sourcing:** structure the core on **opencode**'s adapter; lift execution/parsing/pagination idioms from
**hermes-agent** (Python); copy the **gemini-cli** parameter schema and containment. All are MIT/Apache-2.0, so
reimplementation is clean. This also retires the audit's XC-1/XC-3 amplifiers for search (no Python ReDoS to
serialize; no per-call hang to wedge `max_workers=1`).

### If a full port is not yet scheduled — **interim stay-and-fix band-aids** (partial, documented as such)
None of these fix the dialect/ignore root causes, but they reduce the worst exposure while a port is planned:
- **Pin ripgrep in the deploy image** so the executors never select system-grep or the Python ReDoS path
  (neutralizes SEARCH-3's grep/Python branches and SEARCH-4 in practice). Document that rg is required.
- **Add containment** in `_resolve_search_path` (XC-2) — resolve and reject out-of-workspace paths; this is
  entirely in our wrapper.
- **Stop trusting `obs.truncated` for glob** (SEARCH-6): the wrapper can ignore it and re-derive, or just
  document the off-by-one.
- **Fix the docstrings** (SEARCH-3/8/1/2/5): state ripgrep dialect (no backreferences), case-insensitivity, and
  gitignore behavior so the LLM and operators aren't misled.
- File openhands/calfkit issues for: a backend-pin/no-fallback option, flag injection (`--no-ignore`/`--hidden`/
  case), per-file regex timeout on the Python path, and the glob `truncated` off-by-one (`>=100` → `>100`).

These are explicitly a stopgap; the durable fix is the ripgrep-only port in §6.
