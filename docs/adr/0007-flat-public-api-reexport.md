# Expose tools through a flat `calfkit_tools.tools` namespace

**Status:** accepted · 2026-06-16

## Context

Internally the distribution is organised **by vendored upstream source**: each
source is a subpackage of `calfkit_tools` (`calfkit_tools.hermes`,
`calfkit_tools.web_fetch`) and its deployable tool nodes live under
`<source>/node/`. Until now that internal layout *was* the public API — the
package-root `__init__` told consumers to `from calfkit_tools.hermes.node import
terminal` and the PyPI summary enumerated tools by their source ("hermes
shell/files/web/todo …").

That leaks an implementation detail into user code: the name `hermes` is the
upstream codebase a tool was ported from, not anything a consumer should depend
on. Provenance can change on re-sync, and grouping the import surface by "who we
vendored it from" forces users to learn the vendoring map to import a shell
tool. We want a flat, vendor-free public surface.

The obvious flat surface — re-exporting every tool from the package root
(`from calfkit_tools import <tool>`) — has a hard collision: the `web_fetch`
*tool* shares its name with the `web_fetch` *source subpackage*. Importing
`calfkit_tools.web_fetch.node` (which happens in almost any real use) binds
`calfkit_tools.web_fetch` to the subpackage module, shadowing any package-root
attribute of the same name — so `from calfkit_tools import web_fetch` returns
the **module**, not the tool. A package-root re-export cannot reliably expose
`web_fetch` without renaming the source subpackage.

## Decision

**Tools are re-exported from a dedicated `calfkit_tools.tools` module.** Every
tool — plus `InMemoryTodoStore` and an `ALL_TOOLS` bundle — is importable as
`from calfkit_tools.tools import <name>`. The vendor-by-source subpackage layout
is an internal implementation detail and must not appear in the public import
surface, the README, or the PyPI summary.

`calfkit_tools.tools` is a leaf module with no submodules, so no tool name can
collide with a subpackage the way `web_fetch` does at the package root. The
re-export is **lazy (PEP 562)** and the top-level package stays side-effect free:

1. `calfkit_tools/tools.py` defines `_EXPORTS` (public name → internal module).
   `__getattr__` resolves a name against the live source module on every access
   (uncached, so a re-export is always identical to the source object, robust if
   a source module is reloaded); `__dir__`/`__all__` advertise the surface.
2. `import calfkit_tools` pulls in no tool code, and `import calfkit_tools.tools`
   pulls in no source module until a specific tool is accessed.
3. `ALL_TOOLS` is the list of all eleven tool nodes (built on demand from the
   source node lists), replacing any public reference to the source-named bundle
   `HERMES_NODES`.
4. The source submodule paths (`calfkit_tools.hermes.node`, …) remain importable
   — they are simply no longer the advertised path, so nothing breaks.

## Considered and rejected

- **Re-export from the package root (`from calfkit_tools import web_fetch`)** —
  blocked by the `web_fetch` tool-vs-subpackage name collision above; the
  subpackage module shadows the re-export once it is imported.
- **Rename the `web_fetch` source subpackage** (e.g. to `pydantic_web_fetch`) to
  free the package-root name — works, but churns the internal layout, imports,
  and tests for a cosmetic gain over a `.tools` module that already sidesteps the
  collision with zero structural change.
- **Keep source-named import paths as the public API** — leaks the internal
  vendor organisation (`hermes`) into user code and the PyPI summary, and ties
  the public contract to provenance that may change on re-sync.
- **Eager re-export** — would import every tool module (and its deps) when the
  namespace is imported; the PEP 562 lazy path keeps imports cheap.

## Consequences

- The public surface is pinned by `tests/test_public_api.py`: name presence,
  identity against the source object, the `ALL_TOOLS` bundle, and the
  side-effect-free invariant (asserted in a fresh interpreter).
- `calfkit run` targets the namespace directly: `calfkit run
  calfkit_tools.tools:terminal` or `…:ALL_TOOLS` (the loader's `getattr`
  triggers the lazy `__getattr__`).
- Adding a source gains one step — register the tool in `_EXPORTS` and add its
  nodes to `_load_all_tools` (documented in `CONTRIBUTING.md`).
- Provenance and attribution live in `CONTRIBUTING.md`, `THIRD_PARTY_NOTICES.md`,
  and the bundled per-source `LICENSE` files; the user-facing README and PyPI
  summary stay vendor-free. The `Apache-2.0 AND MIT` SPDX expression is
  unchanged — MIT attribution for the vendored trees still travels in the
  distribution.
