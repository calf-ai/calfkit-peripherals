# components/

One self-contained calfkit **node component** per upstream source. Each component vendors
one open-source project and exposes its tools over the calfkit Kafka contract.

To add a port: copy [`_template/`](_template/) → `<source>/` and follow
[`../docs/project-structure.md`](../docs/project-structure.md). Vendor license-first per
the `open-source-vendoring-best-practices` conventions.
