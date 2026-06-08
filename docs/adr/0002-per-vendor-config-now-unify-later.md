# Each vendored source keeps its own config mechanism; a unified config comes later

**Status:** accepted · 2026-06-08

## Context

Hermes' web-search provider *selection* reads a `config.yaml` (`web.search_backend`
/ `web.extract_backend` / `web.backend`) through its own loader
(`hermes_cli.config`, already shimmed in `components/hermes-agent/`). Future vendored
sources will likely each bring their own config-walk too. The question raised during
the web-tools grill: unify config now, or keep each source's mechanism?

## Decision

For now, **each component keeps its vendor-specific config mechanism as vendored**.
Hermes keeps its `config.yaml` keys and the already-shimmed `hermes_cli.config`
loader — i.e. we vendor the registry's config-selection layer (`_resolve` /
`get_active_*` / `_read_config_key`) whole, not just its in-memory provider map. We
do **not** build a unified config layer yet. Later, a single calfkit config will
aggregate and drive all tools by feeding each source the config shape it expects.

## Why

Vendoring the config-selection layer whole avoids precision-cutting surgery on the
registry, costs **zero new shims** (the loader is already shimmed), and defers
cross-source unification to integration time — consistent with the repo's "vendor
faithfully, integrate later" posture (cf. ADR-0001's glue-as-reference).

## Consequences

Short-term, controlling tools means touching N per-source configs; the unification
is a known, deferred follow-up that lands at the calfkit integration boundary, not
inside any one vendored component.
