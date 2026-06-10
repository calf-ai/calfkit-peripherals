# `web_fetch` node

The calfkit node contract for the vendored pydantic-ai SSRF-safe URL fetcher.
Design: [`../../docs/design/web-fetch-tool-port.md`](../../docs/design/web-fetch-tool-port.md)
(§7 tool contract; §4 glue; §6 binary). ADR-0001.

## Tool contract

Pinned now — independent of the Kafka framing (which is deferred, see below).

- **Request:** `{ url: str }`. Exactly one field. The SSRF guard arguments are **fixed at
  safe defaults**, NOT caller-overridable:
  - `allow_local = False`
  - `max_redirects = 10`
  - `timeout = 30` (seconds)
- **Reply:** exactly one of —
  - **markdown** — `{ url, title, content }` (the engine's `WebFetchResult`,
    returned as a plain dict; content is cleaned markdown, truncated at
    `max_content_length = 50_000` chars with a `[Content truncated]` marker);
  - **binary** — `{ data_base64, media_type }` (neutral binary, base64-encoded
    for the JSON envelope; mind broker `max.message.bytes`);
  - **error** — the node lets `WebFetchError` **propagate**; calfkit converts
    any raised exception into a structured `FailedToolCall` reply (the agent
    sees a clean failure, never a hang).
- **Error / edge semantics:** the guard (`safe_download`) *raises* typed errors
  — SSRF-blocked, `ResponseTooLargeError` (oversize, see §5 byte cap), timeout,
  redirect-limit-exceeded, and `httpx` / `ValueError` — which the engine wraps
  in `WebFetchError`. HTTP error statuses (4xx/5xx) → `WebFetchError` (not the
  body). Charset is preserved per §5 (no forced UTF-8 decode).

## Glue port-note

Records how the vendored engine reconnects against calfkit's **live** pydantic-ai. The
`METADATA.yaml` revision + upstream paths are the re-sync pointer; there is no frozen
`reference/` snapshot.

The four pydantic glue symbols were dropped from the vendor set in Stage C (§3 / §4) and are
re-bound at the calfkit boundary as follows. The vendored engine
(`_vendor/common_tools/web_fetch.py`) now exposes only the reusable core — the callable
`WebFetchLocalTool` and the `WebFetchResult` TypedDict.

- **`web_fetch_tool()`** — upstream tool factory (`common_tools/web_fetch.py`). **Dropped.**
  The calfkit node wrapper (Stage D) constructs `WebFetchLocalTool(...)` directly with the
  §7 fixed safe defaults (`allow_local_urls=False`, `timeout=30`, `max_content_length=50_000`)
  and adapts its return onto the Kafka reply. No `Tool` registration object is built.
- **`Tool`** — upstream tool-registration type (`pydantic_ai.tools`). **Dropped.** The Kafka
  node contract is the registration mechanism in calfkit; the engine no longer wraps itself in
  a `Tool[Any]`. Re-bind: when porting back into a live pydantic-ai agent, wrap
  `WebFetchLocalTool(...).__call__` in `Tool(name='web_fetch', ...)` as upstream did.
- **`ModelRetry`** — upstream retry-signal exception (`pydantic_ai.exceptions`). **Replaced**
  by the neutral first-party `WebFetchError` (`calfkit_pydantic_web_fetch.results`). The engine
  raises `WebFetchError('Failed to fetch <url>: <cause>')` on any guard failure (SSRF-blocked,
  HTTP 4xx/5xx, bad URL, `ResponseTooLargeError`, `httpx.RequestError`). Re-bind: the node maps
  `WebFetchError` → the `error:` reply; a live pydantic-ai agent would instead re-raise it as
  `ModelRetry(str(e))`.
- **`BinaryContent`** — upstream binary content type (`pydantic_ai.messages`). **Replaced** by
  the neutral first-party `@dataclass FetchedBinary(data: bytes, media_type: str)`
  (`calfkit_pydantic_web_fetch.results`, §6 — at the engine/`node` boundary, NOT in `_vendor/`).

**`FetchedBinary → BinaryContent` re-wrap (the shape calfkit consumers expect):**

```python
FetchedBinary(data, media_type)  ->  BinaryContent(data=data, media_type=media_type)
```

The engine returns `FetchedBinary` for any non-text media type; the node serializes it as the
`{ data, media_type }` binary reply (§7), and a live pydantic-ai agent re-wraps it field-for-field
into `BinaryContent` so the model can process it natively.

**§5 streaming byte cap (security fork-point):** body consumption now lives INSIDE the guard
(`_vendor/_ssrf.safe_download`), which streams each hop, enforces `MAX_FETCH_BYTES = 5 MiB` on
the final hop's decompressed bytes (raising `ResponseTooLargeError` before any decode), and
returns a detached, body-loaded `httpx.Response` with charset preserved. The diff vs the
verbatim baseline is captured in [`patches/0001-ssrf-streaming-byte-cap.patch`](patches/0001-ssrf-streaming-byte-cap.patch)
so the fork is re-appliable on re-sync.

## Kafka framing (Stage D — implemented)

The node is `calfkit_pydantic_web_fetch.node.web_fetch`, a calfkit
`ToolNodeDef` built with `@agent_tool` (calfkit ≥ 0.9.0): node id
`tool_web_fetch`, topics `tool.web_fetch.input` / `tool.web_fetch.output`,
JSON envelope, schema derived from the function signature (`url: str`,
required, the only field — matching the §7 contract above). Stateless: no
tenancy key, scales horizontally.

```python
from calfkit import Client, Worker
from calfkit_pydantic_web_fetch.node import web_fetch

await Worker(Client.connect("localhost:9092"), nodes=[web_fetch]).run()
# or: calfkit run calfkit_pydantic_web_fetch.node:web_fetch
```
