# LNG carrier reference table

`lng_carriers.csv` is a hand-curated starter set of LNG carriers identified
by IMO number, sourced from individual Wikipedia articles (each row's
`source_url`), which publish under CC BY-SA. This is a small, honestly
partial seed set (6 vessels), not the full global LNG carrier fleet
(~700 vessels per docs/data_sources.md's open question) — it exists to prove
out the registry/capacity lookup pipeline in M2 and needs to be expanded
before production use.

No Global Fishing Watch data is used in this table, so it carries no
CC BY-NC (non-commercial-only) restriction; the Wikipedia CC BY-SA source
requires attribution, which the `source_url` column provides per row.

Some `propulsion_type` values are `unspecified` where the source article did
not document engine details; this is recorded honestly rather than guessed.

Columns:
- `imo`: IMO ship number (permanent, unlike MMSI)
- `name`: vessel name at time of data capture
- `cargo_capacity_cbm`: LNG cargo capacity in cubic meters
- `build_year`: year built/delivered
- `propulsion_type`: engine/propulsion description, or `unspecified`
- `source_url`: the page this row's data was taken from
