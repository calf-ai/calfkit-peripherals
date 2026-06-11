# Design: `web_fetch` tool — vendoring pydantic-ai's SSRF-safe fetcher

> **Note (2026-06-10):** Pre-unification port-design record. Packaging has since been unified into a single `calfkit-tools` distribution — see [`../project-structure.md`](../project-structure.md) for the current layout (`src/calfkit_tools/<source>/`; provenance in `vendor/<source>/`). `components/<source>/` paths and per-component package names (`calfkit-hermes`, `calfkit-pydantic-web-fetch`) below describe the original design and are kept as history.

**Status:** Draft — design converged (grill + deep-review rounds 1–2). Ready to implement.
**Date:** 2026-06-08
**Owners:** Ryan
**Decision:** [ADR-0001](../adr/0001-vendor-pydantic-ai-web-fetch-self-contained.md) — vendor
self-contained; glue as a `NODE.md` port-note; no `pydantic-ai` dependency.
**Component:** NEW `components/pydantic-web-fetch/` (pkg `calfkit-pydantic-web-fetch` /
`calfkit_pydantic_web_fetch`). The dir is tool-named, not source-named (source is
`pydantic-ai`); record the dir↔source mapping in `METADATA.yaml`.
**Independence:** fully independent of [`web_search`](web-search-tool-port.md) — different
source, package, namespace, deps, and a *separate* SSRF guard. See the
[overview](web-tools-port.md).

Vendor pydantic-ai's SSRF-safe URL fetcher as a calfkit `web_fetch` node tool, replacing the
smolagents `VisitWebpageTool` shim that has a critical SSRF hole (WEB-1) and no streaming cap.

---

## 1. Scope
`web_fetch` fetches a URL in-process and returns cleaned markdown (or neutral binary), behind
a best-in-class SSRF guard. New component; no dependency on `web_search`.

**Out of scope (agent-layer concerns, not the node):** untrusted-content wrapping (calfcord
owns it — bodies are **untrusted by contract**); readability-grade extraction (markdownify
parity now).

## 2. Source & provenance
- **Source:** `pydantic/pydantic-ai`, MIT © Pydantic Services Inc. **Pin `v1.106.0`** (latest
  stable; HEAD sits among unreleased `v2.0.0b*` betas — don't chase a beta for a security
  guard). **Record the resolved 40-char SHA in `METADATA.yaml revision:`** (the immutable
  provenance); keep `v1.106.0` as a `tag:`/comment (the human pointer).
- Born from a CVE incomplete-fix chain (GHSA-2jrp-274c-jhv3 → CVE-2026-25580 → CVE-2026-46678)
  — the regression tests are the assurance.
- **Hygiene gate (round-1 H3) — procedure:** before asserting MIT-only, verify secondary
  origin of `_ssrf.py`'s IP/cloud-metadata tables: inspect the file header + `git log`/blame
  for "Ported from"/"Adapted from"; spot-diff the metadata-IP table against likely upstreams
  (OWASP SSRF cheat sheet, `ipaddress` stdlib). Record the outcome (an origin, or "none
  found") in `METADATA.yaml secondary_origins:`.

## 3. Vendor set
**Live code** (`src/calfkit_pydantic_web_fetch/_vendor/`), self-contained — deps = stdlib +
`httpx` + `markdownify` (+ `typing_extensions`, see below), **no `pydantic-ai`** (ADR-0001):
- `_ssrf.py` (~533 LOC) — `safe_download`: scheme allowlist; full private/reserved/
  cloud-metadata IP tables (incl. Azure's public `168.63.129.16`); IPv6-transition decoding;
  **connect-to-validated-IP** (DNS-rebinding/TOCTOU defense); per-redirect-hop revalidation
  with cross-origin auth/cookie stripping. **The load-bearing, re-sync-critical piece.** After
  inlining (§4) it imports stdlib + `httpx` only → **no import-rewrite needed**.
- the fetch/content-type **engine** lifted from `common_tools/web_fetch.py` (reusable core
  ~95–100 of the file's 187 LOC; figures are estimates): content-type gating,
  `markdownify(strip=[img,script,style])`, `<title>` extraction, JSON fencing, binary handling,
  the 50k char cap, `Accept: text/markdown`. Faithful-but-replaceable. It uses
  `from typing_extensions import Any, TypedDict` — either add `typing_extensions` as a dep or
  rewrite to stdlib `typing` (fine on `requires-python >= 3.11`) and log it as a port edit.

**Tests** (under `tests/`, **not** `_vendor/` — vendored tests are not packaged/namespaced):
- `test_ssrf.py` (951 LOC) — the SSRF regression suite.
- **`test_web_fetch.py` (~501 LOC) — the ENGINE regression suite (round-2 CRITICAL: round-1
  missed it).** Tests exactly the content-type/markdownify/JSON/char-cap/binary logic that §5/§6
  restructure. **Must be vendored + port-rewritten** alongside the engine changes — every test
  mocks `safe_download → httpx.Response` and asserts `.text`/`.content`/`BinaryContent`, so all
  break under the new return type + neutral-binary swap.

Both test files are **port-and-rewrites** (the §5 restructure changes their mock shape — see
§5), recorded in `METADATA.yaml local_modifications`.

**Glue port-note** (in `NODE.md`, ≤10 lines — ADR-0001): records the four pydantic glue symbols
(`web_fetch_tool()` / `Tool` / `ModelRetry` / `BinaryContent`) and the
`bytes + media_type → BinaryContent` re-wrap shape, so the calfkit port reconnects against
calfkit's *live* pydantic-ai. The `METADATA.yaml` SHA + upstream path is the re-sync pointer;
**no frozen `reference/` snapshot files** (they rot; `reference/` is not a repo convention).

### 3.1 Import-rewrite (small — inlining eliminates most of it)
Unlike the hermes port (8 rules + a script), inlining (§4) leaves almost nothing to rewrite:
- `_ssrf.py`: none (stdlib + httpx after inlining).
- engine: `from pydantic_ai._ssrf import safe_download` →
  `from calfkit_pydantic_web_fetch._vendor._ssrf import safe_download` (a sibling). Drop the
  glue imports; inline `is_text_like_media_type`.
- tests: `pydantic_ai._ssrf` / `pydantic_ai.common_tools.web_fetch` → the vendored module paths;
  **keep `run_in_executor` / `create_async_http_client` as module-level patchable names** on the
  vendored `_ssrf` module (the tests monkeypatch them by string).
Done **by hand** (≤4 files); no `scripts/rewrite_imports.py` needed. State the literal mapping in
`METADATA.yaml`.

## 4. Self-containment — inline these private helpers
`_ssrf.py`/engine reach into pydantic-ai private modules whose logic is tiny; **inline** (~30
LOC), don't import the home modules:
- `run_in_executor` (`_utils.py`) → **re-implement** the DNS thread-offload over
  `asyncio.to_thread` (upstream uses ContextVars + anyio — NOT a verbatim lift). Only
  `resolve_hostname` needs it. Keep a module-level patchable name.
- `create_async_http_client` + `get_user_agent` (`models/__init__.py`) → ~3-line
  `httpx.AsyncClient(timeout=httpx.Timeout(timeout=…, connect=5), headers={"User-Agent": …})`.
  **Preserve the split `connect=5` timeout** (verified the only load-bearing config; `safe_download`
  sets its own per-hop `follow_redirects=False`). Keep a module-level patchable name.
- `is_text_like_media_type` (`_utils.py`) → ~10-line pure-string leaf, copy verbatim.

Dropped → the `NODE.md` port-note: `ModelRetry`, `BinaryContent`, `Tool`, `web_fetch_tool()`.

## 5. Streaming byte cap — guard/engine restructure ⚠️ (round-1 C1 + round-2 corrections)
`safe_download` buffers the full body (`await client.get(...)` *inside* the
`async with create_async_http_client(...)` block) and returns an `httpx.Response`; the engine
reads `.text`/`.content` **after** that block closes. So the 50k **char** cap runs only after
the whole body is in memory → a multi-GB / chunked / no-`Content-Length` response is a
**memory-DoS** on the single-worker loop. No Python upstream bundles SSRF + streaming.

The fix moves consumption **inside** the guard, and (round-2) is more involved than "swap get
for stream on the final hop":
- **There is no single final request.** `client.get(..., follow_redirects=False)` runs **per
  hop** in one client context; you only know a hop is final *after* reading `is_redirect`. So
  **every** hop becomes `async with client.stream("GET", ...) as resp:` — read headers/
  `is_redirect` first; on a **redirect**, do NOT read the body (let the context close it) and
  `continue` (re-validate the next hop as today); only on the **non-redirect** hop consume
  `resp.iter_bytes()` with a running total.
- **Cap at a pinned constant:** `MAX_FETCH_BYTES = 5 * 1024 * 1024` (5 MiB). Abort (raise a typed
  `ResponseTooLargeError`) once the running total exceeds it, **before** decode/markdownify.
  `iter_bytes()` yields *decompressed* bytes, so this bounds memory correctly; do **not** trust
  `Content-Length` (compressed/absent). The existing 50k **char** cap still runs downstream on
  the decoded text (looser, token-budget concern — distinct from the byte cap).
- **Preserve charset (round-2 HIGH):** upstream `response.text` resolves charset from the
  Content-Type header, not blind UTF-8. Returning stripped `media_type` + raw bytes would force
  `bytes.decode("utf-8")` → wrong text / `UnicodeDecodeError` on non-UTF-8 pages. So the guard
  must return the **charset/encoding** too (e.g. `(media_type, charset, capped_bytes, status)`)
  or a tiny object that reproduces httpx's `.text` resolution; the engine decodes with it.
- **Tests:** every redirect-hop revalidation test must run against the **streaming** path, not
  only the old buffered one. The mocks change shape, not just targets — `mock_client.get`
  (~24 sites) returning an `AsyncMock` becomes `client.stream` returning an **async context
  manager** whose `iter_bytes()` is an async iterator; `test_safe_download_closes_http_client`
  (asserts `.content` + `is_closed`) must assert the stream is **drained inside** the client
  context (else `ResponseNotRead`). Budget: a real restructure, not a repoint.

This is a deep change to security-critical vendored logic. If it can't be expressed as a clean
re-appliable `patches/` diff, treat it as a documented **fork-point in
`METADATA.yaml local_modifications`** (with rationale) rather than a brittle patch — either way,
marked as modified vendored security code.

## 6. Binary representation
Live engine returns binary media as a neutral first-party `@dataclass FetchedBinary(data:
bytes, media_type: str)` — a plain stdlib dataclass (no pydantic import), living at the
**engine/`node` boundary, not in `_vendor/`**. The binary path also reads the body, so it rides
the §5 streamed-read path. The `NODE.md` port-note records the `FetchedBinary → BinaryContent`
re-wrap that calfkit consumers expect.

## 7. Tool contract + node adapter (`node/`, port phase)
The **tool contract** can be pinned now (independent of the Kafka framing, which is deferred):
- **Request:** `{ url: str }`. The SSRF args (`allow_local=False`, `max_redirects=10`,
  `timeout=30`) are **fixed at safe defaults**, not caller-overridable.
- **Reply:** one of — markdown `str`; binary `{ data, media_type }` (binary over Kafka:
  base64 or raw bytes, mind broker `max.message.bytes`); or an `error:`-prefixed `str`.
- **Error/edge semantics:** `safe_stream` *raises* typed errors (SSRF-blocked,
  `ResponseTooLargeError`, timeout, redirect-limit, `httpx`/`ValueError`) → the node maps each
  to the `error:` reply. HTTP error statuses (4xx/5xx) → `error:` (not body). Empty body → empty
  markdown. Charset preserved per §5 (no forced UTF-8).

The **Kafka framing** (topics, envelope) is **gated on the calfkit Kafka node contract**, which
is repo-wide undefined (same blocker as the hermes shell/file port — see
[shell-file-tool-port.md](shell-file-tool-port.md)). `NODE.md` has two parts: the glue port-note
+ tool contract (writable now) and the Kafka framing (deferred with Stage D).

## 8. Implementation plan (staged)
- **Stage A — scaffold.** `cp -r components/_template components/pydantic-web-fetch` — note the
  template is **minimal** (METADATA/pyproject/README only); author the
  `src/calfkit_pydantic_web_fetch/{_vendor,node}/`, `tests/`, `patches/`, `LICENSE`, `NODE.md`
  tree by hand. Fill `METADATA.yaml` (repo, resolved SHA in `revision:` + `v1.106.0` tag, SPDX
  MIT, file list, CVE lineage, `secondary_origins` from §2, the §3.1 rewrite mapping); verbatim
  upstream `LICENSE`; `pyproject.toml` (`calfkit-pydantic-web-fetch`; deps `httpx>=0.27`,
  `markdownify>=1.2`, `typing_extensions` if not rewritten; `requires-python = ">=3.11"`);
  append to `THIRD_PARTY_NOTICES.md`.
- **Stage B — vendor verbatim + rewrite.** Copy `_ssrf.py`, the **whole**
  `common_tools/web_fetch.py`, `tests/test_ssrf.py`, `tests/test_web_fetch.py` **verbatim**
  (commit 1; you copy the whole engine file, then trim/inline in Stage C — you can't copy a
  95-LOC *slice* verbatim); verify secondary origin (§2); apply the §3.1 by-hand rewrite (commit
  2).
- **Stage C — adapt (TDD; `patches/`).** Inline the 3 helpers; drop the 4 glue symbols → the
  `NODE.md` port-note (an explicit deliverable); add `FetchedBinary` (§6); the §5 streaming
  restructure (`safe_stream`/`MAX_FETCH_BYTES=5 MiB`/per-hop-stream/charset/before-decode);
  trim the engine to its reusable core; **port-rewrite both `test_ssrf.py` and
  `test_web_fetch.py`** (reshape mocks to streaming async-CMs; repoint monkeypatch targets;
  retain per-redirect-hop revalidation coverage on the streaming path; add the
  byte-cap-abort + before-markdownify + non-UTF-8-charset tests).
- **Stage D — Kafka node adapter (`node/`, TDD).** Map the tool contract (§7) onto the calfkit
  Kafka contract; **gated on that contract.** Node tests: error→`error:` per exception class;
  binary re-wrap; timeout; max-redirects-exceeded; SSRF-target→blocked at the node boundary;
  oversize→`ResponseTooLargeError`→`error:`.
- **Gate:** ported `test_ssrf.py` + `test_web_fetch.py` pass; byte-cap-abort + charset tests
  pass; redirect-revalidation covered on the streaming path; node smoke (fetch→markdown;
  SSRF→blocked).

## 9. Deep-review findings — round 1
3 opus reviewers; verified at `v1.106.0`. Amended into the body: **C1** streaming = guard/engine
restructure (§5); **C2** `test_ssrf.py` port-and-rewrite (§3/§5); **H1** `run_in_executor`
re-implemented (§4); **H3** provenance/CVE/secondary-origin gate (§2). MEDIUM: engine LOC
~95–100; `_ssrf.py` load-bearing vs engine replaceable; pin the streaming threshold. **M1
resolved:** glue is a port-note, not `reference/` files.

## 10. Deep-review findings — round 2 (3 opus reviewers; gaps · correctness · hygiene)
**NEW CRITICAL/HIGH, amended above:** missing **`test_web_fetch.py`** engine suite added (§3);
**charset/encoding** preserved in the streaming return (§5); the **per-hop streaming** redirect
loop corrected (§5); `MAX_FETCH_BYTES` **pinned** to 5 MiB (§5); **`typing_extensions`** dep
handled (§3); the **tool request/reply contract** specified now (§7); `test_ssrf.py` placement
fixed to `tests/` (§3/§8); **import-rewrite mapping** added (§3.1); the §5 restructure routed to
`patches/` *or* a `local_modifications` fork-point (§5); node-adapter + streaming-path SSRF
tests enumerated (§8). MEDIUM/LOW: `requires-python` + dep floors (§3/§8); verbatim-whole-then-trim
(§8); `FetchedBinary` named/homed (§6); NODE.md as an explicit two-part deliverable (§7/§8);
`_template` is minimal (§8); H3 procedure (§2). **ADR-0001 title** corrected (drop "reference
only"). Stale `§7-Cx` cross-refs repointed to §9/§10.
