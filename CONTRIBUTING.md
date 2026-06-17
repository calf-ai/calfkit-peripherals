# Contributing to calfkit-tools

Thanks for your interest in contributing! `calfkit-tools` grows by **vendoring agent tools
from upstream open-source projects** — one source at a time — and exposing each as a calfkit
tool node. This guide covers the dev setup, how to add a source, and the conventions that keep
provenance clean.

For the full architecture and a detailed walkthrough of where new code goes, see
[`docs/project-structure.md`](docs/project-structure.md) — this doc is the concise front door.

## Development setup

This project uses [uv](https://docs.astral.sh/uv/) for dependency management and Python 3.11+.

```bash
uv sync --group dev          # install the project + dev/test dependencies
```

Run the test suite the same way CI does:

```bash
uv run --no-sync pytest tests/ -v
```

With coverage (matches the coverage workflow):

```bash
uv run --no-sync pytest tests/ -v --cov --cov-report=term-missing
```

A couple of test markers are excluded from normal runs (see `pyproject.toml`):

- `live` — tests that spawn real subprocesses/shells (POSIX-only, timing-sensitive; run on
  Linux CI).
- `integration` — tests that need external services or network access.

Skip them locally with `-m "not live and not integration"`.

CI tests against both the floor and ceiling of the supported `calfkit` range (currently
`>=0.9.0,<0.11`), so keep the node glue limited to the stable surface (`ToolContext`,
`agent_tool`, `ToolNodeDef`).

## Adding a tool source

The repo is **one distribution** organized internally by upstream source. To add a source:

1. **Code** → `src/calfkit_tools/<source>/`
   - Tool nodes live under `<source>/node/` (one calfkit `ToolNodeDef` per tool).
   - Import-rewritten upstream code lives under `<source>/_vendor/`; thin compatibility
     shims under `<source>/_shims/`.
2. **Provenance** → `vendor/<source>/` — copy [`vendor/_template/`](vendor/_template) to start.
   Holds `LICENSE`, `METADATA.yaml`, `NODE.md`, and any `patches/`. This tree is provenance
   only: it is never on `sys.path` and never in the wheel.
3. **Tests** → `tests/<source>/`.
4. **Dependencies / extras** → the root [`pyproject.toml`](pyproject.toml).
5. **Attribution** → update [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).

See [`docs/project-structure.md`](docs/project-structure.md) for the full walkthrough.

### Vendoring principles

- **License first.** Check the upstream license before copying anything, and record it under
  `vendor/<source>/LICENSE` with provenance in `METADATA.yaml` (upstream name, homepage, pinned
  revision, and the exact files lifted).
- **Vendor by source, expose by tool.** Each `vendor/<source>` maps to exactly one upstream
  project; one source may expose several tools (e.g. hermes → shell + files + web).
- **Vendor over hand-roll.** If a vendored tree already provides a dependency or
  implementation, keep it in the vendor rather than reimplementing it.
- **Keep upstream code self-contained** under `_vendor/` with imports rewritten to the vendored
  paths, so each source can be re-synced cleanly from upstream.

## Submitting changes

- Open a pull request against `main`.
- **PR titles follow [Conventional Commits](https://www.conventionalcommits.org/)** and must
  **not contain parentheses.** PRs are squash-merged, so the PR title becomes the commit
  subject that [release-please](https://github.com/googleapis/release-please) parses — and
  parentheses break its strict parser, silently skipping a release. This is enforced by
  [`.github/workflows/pr-title.yml`](.github/workflows/pr-title.yml).
- Releases and version bumps are automated by release-please from merged PR titles, so a clear,
  correctly-typed title is what ships your change.

## Questions

Open an [issue](https://github.com/calf-ai/calfkit-peripherals/issues) if anything here is
unclear — improvements to this guide are welcome too.
