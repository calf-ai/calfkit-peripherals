# Project structure & how to add a source

`calfkit-tools` is the single distribution where calfkit vendors agent tools from many
upstream open-source codebases. This doc explains the layout and **where new sources go**.

## The model: vendor by source, expose by tool

calfkit's tool interface is Kafka (language-agnostic) — a "node tool" is a process that
consumes a request topic and produces a reply. The repo ships as **one Python distribution**,
`calfkit-tools`, organised internally by upstream source. Each source:

- vendors exactly **one upstream source** (provenance + license isolation + clean re-sync), and
- exposes one or more **tools** over the calfkit Kafka contract.

One source can expose several tools (e.g. hermes-agent → shell + files). Each source is a
**subpackage** of the single `calfkit_tools` package — not an independently installable
package. They stay self-contained internally (own `_vendor`/`_shims`, own tests), but they
share one `pyproject.toml`: all base deps install together, and optional backends are gated
behind shared extras.

## Layout

```
calfkit-tools/
  README.md
  THIRD_PARTY_NOTICES.md          # aggregated attribution index
  pyproject.toml                  # the single `calfkit-tools` distribution (deps + extras)
  docs/
    project-structure.md          # this file
    design/                       # per-tool design docs (e.g. shell-file-tool-port.md)
    tools-research/               # the upstream survey behind the vendoring choices
  src/calfkit_tools/
    __init__.py                   # side-effect-free top-level package (no eager tool imports)
    <source>/                     # one subpackage per upstream source (e.g. hermes, web_fetch)
  vendor/
    _template/                    # copy this to start a new source
    <source>/                     # provenance only (never on sys.path, never in the wheel)
  tests/
    <source>/                     # per-source test suites
```

## Anatomy of a source

The runnable code lives under the package; provenance and tests live alongside it:

```
src/calfkit_tools/<source>/        # e.g. src/calfkit_tools/hermes/
  _vendor/                         # upstream tree: copied verbatim, then import-rewritten + namespaced
  _shims/                          # thin replacements for the upstream's app-runtime edges
  node/                            # the calfkit Kafka adapter + thin tool wrappers (your code)

vendor/<source>/                   # e.g. vendor/hermes/ — PROVENANCE ONLY (not shipped)
  METADATA.yaml                    # provenance: repo URL, commit SHA, date, SPDX, files, local mods
  LICENSE                          # verbatim upstream license
  NODE.md                          # the Kafka contract this source's nodes expose (request/reply, topics)
  README.md                        # per-source overview
  patches/                         # re-appliable functional changes to vendored code (keep minimal)
  scripts/                         # optional per-source tooling (e.g. hermes' rewrite_imports.py)

tests/<source>/                    # vendored upstream tests (regression) + your adapter tests
```

### Conventions

- **Vendored code stays under `_vendor/`**, copied as close to verbatim as possible.
  Internal absolute imports are **mechanically rewritten** to the source's namespace
  (`<pkg>.* → calfkit_tools.<source>._vendor.<pkg>.*`); the upstream's app-runtime edges go to
  `calfkit_tools.<source>._shims.*` (apply **most-specific-prefix-wins**). Logic lines are not
  edited; functional changes live in `vendor/<source>/patches/` so re-syncs stay clean.
- **Shims** replace the few upstream modules that belong to the original app's runtime
  (its CLI / gateway / config). Keep them thin; prefer **re-exporting vendored code** over
  reimplementing. Watch for import-time and security-frozen-at-import behavior.
- **`node/` is your code** — TDD it. The Kafka adapter + per-tool wrappers.
- **Distribution:** there is one distribution, `calfkit-tools`. New runtime deps go in the
  root `pyproject.toml` `[project.dependencies]`; heavy / optional backends go behind a shared
  extra in `[project.optional-dependencies]` (e.g. `shell-docker = ["docker"]`), and roll up
  into the `all` extra. Keep the top-level `calfkit_tools` package side-effect-free — no eager
  tool imports — so importing the distribution pulls in no optional deps.
- **Public API:** tools are re-exported from the lazy `calfkit_tools.tools` namespace (PEP 562),
  so consumers import them vendor-free as `from calfkit_tools.tools import <tool>`. The
  source-subpackage layout is an internal detail and must not appear in the public import
  surface (see [`adr/0007-flat-public-api-reexport.md`](adr/0007-flat-public-api-reexport.md)).

## How to add a new source

1. **License first.** Follow the `open-source-vendoring-best-practices` skill: find + classify the
   upstream license **before** copying. Permissive only; copyleft / no-license = stop & flag.
2. **Create the source layout:** `src/calfkit_tools/<source>/{_vendor,_shims,node}/`,
   `vendor/<source>/` (copy `vendor/_template/` for the provenance scaffold), and
   `tests/<source>/`.
3. **Record provenance** in `vendor/<source>/METADATA.yaml` (repo, commit SHA, date, SPDX,
   file list) and copy the upstream `LICENSE` verbatim into `vendor/<source>/`.
4. **Scope the vendor set.** Compute the transitive import closure of the tool(s) you want,
   treating the upstream's app-runtime packages as a shim boundary. Pitfalls a naive closure
   misses: `from pkg import submodule`, and dynamic / `__file__`-relative imports.
5. **Vendor verbatim** into `_vendor/` (commit 1), then **import-rewrite + author shims**
   (commit 2). Add provenance headers + any secondary-origin attributions; append the
   source to `THIRD_PARTY_NOTICES.md`. Add the upstream `LICENSE` to the root `pyproject.toml`
   `license-files`.
6. **Add deps/extras** for the source to the root `pyproject.toml` (base deps under
   `[project.dependencies]`, optional backends under `[project.optional-dependencies]`).
7. **Write the node adapter** (`node/`) **test-first**, mapping the tool(s) onto the calfkit
   Kafka contract (`vendor/<source>/NODE.md`).
8. **Re-export the public API.** Add each tool (and any resource it binds) to `_EXPORTS` in
   `src/calfkit_tools/tools.py`, and its nodes to the `ALL_TOOLS` bundle, so users import it
   vendor-free as `from calfkit_tools.tools import <tool>`. Pin it in `tests/test_public_api.py`.
9. **Gate:** the vendored core tests + an adapter smoke test must pass.

See [`design/shell-file-tool-port.md`](design/shell-file-tool-port.md) for a worked
example (the hermes-agent port) — including the security and multi-tenancy concerns a
real port must address.
