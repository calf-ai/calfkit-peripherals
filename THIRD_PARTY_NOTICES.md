# Third-Party Notices

`calfkit-tools` vendors source from external open-source projects. Each vendored source's
provenance lives under `vendor/<source>/` and carries its own **authoritative** `LICENSE`
(verbatim upstream) and `METADATA.yaml` (provenance: repo, commit, license, files, local
modifications); the vendored code itself lives under `src/calfkit_tools/<source>/_vendor/`.
This file is the aggregated index across sources.

## Conventions

See `docs/project-structure.md` and the `open-source-vendoring-best-practices` skill.

- Vendor **by source**, with the license **checked and classified before** copying.
- **Permissive licenses only** (MIT / BSD / ISC / Apache-2.0 / Unlicense / 0BSD / CC0).
  Weak/strong copyleft, "no license", and source-available/restricted licenses are a
  **stop-and-flag** — do not copy without an explicit decision.
- Preserve all upstream copyright/license headers. Record **secondary-origin**
  attributions when an upstream itself ported code from a third project.
- Note local modifications (the mechanical import-rewrite and any `patches/`).

## Sources

### hermes  (MIT)
- Upstream: https://github.com/NousResearch/hermes-agent @ `5a36f76a00cc448948856a5c1b52710aafec264e`, retrieved 2026-06-07
- License: MIT — © 2025 Nous Research  (full text: `vendor/hermes/LICENSE`)
- Vendored under: `src/calfkit_tools/hermes/_vendor/`  (provenance: `vendor/hermes/`)
- Tools: shell (terminal, process, execute_code), files (read/write/patch/search), web_search + web_extract (provider system: ddgs / brave-free / searxng / tavily), todo (per-session task list)
- Secondary origins: opencode (MIT), nearai/ironclaw (elect-MIT), free-code (MIT) — see `vendor/hermes/METADATA.yaml`

### web_fetch  (MIT)
- Upstream: https://github.com/pydantic/pydantic-ai @ `1b42945de65b2816fed3cffa371671a2ac759241` (tag `v1.106.0`), retrieved 2026-06-08
- License: MIT — © Pydantic Services Inc.  (full text: `vendor/web_fetch/LICENSE`)
- Vendored under: `src/calfkit_tools/web_fetch/_vendor/`  (provenance: `vendor/web_fetch/`)
- Tools: web_fetch (SSRF-safe URL → markdown / neutral binary)
- Secondary origins: none found (H3 verified — `_ssrf.py` IP/cloud-metadata tables are independently authored from primary RFCs; see `vendor/web_fetch/METADATA.yaml`)

<!-- Per-source entry template:
### <source-name>  (<SPDX>)
- Upstream: <repo URL> @ <commit SHA>, retrieved <YYYY-MM-DD>
- License: <SPDX> — © <year> <holder>  (full text: vendor/<source>/LICENSE)
- Vendored under: src/calfkit_tools/<source>/_vendor/  (provenance: vendor/<source>/)
- Secondary origins (if any): <project> (<SPDX>) — <files>
-->
