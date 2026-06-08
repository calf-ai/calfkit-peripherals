# Web tools vendoring — overview (index)

**Date:** 2026-06-08 · **Owners:** Ryan · **Status:** design converged (grill + deep-review
round 1); per-tool specs ready to implement.

The second batch of agent tools vendored into `calfkit-peripherals`, from the web-tools
research report (`docs/tools-research/reports/web-tools-report.md`). It is **two independent
tools**, each with its own self-contained spec + staged plan:

| Tool | Spec + plan | Source / component | Decision |
|---|---|---|---|
| **`web_fetch`** — URL → cleaned markdown, behind an SSRF guard | [web-fetch-tool-port.md](web-fetch-tool-port.md) | pydantic-ai → **new** `components/pydantic-web-fetch/` | [ADR-0001](../adr/0001-vendor-pydantic-ai-web-fetch-self-contained.md) |
| **`web_search`** (+ `extract`) — query → links; URLs → content | [web-search-tool-port.md](web-search-tool-port.md) | hermes-agent → **extends** `components/hermes-agent/` | [ADR-0002](../adr/0002-per-vendor-config-now-unify-later.md) |

## Independence
The two share **no code**: different upstream sources, packages, namespaces, and Python deps —
and *separate* SSRF guards (pydantic `_ssrf.safe_download` for fetch; hermes
`url_safety.async_is_safe_url` for extract), a deliberate redundancy that keeps the components
isolated. Either can be built, tested, shipped, or abandoned alone, in any order or in parallel.
The only shared dependencies are **external**: both `node/` adapters target calfkit's
(currently undefined) Kafka node contract, and `web_search` depends on the **already-landed
hermes shell+file port** (for vendored helpers) — *not* on `web_fetch`. Only `THIRD_PARTY_NOTICES.md`
is touched by both (each appends its own entry).

## Shared posture & boundaries
- **Vendor, don't hand-roll.** "Buy" = vendor existing upstream source; the repo never takes a
  pip dependency (see [`CONTEXT.md`](../../CONTEXT.md)). Net hand-roll across both tracks: the
  inlined leaf helpers + streaming restructure (`web_fetch`), the `asyncio.to_thread` offload
  (`web_search`), and the two `node/` Kafka adapters.
- **Out-of-scope = agent-layer, not the node** (a recurring boundary for both): untrusted-content
  wrapping, LLM summarization of results, and readability-grade extraction all belong to the
  agent (calfcord), not a stateless tool node. The node returns raw/markdown content; bodies are
  **untrusted by contract**.

## Architecture
Per [`project-structure.md`](../project-structure.md): vendor by source, expose by tool;
polyglot Kafka-node components; `_vendor/` (verbatim + import-rewritten), `_shims/` (runtime
edges), `node/` (the calfkit Kafka adapter — your code), `tests/`. Glossary in
[`CONTEXT.md`](../../CONTEXT.md).

## Status
Both specs are **design-converged** (grill complete; deep-review **rounds 1–2** done — full
findings in each per-tool doc's §9–§10). Round 2 (6 agents, 3 per doc) found two new web_fetch
CRITICALs (missing engine test suite; charset bug in the streaming restructure) plus gaps and
stale post-split cross-refs — all folded in. Open: TDD-scaffold per `project-structure.md`.
Tracked on branch `vendor/web-tools`.
