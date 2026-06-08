# File-tools research log

Realtime log for the file-tools (read_file/write_file/edit_file) replacement research.

## Step 1 — Verification of our issues (source read + empirical)

Source read: `src/calfcord/tools/builtin/fs.py`, `workspace.py`, `_observation.py`;
upstream `.venv/.../openhands/tools/file_editor/{editor,impl,definition,exceptions}.py`
and `utils/encoding.py`.

### Architecture facts confirmed
- `fs.py` wraps a module-global `FileEditorExecutor(workspace_root=...)` for `read_file`
  (`view`) and the non-`replace_all` `edit_file` (`str_replace`).
- `write_file` and the `replace_all` branch are **pure Python** (`Path.write_text`,
  `str.replace`) — they do NOT go through the executor.
- One shared `calfkit-tools` worker, `max_workers=1`, module-global executor shared
  across all agents.

### FS-1 — `replace_all` ignores upstream whitespace-strip fallback — CONFIRMED
- Upstream `editor.py:204-223`: when `re.finditer(re.escape(old_str))` finds **zero**
  occurrences, it `old_str.strip()`/`new_str.strip()` and retries before erroring.
- Our `replace_all` (`fs.py:212-218`) uses raw `old_string in original` /
  `original.count` / `str.replace` — **no strip fallback**.
- Result: a whitespace-padded `old_string` succeeds with `replace_all=False`
  (upstream strips) but errors `old_string did not appear verbatim` with
  `replace_all=True`. Behavior splits on the flag. (Also: upstream re-`escape`s for
  regex but does literal compare; net effect is literal, so no regex difference —
  only the strip fallback differs.)

### FS-2 — `replace_all` `old == new` false success — CONFIRMED
- Upstream guards at `editor.py:129-135`: `if new_str == old_str: raise
  EditorToolParameterInvalidError(...)`. Non-`replace_all` path hits this.
- Our `replace_all` path has no guard → `count > 0`, `str.replace` is a no-op,
  file rewritten byte-identical, returns `Edited …: replaced N occurrence(s)` —
  success message for a no-op.

### FS-3 — `UnicodeEncodeError` escapes `except OSError` — CONFIRMED (empirically)
- `write_file` (`fs.py:147-149`) and `edit_file` replace_all (`fs.py:220-222`) both
  catch only `OSError`.
- Empirical: `Path('…').write_text('a\ud800b', encoding='utf-8')` raises
  `UnicodeEncodeError`; `isinstance(UnicodeEncodeError(), OSError) -> False`.
- So a lone surrogate in LLM output makes the tool **raise** instead of returning
  `error:`, breaking the `error:`-string contract. (Read side correctly catches
  `UnicodeDecodeError` at `fs.py:210`.)
- Note: upstream `write_file` (`editor.py:465-470`) wraps the write in
  `except Exception` → `ToolError`, so the **executor** path is robust; only our
  pure-Python paths are exposed.

### FS-4 — docstring claims a loop the code doesn't implement — CONFIRMED
- Module docstring `fs.py:23-26`: "drives upstream in a loop replacing one
  occurrence at a time until `old_string` no longer appears."
- Actual `replace_all` (`fs.py:191-224`): single `str.replace`, no executor, no loop.
- Empirical: `'aaa'.replace('aa','X') == 'Xa'` (1 replacement, non-overlapping),
  `'aaa'.count('aa') == 1`. A true loop-until-gone could differ on overlapping
  matches. So the doc is both factually wrong (no loop) and semantically wrong
  (single-pass non-overlapping).

### FS-5 — silent overwrite + symlink write-through — CONFIRMED (empirically)
- `write_file` uses `Path.write_text` (`fs.py:147`), which:
  - Overwrites with no created-vs-overwrote signal (returns `Wrote N characters`
    either way). Upstream `create` refuses to clobber (`editor.py:592-598`).
  - **Writes through symlinks**: empirical — `link.symlink_to(target);
    link.write_text('CLOBBERED')` left `target` == `CLOBBERED` and `link` still a
    symlink. So `write_file("link", …)` clobbers the link target.
  - `mkdir(parents=True, exist_ok=True)` creates dir trees wherever the path lands.
- No file history / undo (pure-Python path bypasses upstream `FileHistoryManager`,
  which keeps last 10 versions per file — `editor.py:78`).

### FS-6 — docstring conflates "binary" and "too large" — CONFIRMED
- `read_file` docstring `fs.py:104`: "binary file too large". Upstream treats these
  as TWO distinct failures: size cap `MAX_FILE_SIZE_MB=10` (`editor.py:58,651-661`)
  and binary detection via `binaryornot.is_binary` (`editor.py:665-672`). Both
  surface as `error:` (via `FileValidationError(ToolError)`).

### FS-7 — absolute paths not `.resolve()`d → lock-key mismatch — CONFIRMED
- `_resolve_path` (`fs.py:74-77`) returns raw absolute strings unchanged; only the
  relative branch `.resolve()`s.
- Upstream lock key `definition.py:205-206`: `file:{Path(action.path).resolve()}`.
- So an absolute path with `..`/symlink reaches the executor un-normalized; the
  per-file `DeclaredResources` lock key differs per call style. Latent at
  `max_workers=1`; would break per-file concurrency locking if raised.

### XC-2 — no workspace containment — CONFIRMED
- `_resolve_path` passes absolute paths through unchanged and joins relative then
  `.resolve()` with **no `is_relative_to`/`commonpath` assertion**. `../../../etc/passwd`
  escapes; absolute `/home/.../.ssh/id_rsa` passes through; in-workspace symlink to
  an external target resolves out (read AND write). LLM path string is influenced by
  untrusted Discord content → prompt-injection exfiltration of `.env`/SSH keys.

### XC-1 — module-global executor never closed — CONFIRMED
- `_executor` global (`fs.py:48,51-63`) created lazily, never torn down. Less severe
  for file_editor than shell (no tmux), but still an unclosed resource; the
  `EncodingManager` LRU cache and `FileHistoryManager` leak across restart with no
  lifecycle hook registered.

### Key upstream nuance for candidate grading
- Upstream `str_replace` uses the file's **auto-detected encoding**
  (`with_encoding` + `charset_normalizer`, `encoding.py:36-99`) for read AND write.
  Our pure-Python `replace_all` pins UTF-8 → can corrupt a latin-1/utf-16 file that
  upstream would round-trip. Another flag-split divergence.
- Upstream `insert` uses tempfile + `shutil.move` (atomic-ish) — but
  `create`/`str_replace`/`write_file` use plain `open(... 'w')` (NON-atomic,
  truncate-in-place). So even the incumbent is not fully atomic on the write path.
- Upstream catches only `ToolError` in `impl.py:68` and `file_editor():102`;
  `EditorToolParameterInvalidError`/`Missing` subclass `ToolError` so they ARE
  caught. A non-`ToolError` (e.g. surprise `OSError` outside the wrapped spots)
  would still propagate — but the editor wraps read/write in `except Exception`.

---
## Step 2 — Candidate survey (appended below as gathered)

---
## Candidate: OpenCode (sst/opencode) — TypeScript/Bun, MIT
Repo path: `packages/opencode/src/tool/{read,write,edit}.ts`,
`packages/opencode/src/tool/external-directory.ts`,
`packages/core/src/fs-util.ts`, `packages/opencode/src/util/bom.ts`.

### Highlights
- **edit.replace() cascade of 9 replacers** (`edit.ts:682-729`): Simple →
  LineTrimmed → BlockAnchor → WhitespaceNormalized → IndentationFlexible →
  EscapeNormalized → TrimmedBoundary → ContextAware → MultiOccurrence. First
  replacer that yields a locatable span wins. Beats upstream's single
  strip-fallback by a mile.
- **`old==new` guard** (`edit.ts:75-77,683-685`) — addresses FS-2.
- **empty-oldString guard** on existing files (`edit.ts:90-96`) — refuses
  accidental full-file clobber via edit.
- **isDisproportionateMatch** (`edit.ts:731-737`) — refuses a fuzzy match whose
  span is wildly larger than oldString (anti-footgun on the fuzzy replacers).
- **Per-file in-process lock** via `Map<resolvedPath, Semaphore>` (`edit.ts:35-45`)
  — concurrency safety (addresses FS-7 intent in-process).
- **BOM preserve** (`bom.ts`) + **CRLF/LF preserve** (`edit.ts:22-33,129-131`).
- **created-vs-overwrote**: write returns `metadata.exists` (`write.ts:97`).
- **read**: byte-capped (50KB) streaming, per-line length cap (2000), binary
  detection (ext list + NUL/non-printable ratio >0.3), image/PDF as attachments,
  directory listing with symlink-aware suffixing.
- **containment**: `assertExternalDirectoryEffect` → `containsPath` (relative,
  `..`-aware via `FSUtil.contains`, `fs-util.ts:245-248`). `FSUtil.resolve`
  (`fs-util.ts:222-230`) does `realpathSync` (symlink-resolving) with ENOENT
  fallback. BUT the tools call `containsPath` on the **non-realpath** path, then
  fall back to a *permission prompt* (`ask permission: external_directory`), not a
  hard reject. So traversal/symlink is gated by an interactive approval, not a
  containment assertion. For a Discord bot with no human-in-the-loop per tool
  call, the permission model maps poorly (would need an auto-deny policy).
- **NOT atomic**: `writeWithDirs` → `fs.writeFileString` (truncate-in-place); no
  temp+rename, no fsync. Writes through symlinks at the leaf (no O_NOFOLLOW).
- **Stale-read protection**: read registers files; edit/write don't hard-require a
  prior read in this version (older opencode did via a "FileTimes" check).

### Issue scorecard
- FS-1 ✅ (cascade replacers incl. WhitespaceNormalized/TrimmedBoundary)
- FS-2 ✅ (guard) ; FS-3 N/A (TS, no encode-exception class issue; JS strings)
- FS-4 N/A ; FS-5 ⚠️ partial (created/overwrote signal ✅; symlink leaf write-through ❌; no history but has Snapshot/git)
- FS-6 ✅ (binary vs size distinguished) ; FS-7 ✅ in-process per-file lock
- XC-2 ⚠️ (containment exists but via permission-prompt, not hard reject)
- XC-1 N/A (no long-lived OS resource like tmux)
- Integration: **port (design)**, not a dep (TS/Bun + Effect runtime). The
  replacer cascade is the crown jewel and is cleanly portable to Python.

---
## Candidate: Gemini CLI (google-gemini/gemini-cli) — TypeScript, Apache-2.0
Repo path: `packages/core/src/tools/{write-file,edit,read-file}.ts`,
`packages/core/src/utils/{workspaceContext,paths}.ts`,
`packages/core/src/utils/editCorrector.ts`,
`packages/core/src/services/fileSystemService.ts`.

### Highlights
- **Best-in-class traversal/symlink defense**: `WorkspaceContext.isPathWithinWorkspace`
  (`workspaceContext.ts:181-194`) → `fullyResolvedPath` → `resolveToRealPath` →
  `robustRealpath` (`paths.ts`). `robustRealpath` resolves symlinks WITH loop
  detection AND resolves NON-existent paths by recursing on the parent and
  re-joining basename — so the workspace check is correct even for a file about to
  be created. Then `isPathWithinRoot` does `..`-aware relative containment.
  **Directly solves XC-2 and FS-7 (resolve-before-compare).**
- Write does `validatePathAccess(resolvedPath)` → `PATH_NOT_IN_WORKSPACE` hard
  error (`write-file.ts:278-288`). HARD REJECT (no human prompt needed) — fits a
  Discord bot.
- **created-vs-overwrote** via `isNewFile` (`write-file.ts:320,374-377`):
  "Successfully created…" vs "Successfully overwrote…".
- **`lstatSync` directory guard** (`write-file.ts:546-551`) — note lstat (doesn't
  follow link) for the is-dir check.
- **Per-errno mapping**: EACCES→PERMISSION_DENIED, ENOSPC→NO_SPACE_LEFT,
  EISDIR→TARGET_IS_DIRECTORY (`write-file.ts:453-467`). Robust error contract.
- **CRLF/LF preserve** (`write-file.ts:334-341`, `detectLineEnding`).
- **omission-placeholder detection** (`write-file.ts:558-561`) — rejects content
  with "rest of methods …" truncation markers (anti-lazy-LLM).
- **edit.ts**: multi-strategy (exact → flexible/whitespace → regex), explicit
  occurrence count with `allow_multiple` flag (FS-2-style 0/1/N handling at
  `edit.ts:362-372`), **hash-based stale-content detection** (`onDiskContentHash`,
  `edit.ts:538`), and an **LLM edit-fixer** fallback (`FixLLMEditWithInstruction`).
- read returns diff snippet to save a verification turn.
- **NOT atomic**: `fileSystemService.writeTextFile` = plain `fs.writeFile`
  (`fileSystemService.ts:38-39`). No temp+rename, no fsync. Leaf symlink still
  followed on write — but containment resolves symlinks first so an in-workspace
  symlink→outside is rejected pre-write.

### Issue scorecard
- FS-1 ✅ (flexible/whitespace strategies) ; FS-2 ✅ (occurrence count + allow_multiple)
- FS-3 N/A ; FS-4 N/A ; FS-5 ✅ (created/overwrote + lstat dir guard) [history: relies on IDE/diff + git]
- FS-6 ✅ ; FS-7 ✅ (realpath before workspace check)
- XC-2 ✅✅ **strongest** (hard reject, symlink+`..`+nonexistent handled)
- XC-1 N/A
- Integration: **port (design)**. `robustRealpath`/`isPathWithinWorkspace` is ~40
  lines, trivially portable to Python (`os.path.realpath` + walk-up). The
  containment logic is the single most valuable thing to lift for calfcord/XC-2.

---
## Candidate: Codex CLI (openai/codex) — Rust, Apache-2.0
Repo path: `codex-rs/apply-patch/src/{lib,seek_sequence,parser}.rs`,
`codex-rs/core/src/tools/{sandboxing,runtimes/apply_patch}.rs`.

### Highlights
- **Patch-envelope edit model** (`*** Begin Patch / *** Update File: … / @@ …`),
  not str-replace. One tool (`apply_patch`) does add/update/delete/move.
- **`seek_sequence` graduated fuzzy matcher** (`seek_sequence.rs`): exact →
  trim_end → trim both sides; EOF-anchored matching; guards empty pattern and
  pattern-longer-than-file (no panic). This is the patch analog of the strip
  fallback — solves FS-1 class issues robustly.
- **symlink-aware**: `note_existing_path_delta_support` (`lib.rs:599-611`) marks
  `is_symlink` paths as non-exact; `lib.rs:606` requires `is_file && !is_symlink`;
  there's a `test_delete_symlink_still_verifies`. Codex treats symlinks as a
  first-class hazard.
- **Sandbox/containment at the FS-trait layer**: `ExecutorFileSystem` +
  `FileSystemSandboxContext` thread a sandbox through every read/write; writable
  roots enforced (OS-level sandbox: Seatbelt/macOS, Landlock/Linux). Defense in
  depth beyond path strings.
- `write_file_with_missing_parent_retry` (`lib.rs:613-645`): writes, on NotFound
  creates parents then retries (lazy mkdir).
- NOT a simple temp+rename atomic write at this layer (the OS sandbox is the
  safety net); patch model means the LLM submits a contextual diff, which is
  inherently more verifiable than blind str-replace.

### Issue scorecard
- FS-1 ✅ (seek_sequence) ; FS-2 N/A (patch model; empty/no-op hunks rejected at parse)
- FS-3 N/A ; FS-4 N/A
- FS-5 ✅ symlink-aware; created/overwrote implicit in Add vs Update file ops
- FS-6 ✅ binary detected ; FS-7 ✅ (AbsolutePathBuf normalized, sandbox roots)
- XC-2 ✅ (OS sandbox + writable-root containment) — heaviest machinery
- XC-1 N/A
- Integration: **not feasible as a dep** (Rust). The **patch-edit semantics +
  seek_sequence matcher are portable as a design**, but porting a contextual-diff
  applier is much more work than str-replace + a replacer cascade. The apply_patch
  format also widens the LLM tool surface vs Claude-Code-style Edit. Lower priority
  to port than gemini's containment + opencode's cascade.

---
## Candidate: Cline (cline/cline) — TypeScript (VS Code extension), Apache-2.0
Repo path: `apps/vscode/src/core/assistant-message/diff.ts`,
`apps/vscode/src/core/task/tools/handlers/WriteToFileToolHandler.ts`,
`apps/vscode/src/integrations/editor/DiffViewProvider.ts`.

### Highlights
- **SEARCH/REPLACE block edit format** (`------- SEARCH / ======= / +++++++ REPLACE`)
  parsed incrementally (streaming) — `diff.ts`. This is the ORIGIN of opencode's
  and gemini's fuzzy matchers (opencode header credits cline diff-apply).
- **Three-tier matcher**: exact `indexOf` → `lineTrimmedFallbackMatch` (per-line
  trim compare) → `blockAnchorFallbackMatch` (first/last line as anchors, ≥3
  lines). Solves FS-1 class robustly at the block level.
- containment: `isLocatedInWorkspace(relPath)` + `resolveWorkspacePath` (multi-root
  workspace aware). Surfaced to the user as an approval flag, not a hard reject.
- **persists through VS Code `DiffViewProvider`** (opens an editor diff, applies via
  the workspace edit/document API, user approves). Deeply editor-coupled.

### Issue scorecard
- FS-1 ✅ (3-tier matcher) ; FS-2 — (SEARCH/REPLACE; identical block is a no-op match)
- FS-3/FS-4 N/A ; FS-5 — (editor handles create vs overwrite UX) ; FS-6 ✅
- FS-7 — ; XC-2 ⚠️ (workspace flag + human approval, not hard reject)
- Integration: **not feasible** as-is — the value (DiffViewProvider, streaming
  approvals) is VS Code-bound. Only the **matcher algorithm is portable**, and
  opencode already packages that algorithm better (9 strategies vs 3) in a
  non-editor context. Use opencode as the port source, not cline.

---
## Candidate: OpenAI Agents SDK (openai/openai-agents-python) — Python, MIT
Repo path: `src/agents/sandbox/capabilities/filesystem.py`,
`src/agents/sandbox/apply_patch.py`, `src/agents/sandbox/workspace_paths.py`.

### Highlights
- File tools are NOT host-local: the `Filesystem` capability exposes
  `apply_patch` + `view_image` that run **inside a SandboxSession** (docker /
  unix_local / remote). The sandbox IS the safety boundary.
- **`WorkspacePathPolicy`** (`workspace_paths.py:106+`) is a strong, pure-Python
  containment reference: `normalize_path(resolve_symlinks=...)`,
  `_resolved_host_path_and_grant` does `Path.resolve(strict=False)` then
  `_is_under(resolved, workspace_root)`; supports absolute-path **grants** and
  **read-only grants**; rejects root-resolving grants; rejects Windows-drive
  absolute escapes. Comprehensive XC-2 logic with a test suite
  (`tests/sandbox/test_workspace_paths.py`: symlink resolution, relative-root
  reject, windows-drive escape reject, read-only grant write reject).
- edit model is **apply_patch (V4A diff format)** — same family as Codex.
- `_write_text` (`apply_patch.py:171-177`): mkdir parents + session.write of
  UTF-8 bytes. NOT atomic at this layer (sandbox/session abstracts the FS).

### Issue scorecard
- FS-1 ✅ (apply_patch fuzzy) ; FS-2 N/A ; FS-3 ✅ (UnicodeDecodeError wrapped, apply_patch.py:164)
- FS-4 N/A ; FS-5 — (apply_patch add/update distinguishes) ; FS-6 ✅
- FS-7 ✅ (resolve before _is_under) ; XC-2 ✅ (WorkspacePathPolicy, with grants)
- XC-1 N/A (sandbox session has explicit lifecycle)
- Integration: **the WorkspacePathPolicy/containment is directly portable Python**
  and is a peer of gemini's `robustRealpath`. The whole sandbox machinery
  (docker/mounts/manifests/grants) is FAR too heavy to adopt wholesale — calfcord
  doesn't want a per-agent docker sandbox just for file tools. Adopt the
  containment idea; skip the sandbox.

---
## Incumbent confirmation: OpenHands file_editor is current
- The OpenHands monorepo (`All-Hands-AI/OpenHands` main) NO LONGER vendors a
  Python file editor — it consumes the separate `openhands-tools` / agent-sdk
  package, which is exactly what calfcord already pulls into `.venv`
  (`openhands/tools/file_editor/`). So our incumbent baseline == upstream current.
- Upstream's own gaps remain in the shipped package: non-atomic create/str_replace
  writes (plain `open('w')`), leaf symlink follow, no path containment (relies on
  caller), `create` refuses overwrite (why calfcord went pure-Python and inherited
  FS-1..FS-5). Upstream DOES have: charset auto-detect (read+write), 10MB cap,
  binaryornot detection, `FileHistoryManager` (10 versions/file → undo_edit),
  per-file `DeclaredResources` lock, tempfile+move for `insert` only.

---
## Claude Code (anthropics) — design from docs (no OSS tool source)
- `claude-agent-sdk-python` is a thin client around the closed-source Claude Code
  CLI; it does NOT ship Read/Write/Edit implementations. Graded from public docs/
  observed behavior only:
  - **Edit requires a prior Read** of the file in the same session (staleness
    guard) — strong anti-clobber design calfcord lacks.
  - Edit is **exact string match**, errors on non-unique `old_string` unless
    `replace_all`; `old==new` rejected. (Mirrors what we want for FS-2.)
  - Write **warns/blocks overwrite of a file not yet read**.
  - Read: line-numbered, offset/limit, multimodal (images/PDF), long-line + total
    truncation.
- Not adoptable as code (closed); a useful **design north-star** (read-before-edit
  staleness contract) that none of the OSS Python options enforce by default.

---
## Candidate: hermes-agent (NousResearch/hermes-agent) — Python, license TBD ⭐ TOP
Repo path: `tools/file_operations.py`, `tools/fuzzy_match.py`,
`agent/file_safety.py`, `tools/patch_parser.py`, `tools/binary_extensions.py`.
**Surface is a 1:1 match for calfcord: `read_file` / `write_file` / `patch`
(== edit_file with old/new/replace_all) + V4A `patch_v4a`.** PYTHON.

### Highlights (directly addresses our entire FS-*/XC-* list)
- **`_atomic_write`** (`file_operations.py:839-891`): streams content via stdin to
  a temp file in the SAME directory (`mktemp -p` → same-FS rename), copies the
  existing file's mode (`stat`+`chmod`), `mv -f` over target, `trap ... EXIT`
  cleans the temp on any failure. **Atomic + crash-safe + mode-preserving** —
  exactly the temp+rename our incumbent (and gemini/opencode) lack (FS-5).
  (NB: implemented as a shell script because hermes targets docker/ssh/modal
  backends; for calfcord's host-local model the pattern ports trivially to
  `tempfile.NamedTemporaryFile(dir=parent)` + `os.replace` + `os.fsync`.)
- **`fuzzy_match.fuzzy_find_and_replace`** (`fuzzy_match.py:50-144`): 9-strategy
  Python cascade — exact, line_trimmed, whitespace_normalized,
  indentation_flexible, escape_normalized, trimmed_boundary, unicode_normalized,
  block_anchor, context_aware. This is the opencode replacer cascade, **already
  ported to Python**. Solves FS-1.
  - **`old==new` guard** at `fuzzy_match.py:69` → FS-2. Empty-old guard at :66.
  - **Multi-match uniqueness** (`>1 and not replace_all` → error asking for
    context or replace_all) at :90-94 → consistent 0/1/N semantics (kills the
    flag-split that causes FS-1/FS-2).
  - **escape-drift guard** (:96-109) + **indentation reindent** (:111-141) — fixes
    LLM JSON tool-call serialization artifacts. Beyond what we need but valuable.
- **`file_safety.is_write_denied`** (`file_safety.py:96-105`): `os.path.realpath`
  (symlink-resolving) then deny exact paths (`~/.ssh/id_rsa`, `~/.git-credentials`,
  `.env`, `/etc/passwd`, `/etc/shadow`, `/etc/sudoers`) + deny prefixes (`~/.ssh`,
  `~/.aws`, `~/.gnupg`, `/etc/sudoers.d`, `/etc/systemd`). **Deny-list defense for
  XC-2**, applied on every write/patch/delete/move.
- **`HERMES_WRITE_SAFE_ROOT`** (`file_safety.py:85-93`): opt-in allow-root
  confinement (realpath-resolved) — exactly the `CALFCORD_WORKSPACE_CONFINE`
  pattern the audit proposed for XC-2.
- **post-write verification** in patch (`file_operations.py:1436-1466`): re-reads
  the file, normalizes endings/BOM, and confirms bytes landed — catches silent
  persistence failures. No calfcord path does this.
- **BOM preserve** (`_strip_bom`/`_has_bom`, restore on write) + **CRLF/LF
  preserve** (`_detect_line_ending`/`_normalize_line_endings`).
- **created/overwrote + dirs_created** in WriteResult; binary/image/size
  detection; similar-file suggestions on miss; in-process linters
  (ast/json/yaml/toml) + optional LSP delta diagnostics.

### Issue scorecard
- FS-1 ✅ (9-strategy cascade) ; FS-2 ✅ (old==new guard) ; FS-3 ✅ (UTF-8
  handled; errors returned as `error` field, no raised exception escapes)
- FS-4 N/A (its docstrings match impl) ; FS-5 ✅✅ (atomic temp+rename + mode
  preserve + dirs_created + symlink: writes through but deny-list+realpath guards
  the dangerous targets) ; FS-6 ✅ (binary vs size separate) ; FS-7 ✅ (realpath)
- XC-2 ✅✅ (deny-list realpath + opt-in safe-root) ; XC-1 N/A
- **History/undo**: none built-in (relies on git like calfcord) — only gap vs the
  openhands incumbent's FileHistoryManager.

### Integration feasibility — BEST of the field
- Pure-Python; the three crown-jewel modules (`fuzzy_match.py` ~860 lines,
  `file_safety.py`, the `_atomic_write` pattern) are **portable as-is** with light
  edits to drop the shell-backend indirection (calfcord is host-local: use
  `tempfile`+`os.replace`+`os.fsync` and `Path.read_text` instead of `_exec`).
  License must be confirmed before lifting code; the **designs** are free to adopt
  regardless. This is the single best source to port from.

---
## Candidate: openclaw (openclaw/openclaw) — TypeScript, license TBD
Repo path: `src/agents/apply-patch.ts`, `src/infra/fs-safe.ts`
(+ `@openclaw/fs-safe` package), `src/agents/path-policy.ts`,
`src/infra/path-alias-guards.ts`, `src/infra/boundary-file-read.ts`.

### Highlights
- **apply_patch (V4A) edit model** with add/update/delete/move hunks.
- **Strongest containment design — capability/root-handle**: `workspaceOnly`
  (default true) routes ALL writes through a confined `root(cwd)` handle
  (`fs-safe.ts`) that takes a RELATIVE path and forbids escape. The
  `@openclaw/fs-safe` package does per-handle realpath verification
  (`resolveOpenedFileRealPathForHandle`) and an explicit **symlink policy**
  (`"follow-within-root"` vs `"reject"`) — defeats TOCTOU symlink swaps, not just
  string-level `..`. `path-alias-guards` add per-op policies (e.g. unlink target).
  This is the most rigorous XC-2 answer in the field.
- Documented config `tools.exec.applyPatch.workspaceOnly` + a
  `core-dangerous-config-flags` warning when it's disabled — security posture is
  first-class and operator-visible.
- Non-`workspaceOnly` path falls back to plain `fs.writeFile` (no atomic temp+rename
  at this layer).

### Issue scorecard
- FS-1 ✅ (apply_patch fuzzy) ; FS-2 N/A ; FS-3 N/A ; FS-4 N/A
- FS-5 ⚠️ (create/update distinct; symlink handled by fs-safe policy; not temp+rename)
- FS-6 ✅ ; FS-7 ✅ (realpath in fs-safe) ; XC-2 ✅✅✅ (root-handle capability +
  per-handle realpath + symlink policy — best) ; XC-1 N/A
- Integration: **not feasible as a dep** (TS, large). The **root-handle capability
  pattern** (resolve to realpath, verify the opened handle stays within root,
  explicit symlink policy) is the best *design* to emulate for hardening XC-2 if
  calfcord wants TOCTOU-safe containment beyond a pre-check. Heavier than gemini's
  realpath-precheck; adopt only if the threat model warrants it.

---
## Candidate: Aider (Aider-AI/aider) — Python, Apache-2.0 (expansion)
Repo: `aider/coders/editblock_coder.py` (SEARCH/REPLACE matcher), `aider/io.py`.
- **SEARCH/REPLACE block** edit format; **layered matcher**: exact →
  whitespace-insensitive → indentation-preserving → difflib fuzzy. Same family as
  cline/opencode/hermes (a subset of hermes' 9 strategies).
- **Atomic git commits** per edit (its "history/undo" answer is git, like calfcord).
- Strongly git/repo-coupled and interactive-terminal-centric. Python but the file
  I/O is entangled with the chat/commit loop; less cleanly extractable than
  hermes' standalone `fuzzy_match.py`.
- Issue coverage: FS-1 ✅ (layered matcher), FS-2 ✅ (identical block rejected),
  FS-5 partial (git history), XC-2 — (assumes a trusted local repo; no hardening).
- Integration: **design reference only**. hermes' Python cascade is a strictly
  better port source (standalone module, MIT, 9 strategies, atomic write, deny-list).

---
## Cross-candidate synthesis (for the report)
- **Edit semantics**: the whole field has converged on a *graduated fuzzy matcher*
  (exact → trimmed → whitespace-normalized → indentation/block-anchor → fuzzy).
  Cline originated it; opencode (9 TS strategies) + gemini + Aider adapted it;
  **hermes ported the 9-strategy cascade to Python** (the directly usable artifact).
  Codex/openai-agents/openclaw use the **apply_patch (V4A) contextual-diff** model
  with `seek_sequence`-style fuzzy line matching — more powerful but a wider LLM
  tool surface than Claude-Code-style Edit.
- **Atomic writes**: only **hermes** does true temp-file-same-dir + rename (+mode
  preserve + trap cleanup + post-write verify). gemini/opencode/openhands all do
  truncate-in-place `writeFile`/`open('w')`. Codex/openai-agents/openclaw lean on
  OS sandbox / root-handle rather than rename-atomicity.
- **Traversal/symlink defense (XC-2)** ranked:
  1. openclaw root-handle + per-handle realpath + symlink policy (TOCTOU-safe)
  2. gemini `robustRealpath`+`isPathWithinWorkspace` (realpath precheck incl.
     non-existent paths; hard reject) — best effort/complexity ratio, ~40 LOC
  3. openai-agents `WorkspacePathPolicy` (resolve + `_is_under`, grants)
  4. hermes deny-list + opt-in safe-root (realpath) — pragmatic, complements #2
  5. opencode / cline — permission-prompt gated (needs a human; poor fit for a bot)
  Our incumbent: NONE.
- **Encoding (FS-3 class)**: openhands incumbent auto-detects charset (read+write);
  our pure-Python paths pin UTF-8 and don't catch UnicodeEncodeError. Every TS
  candidate sidesteps via JS strings + BOM handling (opencode/gemini/hermes all
  preserve BOM + line endings — a robustness bar our tools don't meet).
- **History/undo**: only the openhands incumbent has it (FileHistoryManager, 10
  versions/file, undo_edit). hermes/opencode/gemini/cline/aider all defer to git.
