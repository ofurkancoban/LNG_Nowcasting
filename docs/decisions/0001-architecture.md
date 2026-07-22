# ADR 0001: Core storage and idempotency architecture

## Status

Proposed. Several points below are flagged UNCERTAIN and need explicit
sign-off before M1 implementation starts.

## Context

The pipeline ingests a continuous AIS WebSocket stream (AISStream.io, no
resume/backfill capability, see docs/data_sources.md) and a daily-batch
ground-truth source (GIE ALSI, which retroactively revises historical data).
Per CLAUDE.md: raw data is immutable and append-only, reprocessing rebuilds
downstream layers from raw and never mutates raw, and every design decision
not already covered must be written up here rather than guessed.

Four things need deciding: storage format/partitioning, idempotency for the
streaming source, handling of late-arriving AIS messages, and how to
preserve vintages so a nowcast can be reconstructed as of any past date.

## Decision 1: Storage format and partitioning

**Options considered:**

- Parquet partitioned by `source=aisstream/ingest_date=YYYY-MM-DD/hour=HH`,
  one file written per ingestion window (e.g. flush every N minutes or M
  messages). Uneven query patterns (by vessel) go through a DuckDB view/index
  layer on top rather than the physical partitioning.
- Parquet partitioned by MMSI. Rejected: MMSI count is large and uneven (a
  handful of vessels near a terminal generate far more messages than a
  vessel mid-ocean), producing many tiny files and a few huge ones, and it
  complicates simple append-only writes from a single streaming process.
- A single growing Parquet file per day with no sub-partitioning. Rejected:
  breaks append-only-safe writes from a long-running process (Parquet is not
  designed for concurrent append) and makes crash recovery harder to reason
  about.

**Recommendation**: date/hour partitioning for raw AIS, one Parquet file per
flush window, queried through DuckDB. GIE ALSI raw data partitions by
`source=alsi/ingest_date=YYYY-MM-DD` since it's a daily batch, not a stream.

## Decision 2: Idempotency for the streaming source

**Options considered:**

- Dedupe key `(MMSI, message_type, timestamp_received_utc)`. Rejected:
  AIS-reported timestamps can repeat (some vessels resend identical
  timestamps across successive reports, and second-precision timestamps
  cannot distinguish rapid successive positions).
- Dedupe key `(MMSI, message_type, hash(raw_payload))`, applied only when
  building the staging layer, never on raw. Raw stays append-only and
  intentionally undeduplicated, consistent with the "raw is immutable"
  rule in CLAUDE.md.

**Recommendation**: the second option. Raw ingestion writes every message it
receives, duplicates included; staging build computes the payload hash and
drops exact duplicates when constructing the one-row-per-event tables.

## Decision 3: Late-arriving AIS messages

AISStream has no documented replay/backfill mechanism (see
docs/data_sources.md), so "late arrival" in this system can only mean the
ingester itself was down and missed messages that were never captured, not
that messages arrive out of network order after being sent. Out-of-order
delivery within an active connection is still possible and must be handled
by sorting on the AIS-reported timestamp within staging, not by insertion
order.

**Recommendation**: staging builds always fully recompute the affected
date/hour partitions from raw rather than attempting incremental patches.
This is simpler to reason about and cheap enough at this data volume; there
is no attempt to patch in messages after the fact, since AISStream cannot
supply them anyway.

## Decision 4: Vintage preservation

**Recommendation**: every staging and marts build is stamped with a
`built_at` timestamp and writes a new dated snapshot directory
(`marts/nowcast/built_at=YYYY-MM-DDTHH-MM-SSZ/...`) rather than overwriting
the previous one in place. The dashboard and backtest harness always read
either "latest" or an explicit `built_at` to reconstruct what the nowcast
looked like as of any past date. This is required for honest backtesting
against ALSI, since ALSI values are themselves revised after publication
(see docs/data_sources.md) — the ADR's vintage snapshots must exist for both
the nowcast output and the ALSI ground truth pulled at each build time, or
backtest accuracy will be inflated by hindsight-corrected data.

**Decided**: keep every vintage forever, no pruning. Simplest option for full
reconstructability; storage growth is accepted as a known tradeoff rather
than optimized for at this stage. Revisit if storage costs become a problem.

## Decision 5: Dual-write to MotherDuck, local Parquet as a rotating buffer

**Context**: the dashboard needed to be hostable on GitHub Pages, which
serves static files only and has no access to the VPS's local disk. This
required moving the dashboard's data source to a network-reachable
database. MotherDuck (hosted DuckDB, already declared in the project's
stack) was chosen for this, since Evidence.dev has a native connector for
it and the existing DuckDB-based query patterns transfer directly.

**Decided**: every ingestion path (AIS live ingestion, ALSI ingestion, and
the orchestrator's backtest metrics) now dual-writes: local Parquet first
(unchanged, network-independent, so a MotherDuck outage never blocks
ingestion), then MotherDuck (`write_rows_motherduck`,
`write_vintage_motherduck`, `write_metrics_motherduck`). A MotherDuck write
failure is logged but does not crash the ingester or discard the local
copy. MotherDuck becomes the permanent, queryable record; local Parquet is
now a rotating buffer, not the permanent record it was originally decided
to be.

Local files are deleted once they exceed 3 days old
(`src/lng/quality/retention.py`, run via
`scripts/cleanup_local_raw.py` on a schedule, independent of any ingestion
process so cleanup still happens if an ingester is down). This is a
deliberate revision of this ADR's original stance that raw Parquet is the
permanent record — that role now belongs to MotherDuck, and the local copy
exists only for the fast, network-independent write path plus a short
recovery buffer if MotherDuck needs to be re-synced from local files after
an extended outage.

**Consequence, flagged rather than hidden**: this introduces a real
constraint — if MotherDuck is unreachable for longer than 3 days, the
local buffer will be cleaned up before a delayed dual-write can occur,
and that window's data would only exist in the (still-immutable, still
authoritative for that window) local copy until it is cleaned. This
tradeoff was accepted explicitly rather than solved with more elaborate
retry/backfill machinery, to keep the retention mechanism simple.

## Consequences

- Raw layer is larger than a deduplicated store would be (duplicates from
  network retransmission are kept), by design, to preserve auditability.
- Every staging/marts rebuild is a full recompute of the touched partitions,
  not an incremental merge — simpler code, higher compute cost per rebuild,
  acceptable at expected data volumes (a few hundred LNG carriers, not the
  full global AIS firehose).
- Vintage snapshots add storage overhead whose long-term growth rate is not
  yet bounded (see Decision 4's open retention question) in MotherDuck,
  though local disk usage is now bounded by Decision 5's 3-day window.
