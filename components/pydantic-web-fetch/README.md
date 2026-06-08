# calfkit-pydantic-web-fetch

calfkit node component vendoring the **SSRF-safe `web_fetch` tool** from
[pydantic/pydantic-ai](https://github.com/pydantic/pydantic-ai) (MIT).

Exposes (over the calfkit Kafka contract — see `NODE.md`):
- **web_fetch:** fetch a URL in-process behind a best-in-class SSRF guard and return
  cleaned markdown (or neutral binary).

## Provenance & license

- Upstream: `pydantic/pydantic-ai` @ `v1.106.0` (`1b42945`) — see `METADATA.yaml`.
- License: MIT © Pydantic Services Inc. (`LICENSE`, verbatim).
- Motivated by a CVE incomplete-fix chain (GHSA-2jrp-274c-jhv3 / CVE-2026-25580 /
  CVE-2026-46678) — the vendored regression tests are the assurance. See `METADATA.yaml`.

## Structure

- `src/calfkit_pydantic_web_fetch/_vendor/` — the vendored upstream `_ssrf.py` + fetch
  engine (import-rewritten, self-contained: stdlib + `httpx` + `markdownify`).
- `src/calfkit_pydantic_web_fetch/node/` — calfkit Kafka adapter + tool wrapper (your code).
- `tests/` — vendored upstream regression suites (`test_ssrf.py`, `test_web_fetch.py`) +
  adapter tests.
- `patches/` — re-appliable functional changes to vendored code (the §5 streaming restructure).

## Status

**Stage A — scaffold only.** No upstream source is vendored yet. `METADATA.yaml` records
the pinned provenance and the *planned* vendor set + local modifications; the `_vendor/`,
`node/`, and `tests/` trees hold empty package markers. Stage B vendors verbatim, Stage C
adapts (inline helpers, streaming byte-cap, port-rewrite tests), Stage D adds the Kafka
node adapter (gated on the calfkit Kafka contract). See
[`../../docs/design/web-fetch-tool-port.md`](../../docs/design/web-fetch-tool-port.md).
Not yet runnable as a node.
