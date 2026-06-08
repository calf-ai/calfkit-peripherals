# Web Tools Research Report — `web_fetch` / `web_search`

**Scope:** evaluate replacements/improvements for calfcord's built-in `web_fetch` and
`web_search` tools (`src/calfcord/tools/builtin/web.py`, a thin shim over
`smolagents.VisitWebpageTool` / `DuckDuckGoSearchTool`) against the issues catalogued in
`docs/reviews/builtin-tools-audit.md` (WEB-1…WEB-7, XC-3), with **SSRF (WEB-1) treated as the
top priority**.

**Method:** verified each issue against our source and the upstream smolagents source; then
located, cloned, and read the actual web-fetch / web-search source of every candidate
framework; then expanded into dedicated SSRF-safe HTTP and HTML-extraction libraries. Every
candidate claim cites a repo + file path. Realtime notes:
`docs/tools-research/logs/web-tools-log.md`.

---

## 1. Executive summary + top recommendation

Our web tools have a **critical, exploitable SSRF hole** (WEB-1) plus six functional/robustness
defects, all rooted in delegating to `smolagents` which does a bare `requests.get(url)` with no
URL validation, no streaming cap, and string-returning error handling, called blocking inside an
`async def` on a single shared event loop.

The open-source landscape splits cleanly:

- **Hosted-search frameworks** (Codex CLI, OpenAI Agents SDK, Gemini CLI's search, Claude Agent
  SDK) delegate web search/fetch to a vendor backend. Not self-hostable, no portable SSRF code —
  but they confirm a design truth: **most serious agents do not run a naive in-process fetcher;
  they either harden it heavily or push it to a trusted backend.**
- **Self-hosted fetchers** that take the SSRF problem seriously: **Pydantic AI** (Python),
  **Gemini CLI** (TS, the local "experimental" fetch path), and **openclaw** (TS). These three
  are the only candidates with real private-IP/DNS-rebinding defenses.
- **No-SSRF fetchers**: OpenCode (scheme allowlist only), Cline (Puppeteer, no checks),
  OpenHands (BrowserGym/Playwright, no checks). Useful for caps/content-type/markdown patterns,
  not for SSRF.

### Top recommendation

**Adopt Pydantic AI's SSRF-safe fetch (`pydantic_ai._ssrf.safe_download` + the
`common_tools.web_fetch` design) for `web_fetch`, and port Hermes Agent's pluggable
`WebSearchProvider` ABC pattern for `web_search`.**

- Pydantic AI's `_ssrf.py` is the **most complete SSRF defense surveyed in any language**: it is
  the only one that, out of the box, (a) resolves DNS then **connects to the validated IP** with
  the original `Host`/SNI (closing the DNS-rebinding/TOCTOU gap), (b) **re-validates every
  redirect hop**, (c) decodes IPv6 transition forms (NAT64/6to4/ISATAP/Teredo) to block an IPv4
  smuggled in IPv6 clothing, and (d) explicitly blocks cloud-metadata IPs including Azure's
  *public* `168.63.129.16`. It is Python, MIT-licensed, async, and its tool layer already does
  content-type gating, markdown conversion, and char caps. It directly fixes WEB-1, WEB-2, WEB-3,
  WEB-5, WEB-7. The one thing it does **not** do is stream a pre-decode byte cap (WEB-4); we add
  that on top.
- Hermes Agent's `WebSearchProvider` ABC answers the audit's open "pluggable vs hardcoded search
  provider" question and lets us drop the shared, event-loop-sleeping `DuckDuckGoSearchTool`
  singleton (WEB-6) for a config-selected, offloaded provider.

This is faster, safer, and lower-maintenance than hand-rolling an SSRF guard (which the CVE
record shows is easy to get subtly wrong).

---

## 2. Verified recap of our web tools' issues and true causes

Verified against `src/calfcord/tools/builtin/web.py` and
`.venv/lib/python3.12/site-packages/smolagents/default_tools.py`.

| ID | Severity | True root cause (verified) |
|----|----------|----------------------------|
| **WEB-1** | 🔴 Critical | `VisitWebpageTool.forward` (default_tools.py ~478-507) does `requests.get(url, timeout=20)` with the agent-supplied URL — no scheme check, no host/IP validation. `requests` defaults `allow_redirects=True`, so even a host allowlist would be bypassable via 30x. `web_fetch` passes the URL straight through. Reachable: `http://169.254.169.254/...` (cloud IAM creds), `localhost`/`127.0.0.1`/internal hosts. No defense anywhere in calfcord. |
| **WEB-2** | 🟠 High | `forward()` is synchronous/blocking (`requests.get`; and `_enforce_rate_limit`'s `time.sleep`). We call it directly inside `async def web_fetch`/`web_search` with no `asyncio.to_thread`. calfkit auto-offloads **sync** tool fns to a thread pool; by writing `async def` we opt out, so the blocking socket read freezes the single shared `calfkit-tools` loop (`max_workers=1`, XC-3) for every agent. |
| **WEB-3** | 🟠 High | `VisitWebpageTool.forward` **catches its own exceptions and returns strings** (`"The request timed out…"`, `"Error fetching the webpage: …"`, `"An unexpected error occurred: …"`). These never reach our `except`, so they get **no `error:` prefix** — the LLM cannot distinguish a 404/timeout from page content. (`web_search` differs: its empty-results path `raise`s, so that one *does* get our prefix.) |
| **WEB-4** | 🟠 High | No `stream=True`, no `Content-Length` check. `requests.get` → `response.text` (whole body decoded) → `markdownify(...)` (full parse tree) → *then* truncate. A multi-GB / chunked / "infinite" response is fully buffered + parsed before any char cap → OOM on the shared worker. Char caps protect token budget, not memory. |
| **WEB-5** | 🟡 Medium | `VisitWebpageTool(max_output_length=40000)` (our `_get_visit_tool()` uses the default) truncates to **40K** and appends its own trailer *before returning*. Our `_MAX_FETCH_CHARS=50_000` guard (`web.py:43,100`) therefore never fires; the docstring's "capped at 50KB" is wrong (real cap ~40K), and the truncation wording the model sees is smolagents', not ours. |
| **WEB-6** | 🟡 Medium | `_search_tool` is one module-global `DuckDuckGoSearchTool` shared by all agents. Its `_enforce_rate_limit` (default 1 req/s) uses `time.sleep`, **sleeping the event loop**, and gates every agent through one global lane. |
| **WEB-7** | 🟢 Low | `markdownify(response.text)` — `response.text` decodes via the Content-Type charset or `charset_normalizer`/`chardet`, defaulting to ISO-8859-1 for charset-less non-HTML → mojibake; and there is **no Content-Type gate**, so a PDF/image/zip is fed through `response.text` + `markdownify`, returning a large garbage string (no error). |

These are not configuration mistakes — they are inherent to delegating to `smolagents`'
`VisitWebpageTool` (a deliberately minimal demo tool) without wrapping its network layer.

---

## 3. Per-candidate deep review (SSRF first)

### 3.1 Pydantic AI — `pydantic/pydantic-ai` — Python, MIT ★★ best port/adopt

- **Files:** `pydantic_ai_slim/pydantic_ai/_ssrf.py` (the guard, ~530 lines);
  `pydantic_ai_slim/pydantic_ai/common_tools/web_fetch.py` (the agent tool).
- **Overview:** `safe_download(url, allow_local=False, max_redirects=10, timeout=30, headers,
  allowed_domains, blocked_domains) -> httpx.Response` performs the full OWASP-recommended SSRF
  dance; `web_fetch_tool(...)` wraps it into an agent tool with content-type gating and markdown
  conversion.

**Issue-by-issue:**

- **WEB-1 (SSRF) — BEST IN CLASS, fully solved.**
  - Scheme allowlist (`validate_url_protocol`, `_ssrf.py:249`): http/https only.
  - Private/reserved IP table (`_PRIVATE_NETWORKS`, `_ssrf.py:25-52`): all RFC1918, loopback,
    link-local (169.254/16), CGNAT 100.64/10 (Alibaba metadata), TEST-NETs, 198.18/15, multicast,
    reserved; IPv6 ::1, fe80::/10, fc00::/7, Teredo, doc, discard, multicast.
  - Cloud-metadata blocklist (`is_cloud_metadata_ip`, `_ssrf.py:185`): AWS IMDS v4+v6, ECS/EKS,
    GCP, **Azure 168.63.129.16 (a *public* IP — only the metadata guard catches it)**, Alibaba,
    Oracle, Scaleway. Blocked even with `allow_local=True`.
  - IPv6 transition decoding (`_embedded_ipv4s`, `_ssrf.py:142`): IPv4-mapped, 6to4, NAT64
    (/96 + /48), ISATAP, Teredo (XOR-decoded) → blocks a private IPv4 smuggled inside an IPv6
    literal. No other candidate does this.
  - **DNS-rebinding / TOCTOU defense (the crux):** `safe_download` (`_ssrf.py:434-533`) resolves
    the hostname, validates **every** resolved IP, then **connects to the resolved IP**
    (`build_url_with_ip`) with `Host: <hostname>` and TLS `sni_hostname` set to the hostname — so
    the validated IP *is* the connected IP; no second (rebinding) lookup occurs. This is the layer
    the OWASP cheat sheet and the CVE record insist the fix must live at.
  - **Per-redirect-hop revalidation:** `follow_redirects=False`, manual loop re-resolving +
    re-validating each hop; `max_redirects`; strips Authorization/Cookie/Proxy-Auth on
    cross-origin redirect; domain allow/blocklists checked on every hop.
  - Trailing-dot FQDN strip (defeats `169.254.169.254.`); fail-closed on unparseable IP.
- **WEB-2 (async):** fully async; DNS via `_utils.run_in_executor` (thread offload); httpx async
  client. Non-blocking by construction.
- **WEB-3 (errors):** `web_fetch.py:98` catches `(ValueError, httpx.HTTPStatusError,
  httpx.RequestError)` and raises `ModelRetry`. For calfcord we'd map these to our `error:`
  string instead — clean, unambiguous discriminator either way.
- **WEB-4 (size cap) — PARTIAL GAP (honest):** `safe_download` returns the full `httpx.Response`
  and the tool reads `response.text`/`response.content`; there is **no streaming pre-decode byte
  budget**. So the OOM vector is only partly mitigated (the char cap still runs after full
  buffering). Must be added (httpx `client.stream(...)` + running byte total, abort past
  threshold). Gemini/OpenCode/openclaw show the exact pattern.
- **WEB-5 (truncation):** explicit, honest `max_content_length=50_000` char cap (`web_fetch.py:
  127`); no dead-code mismatch.
- **WEB-6 (rate limit):** N/A (fetch tool). Search is out of scope for this lib.
- **WEB-7 (content-type) — fully solved:** `is_text_like_media_type` gate; text/markdown
  passthrough; html→`markdownify(strip=[img,script,style])` + `<title>` extraction;
  application/json→pretty fenced; **binary→`BinaryContent`** for native model handling (no
  garbling). Sends `Accept: text/markdown` to get server-rendered markdown (Cloudflare/Vercel/
  Mintlify) → fewer tokens, better quality.
- **XC-3:** async + thread-offloaded DNS means it won't freeze the shared loop.

- **Code quality:** excellent — small, dependency-light (httpx, markdownify which we already
  pull via smolagents), thoroughly documented with RFC citations, fail-closed throughout. Born
  from a real CVE fix (GHSA-2jrp-274c-jhv3), so battle-tested.
- **Pros:** Python; MIT; the strongest SSRF posture anywhere; async; content-type gating + binary
  handling; drop-in `safe_download`.
- **Cons:** no streaming byte cap (we add it); markdownify (not readability-grade extraction) —
  fine, matches our current behavior; `web_search` not provided (pair with Hermes pattern).
- **Integration feasibility — HIGHEST.** Two options:
  1. `uv add 'pydantic-ai-slim[web-fetch]'` and call `safe_download` (or reuse `web_fetch_tool`'s
     body), wrapping errors into our `error:` convention and adding a streaming cap; or
  2. Vendor/port `_ssrf.py` (self-contained: `ipaddress` + `socket` + `httpx` + one internal
     helper) into calfcord to avoid the dependency. Either way, this replaces smolagents'
     blocking `requests.get` with an async, SSRF-safe httpx path in one move.

### 3.2 Gemini CLI — `google-gemini/gemini-cli` — TypeScript, Apache-2.0 ★ strong SSRF reference

- **Files:** `packages/core/src/tools/web-fetch.ts`; SSRF helpers
  `packages/core/src/utils/fetch.ts`; search `packages/core/src/tools/web-search.ts`.
- **Overview:** `web_fetch` is **hosted-by-default** (sends URLs to the Gemini API to fetch +
  summarize). The self-hostable part is the local `executeExperimental`/`executeFallback` path
  (`getDirectWebFetch()`), which is a real, hardened fetcher. `web_search` is **hosted** (Gemini
  grounding) — not self-hostable.

**Issue-by-issue (local fetch path):**

- **WEB-1 (SSRF) — strong, with one gap.** `parsePrompt` (web-fetch.ts:111) allowlists
  http/https. `isBlockedHost` (web-fetch.ts:270) blocks localhost/127.0.0.1 + `isPrivateIp`.
  `isAddressPrivate` (fetch.ts:115) uses **ipaddr.js**: `addr.range() !== 'unicast'` blocks
  loopback/link-local(169.254)/private/reserved/multicast in one check; unmaps IPv4-mapped IPv6;
  explicit IANA benchmark 198.18/15. A DNS-resolving `isPrivateIpAsync` (fetch.ts:150,
  `lookup({all:true})`) exists. **Gap:** the fetch path's `isBlockedHost` checks the literal host,
  not the connected IP, and uses normal `fetch` (which re-resolves) — so it is **not fully
  rebinding-proof** the way Pydantic AI's connect-to-IP is. Still far ahead of us, and more than
  most.
- **WEB-2 (async):** native `fetch` + `AbortController` timeout (`fetchWithTimeout`, fetch.ts:190).
- **WEB-3 (errors):** structured `ToolResult.error{message,type}` + `llmContent:"Error: …"`.
- **WEB-4 (size cap) — gold standard:** `readResponseWithLimit` (web-fetch.ts:555) checks
  Content-Length AND streams via a reader with a running total, **cancelling the reader on
  overflow** (10MB). Exactly the streaming pattern we must add.
- **WEB-5 (truncation):** explicit `MAX_CONTENT_LENGTH=250000`; water-filling budget across
  multiple URLs.
- **WEB-6 (rate limit):** `checkRateLimit` per-host LRU sliding window (10/min), **non-blocking**
  (returns a skip; never sleeps). The right model.
- **WEB-7 (content-type):** gates on content-type → html-to-text / raw / base64-inline for
  image/video/pdf.
- **Integration:** DESIGN-TO-PORT (TS→Python). `fetch.ts` is the second-best SSRF blueprint
  (Python `ipaddress` mirrors ipaddr.js); `readResponseWithLimit` is the streaming-cap blueprint.
  Worth borrowing patterns even though we'll adopt Pydantic AI's guard.

### 3.3 openclaw — `openclaw/openclaw` — TypeScript, MIT ★ most feature-complete production design

- **Files:** `src/agents/tools/web-fetch.ts`; SSRF `src/infra/net/ssrf.ts` (727 lines) +
  `src/agents/tools/web-guarded-fetch.ts`; provider contract
  `src/plugin-sdk/provider-web-fetch.ts`; e.g. `extensions/firecrawl/web-fetch-provider.ts`.
- **Overview:** the richest production web_fetch surveyed — SSRF guard, provider fallback chain,
  caps, caching, untrusted-content wrapping.

**Issue-by-issue:**

- **WEB-1 (SSRF) — very strong.** `SsrFPolicy`; `isPrivateIpAddress` decodes embedded IPv4 in
  IPv6 and **fails closed on malformed IPv6**; `isBlockedHostname` blocks `localhost`,
  `*.localhost`, `metadata.google.internal`, a BLOCKED_HOSTNAMES set; cloud-metadata + special-use
  v4/v6 blocklists; **pinned DNS lookup** (resolve→connect-to-IP, anti-rebinding) + **per-redirect
  re-evaluation** ("redirects must re-evaluate against their own URL"); configurable escapes
  (allow private / RFC2544 / IPv6-ULA) for fake-ip proxy stacks. On par with Pydantic AI
  (Pydantic AI edges it on IPv6-transition breadth; openclaw edges it on policy configurability +
  caching + provider chain).
- **WEB-2 (async):** async fetch with `signal`/timeout; abort handling throughout.
- **WEB-3 (errors):** throws structured errors; `ModelRetry`-style; surfaces wrapped error detail.
- **WEB-4 (size cap):** `maxResponseBytes` 750KB default (32KB–10MB clamp) via
  `readResponseText({maxBytes})`; plus `maxChars` 20K. Dual byte+char cap.
- **WEB-5:** explicit configurable caps; no dead code.
- **WEB-6:** N/A (fetch). (Search has its own provider tools.)
- **WEB-7 (content-type):** text/markdown (Cloudflare "Markdown for Agents", reads
  `x-markdown-tokens`), html→Readability/markdown, json→pretty.
- **Bonus — prompt-injection mitigation:** `wrapWebContent`/`wrapExternalContent` tag fetched
  bodies as **untrusted external content**. Directly relevant to calfcord (Discord-driven
  injection). Also: provider fallback chain (Readability → Firecrawl provider → basic HTML),
  caching (TTL), redirect cap (3).
- **Integration:** DESIGN-TO-PORT (TS→Python, large). Best *reference architecture*, heavier to
  adopt than Pydantic AI. Borrow specifically: untrusted-content wrapping, provider-fallback
  chain, dual byte+char cap.

### 3.4 Hermes Agent — `NousResearch/hermes-agent` — Python ★ search-provider reference

- **Files:** `agent/web_search_provider.py` (ABC), `agent/web_search_registry.py` (registry),
  `plugins/web/{brave_free,ddgs,searxng,exa,parallel,tavily,firecrawl,xai}/provider.py`.
- **Overview:** a clean **pluggable web-search/extract provider** system — exactly the design the
  audit asks us to consider for WEB-6 and "pluggable vs hardcoded."
  - `WebSearchProvider` ABC: `name`, `is_available()` (cheap, no network), `supports_search/
    extract`, `search(query, limit) -> {success, data:{web:[{title,url,description,position}]}}`,
    `extract(urls) -> [{url,title,content,raw_content,metadata}]`, `get_setup_schema()`.
  - Config-selected backend (`web.search_backend` / `web.extract_backend`); registry routes calls
    by capability; sync providers offloaded via `inspect.iscoroutinefunction` detection
    (docstring explicitly recommends `asyncio.to_thread`).
  - `plugins/web/ddgs/provider.py`: the **same ddgs library we use**, but returns
    `{"success":False,"error":...}` instead of raising, caps the limit defensively, and probes
    availability by import.
- **SSRF:** not a focus of the search providers (search APIs are trusted endpoints; hosted
  extract providers like Firecrawl/Exa/Tavily absorb the fetch-SSRF risk on their servers).
- **Issue mapping:** solves **WEB-6** (no shared event-loop-sleeping singleton; per-provider, can
  be offloaded), and turns `web_search` from a hardcoded smolagents dependency into a swappable,
  config-driven provider with consistent error shape (helps WEB-3 on the search side).
- **Integration:** PORT THE PATTERN (Python, small). Replace our hardcoded
  `DuckDuckGoSearchTool` singleton with a `WebSearchProvider` ABC + registry + config key; ship a
  ddgs provider first, allow Brave/Tavily/SearXNG later (matches our existing
  "see docs/authoring-tools.md for the swap path" promise, but real).

### 3.5 OpenCode — `sst/opencode` — TypeScript, MIT

- **Files:** `packages/core/src/tool/webfetch.ts` (newer), `packages/opencode/src/tool/
  webfetch.ts` (older); search `packages/opencode/src/tool/websearch.ts`.
- **WEB-1 (SSRF) — ABSENT.** Only `assertHttpUrl` (scheme allowlist). **No private-IP/localhost
  block** — `webfetch("http://169.254.169.254/...")` would go through. The only gate is a
  permission prompt (`permission.assert`) — acceptable for a local single-user CLI, **not** for
  an autonomous multi-agent server.
- **WEB-4 — strong:** `collectBody` checks Content-Length then streams `Stream.runForEach` with a
  running total, failing past 5MB. Good streaming-cap reference.
- **WEB-3:** typed `ToolFailure`. **WEB-7:** `isTextualMime` allowlist; rejects images/binaries
  with an explicit error; html→Turndown / htmlparser2 (strips script/style/iframe/etc.).
- **Search:** `websearch.ts` is a **pluggable provider** design too (Exa / Parallel via MCP, env
  override `OPENCODE_WEBSEARCH_PROVIDER`) — a second vote for the provider pattern.
- **Integration:** DESIGN-TO-PORT for caps/content-type; **must add SSRF (it has none).** Not a
  primary candidate because the SSRF gap is exactly our critical issue.

### 3.6 Cline — `cline/cline` — TypeScript, Apache-2.0

- **File:** `apps/vscode/src/services/browser/UrlContentFetcher.ts`.
- Uses **puppeteer-core (headless Chrome)**: `page.goto(url, {timeout:10s, networkidle2})` →
  cheerio strips script/style/nav/footer/header → Turndown markdown.
- **WEB-1 — ABSENT.** No scheme/host/IP checks; a full browser will hit anything (incl. metadata
  endpoints) and also runs page JS + fetches subresources → a *larger* SSRF surface.
- **Integration: NOT FEASIBLE** for calfcord's lightweight model (bundles Chromium, no SSRF). Its
  only lesson: rendering JS-heavy SPAs genuinely needs a real browser — a separate, future
  concern, not a `web_fetch` replacement.

### 3.7 OpenHands — `All-Hands-AI/OpenHands` — Python, MIT

- Browsing = **BrowserGym + Playwright** (`browsergym-core==0.13.3`, `playwright>=1.55`,
  `html2text` in `pyproject.toml`) — a full interactive browser env (`browse_interactive`), not a
  fetch-as-markdown tool. There is **no lightweight openhands web_fetch** to port (the
  shell/fs/grep built-ins we already wrap come from `openhands-tools`; web is not among them).
- **WEB-1:** no SSRF guard found in the browse path. Heavyweight like Cline.
- **Integration: NOT A FIT** for a lightweight SSRF-safe fetch. `html2text` is just an HTML→md
  lib (an alternative to markdownify), nothing more.

### 3.8 Codex CLI — `openai/codex` — Rust, Apache-2.0

- **File:** `codex-rs/ext/web-search/src/tool.rs`. `web.run` is a **hosted** OpenAI server-side
  search (`SearchClient` → OpenAI Responses API → `EncryptedSearchOutput`),
  `ToolExposure::DirectModelOnly`, config modes live/cached/disabled. No local fetch, no SSRF
  surface.
- **Integration: NOT FEASIBLE** (hosted-only, Rust). Data point: a leading CLI deliberately keeps
  web search server-side.

### 3.9 OpenAI Agents SDK — `openai/openai-agents-python` — Python, MIT

- **File:** `src/agents/tool.py:582` `WebSearchTool` — a **hosted** descriptor only (OpenAI
  Responses API; `user_location`, `search_context_size`, `external_web_access`). No local fetch,
  no web_fetch tool, no SSRF surface.
- **Integration: NOT FEASIBLE** (hosted-only). Confirms the "delegate search to a backend"
  pattern.

### 3.10 Claude Agent SDK — `anthropics/claude-agent-sdk-python` — Python, MIT

- `WebFetch`/`WebSearch` are tool-**name** strings (`src/claude_agent_sdk/types.py`) executed
  server-side by the Claude Code CLI / Anthropic API and gated by permission allow/deny network
  rules ("Network restrictions: Use WebFetch allow/deny rules"). **No local Python HTTP fetch.**
- The CLI's WebFetch (per its tool contract) upgrades HTTP→HTTPS, **returns cross-host redirects
  to the caller rather than following them**, caches 15 min, and fails on authed/private URLs.
- **Integration: NOT FEASIBLE to port** (hosted/CLI-side). Design notes worth stealing:
  (a) permission allow/deny **network rules**, (b) **don't auto-follow cross-host redirects —
  hand them back** (a dead-simple, very effective redirect-SSRF mitigation).

### 3.11 Dedicated libraries (expansion)

**SSRF-safe HTTP**
- **OWASP SSRF Prevention Cheat Sheet** — canonical: scheme allowlist; resolve DNS, validate IP,
  **connect to the validated IP** (preserve Host) to defeat TOCTOU/rebinding; validate every
  redirect; block cloud-metadata. Pydantic AI `_ssrf.py` and openclaw `ssrf.ts` implement this
  faithfully.
- **CVE record** confirms both our failure mode *and* the subtler one (validate-then-refetch is
  still rebinding-vulnerable): AutoGPT GHSA-wvjg-9879-3m7w, Craft CMS GHSA-gp2f-7wcm-5fhx,
  **pydantic-ai GHSA-2jrp-274c-jhv3 (which produced `_ssrf.py`)**, mindsdb GHSA-4jcv-vp96-94xr,
  ragflow #15173. Lesson: naive "parse + check host" is insufficient — **pin DNS at connect
  time.**
- **Stripe Smokescreen** (Go egress proxy) — deployment-level alternative: route the tools
  worker's egress through an SSRF-enforcing proxy. Complementary defense-in-depth, not a code dep.
- **azu/request-filtering-agent** (Node) — connect-time private-IP-blocking agent; the Node
  analogue, confirming the cross-ecosystem pattern.
- **Python `ipaddress` stdlib** — `is_private/is_loopback/is_link_local/is_reserved/is_multicast`
  are the primitives; rolling our own is feasible but re-deriving `_ssrf.py`'s IPv6-transition +
  metadata coverage is error-prone. Prefer Pydantic AI's.

**HTML → markdown / extraction**
- **markdownify** (smolagents + Pydantic AI) — straight HTML→md, no boilerplate stripping;
  already in our tree. Adequate.
- **trafilatura** (Python) — best-in-class main-content/boilerplate extraction (txt/md/xml);
  heavier dep; best *quality* if we later want readability-grade output.
- **readability-lxml / python-readability** — Mozilla Readability port (what openclaw uses
  first). Good middle ground.
- **html-to-text** (Gemini), **Turndown + cheerio/htmlparser2** (OpenCode/Cline) — JS equivalents.
- **Takeaway:** keep markdownify for parity now; optionally add trafilatura/readability later
  behind openclaw's Readability→provider fallback. Not the priority — SSRF is.

---

## 4. Comparison matrix

Legend: ✅ solved · ⚠️ partial · ❌ absent · — N/A · (H) hosted/not self-hostable

| Candidate | Lang / License | WEB-1 SSRF | WEB-2 async | WEB-3 errors | WEB-4 stream cap | WEB-5 trunc | WEB-6 search RL | WEB-7 ctype | Search design | Integration |
|---|---|---|---|---|---|---|---|---|---|---|
| **Pydantic AI** | Py / MIT | ✅ best (DNS-pin, redirect, IPv6-transition, metadata) | ✅ | ✅ | ⚠️ none (add) | ✅ | — | ✅ binary-aware | — | **Adopt/port (highest)** |
| **Hermes Agent** | Py / — | ⚠️ (extract via hosted) | ✅ (offload) | ✅ shape | — | — | ✅ pluggable | n/a | ✅ ABC + 9 providers | **Port pattern (search)** |
| Gemini CLI (local) | TS / Apache-2.0 | ✅ strong (⚠️ not full rebind-pin) | ✅ | ✅ | ✅ streaming | ✅ | ✅ per-host LRU | ✅ | (H) Gemini grounding | Design-to-port |
| openclaw | TS / MIT | ✅ very strong (DNS-pin, redirect, fail-closed) | ✅ | ✅ | ✅ byte+char | ✅ | — | ✅ + untrusted-wrap | provider chain | Design-to-port (large) |
| OpenCode | TS / MIT | ❌ scheme only | ✅ | ✅ | ✅ streaming | ✅ | — | ✅ allowlist | ✅ pluggable (Exa/Parallel) | Design-to-port (+SSRF) |
| Cline | TS / Apache-2.0 | ❌ | ✅ | ⚠️ | ⚠️ (browser) | ⚠️ | — | (browser) | — | Not feasible (Chromium) |
| OpenHands | Py / MIT | ❌ | ✅ (browser) | ⚠️ | ❌ | ⚠️ | — | (browser) | — | Not a fit (Playwright) |
| Codex CLI | Rust / Apache-2.0 | — (H) | — | — | — | — | — (H) | — | (H) OpenAI | Not feasible |
| OpenAI Agents SDK | Py / MIT | — (H) | — | — | — | — | — (H) | — | (H) OpenAI | Not feasible |
| Claude Agent SDK | Py / MIT | — (H, return-redirects) | — | — | — | — | — (H) | — | (H) Anthropic | Not feasible (steal ideas) |
| *Our current (smolagents)* | Py | ❌ critical | ❌ blocks loop | ❌ unprefixed | ❌ | ⚠️ dead code | ❌ sleeps loop | ❌ | hardcoded ddgs singleton | — |

---

## 5. Ranking with rationale

1. **Pydantic AI (`_ssrf.safe_download` + `common_tools.web_fetch`).** Python, MIT, async, and
   the only candidate that ships a *complete, connect-time-pinned, redirect-revalidating,
   IPv6-transition-aware, metadata-blocking* SSRF guard — the exact thing WEB-1 needs, in our
   language, born from a real CVE fix. Fixes WEB-1/2/3/5/7 directly; WEB-4 needs a small streaming
   addition. Lowest effort, highest safety.
2. **Hermes Agent `WebSearchProvider` ABC.** Best answer to the search side: a Python pluggable
   provider registry that kills the shared loop-sleeping ddgs singleton (WEB-6), normalizes error
   shape (WEB-3), and delivers the "swap to Brave/Tavily/SearXNG" capability our docstring already
   promises. Pairs with #1 (fetch vs search are separable).
3. **openclaw web_fetch architecture.** The richest production design (SSRF on par with Pydantic
   AI, plus provider-fallback, caching, dual caps, and untrusted-content wrapping). Best source of
   *additional* patterns to layer on #1 — especially untrusted-content wrapping (prompt-injection
   defense for Discord-sourced URLs) and the streaming byte cap. Heavier to port wholesale.
4. **Gemini CLI local fetch.** Excellent streaming size cap (`readResponseWithLimit`) and
   non-blocking per-host rate limiter; solid (not fully rebind-proof) SSRF. Borrow the
   streaming-cap and rate-limit patterns.
5. **OpenCode.** Good caps/content-type patterns; pluggable search; but **no SSRF** — disqualifies
   it as a fetch base for an autonomous server.
6. Cline / OpenHands (browser-based; heavy; no SSRF) and the hosted-only SDKs (Codex, OpenAI
   Agents, Claude) — not portable; mined only for design ideas (network permission rules;
   return-cross-host-redirects).

---

## 6. Final recommendation

**Port-and-adopt, not stay-and-fix; not a single drop-in.** Concretely:

### `web_fetch` — adopt Pydantic AI's SSRF guard, drop smolagents `VisitWebpageTool`
- Add `pydantic-ai-slim[web-fetch]` via `uv add` **or** vendor `_ssrf.py` into calfcord (it is
  self-contained: `ipaddress` + `socket` + `httpx` + one helper, MIT). Replace the
  `requests.get`-based smolagents path with `await safe_download(url, allow_local=False, ...)`.
  This alone fixes **WEB-1 (SSRF, critical), WEB-2 (async/offloaded), WEB-7 (content-type +
  binary), WEB-5 (honest cap)**, and gives clean error surfacing for **WEB-3** (map
  `ValueError`/`httpx` errors to our `error:` string instead of raising).
- **Close WEB-4 ourselves:** wrap the download in httpx streaming with a running byte budget
  (Gemini's `readResponseWithLimit` / OpenCode's `collectBody` is the template), aborting past a
  threshold *before* decode/markdownify.
- **Layer openclaw's untrusted-content wrapping** around the returned body — calfcord ingests
  URLs influenced by untrusted Discord chat, so tagging fetched content as untrusted is a
  meaningful prompt-injection mitigation (and cheap).
- Keep markdownify for parity; consider trafilatura/readability later for quality.

### `web_search` — port Hermes Agent's `WebSearchProvider` ABC
- Introduce a `WebSearchProvider` ABC + registry + config key; ship a `ddgs` provider first
  (returning `{success, error}` rather than raising, offloaded via `asyncio.to_thread`), with
  Brave/Tavily/SearXNG as opt-in providers. This removes the shared, event-loop-sleeping
  `DuckDuckGoSearchTool` singleton (**WEB-6**), normalizes error shape (**WEB-3** search side),
  and makes good on the existing "swap path" promise.

### Cross-cutting (XC-3)
- Both new paths must be genuinely non-blocking (async httpx for fetch; `asyncio.to_thread` for
  any sync provider) so a slow/large fetch never freezes the `max_workers=1` `calfkit-tools` loop.

### Why not "stay and fix"
We *could* keep smolagents and bolt on a guard, but smolagents' `VisitWebpageTool` swallows
errors as strings and offers no injection point for streaming/validation — we'd be reimplementing
the whole network layer anyway. Adopting Pydantic AI's purpose-built, CVE-hardened SSRF guard is
both safer and less code than a hand-rolled fix, and the CVE record shows hand-rolled SSRF guards
are easy to get subtly (rebinding-)wrong.

### Optional defense-in-depth
For a belt-and-suspenders posture, route the `calfkit-tools` worker's egress through an
SSRF-enforcing proxy (Stripe **Smokescreen**) so the network blocklist is enforced even if a
code-level guard regresses. Complementary to, not a replacement for, the in-process guard.
