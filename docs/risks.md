# Risks

Specific, pessimistic failure modes and their mitigations. Each item is
something that plausibly goes wrong in production, not a generic caveat.

## AIS coverage gaps

**Failure mode**: AISStream aggregates terrestrial AIS receivers. A vessel
mid-Atlantic or mid-Mediterranean can go hours or days without a position
report, then reappear near a European terminal with no trace of its transit.
If gaps are silently interpolated, arrival timing and draught-change
inference will be wrong without any visible signal that something was
missing.

**Mitigation**: never interpolate across a coverage gap. Track a per-vessel
"time since last position" metric and surface it as a data quality signal.
Geofence/event detection (M3) must tolerate gaps during a dwell period
(explicitly tested, see M3 acceptance criteria) rather than resetting state
when a vessel briefly drops off the feed near a terminal.

## Vessel misidentification

**Failure mode**: AIS ship type codes 80-89 cover all tankers, not just LNG
carriers, so type alone will misclassify LPG/oil/chemical tankers as LNG
carriers, inflating the nowcast with vessels that are not actually LNG
carriers. Conversely, an LNG carrier broadcasting an incorrect or missing
ship type would be dropped entirely.

**Mitigation**: maintain a curated IMO-number allowlist of actual LNG
carriers (M2), not a ship-type filter, cross-checked against a labeled
fixture with an explicit precision/recall threshold in tests. Where
possible, cross-check against terminal visitor history if any public list
exists, to catch allowlist gaps.

## Terminal geofence errors

**Failure mode**: a busy shipping lane or anchorage near a terminal can
cause a vessel merely transiting or waiting to be misclassified as an
"arrival," inflating the estimated cargo delivered for that terminal on
days when no actual delivery happened. Conversely, a berth polygon drawn
too tight can miss actual arrivals if the vessel's reported position is
noisy near the dock.

**Mitigation**: two-stage geofencing (wide approach zone + tight berth
polygon, per ADR 0001 approach in M3) plus a minimum dwell-time threshold
before declaring an arrival, so a vessel merely passing through or briefly
loitering does not count. Explicit antimeridian-crossing test coverage
guards against a specific known bug class in polygon math.

## Validation data revisions

**Failure mode**: GIE ALSI allows SSOs/LSOs to retroactively correct
historical inventory/send-out data. If the backtest harness always reads
"current" ALSI values instead of the value that was known as of each
backtest date, accuracy metrics will look artificially good, because the
model is effectively being scored against hindsight-corrected numbers it
could never have seen in real time.

**Mitigation**: vintage-preserving storage for both nowcast output and
ingested ALSI data (ADR 0001, Decision 4). The backtest harness (M5) is
required to select ALSI vintages at or before the simulated "as of" date for
each fold, with a test asserting no lookahead occurs.

## AISStream schema or service drift

**Failure mode**: AISStream's exact JSON field names for `ShipStaticData`
were not verifiable from documentation alone this session (see
docs/data_sources.md). If the service changes field names, or if the
initial assumption is simply wrong, parsers can silently produce null or
garbage draught/dimension values instead of failing.

**Mitigation**: a captured live fixture (M1) is the source of truth for
field names, not the prose documentation. A schema-drift test asserts every
field the ingestion code reads by name is actually present in a live
sample, and fails loudly rather than defaulting to null on a missing key.

## Single point of failure for a 24/7 stream

**Failure mode**: GitHub Actions cron jobs are not designed to hold a
long-lived WebSocket connection; a scheduled job that tries to do so will
be killed at the workflow time limit, causing large ingestion gaps on a
predictable cadence rather than random outages.

**Mitigation**: the long-lived AIS ingester runs as a systemd-supervised
process on a dedicated VPS (restart-on-failure), not on GitHub Actions,
which is reserved for the daily ALSI ingestion and marts/dashboard rebuilds
(M7). A separate GitHub Actions health-check job monitors the VPS
ingester's last-write freshness and fails loudly if the ingester has gone
silent, since the VPS itself is now a single point of failure that needs
external monitoring.

## Licensing exposure from vessel reference data

**Failure mode**: Global Fishing Watch's vessel identity data is CC BY-NC
4.0 (non-commercial only, see docs/data_sources.md). If any part of the
dashboard or downstream data is ever monetized while still depending on GFW
data, that would violate the licence.

**Mitigation**: prefer a hand-curated LNG carrier reference table (M2) over
bulk GFW data for the core vessel/capacity registry; if GFW data is used
for anything, keep it clearly scoped to non-commercial use and do not
redistribute it as part of any paid product.

## Capacity estimation source uncertainty

**Failure mode**: no fully free, redistributable, bulk-downloadable source
of LNG carrier cargo capacity was confirmed this session (see
docs/data_sources.md open question). If M2 proceeds without resolving this,
capacity estimates could be built on an unverified or unlicensed data
source, discovered only after significant downstream work depends on it.

**Mitigation**: this is called out as an explicit open question requiring
the user's decision before M2 begins, rather than silently choosing a
source.
