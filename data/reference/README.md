# LNG carrier reference table

`lng_carriers.csv` is a hand-curated starter set of LNG carriers identified
by IMO number. This is a partial seed set (50 vessels), not the full global
LNG carrier fleet (~700-715 vessels per docs/data_sources.md's open
question) — it exists to prove out the registry/capacity lookup pipeline
and needs to be expanded further before production use.

45 of these vessels are Qatar's entire Q-Flex (31 ships) and Q-Max (14
ships) fleets, added specifically because the draught-based delivery model
(src/lng/nowcast/model.py) cannot produce a meaningful estimate for a
permanently-moored FSRU (see Hoegh Esperanza's near-constant draught) — a
genuine end-to-end fold requires a vessel that actually transits
full-to-empty, which these conventional shuttle carriers do and floating
storage units do not. Capacity and build year for these come from Nakilat's
own official fleet list PDF (source_url); IMO numbers were independently
cross-verified per vessel via VesselFinder/MarineTraffic/Balticshipping,
since Nakilat's own document does not list IMO numbers. Two ship names
(Al Kharsaah, Al Shamal) are reused by Nakilat for both a small support
vessel and an unrelated Q-Flex carrier — the IMO numbers here were verified
to correspond to the 315m Q-Flex LNG tankers, not the support craft.

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
