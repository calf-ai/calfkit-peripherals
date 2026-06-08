# Design: `web_search` tool — vendoring hermes-agent's provider system

**Status:** Draft — design converged (grill + deep-review round 1). Tier-0 ready to implement.
**Date:** 2026-06-08
**Owners:** Ryan
**Decision:** [ADR-0002](../adr/0002-per-vendor-config-now-unify-later.md) — vendor the registry
whole (no surgery); node selects the provider by env; config resolver stays dormant.
**Component:** EXTENDS the existing `components/hermes-agent/` (vendor-by-source: hermes →
shell + files + web_search; pkg `calfkit-hermes` / `calfkit_hermes`).
**Independence:** fully independent of [`web_fetch`](web-fetch-tool-port.md). Its only
dependency is on the **already-landed hermes shell+file port** (for shared helpers), not on
`web_fetch`. See the [overview](web-tools-port.md).

Replace the hardcoded, event-loop-sleeping `DuckDuckGoSearchTool` singleton (WEB-6) with
hermes-agent's pluggable provider system — a config/env-selected backend behind one result
contract.

---

## 1. Scope
`web_search` = query → ranked links. The provider ABC also carries **extract** (URLs →
content via hosted providers — Tavily/Exa/…), included in the vendor. New tool(s) on the
existing hermes component; no dependency on `web_fetch`.

**Out of scope (agent-layer concern, not the node):**
- *LLM summarization of `extract` results* — hermes' `web_extract_tool` post-processes provider
  content through an auxiliary LLM (OpenRouter/Gemini) to compress it; that whole layer lives
  in the un-vendored `web_tools.py`. The node returns the **raw provider content**
  (`{url,title,content,raw_content,metadata}`); the agent summarizes if it wants. No
  auxiliary-LLM dependency in a web node.

## 2. Source & pin
`NousResearch/hermes-agent` @ `5a36f76a00cc448948856a5c1b52710aafec264e`, MIT © 2025 Nous
Research — the **same pin** as the existing component (no divergence).

## 3. Provider model (vendor whole — both verbs)
- `agent/web_search_provider.py` (~185 LOC) — the `WebSearchProvider` ABC with **both**
  `search(query, limit)` and `extract(urls)` verbs + capability flags. Stdlib only.
- `agent/web_search_registry.py` (~245 LOC) — an **in-memory** process-global
  `Dict[name → provider instance]` + `threading.Lock`; populated by `register_provider()`;
  queried by name with capability/availability filtering + a config-driven resolver. One
  internal edge: `hermes_cli.config.load_config` — **already shimmed**. Vendored **whole** incl.
  the resolver, but the resolver is left **dormant** — the node selects by env (ADR-0002, §7).
  Note: under the `{}` config stub the resolver would fall through to an availability-walk
  anyway, so env-selection is the deterministic path.
- `tools/url_safety.py` (~361 LOC) — `async_is_safe_url`, the **extract-side SSRF guard** the
  node runs over URLs before dispatching to an extract provider. Stdlib + already-vendored
  `utils.is_truthy_value` + already-shimmed `hermes_cli.config`. (Independent of, and duplicative
  with, `web_fetch`'s pydantic SSRF guard — deliberate, to keep the components isolated.)

## 4. Providers — vendor by verified coupling
Shared helpers `tools.interrupt`, `tools.lazy_deps`, `utils`, `agent.redact` are **already
vendored** from the shell+file port → **zero new shims** for the Tier-0/Tier-1 set.

| Provider | Cap | Vendor now? | Coupling beyond ABC |
|---|---|---|---|
| ddgs | search | ✅ base | — (`ddgs` lazy) |
| brave-free | search | ✅ extra | `httpx` |
| searxng | search | ✅ extra | `httpx` |
| **tavily** | search + **extract** | ✅ (clean extract backend) | `tools.interrupt` *(vendored)* |
| exa | search + extract | ❌ **defer (fast-follow)** | cache-slot on un-vendored `web_tools` → `ImportError` as-is; MODERATE |
| parallel | search + extract | ❌ **defer (fast-follow)** | same cache-slot coupling |
| firecrawl | search + extract | ❌ defer | 10 helpers, mostly **Nous managed-gateway** infra |
| xai | search | ❌ defer | `tools.xai_http` (own tail) + config |

**exa/parallel — DEFER (§7-C3).** Not "lift a factory fn": each does `import tools.web_tools as
_wt` and reads/writes module-global client **cache slots** (`_wt._exa_client` …) that live in
the un-vendored 1347-LOC `web_tools.py` (they live there *only* as a hermes-test reset point) →
the rewriter maps the import to a non-existent module → runtime `ImportError`. Fix = relocate
the cache slot onto each provider module (a marked local edit) + a rewriter rule = MODERATE →
fast-follow. firecrawl/xai defer because "take whole" drags infra we'll never run and "lift
clean" is precision-cutting.

**Vendor path + scaffolding (§7-C4/H2):** vendor only each provider's `provider.py` under a
`tools.*`-covered path (e.g. `_vendor/tools/web_providers/<name>.py`); **drop** the
`plugins/web/<name>/__init__.py` + `plugin.yaml` (their `register(ctx)` hook needs an
un-vendored `PluginContext`; the node calls `register_provider()` directly). The import-rewriter
(`components/hermes-agent/scripts/rewrite_imports.py`) currently has **no `plugins` rule** — use
a `tools.*`-covered path so existing rules apply, or add the rule.

## 5. What we DON'T vendor — `web_tools.py`
`tools/web_tools.py` (1347 LOC) is the in-process dispatcher + the place hermes' `web_search_tool`
/`web_extract_tool` are defined. It bundles things we deliberately drop: an **auxiliary-LLM
summarization** sub-system for extract, legacy backend resolution (replaced by env-select),
Nous managed-gateway routing, secret-redaction/base64-strip/debug logging, and the exa/parallel
client-cache slots (a test artifact). The node adapter re-implements only the thin orchestration
(register → select → url-safety → call provider → return **raw** content).

## 6. Offload
The provider `search()` path is **synchronous/inline** upstream; the node consume-loop must
offload it via `asyncio.to_thread`. `extract` already has the `iscoroutinefunction`/`to_thread`
dispatch upstream. Small adapter glue, not vendored code.

## 7. Node adapter (`node/`, port phase) — env-based selection (ADR-0002)
Registers the shipped providers explicitly at startup; selects the active provider from an env
var (e.g. `WEB_SEARCH_BACKEND`) → `registry.get_provider(name)` directly (the vendored
`config.yaml` resolver stays dormant); for `extract`, runs `async_is_safe_url` over the URLs
first; offloads sync `search` via `asyncio.to_thread`; returns the provider's already-
`{success, data}`-shaped **raw** result over the Kafka contract. Update `NODE.md` with the
`web_search` (and `web_extract`) contract. Gated on the calfkit Kafka node contract.

## 8. Implementation plan (staged)
- **Stage A — scaffold (into the existing component).** Add the web_search files to
  `components/hermes-agent/`; update `METADATA.yaml` (add the new vendored files under the same
  pin); extend `pyproject.toml` (base extra `web-search` → `ddgs`; opt-in extras
  `web-brave`/`web-searxng`/`web-tavily` → `httpx`); update the hermes entry in
  `THIRD_PARTY_NOTICES.md` (or add a web-providers sub-entry).
- **Stage B — vendor + rewrite.** Copy `web_search_provider.py`, `web_search_registry.py`,
  `url_safety.py`, and the ddgs/brave-free/searxng/tavily `provider.py` into `_vendor/` under a
  `tools.*`-covered path (verbatim, commit 1); confirm/add the rewriter rule (C4); import-rewrite
  (commit 2); **drop** the `__init__.py`/`plugin.yaml` scaffolding. Confirm **zero new shims**
  (config + url_safety edges already shimmed/vendored).
- **Stage C — tests (TDD).** Vendored provider/registry/url_safety unit tests (network mocked);
  import smoke (providers import + `register_provider` populates the registry); search + extract
  dispatch smoke (ddgs search returns the contract; tavily extract returns raw content;
  `async_is_safe_url` blocks a private target).
- **Stage D — Kafka node adapter (`node/`, TDD).** Register providers; env-select; `url_safety`
  on extract; offload sync `search`; return raw `{success, data}`. **Gated on the calfkit Kafka
  node contract.**
- **Fast-follow (post Tier-0):** exa/parallel (relocate the cache slots onto the provider
  modules + rewriter rule); firecrawl/xai later only if needed (+ Nous gateway / `xai_http`).
- **Gate:** vendored unit tests + the import/register + dispatch smoke pass.

## 9. Deep-review findings (round 1) — relevant slice
3 adversarial opus reviewers; verified at pin `5a36f76` (files present; MIT clean; same pin as
the existing component). Amended into the body above: **C3** exa/parallel cache-slot coupling →
DEFER (§4); **C4/H2** vendor providers under `tools.*`, drop plugin scaffolding, rewriter rule
(§4). Confirmed: Tier-0 closures are zero-new-shim; tavily is the clean extract backend;
firecrawl/xai defers justified; `search()` is sync (offload needed). MEDIUM: under the `{}`
config stub, selection is availability-walk-only → env-select is the deterministic path. **M2
resolved:** registry vendored whole (no surgery), node selects by env, resolver dormant.
