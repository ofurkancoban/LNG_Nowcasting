
# LNG Nowcaster

Predicts European LNG import volumes 2-3 weeks ahead of official statistics
by tracking LNG carriers via AIS and detecting terminal arrival events.

## Non-negotiable rules

- All code, comments, docstrings, commit messages in English.
- Never use em dashes in any generated text, docs, or dashboard copy.
- Never add Claude, Anthropic, or any AI as co-author or contributor
  in commits, AUTHORS files, or documentation.
- Never commit secrets. All credentials via environment variables only.
- Never commit data files. raw/, staging/, marts/ are gitignored.
- Never rewrite git history. Never force push.
- Raw data is immutable and append-only. Reprocessing rebuilds
  downstream layers from raw, never mutates raw.
- If a task requires a design decision not covered here, write the
  options to docs/decisions/ and stop. Do not guess on architecture.

## Stack

- Python 3.11, uv for dependency management
- Ingestion: websockets, httpx
- Storage: Parquet on local disk (later Cloudflare R2), DuckDB query layer
- Transform: dbt-duckdb
- Geo: shapely, geopandas
- Testing: pytest, pandera for dataframe schemas
- Dashboard: Evidence.dev
- Orchestration: GitHub Actions (cron)

## Layout

src/lng/ingest/     external data collection, one module per source
src/lng/vessels/    registry, LNG identification, capacity estimation
src/lng/events/     geofencing, arrival/berth/departure detection
src/lng/nowcast/    forecasting models and backtest harness
transform/          dbt project
dashboard/          Evidence.dev site
tests/fixtures/     recorded API responses, never regenerate silently
docs/decisions/     architecture decision records

## Data layers

raw/       exactly as received, timestamped, never edited
staging/   parsed, typed, deduplicated, one row per event
marts/     analysis ready tables consumed by dashboard and models

## Verification loop

Run before claiming any task complete:
    make check    # ruff + mypy --strict + pytest
A task is not done until `make check` passes and the milestone
acceptance criteria in docs/milestones/ are demonstrably met.

## Testing rules

- No test may hit a live network. All external calls use recorded
  fixtures from tests/fixtures/.
- Every ingestion module ships with a fixture captured from the real API.
- Geospatial and time logic requires explicit edge case tests:
  antimeridian crossing, UTC boundaries, missing draught fields.

## Domain notes

- AIS type 1/2/3 = position reports. Type 5 = static and voyage data
  (includes draught, dimensions, destination).
- Tanker ship types are 80-89. LNG carriers need registry cross-reference,
  ship type alone is insufficient.
- Laden vs ballast is inferred from reported draught relative to the
  vessel's observed maximum draught, not from a fixed threshold.
- Terrestrial AIS coverage is coastal. Mid-ocean gaps are expected and
  must be handled as missing data, never interpolated silently.
- Ground truth for validation is GIE ALSI daily send-out per terminal.
