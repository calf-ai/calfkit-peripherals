# Design: `web_fetch` tool — vendoring pydantic-ai's SSRF-safe fetcher

**Status:** Draft — design converged (grill + deep-review round 1). Ready to implement.
**Date:** 2026-06-08
**Owners:** Ryan
**Decision:** [ADR-0001](../adr/0001-vendor-pydantic-ai-web-fetch-self-contained.md) — vendor
self-contained; glue as a `NODE.md` port-note; no `pydantic-ai` dependency.
**Component:** NEW `components/pydantic-web-fetch/` (pkg `calfkit-pydantic-web-fetch` /
`calfkit_pydantic_web_fetch`).
**Independence:** fully independent of [`web_search`](web-search-tool-port.md) — different
source, package, namespace, deps, and a *separate* SSRF guard. See the
[overview](web-tools-port.md).

Vendor pydantic-ai's SSRF-safe URL fetcher as a calfkit `web_fetch` node tool, replacing the
smolagents `VisitWebpageTool` shim that has a critical SSRF hole (WEB-1) and no streaming cap.

---

## 1. Scope
`web_fetch` fetches a URL in-process and returns cleaned markdown (or neutral binary), behind
a best-in-class SSRF guard. New component; no dependency on `web_search`.

**Out of scope (agent-layer concerns, not the node):**
- *Untrusted-content wrapping* (prompt-injection defense) — calfcord owns it. `NODE.md` should
  state returned bodies are **untrusted by contract**.
- *Readability-grade extraction* (trafilatura/readability) — markdownify matches current
  behavior; revisit later if quality demands.

## 2. Source & provenance
- **Source:** `pydantic/pydantic-ai`, MIT © Pydantic Services Inc. **Pin: `v1.106.0`**
  (latest stable; `_ssrf.py` HEAD currently sits among unreleased `v2.0.0b*` betas — do not
  chase a beta for a security-critical guard; re-sync to v2.0.0 when it GAs).
- Born from a real CVE incomplete-fix chain (GHSA-2jrp-274c-jhv3 → CVE-2026-25580 →
  CVE-2026-46678) — the regression tests are the assurance.
- **Hygiene gate (H3):** record the GHSA/CVE lineage in `_ssrf.py`'s provenance header **and**
  `METADATA.yaml` (not only in the test); record both the `v1.106.0` tag **and** its resolved
  SHA; explicitly **verify whether `_ssrf.py` adapts its IP/cloud-metadata tables from a third
  project** (secondary origin) before asserting MIT-only.

## 3. Vendor set
**Live code** (`src/calfkit_pydantic_web_fetch/_vendor/`), self-contained — deps = stdlib +
`httpx` + `markdownify`, **no `pydantic-ai`** (ADR-0001):
- `_ssrf.py` (~533 LOC) — `safe_download`: scheme allowlist; full private/reserved/
  cloud-metadata IP tables (incl. Azure's public `168.63.129.16`); IPv6-transition decoding;
  **connect-to-validated-IP** (DNS-rebinding/TOCTOU defense); per-redirect-hop revalidation
  with cross-origin auth/cookie stripping. **The load-bearing, re-sync-critical piece.**
- the fetch/content-type **engine** lifted from `common_tools/web_fetch.py` (the reusable core
  is ~95–100 of the file's 187 LOC): content-type gating, `markdownify(strip=[img,script,
  style])`, `<title>` extraction, JSON fencing, binary handling, the 50k char cap, the
  `Accept: text/markdown` header. Faithful-but-replaceable (vendored to avoid growing
  first-party surface against the repo's hand-roll-aversion).

**Tests** (`tests/`): `test_ssrf.py` — a **port-AND-rewrite, not a verbatim vendor** (the
streaming restructure in §5 changes ~23 `client.get` mock sites + the buffer/close regression
test; monkeypatch string targets must be repointed to the vendored module).

**Glue port-note** (in `NODE.md`, ≤10 lines — ADR-0001/§7-M1): records the four pydantic glue
symbols (`web_fetch_tool()` / `Tool` / `ModelRetry` / `BinaryContent`) and the
`bytes + media_type → BinaryContent` re-wrap shape, so the calfkit port reconnects the glue
against calfkit's *live* pydantic-ai runtime. The `METADATA.yaml` SHA + upstream path is the
re-sync pointer; **no `reference/` files** (a frozen snapshot would rot).

## 4. Self-containment — inline these private helpers
`_ssrf.py` and the engine reach into pydantic-ai private modules whose home files are huge but
whose logic is tiny; **inline** (~30 LOC total), do **not** import the home modules:
- `run_in_executor` (`_utils.py`) → **re-implement** the DNS thread-offload over
  `asyncio.to_thread` (functional equivalent — the real upstream impl uses ContextVars + anyio,
  NOT a verbatim lift). Only `resolve_hostname` needs it. Keep it a module-level patchable name
  (the tests monkeypatch it).
- `create_async_http_client` + `get_user_agent` (`models/__init__.py`, 1900+ LOC — the whole
  agent framework) → ~3-line `httpx.AsyncClient(...)` factory with our own UA. Keep it a
  module-level patchable name.
- `is_text_like_media_type` (`_utils.py`) → ~10-line leaf, copy verbatim.

Dropped → the `NODE.md` port-note: `ModelRetry`, `BinaryContent`, `Tool`, `web_fetch_tool()`.

## 5. Streaming byte cap — guard/engine restructure (NOT a localized patch) ⚠️ §7-C1
`safe_download` buffers the full body (`await client.get(...)` *inside* the
`async with create_async_http_client(...)` block) and returns an `httpx.Response`; the engine
reads `.text`/`.content` **after** that block has closed. So the 50k **char** cap runs only
after the whole body is in memory + parsed → a multi-GB / chunked / no-`Content-Length`
response is a **memory-DoS** on the single-worker loop. No Python upstream bundles SSRF +
streaming.

You **cannot** fix this with a swap-`get`-for-`stream` patch that still returns a `Response`:
once the client context exits, a streamed body is unreadable (`ResponseNotRead`). The byte-cap
must move **inside** the guard — a `safe_stream` variant (or flag) that, on the **final
non-redirect hop**, consumes `iter_bytes()` with a running total, **aborts past a named
`MAX_FETCH_BYTES` constant** (pick one value, e.g. 5 MB per OpenCode), and returns
`(media_type, capped_bytes, status)` rather than a `Response` — **before** decode/markdownify
(assert this in tests). Restructures the guard↔engine boundary + rewrites the test sites above.
Realistic budget: ~40–60 LOC + a real test rewrite, recorded in `patches/`. Done now —
deferring ships a known memory-DoS.

## 6. Binary representation
Live engine returns binary media as a neutral `bytes + media_type` (small dataclass), not
pydantic's `BinaryContent`. The `NODE.md` port-note documents the re-wrap at port time. (Note:
the binary path also reads `response.content`, so it rides the §5 streamed-read path.)

## 7. Node adapter (`node/`, port phase)
Calls `safe_download(url, allow_local=False, max_redirects=10, timeout=…)` (sensible defaults),
maps `ValueError`/`httpx` errors to calfkit's `error:` reply convention, returns the capped
markdown (or neutral binary) over the Kafka contract. Async throughout. Gated on the calfkit
Kafka node contract being defined.

## 8. Implementation plan (staged)
- **Stage A — scaffold.** `cp -r components/_template components/pydantic-web-fetch`; fill
  `METADATA.yaml` (repo, `v1.106.0` tag + resolved SHA, SPDX MIT, file list, CVE lineage,
  secondary-origin result); verbatim upstream `LICENSE`; `pyproject.toml`
  (`calfkit-pydantic-web-fetch`, deps `httpx` + `markdownify`, `requires-python`); append to
  `THIRD_PARTY_NOTICES.md`.
- **Stage B — vendor verbatim + rewrite.** Copy `_ssrf.py`, `common_tools/web_fetch.py`,
  `tests/test_ssrf.py` into `_vendor/` **verbatim** (commit 1); verify secondary origin (H3);
  import-rewrite to the component namespace (commit 2).
- **Stage C — adapt (TDD; `patches/` for vendored edits).** Inline the 3 helpers; drop the 4
  glue symbols → `NODE.md` port-note; binary → `bytes + media_type`; the §5 streaming
  restructure (`safe_stream` / `MAX_FETCH_BYTES` / final-hop / before-decode); port-rewrite
  `test_ssrf.py` (repoint monkeypatch targets; rewrite `get`→`stream` mocks; add the
  byte-cap-abort + before-markdownify assertions).
- **Stage D — Kafka node adapter (`node/`, TDD).** Map `safe_download` + engine onto the
  calfkit Kafka contract; error→`error:`. **Gated on the calfkit Kafka node contract.**
- **Gate:** the port-rewritten `test_ssrf.py` passes; the byte-cap-abort test passes; a node
  smoke test (fetch → markdown; SSRF target → blocked).

## 9. Deep-review findings (round 1) — relevant slice
3 adversarial opus reviewers; verified at `v1.106.0` (all files present; MIT clean). Amended
into the body above: **C1** streaming = guard/engine restructure (§5); **C2** `test_ssrf.py` is
a port-and-rewrite (§3); **H1** `run_in_executor` re-implemented, not lifted (§4); **H3**
provenance/CVE/secondary-origin gate (§2). MEDIUM: engine LOC ~95–100 (not 115); `_ssrf.py` is
load-bearing, engine is faithful-but-replaceable; pin the streaming threshold as a named
constant. **M1 resolved:** glue is a port-note, not `reference/` files.
