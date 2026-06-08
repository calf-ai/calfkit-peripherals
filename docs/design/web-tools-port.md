# Design: Web tools (`web_fetch` / `web_search`) — vendoring & calfkit integration

**Status:** Draft (grill complete; **deep-review round 1 done — spec amended below, see §7**). Two simplifications pending user decision (glue-as-reference, config resolver). Sources confirmed + closures verified against upstream at the pinned versions.
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
- `run_in_executor` (`_utils.py`) → **re-implement** the DNS thread-offload over
  `asyncio.to_thread` (functional equivalent — the real upstream impl uses ContextVars +
  anyio, so this is NOT a verbatim lift). Only `resolve_hostname` needs it.
- `create_async_http_client` + `get_user_agent` (`models/__init__.py`, 1900+ LOC — the
  whole agent framework) → ~3-line `httpx.AsyncClient(...)` factory with our own UA.
- `is_text_like_media_type` (`_utils.py`) → ~10-line leaf, copy verbatim.

Dropped (framework glue, kept as reference): `ModelRetry`, `BinaryContent`, `Tool`,
`web_fetch_tool()`.

### 3.4 Streaming byte cap — guard/engine restructure (NOT a localized patch) ⚠️ amended §7-C1
`safe_download` buffers the full body (`await client.get(...)` *inside* the
`async with create_async_http_client(...)` block) and returns an `httpx.Response`; the
engine reads `.text`/`.content` **after** that block has closed. So the 50k **char** cap
runs only after the whole body is in memory + parsed → a multi-GB / chunked /
no-`Content-Length` response is a **memory-DoS** on the single-worker loop. No Python
upstream bundles SSRF + streaming.

You **cannot** fix this with a swap-`get`-for-`stream` patch that still returns a
`Response`: once the client context exits, a streamed body is unreadable
(`ResponseNotRead`). The byte-cap must move **inside** the guard — a `safe_stream` variant
(or a flag) that, on the **final non-redirect hop**, consumes `iter_bytes()` with a running
total, **aborts past a named `MAX_FETCH_BYTES` constant** (pick one value, e.g. 5 MB per
OpenCode), and returns `(media_type, capped_bytes, status)` rather than a `Response` —
**before** decode/markdownify. This restructures the guard↔engine boundary and **rewrites
~23 `client.get` mock sites + the buffer/close regression test** in `test_ssrf.py` (see §7).
Realistic budget: ~40–60 LOC + a real test rewrite, recorded in `patches/`. Still done now
(deferring ships a known memory-DoS) — just scoped honestly as a restructure, not a patch.

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
| exa | search + extract | ❌ **defer (fast-follow)** | reads/writes a shared mutable client **cache slot on `web_tools`** (not vendored) → `ImportError` as-is; MODERATE — see §7-C3 |
| parallel | search + extract | ❌ **defer (fast-follow)** | same cache-slot coupling (`_parallel_client` + async) |
| firecrawl | search + extract | ❌ defer | 10 helpers, mostly **Nous managed-gateway** infra |
| xai | search | ❌ defer | `tools.xai_http` (own tail) + config |

**exa/parallel decision (was "optional"): DEFER.** Deep review (§7-C3) corrected the coupling
— it is **not** "lift a factory fn." Each provider does `import tools.web_tools as _wt` and
reads/writes module-global client **cache slots** (`_wt._exa_client` …) that live in the
1347-LOC `web_tools.py` we are deliberately not vendoring; the rewriter would map the import
to a non-existent module → runtime `ImportError`. Making them work needs relocating the cache
slots onto each provider (a marked local edit) + a rewriter rule = MODERATE. Per the "if not a
lot more work, keep" rule, that crosses the line → ship them in a fast-follow.

firecrawl/xai defer because "take it whole" drags infra we will never run (Nous gateway /
xai_http) and "lift it clean" is exactly the precision-cutting we avoid.

**Vendor path + scaffolding (§7-C4):** vendor only each provider's `provider.py` under a
`tools.*`-covered path (e.g. `_vendor/tools/web_providers/<name>.py`); **drop** the
`plugins/web/<name>/__init__.py` + `plugin.yaml` (their `register(ctx)` hook needs an
un-vendored `PluginContext`; the node calls `register_provider()` directly). Add the rewriter
rule for the chosen path if it isn't already under `tools.*`.

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
1. **User decision on two simplifications** (§7-M1, §7-M2) before scaffolding.
2. Resolve component naming (§7-L: dir `pydantic-web` ↔ pkg `calfkit_pydantic_web`).
3. (Optional) round-2 confirmation review of the amended spec → converge to no criticals.
4. Implement per `project-structure.md` §"How to add a new port" + TDD the `node/` adapters.
5. Append both components to `THIRD_PARTY_NOTICES.md`; record provenance + CVE lineage +
   secondary-origin check in each `METADATA.yaml`.

## 7. Deep-review findings (round 1, 3 adversarial opus agents) — spec amended

Verified against upstream **at the pinned versions** (pydantic-ai `v1.106.0`; hermes
`5a36f76`). Both pins confirmed valid — all target files present at tag/SHA; licenses clean
(pydantic MIT © Pydantic Services Inc.; hermes MIT © Nous Research).

**CRITICAL (amended into the body above):**
- **C1 — streaming cap is a guard/engine restructure, not a ~10-LOC patch** (body read inside
  a closed client context; can't return a streamed `Response`). ~40–60 LOC + test rewrite.
  → §3.4 rewritten.
- **C2 — the streaming change rewrites ~23 `client.get` mock sites + the buffer/close
  regression test** in `test_ssrf.py`. So `test_ssrf.py` is a **port-and-rewrite, not a
  verbatim vendor.** Also: the test monkeypatches `_ssrf.run_in_executor` /
  `create_async_http_client` by module-string — keep those module-level patchable names and
  repoint the strings after inlining.
- **C3 — exa/parallel coupling was inverted; vendoring as-is = `ImportError`.** They depend on
  a shared mutable client cache in the un-vendored `web_tools.py`. → DEFERRED (§4.2).
- **C4 — rewriter has no `plugins` rule.** Vendor providers under a `tools.*` path; drop
  plugin scaffolding (`__init__.py`/`plugin.yaml`). → §4.2.

**HIGH:**
- **H1 — `run_in_executor` is not a verbatim lift** (ContextVars + anyio). Re-implement over
  `asyncio.to_thread`. → §3.3 fixed.
- **H2 — vendor path + dropped plugin scaffolding must be stated** (un-vendored `PluginContext`
  edge otherwise). → §4.2.
- **H3 — provenance hygiene:** record the GHSA-2jrp/CVE lineage in `_ssrf.py`'s provenance
  header + `METADATA.yaml` (not only in the test), AND explicitly **verify whether `_ssrf.py`
  adapts its IP/cloud-metadata tables from a third project** (secondary-origin) before
  asserting MIT-only. Gate added to §3.1/§6.

**MEDIUM — confirmed / minor:**
- Engine LOC: reusable core is ~95–100 of the 187-LOC file (not "~115").
- Registry resolves standalone, BUT with the `{}` config stub, selection is
  **availability-walk-only** until a real config path is wired (relevant to M2).
- Keep vendoring the content engine (hand-rolling it grows first-party surface against the
  repo's hand-roll-aversion); but mark `_ssrf.py` as the load-bearing, re-sync-critical piece
  and the engine as faithful-but-replaceable.
- Streaming threshold: pin as a named constant (one value), assert it fires before
  decode+markdownify.

**MEDIUM — TWO SIMPLIFICATIONS PENDING USER DECISION (revise earlier grill calls):**
- **M1 — glue-as-reference whole files may be cruft.** Since calfkit *runs* pydantic-ai, the
  4-symbol glue (`web_fetch_tool`/`Tool`/`ModelRetry`/`BinaryContent`) is re-derivable from
  calfkit's live runtime at port time; a frozen `reference/upstream/web_fetch.py` snapshot
  rots (not imported, not tested). **Proposed:** replace the reference files with a ≤10-line
  "Port note" recording the 4 symbols + the `bytes+media_type → BinaryContent` re-wrap shape;
  the METADATA SHA + upstream path is the re-sync pointer. *(Revises the earlier "keep glue as
  reference files" call — the doc note serves the same stated goal more durably.)*
- **M2 — vendoring the config-selection layer (ADR-0002) may be throwaway.** Once the node
  registers providers explicitly, selection is `os.environ[...]` → the registry's in-memory
  `Dict` lookup; `_resolve`/`get_active_*`/`_read_config_key` encode hermes-CLI semantics
  calfkit never runs (and are inert under the `{}` stub). **Proposed:** keep the in-memory
  registry (real value), drop the resolver, select by env in the node (~10 LOC). *(Revises
  ADR-0002; still "per-vendor config now, unify later" — just first-party glue, not vendored
  hermes-CLI config.)*

**LOW:** component naming dir↔pkg mismatch (→ `pydantic-web`); `NODE.md` should state returned
bodies are **untrusted by contract** (calfcord owns wrapping — boundary affirmed); record both
the `v1.106.0` tag AND its resolved SHA in METADATA.

**Convergence:** no contradictory findings across the 3 agents; the Tier-0 cut (registry +
url_safety + ddgs/brave/searxng/tavily) is confirmed sound, zero-new-shim, and shippable. A
round-2 review (optional) should confirm the amended §3.4 + §4.2 + the M1/M2 resolutions.
