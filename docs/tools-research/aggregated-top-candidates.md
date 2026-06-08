# Built-in Tools — Aggregated Top-3 Candidate Report

A cross-tool synthesis of the six per-tool research reports in
`docs/tools-research/reports/`. For each built-in tool it compiles the **top 3
recommendations** with the **pros and cons of each candidate**, preserving the
implementation detail, issue-by-issue coverage, and integration-feasibility
notes from the source reports.

**Sources** (full detail, including the per-issue root-cause verification, the
complete candidate field, comparison matrices, and "stay-and-fix" fix-lists):
`reports/{shell,file-tools,search-tools,web-tools,todos,private-chat}-report.md`
and the realtime logs in `logs/`. Issue IDs (`SHELL-*`, `FS-*`, `SEARCH-*`,
`WEB-*`, `TODO-*`, `PCHAT-*`, `XC-*`, `KNOWN-*`) are defined in
`docs/reviews/builtin-tools-audit.md`.

**Verdict legend:** 🔌 adopt as dependency · 🛠️ port the design/code · 🩹
stay-and-fix in our wrapper · 📐 adopt the pattern/protocol model.

---

## Cross-tool summary

| Tool | Verdict | #1 candidate | #2 | #3 |
|---|---|---|---|---|
| **shell** | 🩹 then 🛠️ (phased) | Stay-and-fix the OpenHands wrapper | agent-zero (port) | hermes-agent (port) |
| **file tools** | 🛠️ (+keep openhands `read`) | hermes-agent (port) | gemini-cli (containment) | OpenCode (cascade) |
| **search tools** | 🛠️ (ripgrep-only core we own) | opencode (design) | hermes-agent (Python port) | gemini-cli (param schema) |
| **web tools** | 🔌+🛠️ | Pydantic AI `safe_download` | Hermes `WebSearchProvider` ABC | openclaw web_fetch arch |
| **todos** | 🩹 (+small hermes port) | hermes-agent `todo_tool.py` | Stay on OpenHands + bound | Codex `update_plan` (stateless) |
| **private_chat** | 📐 (+small fixes now) | openclaw spawn/announce | AutoGen RPC + CancellationToken | Google A2A protocol |

**Recurring signals across all six reports:**
- **hermes-agent** (NousResearch, Python, MIT) is the most frequently shortlisted
  Python option — top-3 for file (#1), search (#2), web-search (#2), todos (#1),
  shell (#3). It is the closest architectural match to calfcord (Python,
  pluggable/distributed-minded), but is *app code, not a packaged library*, so it
  is a **port** source, not a dependency.
- **Language is the gating constraint.** The best-engineered tools are Rust
  (Codex) or TypeScript (OpenCode / Gemini CLI / Cline / openclaw) — **design-to-port,
  not importable**. The only true Python *dependencies* in play are Pydantic AI,
  OpenHands (incumbent), and (heavier) OpenAI Agents SDK.
- **"Just upgrade upstream" fixes nothing** for shell, file, or search — agents
  confirmed our incumbent IS the current `openhands-tools`, and latest OpenHands
  still carries the defects.
- **Provenance caution:** `openclaw` and `hermes-agent` are real but **post-cutoff
  (late 2025)** repos; GitHub name-search returns spam forks with inflated stars.
  Agents verified canonical repos via the GitHub API and read source directly.
  **Confirm license/attribution before copying any code verbatim** (designs are
  free to reimplement).

---

## 1. shell

**Verdict:** 🩹→🛠️ phased. The command-runner is solid; the bugs are in our
rendering + lifecycle wiring + OpenHands' timeout design. Fix the wrapper now;
graduate to a ported async-PTY engine if/when multitenancy and long-running
output become first-class needs.

### #1 — Stay-and-fix the OpenHands wrapper (incumbent) · Python · MIT · already a dependency
The wrapper sits over `TerminalExecutor`; the runner itself is high quality
(tmux pane pool, PS1-sentinel exit codes, secret masking). Every `SHELL-*` fix
lives in our two files (`shell.py`, `_observation.py`); `XC-1`/`XC-3` are runner
wiring; `KNOWN-1` is a backend choice.
- **Issue coverage:** fixes SHELL-1 (render `obs.to_llm_content` / stitch
  `metadata.prefix+text+suffix`), SHELL-2 (detect `exit_code==-1` + suffix →
  prefix `error:`), SHELL-5 (omit `exit code:` when `None`), XC-3/KNOWN-3
  (`asyncio.to_thread`), XC-1 (register `_executor.close()` on `on_shutdown` +
  `atexit`), KNOWN-1 (force the subprocess single-session backend). SHELL-3/4 can
  only be **documented**, not structurally fixed (OpenHands makes no-change and
  hard timeouts mutually exclusive). **KNOWN-2 (multitenancy) is NOT fixed.**
- **Pros:** lowest cost / highest leverage (~1 day); keeps the proven tmux/PS1
  runner; no new dependency; removes every 🟠 except multitenancy.
- **Cons:** doesn't address multitenancy; inherits OpenHands' timeout
  mutual-exclusion (SHELL-3/4 remain sharp edges); **verified that a version bump
  fixes nothing** — latest OpenHands still omits `is_error` on timeout and buries
  the notice in `metadata.suffix`.
- **Integration:** already integrated; pure wrapper edits + runner wiring.

### #2 — agent-zero (port) · `frdel/agent-zero` · Python · MIT · best Python *engine*
`plugins/_code_execution/helpers/tty_session.py` (422 LOC) + pluggable
`LocalInteractiveSession`/`SSHInteractiveSession`; **numbered concurrent
persistent sessions** with explicit per-/all-session `reset`. `TTYSession` =
`pty.openpty` + `asyncio.create_subprocess_shell` + `add_reader` → fully
non-blocking async I/O. **Four coexisting timers** (`first_output_timeout=30`,
`between_output_timeout=15` idle, `dialog_timeout=5` interactive-prompt,
`max_exec_timeout=240` hard cap) + prompt-pattern early-return; every
timeout/pause emits an explicit framework message telling the agent the command
is still running and how to continue.
- **Issue coverage:** solves SHELL-1 (always surfaces state + how-to-continue),
  SHELL-2 (explicit framework message), **SHELL-3 AND SHELL-4 together** (the
  only candidate that does — coexisting idle + hard timeouts), SHELL-5; XC-3 /
  KNOWN-3 (async PTY); KNOWN-1 (real persistent numbered sessions); KNOWN-2
  (numbered sessions → key on `ctx.agent_name`). XC-1 partial (`close`/`__del__`,
  needs explicit shutdown wiring).
- **Pros:** best Python engine to graduate to; the only candidate solving
  SHELL-3+4 jointly; real persistent sessions; `tty_session.py` is fairly
  self-contained; MIT.
- **Cons:** app code (imports agent-zero helpers/prompts), not a pip package → a
  **port**, not a dep; XC-1 needs wiring; no OS-level sandbox.
- **Integration:** port `TTYSession` + the four-tier timeout behind our tool,
  keyed per `ctx.agent_name`. Phase-2 scope.

### #3 — hermes-agent (port) · `NousResearch/hermes-agent` · Python · MIT · closest *architecture*
`tools/process_registry.py`, `terminal_tool.py`, `tools/environments/{base,local,docker,ssh,modal,daytona}.py`.
`BaseEnvironment` ABC with **pluggable backends** (= calfcord's distributed
shape). `_wait_for_process` adaptive poll + process-group kill + a separate
non-blocking `drain_thread`. `ProcessRegistry`: thread-safe, `MAX_OUTPUT_CHARS=200KB`
rolling buffer, `FINISHED_TTL_SECONDS=1800` + `MAX_PROCESSES=64` **LRU pruning**,
`task_id`/`session_key` **isolation**, crash-recovery checkpoint, watch-patterns.
cwd persists across one-shot calls via a `_cwd_file` (`pwd -P` marker).
- **Issue coverage:** solves SHELL-1/2/3/5; SHELL-4 n/a by design (one
  always-enforced timeout + instant-exit); **XC-1** (TTL/LRU-bounded + explicit
  lifecycle); **XC-3** (drain thread, non-blocking); **KNOWN-1** (file-based cwd);
  **KNOWN-2** (`task_id`/`session_key` isolation).
- **Pros:** closest architectural twin to calfcord (pluggable distributed
  backends, bounded registry, crash recovery); Python; MIT; production-grade.
- **Cons:** app code, not a packaged lib (imports `hermes_cli.*`); a fuller port
  than agent-zero's engine; only ~partial sandbox.
- **Integration:** port `ProcessRegistry` + `_wait_for_process` + file-cwd
  persistence + pluggable `BaseEnvironment` — the architecture to mirror when the
  shell becomes distributed and multi-tenant.

> **Other notable references:** **Codex exec-server** (Rust; gold-standard
> distributed, PTY-backed, lifecycle-managed, OS-sandboxed process server — port
> the design or run as a sidecar if XC-2 OS-confinement is ever required);
> **OpenCode** (`killTree` process-group kill, file-spill truncation,
> `<shell_metadata>` notice — the SHELL-1 fix done right); **Gemini CLI**
> (xterm-headless ANSI serialization, lifecycle registry, truncation-warning);
> **openclaw** (auto-yield-to-background + notify-on-exit, binary-allowlist);
> **OpenAI Agents SDK** (executor-callable + approval-hook *shape*); **Claude Code**
> ("timeouts are errors with an explicit message" contract; allowlist guidance).

---

## 2. file tools (`read_file` / `write_file` / `edit_file`)

**Verdict:** 🛠️ port. The field converged on a *graduated fuzzy matcher* for
edits and *resolve-then-contain* path security; we have neither. **Keep the
OpenHands executor only for `read`** (size cap, binary detection, charset
auto-detect, line-numbered output, `FileHistoryManager` undo), and rebuild
write/edit on ported designs.

### #1 — hermes-agent (port) · `NousResearch/hermes-agent` · Python · MIT · TOP PORT SOURCE
`tools/file_operations.py`, `tools/fuzzy_match.py`, `agent/file_safety.py`,
`tools/patch_parser.py`. Exposes `read_file`/`write_file`/`patch(old,new,replace_all)`
— a **1:1 superset of calfcord's surface** — and is the only candidate that is
Python, exact-surface, and solves the whole list.
- **Issue coverage:** FS-1 ✅ (9-strategy Python fuzzy cascade:
  exact/line-trimmed/whitespace-normalized/indentation-flexible/escape-normalized/
  trimmed-boundary/unicode-normalized/block-anchor/context-aware); FS-2 ✅
  (`old==new` + empty-old + multi-match uniqueness guards, consistent 0/1/N for
  both flag values — kills the flag-split); FS-3 ✅ (errors return as a field, no
  raised exception escapes); FS-4 n/a; FS-5 ✅✅ (`_atomic_write` temp-file +
  same-dir rename, mode copy, created-vs-modified signal, **post-write
  verification** re-reads to confirm); FS-6 ✅; FS-7 ✅ (`os.path.realpath`); XC-2
  ✅✅ (`file_safety.is_write_denied`: realpath then deny `.env`/`.ssh`/`.aws`/
  `/etc/*` exact + prefix; opt-in `HERMES_WRITE_SAFE_ROOT` == our proposed
  `CALFCORD_WORKSPACE_CONFINE`). **Gap:** no history/undo (defers to git, like us).
- **Pros:** Python + exact surface + solves everything + MIT; modular; heavily
  commented with rationale; defensive (mode preservation, post-write verify,
  ARG_MAX-safe streaming).
- **Cons:** the writer is shell-script-based (needs adapting to host-local
  `tempfile`+`os.replace`+`os.fsync`); no built-in undo.
- **Integration:** **port** — `fuzzy_match.py` and `file_safety.py` lift nearly
  verbatim; the `_atomic_write` *pattern* reimplements in ~15 LOC of pure Python.

### #2 — gemini-cli (port the containment primitive) · `google-gemini/gemini-cli` · TS · Apache-2.0
`packages/core/src/utils/{workspaceContext,paths}.ts` + `tools/{write-file,edit,read-file}.ts`.
- **Issue coverage:** FS-1 ✅ (exact→flexible/whitespace→regex strategies); FS-2 ✅
  (occurrence count + `allow_multiple`); FS-5 ✅ (`isNewFile` created/overwrote,
  `lstatSync` dir guard, per-errno EACCES/ENOSPC/EISDIR mapping); FS-6 ✅; FS-7 ✅;
  **XC-2 ✅✅ strongest hard-reject** — `isPathWithinWorkspace` → `robustRealpath`
  resolves symlinks with loop detection AND resolves non-existent paths via
  parent-recursion, then `..`-aware `isPathWithinRoot`. Not atomic (`fs.writeFile`).
- **Pros:** the cleanest *positive confinement* in the field; ~40 LOC, trivially
  Pythonized (`os.path.realpath` + walk-up + `Path.is_relative_to`); well-typed,
  security-conscious.
- **Cons:** TS → port-design; its edit corrector needs an LLM call (skip); not
  atomic.
- **Integration:** **port the containment primitive** (`robustRealpath` +
  `isPathWithinWorkspace`) as the shared XC-2 resolver; pair with hermes' write/edit.

### #3 — OpenCode (port the cascade — fallback) · `sst/opencode` · TS/Bun · MIT
`packages/opencode/src/tool/{read,write,edit}.ts` — the canonical 9-strategy
replacer cascade (`edit.ts:682-729`) that **hermes ported to Python**.
- **Issue coverage:** FS-1 ✅; FS-2 ✅ (`old==new` + empty-old guards,
  `isDisproportionateMatch` anti-footgun); FS-5 ⚠️ (created/overwrote signal ✅,
  **leaf symlink write-through ❌**, no temp+rename); FS-6 ✅; FS-7 ✅ (in-process
  per-file semaphore lock); **XC-2 ⚠️** (containment via *permission prompt*, not
  hard reject — poor fit for a Discord bot with no per-call human approval). BOM +
  CRLF preserve; byte-capped streaming reads; binary detection.
- **Pros:** excellent, modern; the canonical cascade source (same algorithm as
  hermes); MIT.
- **Cons:** TS → port; permission-prompt containment doesn't fit an autonomous
  server; the Effect runtime doesn't transfer.
- **Integration:** **fallback** edit source if hermes' code/license can't be used
  — port the cascade only.

> **Other notable references:** **OpenAI Agents SDK `WorkspacePathPolicy`**
> (Python containment peer of gemini's, with absolute-path grants + read-only
> grants; but its full sandbox machinery is too heavy to adopt for file tools);
> **openclaw** root-handle + per-handle realpath + symlink policy (best
> **TOCTOU-safe** containment; revisit only if the threat model demands it);
> **Codex** `apply_patch`/`seek_sequence` (Rust; symlink-aware; OS sandbox);
> **Cline** (origin of the 3-tier matcher; VS Code-bound); **Aider** (atomic *git
> commits* per edit; chat-loop-coupled); **OpenHands incumbent** (keep `view` for
> read + history — its `create` refuses to overwrite, which is exactly why
> calfcord forked to the buggy pure-Python paths).
>
> **Combined recommendation:** `read_file` → keep openhands `view` (fix the FS-6
> docstring); `edit_file` → port hermes `fuzzy_match`; `write_file` → port
> hermes' atomic temp+rename (host-local, catch `(OSError, UnicodeError)`, refuse
> symlink targets, report Created vs Overwrote); path security → one shared
> resolver from gemini's `robustRealpath` + hermes' deny-list, gated by
> `CALFCORD_WORKSPACE_CONFINE`.

---

## 3. search tools (`grep` / `glob`)

**Verdict:** 🛠️ port a calfcord-owned **ripgrep-only** core. SEARCH-1…8 are all
upstream OpenHands defects rooted in one design choice — a *silent
backend-fallback chain (ripgrep → system grep → pure-Python) with hardcoded flags
and no config surface*. There is no injection point to fix them without forking,
so drop the executors for these two tools.

### #1 — opencode (design) · `sst/opencode` · TS/Bun · MIT · best overall design
`packages/core/src/filesystem/ripgrep.ts` + `ripgrep.ts` adapter + `tool/{grep,glob}.ts`.
Pins ripgrep `15.1.0`; prefers system `rg`, else a cached binary, else **downloads
the exact pinned release** from BurntSushi. Runs with `--no-config` and scrubs
`RIPGREP_CONFIG_PATH`; JSON streaming with `Stream.take(limit+1)`.
- **Issue coverage:** SEARCH-1 ✅ (gitignore honored *identically* for grep &
  files; `--no-ignore` trivially addable since flags are centralized); SEARCH-2 ✅
  (include is just another `--glob=`, only narrows); SEARCH-3 ✅ (single Rust
  dialect, no fallback chain); SEARCH-4 ✅ (no Python regex engine → no
  catastrophic backtracking; `AbortSignal` cancel/timeout); SEARCH-5 ✅ (uniform
  hidden/ignore policy); SEARCH-6 ✅ (`take(limit+1)` then `truncated = rows >
  limit`); SEARCH-7 ✅ (rg only; `--follow` opt-in); SEARCH-8 ✅ (rg default
  case-sensitive); XC-2 ✅. Plus typed `InvalidPatternError` + a `partial` flag.
- **Pros:** the textbook deterministic design; clean separation (binary mgmt /
  adapter / leaf tool); streaming; typed errors. Everything we need, minimally.
- **Cons:** TS + Effect runtime → not a drop-in dep; auto-download adds a
  first-use network dependency (mitigate by preferring system rg / pre-vendoring).
- **Integration:** **design-to-port** (MIT) — port the ideas (pinned/auto-download
  rg, `--no-config`/env scrub, uniform flags, `limit+1` truncation, JSON streaming,
  typed invalid-pattern/partial, cancelation).

### #2 — hermes-agent (Python port) · `NousResearch/hermes-agent` · Python · MIT · easiest port
`tools/file_operations.py` (`FileOperations.search`). One unified `search` tool
(`target="content"`/`"files"`); rg-first → grep fallback for content, rg-first →
find fallback for files; **no pure-Python regex path → no whole-file ReDoS**; if
neither binary present → explicit "install ripgrep" error (never a silent wrong
answer).
- **Issue coverage:** SEARCH-1 ⚠️ (rg default, honest, no toggle); SEARCH-2 ⚠️
  (`--glob` override caveat); SEARCH-3 ➖→✅ (two dialects, grep is a *fallback*; ✅
  if grep fallback dropped); SEARCH-4 ✅ (rg/grep subprocess only, 60s timeout, no
  Python scan); SEARCH-5 ✅-ish (`--exclude-dir='.*'` mirrors rg hidden handling);
  SEARCH-6 ✅ (`truncated = total > offset+limit`); SEARCH-7 ✅; SEARCH-8 ✅ (no
  `-i`); XC-2 ❌ (we'd add it). Plus `_split_tool_diagnostics` (separates rg/grep
  error lines from matches), `set -o pipefail`, limit/offset pagination,
  Windows-drive-aware parsing.
- **Pros:** Python; idioms lift almost verbatim (diagnostics-split, exit-code
  handling, pagination, output modes); battle-tested.
- **Cons:** builds **shell command strings** (swap for argv lists on port); no
  containment; gitignore not toggleable in this version.
- **Integration:** **PORT (Python)** — the lowest-friction execution/parsing layer.

### #3 — gemini-cli (param schema) · `google-gemini/gemini-cli` · TS · Apache-2.0 · richest surface
`packages/core/src/tools/{ripGrep,glob,grep-utils}.ts`. grep = ripgrep, glob =
npm `glob`, **both funnel through one `FileDiscoveryService`** so the backends
agree. `resolveRipgrepPath` prefers a bundled per-platform binary, else a
**trusted** system `rg` (path-hijack hardening), else hard-fails.
- **Issue coverage:** SEARCH-1 ✅ (`no_ignore` param, `.geminiignore`, documented);
  SEARCH-2 ✅; SEARCH-3 ✅ (single Rust runtime dialect); SEARCH-4 ✅
  (`AbortController` timeout; rg linear engine); SEARCH-5 ✅ (both post-filter
  through `FileDiscoveryService`, reports `ignoredCount`); SEARCH-6 ✅
  (`wasTruncated = matchCount >= totalMaxMatches`); SEARCH-7 ✅ (`follow:false`);
  SEARCH-8 ✅ (`case_sensitive` param, best-documented); XC-2 ✅ (strongest
  multi-layer: `validatePathAccess` + per-match `..`/abs rejection). Plus
  `fixed_strings`, `exclude_pattern`, context lines, `max_matches_per_file`,
  `total_max_matches`.
- **Pros:** the parameter surface to copy wholesale; explicit documented ignore +
  case toggles; strongest containment; trusted-system-binary hardening; bundles rg
  at build (alt to runtime download).
- **Cons:** large/feature-heavy; TS; two backends (rg + node-glob) to keep aligned.
- **Integration:** **design-to-port** (Apache-2.0) — primary source for the param
  schema and containment.

> **Other notable references:** **openclaw** (clean alt binary-manager + actionable
> truncation UX + pluggable remote `operations` for distributed calfcord; no
> containment in the grep tool); **codex** `file-search` (**authoritative
> `require_git` gitignore-scope fix** so a stray parent `.gitignore` can't blank
> results, + loop-safe; but Rust and *fuzzy* file-find only); **cline**
> (byte-budget output bounding, useful for Discord-sized output; product path
> lacks a hard timeout). **OpenAI Agents SDK** `FileSearchTool` = hosted vector
> store (n/a); **Claude Agent SDK** = design validation only.
>
> **Recommendation:** build the core on opencode's adapter, lift Python
> execution/parsing from hermes, copy gemini's param schema + containment; add a
> byte budget (cline) and the `require_git` scoping consideration (codex). A
> short-term stay-and-fix stopgap (pin rg in the image, add containment, stop
> trusting `truncated`, fix docstrings, file upstream issues) is in the source
> report §6.

---

## 4. web tools (`web_fetch` / `web_search`)

**Verdict:** 🔌 adopt + 🛠️ port. The critical issue is **WEB-1 (SSRF)**.
Hand-rolled SSRF guards get rebinding subtly wrong (per the CVE record), so adopt
a purpose-built one. Fetch and search are separable.

### #1 — Pydantic AI (`safe_download`) · `pydantic/pydantic-ai` · Python · MIT · best port/adopt
`pydantic_ai_slim/pydantic_ai/_ssrf.py` (~530 LOC) + `common_tools/web_fetch.py`.
`safe_download(url, allow_local=False, max_redirects, timeout, allowed/blocked_domains)
-> httpx.Response` performs the full OWASP SSRF dance.
- **Issue coverage:** **WEB-1 ✅ best-in-class** — scheme allowlist; full
  private/reserved IP table (RFC1918, loopback, link-local 169.254, CGNAT
  100.64/10, TEST-NETs, multicast, IPv6 ::1/fe80::/fc00::/Teredo); cloud-metadata
  blocklist incl. **Azure's *public* `168.63.129.16`**; **IPv6-transition decoding**
  (IPv4-mapped/6to4/NAT64/ISATAP/Teredo — no other candidate does this);
  **DNS-rebinding/TOCTOU defense** (resolve → connect to the *validated IP* with
  original `Host`/SNI); **per-redirect-hop revalidation** + strips Auth/Cookie on
  cross-origin; trailing-dot strip; fail-closed. **WEB-2 ✅** (async; httpx;
  thread-offloaded DNS). **WEB-3 ✅** (raises typed errors we map to `error:`).
  **WEB-4 ⚠️ GAP** — returns the full `httpx.Response`, no streaming pre-decode
  byte budget (we add it). **WEB-5 ✅** (honest `max_content_length` char cap).
  **WEB-7 ✅** (content-type gate; html→markdownify; json→fenced; **binary→`BinaryContent`**;
  sends `Accept: text/markdown`). **XC-3 ✅**.
- **Pros:** Python; MIT; the strongest SSRF posture in any language; async;
  content-type gating + binary handling; drop-in `safe_download`; born from a real
  CVE fix (GHSA-2jrp-274c-jhv3) → battle-tested; dependency-light (httpx +
  markdownify, which we already pull via smolagents).
- **Cons:** no streaming byte cap (WEB-4 — we add it); markdownify (not
  readability-grade — matches current behavior); no `web_search` (pair with #2).
- **Integration:** **HIGHEST** — `uv add 'pydantic-ai-slim[web-fetch]'` and call
  `safe_download`, **or** vendor the self-contained `_ssrf.py`
  (`ipaddress`+`socket`+`httpx`+1 helper). Replaces smolagents' blocking
  `requests.get` in one move; fixes WEB-1/2/3/5/7.

### #2 — Hermes Agent `WebSearchProvider` ABC · `NousResearch/hermes-agent` · Python · search side
`agent/web_search_provider.py` (ABC) + `web_search_registry.py` + 9 provider
plugins (brave_free, ddgs, searxng, exa, parallel, tavily, firecrawl, xai).
Config-selected backend; registry routes by capability; sync providers offloaded;
the ddgs provider returns `{success, error}` instead of raising.
- **Issue coverage:** **WEB-6 ✅** (no shared event-loop-sleeping singleton;
  per-provider, offloadable via `asyncio.to_thread`); **WEB-3** (consistent error
  shape on the search side). SSRF not a focus (search APIs are trusted endpoints;
  hosted extract providers absorb fetch-SSRF on their servers).
- **Pros:** Python, small, pluggable; delivers the "swap to Brave/Tavily/SearXNG"
  capability our docstring already promises; clean ABC + registry + config key.
- **Cons:** search-only (pair with #1 for fetch); SSRF is not its concern.
- **Integration:** **port the pattern** (Python, small) — replace the hardcoded
  `DuckDuckGoSearchTool` singleton with the ABC + registry + config key; ship a
  ddgs provider first.

### #3 — openclaw web_fetch architecture · `openclaw/openclaw` · TS · MIT · most feature-complete
`src/infra/net/ssrf.ts` (727 LOC) + `web-guarded-fetch.ts` + provider contract.
- **Issue coverage:** **WEB-1 ✅ very strong** — decodes embedded IPv4-in-IPv6,
  **fails closed on malformed IPv6**, blocks `localhost`/`*.localhost`/
  `metadata.google.internal` + cloud-metadata, **pinned DNS (resolve→connect-to-IP,
  anti-rebinding) + per-redirect re-evaluation**, configurable escapes (on par
  with Pydantic AI: Pydantic edges it on IPv6-transition breadth, openclaw edges
  it on policy configurability + caching + provider chain). **WEB-2 ✅** (async +
  signal/timeout); **WEB-3 ✅** (structured errors); **WEB-4 ✅** (dual byte+char
  cap: `maxResponseBytes` 750KB + `maxChars` 20K); **WEB-5 ✅**; **WEB-7 ✅** (Cloudflare
  "Markdown for Agents", html→Readability/markdown). **Bonus:** `wrapWebContent`
  tags fetched bodies as **untrusted external content** (prompt-injection defense
  — directly relevant to Discord-sourced URLs); provider-fallback chain; caching;
  redirect cap 3.
- **Pros:** the richest production design; SSRF on par with Pydantic AI;
  untrusted-content wrapping; provider fallback; caching; dual caps.
- **Cons:** TS, large → design-to-port; heavier to adopt wholesale than Pydantic AI.
- **Integration:** **design-to-port** — best *reference architecture*; specifically
  borrow the untrusted-content wrapping, the provider-fallback chain, and the
  streaming dual byte+char cap (to close Pydantic AI's WEB-4 gap).

> **Other notable references:** **Gemini CLI** local fetch (gold-standard
> streaming size cap `readResponseWithLimit` + non-blocking per-host LRU rate
> limiter; strong SSRF but **not fully rebinding-proof** — checks host, not the
> connected IP); **OpenCode** (good caps/content-type + pluggable search, but
> **no SSRF — scheme allowlist only**); **Cline** (Puppeteer/Chromium, no SSRF —
> not feasible); **OpenHands** (Playwright browser, no SSRF — not a fit);
> **hosted-only** Codex / OpenAI Agents / Claude SDK (not portable; steal Claude's
> "return cross-host redirects rather than follow them" + network permission
> rules); **dedicated libs** (OWASP SSRF cheat sheet; Stripe **Smokescreen** egress
> proxy for defense-in-depth; trafilatura/readability for better extraction later).
>
> **Recommendation:** `web_fetch` → adopt Pydantic AI `safe_download` (drop
> smolagents), add a streaming byte cap (Gemini/OpenCode pattern), layer
> openclaw's untrusted-content wrapping. `web_search` → port hermes'
> `WebSearchProvider` ABC. Ensure both are genuinely non-blocking (XC-3). Optional
> Smokescreen egress proxy as belt-and-suspenders.

---

## 5. todos (`todo_view` / `todo_write`)

**Verdict:** 🩹 stay-and-fix + a small hermes port. This is the **cleanest
built-in** (correctly multitenant, no disk I/O, no blocking). Only one real
defect (TODO-1 unbounded `_executors`) + a docstring bug (TODO-2). No surveyed
framework is dramatically better; every "replacement" trades the bounded leak for
filesystem coupling (wrong for our distributed, no-shared-FS topology) or an
architecture change.

### #1 — hermes-agent `todo_tool.py` (closest fit) · `NousResearch/hermes-agent` · Python · MIT
`hermes/tools/todo_tool.py` (~280 LOC, in-memory) + `kanban_tools.py`/`kanban_db.py`
(persistent SQLite). `TodoStore` holds `list[dict]` and **lives on the AIAgent
instance — one per session** (GC'd with the session, no module-global). Schema
`{id (required, agent-chosen), content, status: pending|in_progress|completed|cancelled}`;
`write()` supports **both** `merge=false` full-replace and `merge=true`
incremental update-by-id+append; `_dedupe_by_id` + `_validate`;
`format_for_injection` re-injects only active items after context compaction.
- **Issue coverage:** TODO-1 / XC-1 solved by binding the store to the session
  object; TODO-2 n/a. **Caveat for calfcord:** the store can't literally live on
  an agent object (our tools worker is a *separate process*), so per-agent keying
  remains — **we still must add eviction/TTL/cap**; the kanban path needs a
  network store.
- **Pros:** Python + MIT + single file ≈ drop-in; richer than ours (id, merge,
  dedup, compaction injection) while structurally avoiding the leak; excellent
  self-contained stdlib-only code; the kanban file articulates the exact
  distributed-store lesson calfcord cares about.
- **Cons:** can't bind to an agent object in our topology → still need our own
  bounding; the durable kanban variant requires a network store.
- **Integration:** **HIGHEST** — port the `TodoStore` *ideas* (id + merge mode +
  compaction injection) onto our per-agent registry; keep the registry but bound it.

### #2 — Stay on OpenHands `task_tracker` + bound the registry (incumbent) · Python · MIT
In-memory `_task_list` per executor; schema `{title,notes,status×3}`; `view`/`plan`
(full replace). TODO-1/XC-1 are *calfcord's* registry, not upstream; TODO-2 is our
docstring.
- **Issue coverage:** the fixes are entirely calfcord-side — bound `_executors`
  (LRU + cap, or TTL), register `on_shutdown`/`@resource` teardown, reword the
  TODO-2 docstring.
- **Pros:** already integrated; **zero schema churn**; lowest risk.
- **Cons:** no id/merge; the leak is structural once you key per-agent in a
  long-lived process (so bounding is mandatory either way).
- **Integration:** stay; fix the registry. The default low-risk path.

### #3 — Codex `update_plan` (stateless pattern) · `openai/codex` · Rust · Apache-2.0
A **stateless** handler — emits a `PlanUpdate` event, returns the constant "Plan
updated", server keeps **no** plan state; the plan lives in the conversation
transcript + client render. Schema `{plan:[{step,status×3}], explanation?}`.
- **Issue coverage:** TODO-1 / XC-1 **N/A by construction** — nothing retained,
  zero memory growth, perfect isolation. The cleanest possible leak fix *in
  principle*.
- **Pros:** elegant (~100 LOC total); perfect isolation; no growth.
- **Cons:** **Critical for calfcord** — a tools worker does NOT see the agent's
  transcript, so a pure-stateless `update_plan` would leave the agent unable to
  `todo_view`. Viable only if list ownership moves into the agent loop — an
  **architecture change, not a tool swap**. Rust.
- **Integration:** design-to-port; the strongest leak fix but requires relocating
  list ownership to the agent.

> **Other notable references:** **Claude Code Task tools** (best mature reference —
> id/deps/owner/cross-session; Anthropic itself moved *off* the
> flat-in-memory-full-replace model we use, validating the concern; binary-only,
> disk-persisted → learn, don't adopt); **Gemini trackerTools** (richest schema:
> subtasks/dependency-graph/types — over-built, filesystem-coupled); **OpenCode
> `todowrite`** (clean persisted/session-keyed SQLite; TS, local file); **Cline
> focus chain** (human-editable markdown + proper `dispose()` discipline — the
> XC-1 takeaway; but piggybacked-param shape doesn't fit calfkit); **openclaw
> goal-tools** (single-goal abstraction; non-standard license); **OpenAI Agents
> SDK / Claude Agent SDK (Python)** — **no first-party todo tool** (explicit
> finding, nothing to adopt).
>
> **Recommendation:** STAY-AND-FIX — bound `_executors` (LRU+cap/TTL), add
> `on_shutdown`/`@resource` teardown + `atexit`, fix the TODO-2 wording (and the
> `TASKS.md`→`TASKS.json` nit). **Optional, design-sign-off-gated** additive
> upgrades from hermes: an `id` field + `merge` mode (saves tokens on long lists),
> active-only compaction re-injection, a `cancelled` status. **Do NOT** add
> local-disk/SQLite persistence (wrong for the distributed model). **Don't drop the
> tool** — the prompt-managed/stateless alternatives only work when the list rides
> the transcript the agent loop owns, which the tools worker can't see.

---

## 6. private_chat (agent-to-agent messaging)

**Verdict:** 📐 adopt patterns (+ small fixes now). A drop-in does not exist —
`private_chat` uniquely couples calfkit's Kafka request/reply with a Discord
persona-webhook audit. The strongest distributed candidates **all converge on the
opposite of our design**: don't block on a synchronous request/reply that holds a
consumer slot; instead *spawn → return a correlation/task id immediately → deliver
completion later as a push event → model the lifecycle as a state machine*.

### #1 — openclaw spawn/announce · `openclaw/openclaw` · TS · strongest pattern source
`src/agents/subagent-{depth,run-timeout,run-liveness,recovery-state,spawn-plan}.ts`
+ `tools/sessions-spawn-tool.ts`. **Non-blocking, push-based completion:**
`sessions_spawn` returns a run id immediately; the agent `sessions_yield`s to end
its turn; completion arrives later as a model-visible push with a **stable
idempotency key** and a four-tier delivery fallback (wake/steer → handoff → queue
→ backoff-retry). `spawnDepth`+`spawnedBy` lineage is restart-survivable with a
nesting cap; `maxConcurrent` bounds fan-out.
- **Issue coverage:** **PCHAT-1 — directly the fix** (spawning frees the slot, so
  A→B→A cannot contend; depth cap bounds reentrancy); **PCHAT-4** (`run-liveness`
  ages out stale runs at 2h / `runTimeout+60s`; `recovery-state` bounds orphan
  recovery to 2 attempts/2-min then **tombstones** — the orphan cleanup we lack);
  **PCHAT-5** (integer-second timeout; sub-second treated as "no timeout", never
  "0s"); **PCHAT-2/3** (child output framed as untrusted "report/evidence").
- **Pros:** the most complete, production-shaped answer to our hardest issues;
  closest in spirit to our Discord-projected audit (route preservation, announce);
  defensive (number coercion, restart-survivable lineage, explicit trust framing).
- **Cons:** TS → pattern-to-port; no Discord specifics.
- **Integration:** **pattern-to-port** — maps cleanly onto calfkit `emit_to_node`
  + a push completion consumer + idempotency key; depth cap, `maxConcurrent`,
  stale-run liveness, and bounded orphan recovery are all portable designs.

### #2 — AutoGen core · `microsoft/autogen` · Python · MIT · strongest API-design reference
`autogen-core/src/autogen_core/{_agent_runtime,_single_threaded_agent_runtime,_cancellation_token}.py`.
RPC `send_message(...)` creates a future + envelope; `_process_next` dispatches
each as an **independent `asyncio.create_task`** (no shared slot) → handlers run
concurrently. The `CancellationToken` (~30 LOC: flag + callback list; `cancel()`
fires all; `link_future()` registers `future.cancel()`; `add_callback()` for
cleanup) is passed to the recipient via `MessageContext`.
- **Issue coverage:** **PCHAT-1** (task-per-message concurrency instead of a single
  semaphore slot) and **PCHAT-4** (cancellation token threaded caller→callee) —
  both directly addressed. PCHAT-2/3/5 n/a.
- **Pros:** the cleanest, most portable *API design* for the two structural fixes;
  the `CancellationToken` is ~30 LOC and copyable; mature; MIT.
- **Cons:** an in-process runtime, not a Kafka transport → pattern-to-port;
  cancellation-over-bus needs a calfkit feature (carry a cancel signal across the
  Kafka hop — **file a feature gap**).
- **Integration:** **pattern-to-port** — per-message concurrency (in our world:
  higher `max_workers` + non-blocking spawn) + a `CancellationToken` carried in
  `deps`/headers.

### #3 — Google A2A protocol · `a2aproject/a2a-python` · Python · Apache-2.0 · strongest standard
Standardized distributed A2A (JSON-RPC/gRPC/REST) with an async **task** lifecycle:
`submitted → working → {input-required | completed | canceled | failed | rejected
| auth-required}`. Client surface: `send_message(_streaming)`, `get_task`,
`list_tasks`, `cancel_task`, `subscribe`, push-notification config.
`ActiveTaskRegistry` is a lock-guarded `dict[task_id]` with `on_cleanup`;
out-of-band delivery via SSE + **push notifications** (webhook + token) keyed by
`task_id`.
- **Issue coverage:** **PCHAT-1** (no held slot; **`input-required` models "B asks
  a clarifying question" as a first-class state, not a deadlock**); **PCHAT-4**
  (`cancel_task` first-class + registry cleanup; `TaskNotCancelableError`);
  **PCHAT-5** (state machine, no float-second string); **PCHAT-2** (structured
  `Parts` separate content from control).
- **Pros:** the canonical OSS standard (Python/JS/Java/Go/.NET/Rust SDKs);
  Apache-2.0; `input-required` elegantly resolves the nested-clarification
  deadlock; the Discord audit thread is a natural push sink.
- **Cons:** the wire is HTTP/JSON-RPC/gRPC, not Kafka → adopt the *model*, not the
  transport; heavier conceptual adoption.
- **Integration:** **protocol-to-adopt** (the model; possibly a partial Python dep
  for the task-lifecycle + push types) over calfkit/Kafka.

> **Other notable references:** **Anthropic Claude Agent SDK** (best concrete
> *message-type* template — Started/Progress/Notification keyed by `task_id`,
> terminal `completed|failed|stopped`); **Spring Kafka `ReplyingKafkaTemplate`**
> (canonical Kafka correlation that validates calfkit's `_ReplyDispatcher` design,
> + a per-request timeout reaper that bounds the pending map); **Letta** (ship
> *both* blocking and fire-and-forget A2A tools; per-target failure isolation in
> fan-out; **provenance framing** of inbound peer content — relevant to PCHAT-2);
> in-process frameworks (OpenAI handoffs/agents-as-tools, CrewAI, LangGraph
> interrupt/resume, Cline `new_task`, OpenHands delegation, AgentScope, hermes ACP)
> — lessons only (recursion bound, error-string contract, suspend/resume), no
> transport relevance.
>
> **Recommendation (no drop-in):** ship the cheap fixes now — **PCHAT-2**
> (`allowed_mentions=discord.AllowedMentions.none()`), **PCHAT-5** (`:g`/`.1f`
> format), **PCHAT-3** (reuse the existing `chunk_split`/`classify_error` to return
> a recoverable `error:` or chunk-split the request projection), and **Stage-1**
> raise `max_workers` on the tools worker (verified safe — I/O-bound body,
> node-scoped resources, correlation-keyed dispatch). **Stage-2:** port the
> **spawn → correlate → push-completion → state-machine** model (openclaw + Claude
> SDK + A2A) + AutoGen's `CancellationToken`, and **file a calfkit feature gap**
> for distributed cancellation across the Kafka hop (a cancel topic keyed by
> `correlation_id`; consider a default `reply_ttl`).

---

## Appendix — sourcing & integration-feasibility at a glance

| Candidate | Lang | License | For tool(s) | Adoptable as Python dep? |
|---|---|---|---|---|
| Pydantic AI (`_ssrf`) | Python | MIT | web_fetch | **Yes** (`pydantic-ai-slim[web-fetch]`) or vendor `_ssrf.py` |
| hermes-agent | Python | MIT | file (#1), search (#2), web-search (#2), todos (#1), shell (#3) | No — app code; **port** modules |
| agent-zero | Python | MIT | shell (#2) | No — app code; **port** `tty_session.py` |
| OpenHands (incumbent) | Python | MIT | shell, file-read, todos, search (baseline) | **Yes** (already a dep) |
| AutoGen | Python | MIT | private_chat (#2) | Pattern-to-port (`CancellationToken` ~30 LOC) |
| Google A2A | Python | Apache-2.0 | private_chat (#3) | Protocol-to-adopt; partial dep possible |
| opencode | TS/Bun | MIT | search (#1), file (#3), shell/web/todos refs | No — design-to-port |
| gemini-cli | TS | Apache-2.0 | file (#2), search (#3), shell/web refs | No — design-to-port |
| openclaw | TS | MIT (verify) | private_chat (#1), web (#3), shell/search/file refs | No — design-to-port |
| Codex | Rust | Apache-2.0 | shell ref, search (require_git), file ref, todos (#3) | No — design-to-port or sidecar |
| Cline | TS | Apache-2.0 | refs (byte-budget, dispose, 3-tier matcher) | No |
| Letta / Spring Kafka / Claude SDK | Py / Java / Py | Apache/MIT | private_chat refs | Patterns/references |

**Net adoption shape:** two true dependencies worth adopting (**Pydantic AI** for
web SSRF; **OpenHands** stays for shell command-running + file `read`), one Python
**port source** that recurs everywhere (**hermes-agent**), and a set of
**design-to-port** references (opencode/gemini/openclaw/Codex/agent-zero/AutoGen)
whose value is the design, not the dependency.
