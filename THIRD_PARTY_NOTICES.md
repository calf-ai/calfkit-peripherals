# Third-Party Notices

`calfkit-peripherals` vendors source from external open-source projects. Each vendored
component lives under `components/<source>/` and carries its own **authoritative**
`LICENSE` (verbatim upstream) and `METADATA.yaml` (provenance: repo, commit, license,
files, local modifications). This file is the aggregated index across components.

## Conventions

See `docs/project-structure.md` and the `open-source-vendoring-best-practices` skill.

- Vendor **by source**, with the license **checked and classified before** copying.
- **Permissive licenses only** (MIT / BSD / ISC / Apache-2.0 / Unlicense / 0BSD / CC0).
  Weak/strong copyleft, "no license", and source-available/restricted licenses are a
  **stop-and-flag** — do not copy without an explicit decision.
- Preserve all upstream copyright/license headers. Record **secondary-origin**
  attributions when an upstream itself ported code from a third project.
- Note local modifications (the mechanical import-rewrite and any `patches/`).

## Components

### hermes-agent  (MIT)
- Upstream: https://github.com/NousResearch/hermes-agent @ `5a36f76a00cc448948856a5c1b52710aafec264e`, retrieved 2026-06-07
- License: MIT — © 2025 Nous Research  (full text: `components/hermes-agent/LICENSE`)
- Vendored under: `components/hermes-agent/src/calfkit_hermes/_vendor/`
- Tools: shell (terminal, process, execute_code), files (read/write/patch/search), web_search + web_extract (provider system: ddgs / brave-free / searxng / tavily), todo (per-session task list)
- Secondary origins: opencode (MIT), nearai/ironclaw (elect-MIT), free-code (MIT) — see `components/hermes-agent/METADATA.yaml`

### pydantic-web-fetch  (MIT)
- Upstream: https://github.com/pydantic/pydantic-ai @ `1b42945de65b2816fed3cffa371671a2ac759241` (tag `v1.106.0`), retrieved 2026-06-08
- License: MIT — © Pydantic Services Inc.  (full text: `components/pydantic-web-fetch/LICENSE`)
- Vendored under: `components/pydantic-web-fetch/src/calfkit_pydantic_web_fetch/_vendor/`
- Tools: web_fetch (SSRF-safe URL → markdown / neutral binary)
- Secondary origins: none found (H3 verified — `_ssrf.py` IP/cloud-metadata tables are independently authored from primary RFCs; see `components/pydantic-web-fetch/METADATA.yaml`)

<!-- Per-component entry template:
### <source-name>  (<SPDX>)
- Upstream: <repo URL> @ <commit SHA>, retrieved <YYYY-MM-DD>
- License: <SPDX> — © <year> <holder>  (full text: components/<source>/LICENSE)
- Vendored under: components/<source>/src/calfkit_<source>/_vendor/
- Secondary origins (if any): <project> (<SPDX>) — <files>
-->
