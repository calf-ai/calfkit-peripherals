# Project structure & how to add a port

`calfkit-peripherals` is the shared monorepo where calfkit vendors agent tools from many
upstream open-source codebases. This doc explains the layout and **where new ports go**.

## The model: vendor by source, expose by tool

calfkit's tool interface is Kafka (language-agnostic) — a "node tool" is a process that
consumes a request topic and produces a reply. So the repo is a **polyglot monorepo of
node components**. Each component:

- vendors exactly **one upstream source** (provenance + license isolation + clean re-sync), and
- exposes one or more **tools** over the calfkit Kafka contract.

One source can expose several tools (e.g. hermes-agent → shell + files). Components are
independent islands with their own language/toolchain/build — Python today, possibly
TS/Rust later. This is the "monorepo of services" model (deliberately **not** a central
`third_party/` + single polyglot build tool).

## Layout

```
calfkit-peripherals/
  README.md
  THIRD_PARTY_NOTICES.md          # aggregated attribution index
  docs/
    project-structure.md          # this file
    design/                       # per-tool design docs (e.g. shell-file-tool-port.md)
    tools-research/               # the upstream survey behind the vendoring choices
  components/
    _template/                    # copy this to start a new port
    <source>/                     # one self-contained node component per upstream
```

## Anatomy of a component (Python)

```
components/<source>/               # e.g. components/hermes-agent/
  METADATA.yaml                    # provenance: repo URL, commit SHA, date, SPDX, files, local mods
  LICENSE                          # verbatim upstream license
  NODE.md                          # the Kafka contract this node exposes (request/reply, topics)
  patches/                         # re-appliable functional changes to vendored code (keep minimal)
  pyproject.toml                   # package `calfkit-<source>`, per-backend optional extras
  src/calfkit_<source>/
    _vendor/                       # upstream tree: copied verbatim, then import-rewritten + namespaced
    _shims/                        # thin replacements for the upstream's app-runtime edges
    node/                          # the calfkit Kafka adapter + thin tool wrappers (your code)
  tests/                           # vendored upstream tests (regression) + your adapter tests
```

### Conventions

- **Vendored code stays under `_vendor/`**, copied as close to verbatim as possible.
  Internal absolute imports are **mechanically rewritten** to the component namespace
  (`<pkg>.* → calfkit_<source>._vendor.<pkg>.*`); the upstream's app-runtime edges go to
  `calfkit_<source>._shims.*` (apply **most-specific-prefix-wins**). Logic lines are not
  edited; functional changes live in `patches/` so re-syncs stay clean.
- **Shims** replace the few upstream modules that belong to the original app's runtime
  (its CLI / gateway / config). Keep them thin; prefer **re-exporting vendored code** over
  reimplementing. Watch for import-time and security-frozen-at-import behavior.
- **`node/` is your code** — TDD it. The Kafka adapter + per-tool wrappers.
- **Distribution:** each component is its own installable package, installed by calfkit via
  `uv add "calfkit-<source>[...] @ git+<repo>#subdirectory=components/<source>"`, pinned to
  a tag. Non-Python components ship their own artifact (binary/container) run as a service.

## How to add a new port

1. **License first.** Follow the `open-source-vendoring-best-practices` skill: find + classify the
   upstream license **before** copying. Permissive only; copyleft / no-license = stop & flag.
2. **Copy the template:** `cp -r components/_template components/<source>`.
3. **Record provenance** in `METADATA.yaml` (repo, commit SHA, date, SPDX, file list) and
   copy the upstream `LICENSE` verbatim.
4. **Scope the vendor set.** Compute the transitive import closure of the tool(s) you want,
   treating the upstream's app-runtime packages as a shim boundary. Pitfalls a naive closure
   misses: `from pkg import submodule`, and dynamic / `__file__`-relative imports.
5. **Vendor verbatim** into `_vendor/` (commit 1), then **import-rewrite + author shims**
   (commit 2). Add provenance headers + any secondary-origin attributions; append the
   component to `THIRD_PARTY_NOTICES.md`.
6. **Write the node adapter** (`node/`) **test-first**, mapping the tool(s) onto the calfkit
   Kafka contract (`NODE.md`).
7. **Gate:** the vendored core tests + an adapter smoke test must pass.

See [`design/shell-file-tool-port.md`](design/shell-file-tool-port.md) for a worked
example (the hermes-agent port) — including the security and multi-tenancy concerns a
real port must address.
