# Design: `web_search` tool — vendoring hermes-agent's provider system

> **Note (2026-06-10):** Pre-unification port-design record. Packaging has since been unified into a single `calfkit-tools` distribution — see [`../project-structure.md`](../project-structure.md) for the current layout (`src/calfkit_tools/<source>/`; provenance in `vendor/<source>/`). `components/<source>/` paths and per-component package names (`calfkit-hermes`, `calfkit-pydantic-web-fetch`) below describe the original design and are kept as history.

**Status:** Draft — design converged (grill + deep-review rounds 1–2). Tier-0 ready to implement.
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
hermes-agent's pluggable provider system — an env-selected backend behind one result contract.

---

## 1. Scope
`web_search` = query → ranked links. The provider ABC also carries **extract** (URLs → content
via hosted providers), included in the vendor. New tool(s) on the existing hermes component; no
dependency on `web_fetch`.

**Out of scope (agent-layer concern, not the node):** *LLM summarization of `extract` results*
— hermes' `web_extract_tool` post-processes provider content through an auxiliary LLM; that
layer lives in the un-vendored `web_tools.py`. The node returns **raw provider content**; the
agent summarizes if it wants. No auxiliary-LLM dependency in a web node.

## 2. Source & pin
`NousResearch/hermes-agent` @ `5a36f76a00cc448948856a5c1b52710aafec264e`, MIT © 2025 Nous
Research — the **same pin** as the existing component (no divergence; SHA unchanged).

## 3. Provider model (vendor whole — both verbs)
- `agent/web_search_provider.py` (~185 LOC) — the `WebSearchProvider` ABC with `search(query,
  limit)` and `extract(urls)` + capability flags. Stdlib only.
- `agent/web_search_registry.py` (~245 LOC) — an **in-memory** process-global
  `Dict[name → provider instance]` + `threading.Lock`; `register_provider`/`get_provider` touch
  zero config. The config-driven resolver (`_resolve`/`get_active_*`/`_read_config_key`) is
  vendored **whole** but left **dormant** — the node selects by env (ADR-0002, §7). (Its single
  edge `hermes_cli.config.load_config` is a call-time inline import, already shimmed.)
- `tools/url_safety.py` (~361 LOC) — `async_is_safe_url`, the **extract-side SSRF guard** the
  node runs over URLs before dispatch. stdlib + already-vendored `utils.is_truthy_value` +
  call-time `hermes_cli.config.read_raw_config` (already shimmed → returns `{}`). **Fail-closed
  under the `{}` stub:** `allow_private_urls` stays **False**, so private/metadata targets stay
  **blocked** (the safe default). The only override is the **`HERMES_ALLOW_PRIVATE_URLS`** env
  var — the node must **not** export it to the provider process; the toggle is **cached on first
  call** (security-frozen-at-import — see project-structure.md shim note).
- This guard is **separate from and duplicative with** `web_fetch`'s pydantic SSRF guard —
  deliberate (vendor-by-source isolation). Maintenance cost: an upstream SSRF-bypass fix to one
  guard must be tracked for the other too.

## 4. Providers — vendor by verified coupling
Shared helpers `tools.interrupt`, `tools.lazy_deps`, `utils`, `agent.redact` are **already
vendored** from the shell+file port → **zero new shims** for the Tier-0/Tier-1 set (every
internal edge resolves to vendored/shimmed code; verified round-2).

| Provider | Cap | Vendor now? | Coupling beyond ABC |
|---|---|---|---|
| ddgs | search | ✅ base | — (`ddgs` lazy) |
| brave-free | search | ✅ extra | `httpx` |
| searxng | search | ✅ extra | `httpx` |
| **tavily** | search + **extract** | ✅ (only Tier-0 extract backend) | `tools.interrupt` *(vendored)* |
| exa | search + extract | ❌ **defer (fast-follow)** | call-time `web_tools` cache-slot |
| parallel | search + extract | ❌ **defer (fast-follow)** | same |
| firecrawl | search + extract | ❌ defer | Nous managed-gateway infra |
| xai | search | ❌ defer | `tools.xai_http` + config |

**Vendor mechanics (round-2 corrections).** Providers move from `plugins/web/<name>/provider.py`
to `_vendor/tools/web_providers/<name>.py`. This is a **manual file move recorded in
`local_modifications`**, *not* a rewriter operation (the rewriter rewrites imports, not
locations). The providers' only cross-package import is `from agent.web_search_provider import
…` (+ tavily's `tools.interrupt`), both already covered by existing rewriter rules → **no new
rewriter rule needed**; the `tools.*` path is chosen only so existing rules apply. **Nothing in
the vendored closure imports the providers** (the registry doesn't) — so:
- author an empty `_vendor/tools/web_providers/__init__.py`;
- **drop** each `plugins/web/<name>/__init__.py` + `plugin.yaml` (their `register(ctx)` hook
  needs an un-vendored `PluginContext`);
- the **node maintains its own provider-registration list** (name → class:
  `DDGSWebSearchProvider`, `BraveFreeWebSearchProvider`, `SearXNGWebSearchProvider`,
  `TavilyWebSearchProvider`) and calls `register_provider()` — it replaces the dropped hooks.

**exa/parallel — DEFER (round-1 C3).** Each does an inline `import tools.web_tools as _wt` (inside
`_get_*_client`) and reads/writes module-global client **cache slots** (`_wt._exa_client` …) that
live in the un-vendored `web_tools.py`. They pass import-smoke but raise **`ImportError` at first
search/extract call**. Fix (fast-follow) = **delete the `import tools.web_tools` indirection and
host the cache slot as a global on the provider module itself** (a marked logic edit → `patches/`);
**no rewriter rule helps** (the target module isn't vendored). Both also gate on
`EXA_API_KEY`/`PARALLEL_API_KEY`. firecrawl/xai defer because "take whole" drags infra we'll
never run.

**Extras (Stage A).** Base `web-search` → `ddgs`; opt-in `web-brave`/`web-searxng`/`web-tavily`
→ `httpx`. **`web-tavily` is the sole Tier-0 extract path** (extract is unavailable without it).
Per-provider env keys: `BRAVE_SEARCH_API_KEY`, `SEARXNG_URL`, `TAVILY_API_KEY`/`TAVILY_BASE_URL`.
(Version specifiers bare, matching the existing pyproject convention.)

## 5. What we DON'T vendor — `web_tools.py`
`tools/web_tools.py` (1347 LOC) is the in-process dispatcher + where hermes' `web_search_tool`/
`web_extract_tool` are defined. It bundles things we deliberately drop: the auxiliary-LLM
summarization sub-system, legacy backend resolution (replaced by env-select), Nous
managed-gateway routing, secret-redaction/base64-strip/debug logging, and the exa/parallel
client-cache slots (a test artifact). The node re-implements only the thin orchestration
(register → select → url-safety → call provider → return **raw** content).

## 6. Offload
Every provider's `search()` is **sync** → the node must `asyncio.to_thread` it. `extract()` is
sync for tavily/exa (async only for parallel/firecrawl), and the upstream
`iscoroutinefunction`/`to_thread` dispatch lives in the **dropped** `web_tools.py` — so the node
re-implements that dispatch and must offload **sync extract too** (tavily extract is sync). Small
adapter glue, not vendored code.

## 7. Tool contract + node adapter (`node/`, port phase) — env-based selection (ADR-0002)
- **Selection:** env var **`WEB_SEARCH_BACKEND`** (value = a provider `.name`: `ddgs`,
  `brave-free`, `searxng`, `tavily`); a separate **`WEB_EXTRACT_BACKEND`** (Tier-0: only
  `tavily`) since extract ≠ search. **Default when unset:** `ddgs` for search (no key needed).
  `registry.get_provider(name)` returns `None` for an unknown/unavailable name → the node
  returns an `error:`, never crashes. (Optional later: a per-request `backend` field overriding
  the env default — `get_provider(req.backend or env_default)` — so one node can serve all
  providers without redeploy.)
- **Reply shapes are NOT uniform** (round-2): `search()` returns `{success, data:{web:[{title,
  url,description,position}]}}`; `extract()` returns a **bare `List[Dict]`** (`[{url,title,
  content,raw_content,metadata,error?}]`, per the method docstring — the class docstring's
  `{success,data}` wrap was added by the dropped dispatcher). The node **normalizes both** into
  the Kafka reply (it owns the wrapping the dispatcher used to do); `{success:False, error}` on
  failure.
- **Extract SSRF:** run `async_is_safe_url` over every URL **before** dispatch; reject private/
  metadata targets at the node boundary.
- **Kafka framing** is **gated on the calfkit Kafka node contract** (repo-wide undefined — same
  blocker as the shell/file port). `NODE.md` must be **created** (it does not exist; the shell/
  file Stage D was deferred, and README.md's "see NODE.md" is currently a dangling reference to
  resolve) — co-create the web_search/web_extract contract with (or alongside) the shell/file one.

## 8. Implementation plan (staged)
- **Stage A — scaffold (into the existing component).** Add web_search files to
  `components/hermes-agent/`. **METADATA.yaml upkeep:** add the new files under the **same pin**;
  set `provides: […, web_search, web_extract]`; add to `cut:` (firecrawl/xai/exa/parallel/
  web_tools.py); add `local_modifications:` (the provider relocation `plugins/web/* →
  tools/web_providers/*`, the authored `web_providers/__init__.py`, the dropped scaffolding);
  assert the existing shell/file files/pin/tests are **untouched** (the rewriter is idempotent
  and run only over new files). Extend `pyproject.toml` extras (§4). Fold the web-providers
  attribution into the **existing pending hermes** `THIRD_PARTY_NOTICES.md` entry (not a second).
- **Stage B — vendor + rewrite.** Copy `web_search_provider.py`, `web_search_registry.py`,
  `url_safety.py`, and the ddgs/brave-free/searxng/tavily `provider.py` into `_vendor/` under
  `tools/web_providers/` (verbatim, commit 1); author the package `__init__.py`; import-rewrite
  the existing-rule-covered edges (commit 2); drop `__init__.py`/`plugin.yaml`. Confirm **zero
  new shims**.
- **Stage C — tests (TDD).** Vendored provider/registry/url_safety unit tests (network mocked);
  import smoke (providers import + `register_provider` populates the registry); **registry
  resolution** (`get_provider` hit + `None` miss); dispatch smoke (ddgs search → contract;
  tavily extract → raw list; `async_is_safe_url` blocks a private target).
- **Stage D — Kafka node adapter (`node/`, TDD).** Provider-registration list; **env-select**
  (valid/unset-default/unknown→`error:`); `url_safety` on extract (end-to-end block *before* the
  provider is called); offload **both** sync search and sync extract; reply normalization (§7).
  **Gated on the calfkit Kafka contract.** Add a test asserting the node **never calls the
  dormant resolver** (keeps it provably dormant).
- **Fast-follow (post Tier-0):** exa/parallel (delete `import tools.web_tools`, host cache slots
  on the provider modules; smoke must **invoke** search, not just import); firecrawl/xai later if
  needed.
- **Gate:** vendored unit + import/register + registry-resolution + dispatch + extract-SSRF-block
  tests pass.

## 9. Deep-review findings — round 1
3 opus reviewers; verified at `5a36f76`. Amended into the body: **C3** exa/parallel cache-slot
→ DEFER (§4); **C4/H2** providers under `tools.*`, drop plugin scaffolding (§4). Confirmed:
Tier-0 zero-new-shim; tavily clean; firecrawl/xai defers justified; `search()` sync. **M2
resolved:** registry vendored whole, node selects by env, resolver dormant.

## 10. Deep-review findings — round 2 (3 opus reviewers; gaps · correctness · hygiene)
Verified at `5a36f76`; **Tier-0 plan technically sound** (rewriter resolves cleanly + is
idempotent; registry standalone; url_safety fail-closed under `{}`; tavily clean — all
re-confirmed). **Amended above:** provider relocation is a **manual move, not a rewriter op**, +
authored `__init__.py` + node-owned registration list (§4); exa/parallel ImportError is at
**call time**, fix = **delete the import + host the cache slot** (no rewriter rule) (§4);
**`WEB_SEARCH_BACKEND`/`WEB_EXTRACT_BACKEND`** semantics + default + `None`-error + per-request
option (§7); reply shapes are **non-uniform** (search dict vs extract bare list) (§7); node
offloads **sync extract** too (§6); url_safety uses **`read_raw_config`**, `{}` → fail-closed
blocking, `HERMES_ALLOW_PRIVATE_URLS` not exported (§3); **METADATA upkeep** (`provides`/`cut`/
`local_modifications` + existing-untouched) (§8); extras specifiers + tavily-only-extract (§4);
test plan adds registry-resolution/env-select/offload/extract-SSRF-block (§8); **NODE.md must be
created**, README dangling ref (§7); THIRD_PARTY_NOTICES folds into the pending hermes entry (§8).
**Dormant resolver = dead code (also inert under `{}`):** kept for re-sync fidelity, made
honest in ADR-0002 + a test asserting it's never called (§8). Stale `§7-C3`/`§7-C4` refs
repointed to §9/§10. Two-SSRF-guards maintenance cost acknowledged (§3).
