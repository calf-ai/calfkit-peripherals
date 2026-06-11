# `<source>` provenance (template)

Copy this directory to `vendor/<source>/` to start a new source, then fill in:

- `METADATA.yaml` — provenance (repo, commit SHA, date, license, files, local mods)
- `LICENSE` — the upstream license, **verbatim**
- `NODE.md` — the Kafka contract this source's nodes expose
- `README.md` — a short per-source overview
- `patches/` — re-appliable functional changes to vendored code (keep minimal)

The runnable code does **not** live here — it goes under `src/calfkit_tools/<source>/`
(`{_vendor,_shims,node}/`), with tests under `tests/<source>/`. `calfkit-tools` is a single
distribution, so there is no per-source `pyproject.toml`: add new runtime deps to the root
`pyproject.toml` under `[project.dependencies]`, and gate heavy / optional backends behind a
shared extra under `[project.optional-dependencies]`, e.g.:

```toml
[project.optional-dependencies]
shell-docker = ["docker"]
all = ["calfkit-tools[shell-docker]"]
```

Full instructions: [`../../docs/project-structure.md`](../../docs/project-structure.md).
Follow the `open-source-vendoring-best-practices` skill (license-first) when vendoring.
