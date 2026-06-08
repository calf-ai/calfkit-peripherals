# Vendor pydantic-ai's web_fetch self-contained; keep the framework glue as a NODE.md port-note

**Status:** accepted · 2026-06-08

## Context

`web_fetch` needs a production-grade SSRF-safe fetcher. The strongest one surveyed
is pydantic-ai's `_ssrf.safe_download` + its content-type/markdown engine
(`common_tools/web_fetch.py`) — Python, MIT, born from a real CVE incomplete-fix
chain (GHSA-2jrp-274c-jhv3 → CVE-2026-25580 → CVE-2026-46678). calfkit's agent
runtime *is* pydantic-ai, so the upstream tool's glue (`web_fetch_tool()` / `Tool`
/ `ModelRetry` / `BinaryContent`) is written against public API calfkit already
speaks.

## Decision

Vendor the SSRF guard (`_ssrf.py`) and the fetch/content-type engine as
**self-contained live code**: the three private helpers it reaches for
(`run_in_executor`, `create_async_http_client`, `is_text_like_media_type`) are
**inlined** so the live dependencies are only stdlib + `httpx` + `markdownify`,
with **no `pydantic-ai` dependency**.

Do **not** vendor the agent-tool glue as live code, and do **not** keep verbatim
reference files (a non-imported, non-tested snapshot rots and drifts from both
upstream and calfkit's runtime). Instead, record the glue as a short **port-note**
in `NODE.md`: the four symbols (`web_fetch_tool` / `Tool` / `ModelRetry` /
`BinaryContent`) and the `bytes + media_type → BinaryContent` re-wrap shape, so the
eventual calfkit port reconnects the glue against calfkit's *live* pydantic-ai
runtime. The `METADATA.yaml` SHA + upstream path is the re-sync pointer. Binary
content is returned from live code as a neutral `bytes + media_type`, not pydantic's
`BinaryContent`.

## Considered and rejected

- **Take a `pydantic-ai` dependency and call `safe_download`.** Rejected: this repo
  vendors and ports; it does not take package-manager dependencies. Worse, `_ssrf.py`
  is a private module that drags the entire agent framework on import
  (`from .models import create_async_http_client`), so importing it is both heavy and
  API-unstable.
- **Vendor the glue as live code.** Rejected: it would make `pydantic-ai` a runtime
  dependency of the component for the sake of a thin adapter layer that the calfkit
  node replaces anyway.

## Consequences

The shipped `web_fetch` package has zero `pydantic-ai` dependency and is
re-syncable against a pinned tag (v1.106.0). The pydantic glue is reattached at
port time from the `NODE.md` port-note against calfkit's live pydantic-ai. We also
vendor `test_ssrf.py` **and `test_web_fetch.py`** (the SSRF + engine regression
assurance) — though as **port-and-rewrites** (the streaming-cap restructure changes
their mock shape; see the design doc §5), not verbatim copies. See ADR-0002 for the
parallel "vendor faithfully, integrate later" posture on config.
