# Web tools research — realtime log

## Step 1 — Verification of OUR issues (source-confirmed)

### Our shim: `src/calfcord/tools/builtin/web.py`
- `web_fetch(ctx, url)` (async) → `_get_visit_tool().forward(url=url)` inside `try/except Exception` → on success, truncates at `_MAX_FETCH_CHARS=50_000`.
- `web_search(ctx, query)` (async) → `_get_search_tool().forward(query=query)`.
- Module-global singletons `_visit_tool`, `_search_tool` (lazy). Shared across all agents.
- No URL validation, no scheme check, no offload to thread.

### Upstream smolagents `.venv/.../smolagents/default_tools.py`
**VisitWebpageTool.forward (lines ~478-507):**
```python
response = requests.get(url, timeout=20)
response.raise_for_status()
markdown_content = markdownify(response.text).strip()
markdown_content = re.sub(r"\n{3,}", "\n\n", markdown_content)
return self._truncate_content(markdown_content, self.max_output_length)  # default 40000
except requests.exceptions.Timeout:
    return "The request timed out. Please try again later or check the URL."
except RequestException as e:
    return f"Error fetching the webpage: {str(e)}"
except Exception as e:
    return f"An unexpected error occurred: {str(e)}"
```
- `__init__(self, max_output_length: int = 40000)` — we never override → **WEB-5 confirmed** (40K cap upstream, our 50K guard dead).
- `requests.get(url, timeout=20)`: no scheme/host validation, `allow_redirects=True` default (requests), no `stream=True`, no Content-Length check → **WEB-1, WEB-4 confirmed**.
- `response.text` uses Content-Type charset or charset_normalizer detection; no Content-Type gate → **WEB-7 confirmed**.
- Error paths `return` strings (not raise) → never hit our `except` → no `error:` prefix → **WEB-3 confirmed**.

**DuckDuckGoSearchTool (lines ~144-160):**
```python
def __init__(self, max_results=10, rate_limit: float|None = 1.0, **kwargs):
    self._min_interval = 1.0 / rate_limit if rate_limit else 0.0
    self._last_request_time = 0.0
    from ddgs import DDGS
    self.ddgs = DDGS(**kwargs)
def forward(self, query):
    self._enforce_rate_limit()
    results = self.ddgs.text(query, max_results=self.max_results)
    if len(results)==0: raise Exception("No results found! ...")  # <- this RAISES (caught by our except)
    ...
def _enforce_rate_limit(self):
    import time
    now = time.time(); elapsed = now - self._last_request_time
    if elapsed < self._min_interval: time.sleep(self._min_interval - elapsed)  # blocks event loop
    self._last_request_time = time.time()
```
- `time.sleep` blocks loop; one shared singleton → global lane → **WEB-2, WEB-6 confirmed**.
- Note: empty-results path RAISES (unlike fetch) so it does get `error:` prefixed by our wrapper.
- `GoogleSearchTool` exists upstream (serpapi/serper providers) — smolagents has a pluggable-ish search story but via separate classes, not one tool.

**WEB-2 mechanism:** both `forward()` calls are sync/blocking, invoked directly in `async def` with no `asyncio.to_thread`. calfkit auto-offloads only SYNC tool fns; by writing `async def` we opt out → blocking call freezes the single shared `calfkit-tools` loop (`max_workers=1`). Confirmed.

All 7 WEB issues + XC-3 (single-worker amplifier) verified at source.

---

## Step 2 — Candidate framework survey
(appending below as I go)

---

### CANDIDATE: Gemini CLI (google-gemini/gemini-cli) — TS, Apache-2.0 ★ SSRF reference
**web-fetch:** `packages/core/src/tools/web-fetch.ts`; SSRF helpers `packages/core/src/utils/fetch.ts`.
- **SSRF (WEB-1) — BEST IN CLASS.** `isBlockedHost` (web-fetch.ts:270) blocks localhost/127.0.0.1 + `isPrivateIp`. `fetch.ts`:
  - `parsePrompt` (web-fetch.ts:111) allowlists `http:`/`https:` only, rejects others + malformed.
  - `isAddressPrivate` (fetch.ts:115) uses **ipaddr.js**: `addr.range() !== 'unicast'` → blocks loopback/linkLocal(169.254)/private/reserved/multicast in ONE check; unmaps IPv4-mapped IPv6 (`::ffff:x`); explicit IANA benchmark range 198.18/15; `isValid` else parse-fail→treat unsafe.
  - `isPrivateIpAsync` (fetch.ts:150) does **DNS lookup({all:true})** then checks every resolved A/AAAA → catches DNS-rebinding at resolve time. (NOTE: the sync `isBlockedHost` used in fetch path only parses the literal host, not DNS-resolved — the async variant exists but the experimental path uses sync; partial. Still far ahead of us.)
- **WEB-2 (async):** native `fetch` + `AbortController` timeout (`fetchWithTimeout`, fetch.ts:190); fully non-blocking. N/A to JS but the design = offload.
- **WEB-3 (errors):** structured `ToolResult.error{message,type}` + `llmContent: "Error: ..."`. Clean discriminator.
- **WEB-4 (size cap):** `readResponseWithLimit` (web-fetch.ts:555) checks Content-Length AND streams via reader with running total, cancels reader on overflow. 10MB hard cap. STREAMING — gold standard.
- **WEB-5 (truncation):** explicit `MAX_CONTENT_LENGTH=250000`, water-filling budget across multiple URLs.
- **WEB-6 (rate limit):** `checkRateLimit` per-host LRU sliding window (10/min), NON-blocking (returns skip, no sleep). Excellent.
- **WEB-7 (content-type):** gates on `content-type`: html→html-to-text, json/plain→raw, image/video/pdf→base64 inline. No mojibake-feeding.
- **markdown:** uses `html-to-text` (not markdownify) with selectors stripping a/img.
- **web-search:** `packages/core/src/tools/web-search.ts` — HOSTED (Gemini API grounding, `model:'web-search'`). NOT self-hostable. The default `web_fetch` is ALSO hosted-by-default (sends URLs to Gemini to fetch+summarize); the local fetch is the `getDirectWebFetch()`/`executeExperimental` + `executeFallback` paths (the self-hostable design we care about).
- **Integration for calfcord:** DESIGN-TO-PORT (TS→Python). The `fetch.ts` SSRF logic ports cleanly to Python (`ipaddress` stdlib gives `is_private/is_loopback/is_link_local/is_reserved/is_multicast`; socket.getaddrinfo for DNS). Best blueprint for our SSRF fix.

### CANDIDATE: Codex CLI (openai/codex) — Rust, Apache-2.0
- `codex-rs/ext/web-search/src/tool.rs`: `web.run` is a **HOSTED** OpenAI server-side search (`SearchClient`→OpenAI Responses API, returns `EncryptedSearchOutput`). `ToolExposure::DirectModelOnly`. No local fetch, no SSRF surface, NOT self-hostable. Config modes live/cached/disabled.
- **Takeaway:** like Gemini, Codex delegates web to a hosted backend → no local SSRF concern, but no portable code for us.

### CANDIDATE: OpenCode (sst/opencode) — TS, MIT
- `packages/core/src/tool/webfetch.ts` (newer, 217L) + `packages/opencode/src/tool/webfetch.ts` (older, 192L).
- **SSRF (WEB-1) — ABSENT.** Only `assertHttpUrl` scheme allowlist (http/https). **No private-IP/localhost block** → `webfetch("http://169.254.169.254/...")` would go through. Permission-prompt (`ctx.ask`/`permission.assert`) is the only gate — fine for a local single-user CLI, NOT for an autonomous multi-agent server.
- **WEB-4 (size cap):** STRONG. `collectBody` checks Content-Length then streams `Stream.runForEach` with running `size` total, fails past 5MB. (older file: arrayBuffer + post-check, weaker.)
- **WEB-3 (errors):** `ToolFailure` typed error / Effect failure channel.
- **WEB-6:** no rate limit.
- **WEB-7 (content-type):** `isTextualMime` allowlist; rejects images/binaries with explicit error; html→Turndown markdown, html→text via htmlparser2 stripping script/style/iframe/etc.
- **format param:** text|markdown|html selectable. Cloudflare-challenge retry with honest UA. Timeout (default 30s, max 120s).
- **Integration:** DESIGN-TO-PORT. Good content-type + streaming-cap patterns, but MUST add SSRF (it has none).

### CANDIDATE: Cline (cline/cline) — TS, Apache-2.0
- `apps/vscode/src/services/browser/UrlContentFetcher.ts`: uses **puppeteer-core (headless Chrome)** → `page.goto(url, timeout 10s, networkidle2)` → cheerio strips script/style/nav/footer/header → Turndown markdown.
- **SSRF (WEB-1) — ABSENT.** No scheme/host/IP validation at all; full browser will hit anything incl. metadata endpoints. Heavyweight (spawns Chromium). JS-rendering is its strength (SPA docs) but unsuitable as a lightweight server tool and a bigger SSRF surface (browser also runs JS, fetches subresources).
- **Integration:** NOT FEASIBLE for calfcord's lightweight model (Chromium dep, no SSRF). Useful only as evidence that "render JS" needs a real browser.

### CANDIDATE: OpenHands (All-Hands-AI/OpenHands) — Python, MIT
- Browsing = **BrowserGym + Playwright** (`browsergym-core==0.13.3`, `playwright>=1.55`, `html2text`) — a full interactive browser env (`browse_interactive`), not a fetch-as-markdown tool. The lightweight built-in tools (shell/fs/grep) we already wrap come from openhands-tools; there is NO openhands lightweight web_fetch equivalent to port.
- **Integration:** the browser approach is the heavy end (like Cline). `html2text` is just an HTML→md lib. No SSRF guard found in the browse path. NOT a fit for a lightweight SSRF-safe fetch.

### CANDIDATE: OpenAI Agents SDK (openai/openai-agents-python) — Python, MIT
- `src/agents/tool.py:582` `WebSearchTool` is a HOSTED descriptor only (OpenAI Responses API, `name="web_search"`, `search_context_size`, `external_web_access`). No local fetch, no SSRF surface, NOT self-hostable. No web_fetch tool.
- **Integration:** NOT FEASIBLE (hosted-only). Design note: confirms the "delegate search to a hosted backend" pattern again.

### CANDIDATE: Claude Agent SDK (anthropics/claude-agent-sdk-python) — Python, MIT
- `WebFetch`/`WebSearch` are tool-NAME strings (types.py) executed server-side by the Claude Code CLI / Anthropic API; gated by permission allow/deny rules ("Network restrictions: Use WebFetch allow/deny rules"). NO local Python HTTP fetch in the SDK.
- The CLI's own WebFetch (per the harness tool description) = HTTP→HTTPS upgrade, **cross-host redirects RETURNED to caller (not auto-followed)**, 15-min cache, fails on authed/private URLs. A clean redirect-SSRF mitigation pattern (don't follow cross-host redirects at all).
- **Integration:** NOT FEASIBLE to port (hosted/CLI-side). Design notes: permission allow/deny network rules + return-redirects-rather-than-follow.

### CANDIDATE: Pydantic AI (pydantic/pydantic-ai) — Python, MIT ★★ BEST PORT/ADOPT
- **SSRF module:** `pydantic_ai_slim/pydantic_ai/_ssrf.py` (`safe_download`). **Most complete Python SSRF defense surveyed.**
  - `validate_url_protocol`: http/https allowlist (_ssrf.py:249).
  - `is_private_ip` (_ssrf.py:202) + `_PRIVATE_NETWORKS` table (lines 25-52): IPv4 0/8,10/8,100.64/10(CGNAT incl Alibaba),127/8,169.254/16(link-local+metadata),172.16/12,192.168/16, plus IANA special-use (192.0.0/24,TEST-NETs,198.18/15 RFC2544,224/4 multicast,240/4 reserved); IPv6 ::/128,::1,fe80::/10,fc00::/7 ULA, + Teredo/doc/discard/multicast.
  - `is_cloud_metadata_ip` (_ssrf.py:185): explicit AWS(169.254.169.254/.170.2/.170.23 + IPv6 fd00:ec2::*), GCP, **Azure 168.63.129.16 (a PUBLIC IP — only the metadata guard catches it)**, Alibaba 100.100.100.200, Oracle 192.0.0.192, Scaleway. Always blocked even with allow_local.
  - **IPv6 transition-form decoding** (`_embedded_ipv4s`, _ssrf.py:142): IPv4-mapped ::ffff, 6to4 2002::, NAT64 64:ff9b::/96 + 64:ff9b:1::/48, ISATAP, Teredo (XOR-decoded) — blocks an IPv4 smuggled inside an IPv6 literal. Far beyond Gemini's ipaddr.js single-unmap.
  - **DNS-REBINDING / TOCTOU DEFENSE (the crux):** `safe_download` (_ssrf.py:434) resolves hostname → validates EVERY resolved IP → then **connects to the resolved IP** (`build_url_with_ip`) with `Host: <original hostname>` header AND TLS `sni_hostname` extension. So the IP it validated == the IP it connects to; a second rebinding DNS lookup never happens. This is the layer the OWASP cheat sheet says the fix must live at.
  - **Per-redirect-hop revalidation:** `follow_redirects=False`, manual loop re-resolving+revalidating each hop; max_redirects=10; strips Authorization/Cookie/Proxy-Auth on cross-origin redirect (RFC 7235); domain allow/blocklist checked on EVERY hop.
  - Trailing-dot FQDN strip (`169.254.169.254.` bypass), fail-closed on invalid IP. DNS via `run_in_executor` (thread offload → non-blocking, the WEB-2 fix).
- **Agent tool:** `pydantic_ai_slim/pydantic_ai/common_tools/web_fetch.py` (`web_fetch_tool`):
  - async; calls `safe_download`; `except (ValueError, HTTPStatusError, RequestError) → raise ModelRetry(...)` (clean error surfacing — for us map to `error:` string instead of raising).
  - **Content-type gating** (`is_text_like_media_type`): text/markdown passthrough; html/xhtml→`markdownify(strip=[img,script,style])` + `<title>` extraction; application/json→pretty-printed fenced block; **binary→`BinaryContent`** (native model handling, not garbled). WEB-7 fully solved.
  - `Accept: text/markdown,...` → servers (Cloudflare/Vercel/Mintlify) return pre-rendered markdown → token savings + better quality.
  - `max_content_length=50_000` char cap; collapses 3+ newlines.
- **GAP (honest):** NO pre-decode STREAMING byte cap. `safe_download` returns the full `httpx.Response`; the tool reads `response.text`/`.content` → full body buffered before the char cap. So **WEB-4 (OOM) is only partly addressed** (no Content-Length/iter-bytes budget like Gemini/OpenCode/openclaw). Easy to add (httpx `stream=True` + running total) but not present today.
- **License:** MIT. Deps: httpx, markdownify (we already pull markdownify via smolagents).
- **Integration for calfcord — STRONGEST:** Either `uv add pydantic-ai-slim[web-fetch]` and call `safe_download` directly, OR vendor/port `_ssrf.py` (~530 lines, self-contained: ipaddress+socket+httpx+one internal helper) into calfcord. Replaces the smolagents shim's blocking `requests.get` with an async, SSRF-safe httpx path in one move. Add a streaming byte cap on top to fully close WEB-4.

### CANDIDATE: Hermes Agent (NousResearch/hermes-agent) — Python ★ SEARCH-PROVIDER reference
- **Pluggable web-search/extract provider design:** `agent/web_search_provider.py` (ABC `WebSearchProvider`: `name`, `is_available()`, `supports_search/extract`, `search(query,limit)→{success,data:{web:[...]}}`, `extract(urls)→[{url,title,content,raw_content,metadata}]`, `get_setup_schema()`); registry `agent/web_search_registry.py`; config-selected via `web.search_backend`/`web.extract_backend`. 9 in-tree providers under `plugins/web/`: brave_free, ddgs, searxng, exa, parallel, tavily, firecrawl, xai.
  - `plugins/web/ddgs/provider.py`: same ddgs lib we use, but returns `{"success":False,"error":...}` (no raise), defensive limit cap, `is_available()` import probe. Sync `search` — dispatcher offloads blocking sync via `inspect.iscoroutinefunction` detection (docstring explicitly recommends `asyncio.to_thread`).
- **SSRF:** not the focus of the search providers (search APIs are trusted endpoints); `extract` providers (firecrawl/exa/tavily) are hosted services that fetch on your behalf (their server eats the SSRF risk).
- **Integration for calfcord — search side:** PORT THE PROVIDER ABC PATTERN. Directly answers our "pluggable vs hardcoded search provider" question: a `WebSearchProvider` ABC + registry + config key beats our hardcoded smolagents `DuckDuckGoSearchTool` singleton, and decouples the rate-limiter from a shared global.

### CANDIDATE: openclaw (openclaw/openclaw) — TS, MIT ★ most feature-complete production design
- **web_fetch:** `src/agents/tools/web-fetch.ts`; SSRF `src/infra/net/ssrf.ts` (727L) + `src/agents/tools/web-guarded-fetch.ts`.
  - **SSRF:** `fetchWithWebToolsNetworkGuard` with `SsrFPolicy`. `isPrivateIpAddress` decodes embedded IPv4 in IPv6, **fails closed on malformed IPv6 literal**; `isBlockedHostname` blocks `localhost`, `*.localhost`, `metadata.google.internal`, BLOCKED_HOSTNAMES set; cloud-metadata + special-use v4/v6 blocklists (shared `@openclaw/...` net core). **Pinned DNS lookup** (resolve→connect-to-IP, anti-rebinding) + **per-redirect-hop re-evaluation** ("redirects must re-evaluate against their own URL"); configurable escapes (allowPrivateNetwork/RFC2544/IPv6-ULA) for fake-ip proxy stacks.
  - **Provider fallback chain:** Readability extractor → web-fetch provider (e.g. Firecrawl, `extensions/firecrawl/web-fetch-provider.ts`) → basic HTML cleanup. Pluggable provider contract (`src/plugin-sdk/provider-web-fetch.ts`).
  - **Size caps:** `maxResponseBytes` 750KB default (32KB-10MB clamp) via `readResponseText({maxBytes})`; `maxChars` 20K default (configurable cap).
  - **Content-type gating:** text/markdown (Cloudflare "Markdown for Agents", reads `x-markdown-tokens`), text/html→Readability/markdown, application/json→pretty. 
  - **Prompt-injection mitigation:** `wrapWebContent`/`wrapExternalContent` tag fetched body as `untrusted` external content. (Relevant to us — Discord-driven prompt injection.)
  - Caching (TTL), abort/signal handling, progress events, redirect cap (3 default), `ModelRetry`-style errors.
- **License:** MIT (OpenClaw Foundation). 
- **Integration for calfcord:** DESIGN-TO-PORT (TS→Python, large). Best *reference architecture* for a production web_fetch (provider fallback + untrusted-content wrapping + caps + SSRF), but heavier than Pydantic AI to adopt. Borrow: untrusted-content wrapping, provider-fallback chain, byte+char dual cap.

---

## Step 3 — Dedicated SSRF-safe HTTP / HTML-extraction libraries (web-researched + cross-checked)

### SSRF-safe HTTP
- **OWASP SSRF Prevention Cheat Sheet** — canonical guidance: scheme allowlist; resolve DNS, validate IP, **connect to the validated IP** (Host header preserved) to defeat TOCTOU/DNS-rebinding; validate on every redirect; block cloud-metadata. Pydantic AI's `_ssrf.py` and openclaw's `ssrf.ts` both implement this faithfully. (cheatsheetseries.owasp.org/.../Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html)
- **Multiple recent CVEs confirm the failure mode** we have, plus the SUBTLER one (validate-then-refetch is still vulnerable to rebinding): AutoGPT GHSA-wvjg-9879-3m7w (DNS rebinding in a requests wrapper), Craft CMS GHSA-gp2f-7wcm-5fhx (metadata SSRF via rebinding), pydantic-ai GHSA-2jrp-274c-jhv3 (the advisory that PRODUCED `_ssrf.py`), mindsdb GHSA-4jcv-vp96-94xr, ragflow #15173 (TOCTOU). Lesson: a naive "parse URL + check host" is INSUFFICIENT; must pin DNS at connect time — exactly what Pydantic AI does.
- **Stripe Smokescreen** (Go) — dedicated SSRF-aware egress proxy; the "egress proxy" alternative to in-process guards. Deployment-level option for calfcord (route the tools worker's egress through a proxy that enforces the blocklist) — complementary, not a code dependency.
- **azu/request-filtering-agent** (Node) — http(s).Agent that blocks private/reserved IPs at connect time; the Node analogue of the connect-time pin. Confirms the pattern across ecosystems.
- **Python `ipaddress` stdlib** — gives `is_private/is_loopback/is_link_local/is_reserved/is_multicast/is_unspecified`; the building block Pydantic AI/Gemini use. A from-scratch guard is feasible but reinventing `_ssrf.py`'s IPv6-transition + metadata coverage is error-prone — prefer Pydantic AI's.

### HTML → markdown / content extraction
- **markdownify** (what smolagents + Pydantic AI use) — straight HTML→md; no boilerplate stripping. Adequate; Pydantic AI strips img/script/style.
- **trafilatura** (Python, GPL/Apache dual) — best-in-class main-content/boilerplate extraction (removes nav/ads/footer), outputs txt/md/xml; heavier dep. Best *content quality* if we want readability-grade extraction.
- **readability-lxml / python-readability** — Mozilla Readability port; what openclaw uses (Readability) before falling back. Good middle ground.
- **html-to-text** (Gemini) / **Turndown + cheerio/htmlparser2** (OpenCode/Cline) — JS-side equivalents.
- **Takeaway:** keep markdownify (already present) for parity; optionally add trafilatura/readability for higher-quality extraction later (openclaw's Readability→provider fallback is the model). Not the priority — SSRF is.
