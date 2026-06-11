# Each vendored source keeps its own config mechanism; a unified config comes later

**Status:** accepted · 2026-06-08

## Context

Hermes' web-search provider *selection* reads a `config.yaml` (`web.search_backend`
/ `web.extract_backend` / `web.backend`) through its own loader
(`hermes_cli.config`, already shimmed in `components/hermes-agent/`). Future vendored
sources will likely each bring their own config-walk too. The question raised during
the web-tools grill: unify config now, or keep each source's mechanism?

## Decision

For now, **each component keeps its vendor-specific config as vendored** — we vendor
hermes' registry **whole**, including its `config.yaml` resolver (`_resolve` /
`get_active_*` / `_read_config_key`) and the already-shimmed `hermes_cli.config`
loader, faithfully and without surgery. **But the node does not call that resolver:**
it selects the active provider from an env var and calls `registry.get_provider(name)`
directly (~10 LOC), leaving the `config.yaml` selection vendored-but-dormant. We do
**not** build a unified config layer yet. Later, a single calfkit config will drive
provider selection across all sources.

## Why

Vendoring the config-selection layer whole avoids precision-cutting surgery on the
registry, costs **zero new shims** (the loader is already shimmed), and defers
cross-source unification to integration time — consistent with the repo's "vendor
faithfully, integrate later" posture (cf. ADR-0001's glue-as-port-note).

## Consequences

The registry is vendored faithfully (no surgery), but its config.yaml resolver is
**dormant** — short-term provider selection is first-party env glue the node controls,
not hermes-CLI semantics calfkit would later have to override.

Be honest about the trade-off: the dormant resolver (~110 LOC) is effectively **dead
code** — the node never calls it, and under the `{}` config shim it would fall through
to an availability-walk anyway, so it is *also inert*. It is kept for **re-sync
fidelity** (faithful vendoring, no surgery), not for function. To keep that provable, a
node test asserts the resolver is never imported/called. The live "per-vendor config"
is the env selection, not `config.yaml`; the unification of *that* is the known,
deferred follow-up at the calfkit integration boundary.

## Update — packaging unified (2026-06-10)

The "unify later" promised here is now **partially realized** at the *packaging* layer:
the per-source independent packages (`calfkit-hermes`, `calfkit-pydantic-web-fetch`) were
folded into a single distribution, **`calfkit-tools`**, with each source as a subpackage
(`src/calfkit_tools/<source>/`) and provenance moved to `vendor/<source>/`. One
`pyproject.toml` now carries all base deps and shared backend extras.

This does **not** supersede the decision above. The env-driven, per-source provider
selection (`WEB_SEARCH_BACKEND` / `WEB_EXTRACT_BACKEND`, etc.) is unchanged, the vendored
`config.yaml` resolver remains dormant, and a **unified config layer** is still the open
follow-up. What changed is distribution shape, not config semantics.
