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

_None merged to `main` yet._ The first port (hermes-agent, MIT © Nous Research) is in
progress; its entry — including the upstream MIT notice and secondary-origin
attributions (opencode, nearai/ironclaw, free-code) — lands with that component.

<!-- Per-component entry template:
### <source-name>  (<SPDX>)
- Upstream: <repo URL> @ <commit SHA>, retrieved <YYYY-MM-DD>
- License: <SPDX> — © <year> <holder>  (full text: components/<source>/LICENSE)
- Vendored under: components/<source>/src/calfkit_<source>/_vendor/
- Secondary origins (if any): <project> (<SPDX>) — <files>
-->
