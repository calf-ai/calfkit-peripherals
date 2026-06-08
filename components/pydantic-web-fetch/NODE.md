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
  - **markdown** — a `str` (cleaned markdown; empty body → empty markdown);
  - **binary** — `{ data, media_type }` (neutral binary; over Kafka: base64 or raw bytes,
    mind broker `max.message.bytes`);
  - **error** — an `error:`-prefixed `str`.
- **Error / edge semantics:** the guard (`safe_stream`/`safe_download`) *raises* typed errors
  — SSRF-blocked, `ResponseTooLargeError` (oversize, see §5 byte cap), timeout,
  redirect-limit-exceeded, and `httpx` / `ValueError` — and the node maps each to the
  `error:` reply. HTTP error statuses (4xx/5xx) → `error:` (not the body). Charset is
  preserved per §5 (no forced UTF-8 decode).

## Glue port-note (TO FILL IN STAGE C)

> Placeholder — completed in Stage C when the engine is trimmed and the glue is dropped.
> Records how the vendored engine reconnects against calfkit's **live** pydantic-ai. The
> `METADATA.yaml` revision + upstream paths are the re-sync pointer; there is no frozen
> `reference/` snapshot.

The four pydantic glue symbols dropped from the vendor set (§3 / §4), to be re-bound here:

- `web_fetch_tool()` — upstream tool factory (`common_tools/web_fetch.py`); the calfkit node
  wrapper replaces it.  *(TODO Stage C: record the re-bind.)*
- `Tool` — upstream tool registration type (`pydantic_ai.tools`).  *(TODO Stage C.)*
- `ModelRetry` — upstream retry-signal exception (`pydantic_ai.exceptions`); calfkit maps the
  raised-error cases to the `error:` reply instead.  *(TODO Stage C.)*
- `BinaryContent` — upstream binary content type (`pydantic_ai.messages`).  *(TODO Stage C.)*

**`FetchedBinary → BinaryContent` re-wrap:** the live engine returns a neutral first-party
`@dataclass FetchedBinary(data: bytes, media_type: str)` (§6, at the engine/`node` boundary,
not in `_vendor/`). Record here the re-wrap shape — `FetchedBinary(data, media_type)` →
`BinaryContent(data=..., media_type=...)` — that calfkit consumers expect.
*(TODO Stage C: pin the exact re-wrap.)*

## Kafka framing

**DEFERRED — gated on the calfkit Kafka node contract**, which is repo-wide undefined (the
same blocker as the hermes shell/file port — see
[`../../docs/design/shell-file-tool-port.md`](../../docs/design/shell-file-tool-port.md)).
Topics, envelope, and binary-over-Kafka encoding land in Stage D once that contract exists.
