# File Tools Replacement Research — `read_file` / `write_file` / `edit_file`

Deep research into replacing or hardening calfcord's built-in file tools
(`src/calfcord/tools/builtin/fs.py`), which today wrap
`openhands.tools.file_editor.FileEditorExecutor` for read + single-shot edit, and
use pure-Python paths for `write_file` and `edit_file(replace_all=True)`.

Scope and method: verified each audited issue against our source + the vendored
openhands source (and empirically), then read the actual file-tool source of nine
named frameworks plus expansion candidates (hermes-agent, openclaw, Aider). Every
candidate claim cites repo + file path. Realtime notes:
`docs/tools-research/logs/file-tools-log.md`.

---

## 1. Executive summary + top recommendation

The agent-tooling field has **converged on two patterns** our incumbent predates:
(a) a *graduated fuzzy matcher* for edits (exact → trimmed → whitespace-normalized
→ block-anchor → fuzzy), and (b) *resolve-then-contain* path security
(realpath the target, assert it stays under the workspace, reject otherwise).
Our tools have neither: the `replace_all` path is a raw `str.replace` (FS-1, FS-2,
FS-4), neither write path catches `UnicodeEncodeError` (FS-3), `write_file` is a
non-atomic, symlink-following, signal-less `Path.write_text` (FS-5), and there is
zero path containment (XC-2, FS-7).

**Top recommendation: PORT — rebuild `fs.py` on hermes-agent's Python design, keep
the openhands executor only for `read` + history.** hermes-agent
(NousResearch/hermes-agent, **MIT**) is the single best source because it is the
only candidate that is (1) Python, (2) exposes our exact `read_file` /
`write_file` / `patch(old,new,replace_all)` surface, and (3) already solves our
whole list: a 860-line Python 9-strategy fuzzy matcher (`fuzzy_match.py` → FS-1),
an `old==new` + multi-match-uniqueness guard (FS-2), an **atomic** temp-file +
rename writer with mode preservation (FS-5), a realpath-based **write deny-list**
plus opt-in **safe-root** confinement (XC-2/FS-7), BOM + line-ending preservation,
and post-write verification.

Pair the port with **gemini-cli's `robustRealpath` + `isPathWithinWorkspace`** (~40
LOC, the cleanest hard-reject containment) as the XC-2 primitive, and **keep the
openhands `view` path** (already solid: size cap, binary detection, charset
auto-detect, line-numbered output, and a `FileHistoryManager` that gives us
undo — the one thing hermes lacks). Adopt designs/algorithms regardless of
license; confirm MIT attribution before copying hermes code verbatim.

Ranked top 3 (full ranking in §5): **1) hermes-agent (port)**, **2) gemini-cli
(port the containment primitive)**, **3) OpenCode (port the replacer cascade if
not using hermes')**.

---

## 2. Verified recap of our issues and their true causes

Confirmed by reading `fs.py`, `workspace.py`, `_observation.py`, the vendored
`openhands/tools/file_editor/{editor,impl,definition,exceptions}.py` +
`utils/encoding.py`, and three empirical checks.

| ID | Verified? | True cause |
|----|-----------|-----------|
| **FS-1** | ✅ | Upstream `str_replace` strips whitespace and retries on zero matches (`editor.py:204-223`). Our `replace_all` (`fs.py:212-218`) uses raw `in`/`count`/`str.replace` with no strip → a padded `old_string` succeeds at `replace_all=False` but errors at `replace_all=True`. Behavior splits on the flag. |
| **FS-2** | ✅ | Upstream guards `new_str == old_str` (`editor.py:129-135`); our `replace_all` path doesn't. `count>0`, `str.replace` is a no-op, identical file rewritten, returns "replaced N occurrence(s)" — false success. |
| **FS-3** | ✅ (empirical) | `Path.write_text('a\ud800b', encoding='utf-8')` raises `UnicodeEncodeError`; `isinstance(UnicodeEncodeError(), OSError) == False`. Both write paths (`fs.py:148`, `fs.py:220`) catch only `OSError`, so a lone surrogate in LLM output makes the tool **raise** instead of returning `error:`. |
| **FS-4** | ✅ | Module docstring (`fs.py:23-26`) says `replace_all` "drives upstream in a loop." Code does a single `str.replace`, no executor, no loop. `'aaa'.replace('aa','X')=='Xa'` (single non-overlapping pass) ≠ "loop until gone." |
| **FS-5** | ✅ (empirical) | `write_file` uses `Path.write_text` → (a) overwrites with no created/overwrote signal (upstream `create` refuses to clobber, `editor.py:592-598`); (b) **writes through symlinks** (verified: `link.write_text()` clobbered the target, link stayed a symlink); (c) `mkdir(parents=True)` makes dir trees anywhere; (d) no history (bypasses upstream `FileHistoryManager`). Non-atomic truncate-in-place. |
| **FS-6** | ✅ | `read_file` docstring says "binary file too large," conflating two distinct upstream failures: 10MB cap (`editor.py:58,651-661`) and `binaryornot` detection (`editor.py:665-672`). |
| **FS-7** | ✅ | `_resolve_path` (`fs.py:74-77`) returns raw absolute strings; only the relative branch `.resolve()`s. Upstream lock key is `file:{Path(action.path).resolve()}` (`definition.py:205-206`) → per-call-style lock-key mismatch (latent at `max_workers=1`). |
| **XC-2** | ✅ | `_resolve_path` passes absolutes through and joins+resolves relatives with **no `is_relative_to`/`commonpath` assertion**. `../../etc/passwd`, absolute `/home/.../.ssh/id_rsa`, and in-workspace symlinks→outside all escape, read and write. Path string is LLM-influenced by untrusted Discord content → prompt-injection exfiltration. |
| **XC-1** | ✅ | `_executor` global (`fs.py:48,51-63`) never torn down; `EncodingManager` LRU + `FileHistoryManager` leak across restart, no calfkit lifecycle hook registered. (Low severity for file_editor — no OS resource like tmux.) |

**Key upstream nuances** (matter for grading candidates):
- Upstream `str_replace` uses the file's **auto-detected charset** (`with_encoding`
  + `charset_normalizer`) for read AND write. Our pure-Python `replace_all` pins
  UTF-8 → can corrupt a latin-1/utf-16 file upstream would round-trip (another
  flag-split divergence).
- Even the incumbent is **not atomic** on the common path: `create`/`str_replace`/
  `write_file` use plain `open('w')` (truncate-in-place); only `insert` (which we
  don't expose) uses tempfile + `shutil.move`.
- Upstream `impl.py` catches only `ToolError`, but `EditorToolParameterInvalid/
  Missing` subclass it, so those ARE caught; the read/write internals wrap in
  `except Exception` → `ToolError`. The contract hole is purely in *our* pure-Python
  paths, not the executor.
- The OpenHands monorepo no longer vendors a Python editor; it consumes the same
  `openhands-tools` package we pull into `.venv`. **Our incumbent IS upstream
  current** — there is no newer OpenHands editor to upgrade to.

---

## 3. Per-candidate deep review

### 3.1 OpenHands file_editor — incumbent baseline
- **Repo/path:** `openhands-tools` (vendored `.venv/.../openhands/tools/file_editor/`). **Language:** Python. **License:** MIT.
- **Overview:** `FileEditor` with `view`/`create`/`str_replace`/`insert`/`undo_edit`. Charset auto-detect, 10MB size cap, `binaryornot` detection, `FileHistoryManager` (10 versions/file → `undo_edit`), per-file `DeclaredResources` lock, line-numbered `cat -n` output, image-as-base64.
- **Issue assessment:** `view` is solid (FS-6 is just our docstring; FS-8 acceptable). `str_replace` non-`replace_all` path is robust (strip fallback, `old==new` guard, multi-match error, history). The failures (FS-1..FS-5) are calfcord's *own* pure-Python `write_file`/`replace_all` paths that **bypass** the executor (because upstream `create` refuses to overwrite). Non-atomic writes, leaf symlink follow, no containment (relies on caller).
- **Quality:** mature, well-tested, but a wide tool surface (5 commands) and no path security by design.
- **Integration:** already a Python dep. **Stay-and-fix is viable for `read` and history**; not sufficient alone for write/edit (the overwrite limitation is exactly why we went pure-Python and inherited the bugs).

### 3.2 hermes-agent ⭐ — TOP PORT SOURCE
- **Repo/path:** `NousResearch/hermes-agent` — `tools/file_operations.py`, `tools/fuzzy_match.py`, `agent/file_safety.py`, `tools/patch_parser.py`. **Language:** Python. **License:** **MIT** (confirmed: `LICENSE`, `pyproject.toml license="MIT"`).
- **Overview:** `ShellFileOperations` exposing `read_file`/`write_file`/`patch_replace(old,new,replace_all)`/`patch_v4a`/`delete`/`move`/`search` — a 1:1 superset of calfcord's surface. (Implemented over a shell `_exec` so it works across docker/ssh/modal; the *logic* is host-portable.)
- **Issue-by-issue:**
  - FS-1 ✅ `fuzzy_match.py:73-83` — 9-strategy Python cascade (exact, line_trimmed, whitespace_normalized, indentation_flexible, escape_normalized, trimmed_boundary, unicode_normalized, block_anchor, context_aware).
  - FS-2 ✅ `fuzzy_match.py:69` `old_string == new_string` guard; empty-old guard `:66`; multi-match uniqueness `:90-94` (consistent 0/1/N for both flag values — kills the flag-split).
  - FS-3 ✅ no raised exception escapes; errors return as the `error` field.
  - FS-4 N/A (docstrings match impl).
  - FS-5 ✅✅ `_atomic_write` (`file_operations.py:839-891`): temp file in the SAME dir (`mktemp -p` → same-FS rename), mode copy (`stat`+`chmod`), `mv -f`, `trap...EXIT` cleanup; `write_file` reports `dirs_created` and (via lint-delta) created vs modified; **post-write verification** re-reads and confirms persistence (`:1436-1466`).
  - FS-6 ✅ binary vs size distinct; image redirected to vision.
  - FS-7 ✅ `os.path.realpath` in `file_safety`.
  - XC-2 ✅✅ `file_safety.is_write_denied` (`:96-105`): realpath then deny exact (`~/.ssh/id_rsa`, `.env`, `/etc/passwd`, `/etc/shadow`, `/etc/sudoers`, `.git-credentials`) + deny prefixes (`~/.ssh`, `~/.aws`, `~/.gnupg`, `/etc/sudoers.d`, `/etc/systemd`); applied on every write/patch/delete/move. Opt-in `HERMES_WRITE_SAFE_ROOT` allow-root (realpath) == the audit's proposed `CALFCORD_WORKSPACE_CONFINE`.
  - XC-1 N/A.
  - Extras: BOM preserve, CRLF/LF preserve, escape-drift guard, indentation reindent, in-process linters, similar-file suggestions. **Gap:** no history/undo (defers to git, same as calfcord today).
- **Quality:** high; heavily commented with rationale; defensive (post-write verify, mode preservation, ARG_MAX-safe stdin streaming).
- **Pros:** Python, exact surface, solves everything, MIT, modular. **Cons:** the writer is shell-script-based (needs adapting to host-local `tempfile`+`os.replace`+`os.fsync`); no built-in undo.
- **Integration:** **port.** `fuzzy_match.py` and `file_safety.py` lift nearly verbatim; the `_atomic_write` *pattern* reimplements in ~15 lines of pure Python for our host-local case.

### 3.3 gemini-cli — best containment primitive
- **Repo/path:** `google-gemini/gemini-cli` — `packages/core/src/tools/{write-file,edit,read-file}.ts`, `packages/core/src/utils/{workspaceContext,paths}.ts`, `editorCorrector.ts`. **Language:** TypeScript. **License:** Apache-2.0.
- **Overview:** declarative tools with confirmation/diff UX; an LLM-based content corrector; hash-based stale-edit detection.
- **Issue-by-issue:** FS-1 ✅ (exact→flexible/whitespace→regex strategies); FS-2 ✅ (occurrence count + `allow_multiple`); FS-5 ✅ (`isNewFile` created/overwrote, `lstatSync` dir guard, per-errno mapping EACCES/ENOSPC/EISDIR); FS-6 ✅; FS-7 ✅; **XC-2 ✅✅ strongest hard-reject**: `WorkspaceContext.isPathWithinWorkspace` (`workspaceContext.ts:181-194`) → `robustRealpath` (`paths.ts`) resolves symlinks with loop detection AND resolves non-existent paths via parent-recursion, then `..`-aware `isPathWithinRoot`; write does `validatePathAccess` → `PATH_NOT_IN_WORKSPACE`. Not atomic (`fs.writeFile`).
- **Quality:** high, well-typed, security-conscious. **Pros:** the containment is ~40 LOC and directly portable to Python (`os.path.realpath` + walk-up + `Path.is_relative_to`). **Cons:** TS; edit corrector needs an LLM call (skip for calfcord).
- **Integration:** **port the containment primitive** (`robustRealpath` + `isPathWithinWorkspace`) — the cleanest hard-reject XC-2 answer; pair it with hermes' write/edit.

### 3.4 OpenCode — best replacer cascade (TS)
- **Repo/path:** `sst/opencode` — `packages/opencode/src/tool/{read,write,edit,external-directory}.ts`, `packages/core/src/fs-util.ts`, `util/bom.ts`. **Language:** TypeScript/Bun. **License:** MIT.
- **Overview:** the canonical 9-strategy replacer cascade (`edit.ts:682-729`) — Simple/LineTrimmed/BlockAnchor/WhitespaceNormalized/IndentationFlexible/EscapeNormalized/TrimmedBoundary/ContextAware/MultiOccurrence — that hermes ported to Python. Per-file in-process semaphore lock; `isDisproportionateMatch` anti-footgun; `old==new` + empty-old guards; BOM + CRLF preserve; created/overwrote signal; byte-capped streaming reads; binary detection.
- **Issue-by-issue:** FS-1 ✅, FS-2 ✅, FS-5 ⚠️ (created/overwrote ✅, leaf symlink write-through ❌, no temp+rename), FS-6 ✅, FS-7 ✅ (in-process per-file lock). **XC-2 ⚠️** containment via *permission prompt* (`assertExternalDirectoryEffect`), not hard reject — poor fit for a Discord bot with no per-call human approval.
- **Quality:** excellent. **Integration:** **port** the replacer cascade if not using hermes' Python copy; the permission model and Effect runtime do not transfer.

### 3.5 Codex CLI — apply_patch + seek_sequence (Rust)
- **Repo/path:** `openai/codex` — `codex-rs/apply-patch/src/{lib,seek_sequence,parser}.rs`, `codex-rs/core/src/tools/sandboxing.rs`. **Language:** Rust. **License:** Apache-2.0.
- **Overview:** patch-envelope edit model (`*** Begin Patch / *** Update File / @@`); `seek_sequence` graduated fuzzy line matcher (exact → trim_end → trim both, EOF-anchored, panic-guarded); symlink-aware (`lib.rs:606` `is_file && !is_symlink`, `test_delete_symlink_still_verifies`); containment at the `ExecutorFileSystem` + `FileSystemSandboxContext` trait layer backed by an OS sandbox (Seatbelt/Landlock).
- **Issue-by-issue:** FS-1 ✅, FS-5 ✅ (symlink-aware), FS-6 ✅, FS-7 ✅, XC-2 ✅ (OS sandbox + writable roots). **Cons:** Rust (no dep); patch model widens the LLM tool surface vs Claude-Code-style Edit; porting a contextual-diff applier is much more work than str-replace + a cascade.
- **Integration:** **design reference only.** Lower priority than hermes/gemini.

### 3.6 OpenAI Agents SDK — sandboxed apply_patch (Python)
- **Repo/path:** `openai/openai-agents-python` — `src/agents/sandbox/capabilities/filesystem.py`, `sandbox/apply_patch.py`, `sandbox/workspace_paths.py`. **Language:** Python. **License:** MIT.
- **Overview:** file tools run *inside a SandboxSession* (docker/unix_local/remote); the sandbox is the boundary. `WorkspacePathPolicy` is a strong pure-Python containment reference: `normalize_path(resolve_symlinks=...)`, `Path.resolve(strict=False)` + `_is_under`, absolute-path **grants**, **read-only grants**, root-resolving + windows-drive-escape rejection, with a thorough test suite (`tests/sandbox/test_workspace_paths.py`).
- **Issue-by-issue:** FS-1 ✅ (apply_patch fuzzy), FS-3 ✅ (UnicodeDecodeError wrapped), FS-6 ✅, FS-7 ✅, XC-2 ✅ (`WorkspacePathPolicy`). **Cons:** the full sandbox machinery (docker/mounts/manifests) is far too heavy to adopt just for file tools.
- **Integration:** **port the `WorkspacePathPolicy` containment idea** (peer of gemini's, slightly heavier); skip the sandbox. Gemini's is the leaner pick.

### 3.7 Cline — origin of the fuzzy matcher (TS, VS Code)
- **Repo/path:** `cline/cline` — `apps/vscode/src/core/assistant-message/diff.ts`, `.../tools/handlers/WriteToFileToolHandler.ts`, `integrations/editor/DiffViewProvider.ts`. **Language:** TypeScript. **License:** Apache-2.0.
- **Overview:** SEARCH/REPLACE block format (streaming-parsed); 3-tier matcher (exact `indexOf` → `lineTrimmedFallbackMatch` → `blockAnchorFallbackMatch`) — the algorithm opencode/gemini/hermes all credit. Persists via the VS Code DiffViewProvider with interactive approval.
- **Issue-by-issue:** FS-1 ✅ (3-tier), FS-6 ✅; **XC-2 ⚠️** workspace flag + human approval. **Cons:** deeply VS Code-bound; only the matcher is portable, and opencode/hermes package it better (9 strategies, non-editor).
- **Integration:** **not feasible**; use opencode/hermes as the port source instead.

### 3.8 openclaw — strongest containment design (TS)
- **Repo/path:** `openclaw/openclaw` — `src/agents/apply-patch.ts`, `src/infra/fs-safe.ts` (+ `@openclaw/fs-safe`), `src/agents/path-policy.ts`, `src/infra/path-alias-guards.ts`. **Language:** TypeScript. **License:** TBD.
- **Overview:** apply_patch (V4A); a **capability/root-handle** containment: `workspaceOnly` (default true) routes all writes through a confined `root(cwd)` handle that takes RELATIVE paths; `@openclaw/fs-safe` does per-handle realpath verification (`resolveOpenedFileRealPathForHandle`) and an explicit symlink policy (`follow-within-root` vs `reject`) — **TOCTOU-safe**, not just string-level `..`. Operator-visible danger flag when `workspaceOnly` is off.
- **Issue-by-issue:** FS-1 ✅, FS-5 ⚠️ (no temp+rename), FS-6 ✅, FS-7 ✅, **XC-2 ✅✅✅ best** (root-handle + per-handle realpath + symlink policy). **Cons:** TS, large.
- **Integration:** **design reference for hardening XC-2** if calfcord ever needs TOCTOU-safety beyond gemini's realpath precheck. Heavier than warranted for v1.

### 3.9 Claude Code (Anthropic) — design north-star (closed source)
- `anthropics/claude-agent-sdk-python` is a thin client around the closed-source CLI; **ships no Read/Write/Edit code**. From public docs/behavior: Edit **requires a prior Read** in-session (staleness guard), exact-string match, errors on non-unique `old_string` unless `replace_all`, rejects `old==new`; Write warns/blocks overwrite of an unread file. Read is line-numbered with offset/limit and multimodal.
- **Integration:** not adoptable as code. Valuable **design** to emulate: the *read-before-edit staleness contract* (no OSS Python option enforces it by default; gemini approximates via content hashing).

### 3.10 Aider — expansion (Python)
- `Aider-AI/aider` (Apache-2.0): SEARCH/REPLACE + diff-fenced formats; layered matcher (exact → whitespace → indentation → difflib fuzzy); atomic *git commits* per edit. Strongly git/terminal-coupled; its file I/O entangles with the chat loop. **Design reference only** — hermes' standalone `fuzzy_match.py` is a strictly better port source.

---

## 4. Comparison matrix

Legend: ✅ solved · ⚠️ partial / via human prompt · ❌ not addressed · — N/A ·
Lang/Dep = adoptable as a Python dependency.

| Candidate | Lang | License | FS-1 | FS-2 | FS-3 | FS-5 atomic | FS-5 symlink | FS-6 | FS-7 | XC-2 contain | History/undo | Fuzzy edit | Atomic write | Py-dep? |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **Our tools (incumbent)** | Py | MIT | ❌ | ❌ | ❌ | ❌ | ❌ | ❌(doc) | ❌ | ❌ | partial* | ❌ | ❌ | n/a |
| OpenHands editor | Py | MIT | ✅ᵉ | ✅ᵉ | ✅ᵉ | ❌ | ❌ | ✅ | ✅ᵉ | ❌ | ✅ | strip-only | ❌ | yes |
| **hermes-agent** | Py | MIT | ✅ | ✅ | ✅ | ✅ | ⚠️deny | ✅ | ✅ | ✅✅ | ❌(git) | ✅ 9-strat | ✅ | port |
| gemini-cli | TS | Apache-2.0 | ✅ | ✅ | — | ❌ | ✅ᵖ | ✅ | ✅ | ✅✅ | ⚠️IDE/git | ✅ multi | ❌ | port-design |
| OpenCode | TS | MIT | ✅ | ✅ | — | ❌ | ❌ | ✅ | ✅ | ⚠️prompt | ⚠️snapshot | ✅ 9-strat | ❌ | port-design |
| Codex CLI | Rust | Apache-2.0 | ✅ | — | — | ❌ | ✅ | ✅ | ✅ | ✅(sandbox) | ❌ | ✅ seek_seq | ❌ | no |
| OpenAI Agents | Py | MIT | ✅ | — | ✅ | ❌ | ✅ᵖ | ✅ | ✅ | ✅(policy) | ❌ | ✅ apply | ❌ | heavy |
| Cline | TS | Apache-2.0 | ✅ | — | — | ❌ | — | ✅ | — | ⚠️prompt | ⚠️editor | ✅ 3-tier | ❌ | no |
| openclaw | TS | TBD | ✅ | — | — | ❌ | ✅✅ | ✅ | ✅ | ✅✅✅ | ❌ | ✅ apply | ❌ | no |
| Aider | Py | Apache-2.0 | ✅ | ✅ | — | ⚠️ | — | ✅ | — | ❌ | ✅(git) | ✅ difflib | ⚠️git | design |
| Claude Code | closed | — | ✅ | ✅ | — | ? | ? | ✅ | ? | ✅ | ✅ | exact | ? | no |

\* incumbent's history = git only (the pure-Python paths bypass openhands' FileHistoryManager).
ᵉ = solved only on the openhands-executor path, which our pure-Python write/replace_all bypasses.
ᵖ = symlink handled by resolving realpath *before* the containment check (leaf write still follows the link, but an in-workspace symlink→outside is rejected pre-write).

---

## 5. Ranking with rationale

1. **hermes-agent (port).** Only candidate that is Python + exact surface + solves
   the entire FS-*/XC-* list + MIT. Atomic writer, 9-strategy Python matcher,
   realpath deny-list + safe-root, post-write verification, BOM/EOL preservation.
   Maximum coverage, minimum porting friction. Sole gap: no undo (git covers it,
   or borrow openhands' history).
2. **gemini-cli (port the containment primitive).** Best hard-reject XC-2 in the
   field (`robustRealpath` + `isPathWithinWorkspace`), ~40 LOC, trivially
   Pythonized. The right XC-2 layer to pair with #1; cleaner than hermes' deny-list
   for *positive* confinement.
3. **OpenCode (port the cascade — fallback).** If hermes' license/code can't be
   used, opencode's 9-strategy cascade is the canonical source (hermes is its
   Python port). Same algorithm, MIT.
4. **OpenAI Agents `WorkspacePathPolicy` (design).** Python containment peer of
   gemini's, with grants/read-only — adopt the idea if calfcord wants per-path
   grants; otherwise gemini's is leaner.
5. **openclaw root-handle (design, future hardening).** Best TOCTOU-safe
   containment; revisit only if the threat model demands per-handle realpath
   verification beyond a precheck.
6. **Codex / Aider / Cline (design references).** Confirm the field's convergence;
   none is the right adopt/port target given #1–#3.
7. **OpenHands incumbent (stay-and-fix for `read`).** Keep for `view` + history;
   not sufficient for write/edit because `create` refuses to overwrite (the
   original reason we forked to pure Python and inherited the bugs).

---

## 6. Final recommendation — PORT (hermes design) + reuse openhands for read

**Adopt a port, not a dependency, and not a pure stay-and-fix.**

Rebuild `fs.py` as:

1. **`read_file` → keep openhands `view`.** It already handles size cap, binary
   detection, charset auto-detect, line numbers, and `view_range` correctly
   (FS-6/FS-8 are doc-only). Fix the FS-6 docstring. This also preserves
   `FileHistoryManager`, giving us undo for free if we later expose it.
2. **`edit_file` → port hermes' `fuzzy_match.fuzzy_find_and_replace`** (or
   opencode's cascade if license blocks hermes). This single change resolves
   FS-1, FS-2, and FS-4 and unifies the `replace_all` semantics with the
   single-shot path (no more flag-split). Keep the Claude-Code-style
   `old/new/replace_all` surface — do **not** switch to apply_patch (it widens the
   LLM tool surface; the cascade gives the robustness without the format cost).
3. **`write_file` → port hermes' atomic pattern**, host-local: write to
   `tempfile.NamedTemporaryFile(dir=parent, delete=False)`, `flush()` +
   `os.fsync()`, copy the existing file's mode, then `os.replace(tmp, target)`
   (atomic same-FS rename), removing the temp on any failure. Catch
   `(OSError, UnicodeError)` (closes FS-3). Refuse symlink targets for relative
   paths (or open with `O_NOFOLLOW`), and report **`Created`** vs
   **`Overwrote (was N bytes)`** (closes FS-5). Add post-write verification for
   the edit path.
4. **Path security (XC-2, FS-7) → one resolver shared by all three tools and
   grep/glob.** Port gemini's `robustRealpath` + `isPathWithinWorkspace` and, when
   `CALFCORD_WORKSPACE_CONFINE` is set, hard-reject any target whose realpath is
   not under the workspace root (with `error:`). `.resolve()` absolute paths too
   (closes FS-7). Layer hermes' realpath **deny-list** (`.env`, `.ssh`, `.aws`,
   `/etc/*`) as defense-in-depth even when confinement is off — cheap insurance
   against prompt-injected exfiltration.
5. **XC-1** — register the (now smaller) read executor via a calfkit
   `on_shutdown`/`@resource` hook; the new write/edit paths hold no long-lived
   resource.

**Why not stay-and-fix only:** the incumbent's `create` refuses to overwrite,
which is precisely why calfcord forked to the buggy pure-Python paths. Fixing them
in place means re-implementing exactly the matcher + atomic-write + containment
that hermes/gemini already provide — so a port of those proven pieces is the
lower-risk, higher-quality path. **Why not adopt a dependency:** the only Python
candidates with the right surface are hermes (shell-backend-coupled; its *logic*
ports cleanly but its package assumes a terminal env) and openai-agents (sandbox
machinery far too heavy). Porting the three small, well-isolated modules
(`fuzzy_match`, the realpath containment, the atomic writer) is the right
granularity.

**License action:** confirm hermes-agent MIT attribution before copying its code
verbatim; the *algorithms/designs* (cascade, atomic temp+rename, realpath
containment) are free to reimplement regardless and are well-documented above.
