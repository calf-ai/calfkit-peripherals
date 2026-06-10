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

**Stage D — complete.** Stages B/C vendored + adapted the engine (inline helpers,
streaming byte-cap, port-rewrite tests; 225 regression tests). Stage D added the
calfkit tool node: `calfkit_pydantic_web_fetch.node.web_fetch` is a deployable
`ToolNodeDef` (calfkit ≥ 0.9.0) — see `NODE.md` for the contract and
[`../../docs/design/node-port.md`](../../docs/design/node-port.md) for the
node-layer design.
