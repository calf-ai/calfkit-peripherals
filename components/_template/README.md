# `<source>` component (template)

Copy this directory to `components/<source>/` to start a new port, then fill in:

- `METADATA.yaml` — provenance (repo, commit SHA, date, license, files, local mods)
- `LICENSE` — the upstream license, **verbatim**
- `pyproject.toml` — package name `calfkit-<source>` + optional extras
- `src/calfkit_<source>/{_vendor,_shims,node}/` and `tests/`
- `NODE.md` — the Kafka contract this node exposes

Full instructions: [`../../docs/project-structure.md`](../../docs/project-structure.md).
Follow the `open-source-vendoring-best-practices` skill (license-first) when vendoring.
