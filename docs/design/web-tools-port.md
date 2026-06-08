# Design: Web tools (`web_fetch` / `web_search`) — vendoring & calfkit integration

**Status:** Draft (grill complete; pre deep-review). Sources confirmed + closures verified against upstream.
**Date:** 2026-06-08
**Owners:** Ryan
**Decisions:** [ADR-0001](../adr/0001-vendor-pydantic-ai-web-fetch-self-contained.md) (glue-as-reference), [ADR-0002](../adr/0002-per-vendor-config-now-unify-later.md) (per-vendor config).

This doc captures the design for the second batch of agent tools vendored into
`calfkit-peripherals`, following the web-tools research report
(`docs/tools-research/reports/web-tools-report.md`) and a grilling session that
verified every load-bearing closure against upstream source.

---

## 1. Purpose & scope

Bring two **independent**, production-grade web tools into calfkit by vendoring:

- **`web_fetch`** — fetch a URL, return cleaned markdown — built on **pydantic-ai**'s
  SSRF-safe fetcher. A **new component** (`components/pydantic-web-fetch/`).
- **`web_search`** — query → ranked links, plus optional URL → content **extract** —
  built on **hermes-agent**'s pluggable provider system. **Extends the existing**
  `components/hermes-agent/` component (vendor-by-source: hermes → shell + files +
  web_search).

The two are **not integrated** and share no code. `web_fetch` fetches in-process
behind our own SSRF guard; `extract` (a `web_search` provider capability) is its
hosted-provider twin (Tavily/Exa do the fetching). They are deliberately separate.

**Out of scope (deliberate no-s):**
- *Untrusted-content wrapping* (tagging fetched bodies as untrusted for prompt-injection
  defense) — that is an **agent-layer** concern owned by calfcord, not the node tool.
- *Readability-grade extraction* for `web_fetch` — markdownify matches current behavior;
  hosted `extract` providers cover hard pages.

## 2. Repo architecture

Per [`project-structure.md`](../project-structure.md): vendor by source, expose by tool;
polyglot Kafka-node components; `_vendor/` (verbatim + import-rewritten), `_shims/`
(runtime edges), `node/` (our Kafka adapter), `reference/` (documentation-only).

---

## 3. Component: `pydantic-web-fetch` (`web_fetch`) — NEW

### 3.1 Source & provenance
- **Source:** `pydantic/pydantic-ai`, MIT © Pydantic Services Inc. **Pin: `v1.106.0`**
  (latest stable; `_ssrf.py`'s HEAD currently sits among unreleased `v2.0.0b*` betas —
  do not chase a beta for a security-critical guard; re-sync to v2.0.0 when it GAs).
- Born from a real CVE incomplete-fix chain (GHSA-2jrp-274c-jhv3 → CVE-2026-25580 →
  CVE-2026-46678) — the regression tests are the assurance.

### 3.2 Vendor set
**Live code** (`src/calfkit_pydantic_web/_vendor/`), self-contained — deps = stdlib +
`httpx` + `markdownify`, **no `pydantic-ai`** (ADR-0001):
- `_ssrf.py` (~533 LOC) — `safe_download`: scheme allowlist; full private/reserved/
  cloud-metadata IP tables (incl. Azure's public `168.63.129.16`); IPv6-transition
  decoding; **connect-to-validated-IP** (DNS-rebinding/TOCTOU defense); per-redirect-hop
  revalidation with cross-origin auth/cookie stripping.
- the fetch/content-type **engine** lifted from `common_tools/web_fetch.py` (~115 LOC):
  content-type gating, `markdownify(strip=[img,script,style])`, `<title>` extraction,
  JSON fencing, binary handling, the 50k char cap, the `Accept: text/markdown` header.

**Tests** (`tests/`): vendor `test_ssrf.py` (regression for the CVE chain).

**Reference only** (`reference/upstream/`, not packaged, not imported): verbatim
`_ssrf.py` + `common_tools/web_fetch.py` — preserves the pydantic glue
(`web_fetch_tool()` / `Tool` / `ModelRetry` / `BinaryContent`) so the calfkit port can
reconnect it against calfkit's pydantic-ai runtime without re-fetching upstream.

### 3.3 Self-containment — inline these private helpers
`_ssrf.py` and the engine reach into pydantic-ai private modules whose home files are
huge but whose logic is tiny; **inline** (~30 LOC total), do **not** import the home
modules:
- `run_in_executor` (`_utils.py`) → ~5-line wrapper over `asyncio.to_thread`.
- `create_async_http_client` + `get_user_agent` (`models/__init__.py`, 1900+ LOC — the
  whole agent framework) → ~3-line `httpx.AsyncClient(...)` factory with our own UA.
- `is_text_like_media_type` (`_utils.py`) → ~10-line leaf, copy verbatim.

Dropped (framework glue, kept as reference): `ModelRetry`, `BinaryContent`, `Tool`,
`web_fetch_tool()`.

### 3.4 Streaming byte cap — the one hand-roll (patch, not feature)
`safe_download` buffers the full body (`client.get()`) → the 50k **char** cap runs only
*after* the whole body is in memory + parsed → a multi-GB / chunked / no-`Content-Length`
response is a **memory-DoS** on the single-worker loop. No Python upstream bundles SSRF +
streaming. **Patch the vendored guard** (`patches/`): swap the final request to
`client.stream()` + `iter_bytes()` with a running byte total that aborts past ~5–10 MB,
**before** decode/markdownify. ~10–15 LOC, marked modification.

### 3.5 Binary representation
Live engine returns binary media as a neutral `bytes + media_type` (small dataclass),
not pydantic's `BinaryContent`. The reference glue documents the re-wrap at port time.

### 3.6 Node adapter (`node/`, port phase)
Calls `safe_download(url, allow_local=False, max_redirects=10, timeout=…)` (sensible
defaults), maps `ValueError`/`httpx` errors to calfkit's `error:` reply convention,
returns the capped markdown (or neutral binary) over the Kafka contract. Async throughout.

---

## 4. Component: `hermes-agent` — add `web_search` (+ `extract`)

Extends the existing component at the **same pin** (`5a36f76a…`, MIT © 2025 Nous Research).

### 4.1 The provider model (vendor whole — both verbs)
- `agent/web_search_provider.py` (~185 LOC) — the `WebSearchProvider` ABC with **both**
  `search(query, limit)` and `extract(urls)` verbs + capability flags. Stdlib only.
- `agent/web_search_registry.py` (~245 LOC) — an **in-memory** process-global
  `Dict[name → provider instance]` + a `threading.Lock`; populated by plugin `register()`
  hooks; queried by name with capability/availability filtering and a config-driven
  selection + legacy fallback order. One internal edge: `hermes_cli.config.load_config`
  — **already shimmed**. Vendored whole incl. the config-selection layer (ADR-0002).
- `tools/url_safety.py` (~361 LOC) — `async_is_safe_url`, the **extract-side SSRF guard**
  the node runs over URLs before dispatching to an extract provider. Stdlib + already-
  vendored `utils.is_truthy_value` + already-shimmed `hermes_cli.config`.

### 4.2 Providers — vendor by verified coupling
Shared helpers `tools.interrupt`, `tools.lazy_deps`, `utils`, `agent.redact` are **already
vendored** from the shell+file port. `tools/web_tools.py` (1347-LOC in-process dispatcher,
deep Nous-gateway tail) is **NOT** vendored — the node adapter replaces its dispatch role.

| Provider | Cap | Vendor now? | Coupling beyond ABC |
|---|---|---|---|
| ddgs | search | ✅ base | — (`ddgs` lazy) |
| brave-free | search | ✅ extra | `httpx` |
| searxng | search | ✅ extra | `httpx` |
| **tavily** | search + **extract** | ✅ (clean extract backend) | `tools.interrupt` *(vendored)* |
| exa | search + extract | optional | lift `_wt._exa_client` (1 small factory fn) |
| parallel | search + extract | optional | lift `_wt._parallel_client(+async)` (2 fns) |
| firecrawl | search + extract | ❌ **defer** | 10 helpers, mostly **Nous managed-gateway** infra |
| xai | search | ❌ defer | `tools.xai_http` (own tail) + config |

Rationale for the defers: firecrawl/xai are the only providers where "take it whole" drags
infrastructure we will never run (Nous gateway / xai_http), and "lift it clean" is exactly
the precision-cutting we avoid. Everything clean-to-take is taken.

### 4.3 Offload
The provider `search()` path is **synchronous/inline** upstream; the node consume-loop must
offload it via `asyncio.to_thread` (extract already has the `iscoroutinefunction`/`to_thread`
dispatch). Small adapter glue, not vendored code.

### 4.4 Node adapter (`node/`, port phase)
Registers the shipped providers explicitly at startup; resolves the active provider via the
vendored registry (hermes `config.yaml`); for `extract`, runs `async_is_safe_url` over the
URLs first; offloads sync `search`; returns the provider's already-`{success, data}`-shaped
result over the Kafka contract. Update `NODE.md` with the `web_search` contract.

---

## 5. What we hand-roll (net)
Across both tracks, first-party code is minimal: the ~30 LOC of inlined leaf helpers
(mechanical), the ~10–15 LOC streaming-cap patch (the lone forced hand-roll), the
`asyncio.to_thread` search offload, and the two `node/` Kafka adapters (unavoidable glue).
Everything else is vendored prod-grade code.

## 6. Open items / next steps
1. **Deep review** (per CLAUDE.md): adversarial review of this design + the verified
   closures before implementing; converge to no criticals.
2. Confirm exa/parallel in-or-out for the first cut (optional extract backends).
3. Implement per `project-structure.md` §"How to add a new port" + TDD the `node/` adapters.
4. Append both components to `THIRD_PARTY_NOTICES.md`; record provenance in each
   `METADATA.yaml`.
