"""End-to-end orchestrator: raw AIS + ALSI vintages -> nowcast -> backtest.

No milestone through M7 produced this: M1-M4 shipped unit-tested ingestion
modules, M5 shipped a unit-tested model/backtest library, M6/M7 wired the
dashboard and workflows around a manually-produced demo metrics file. This
module is the first thing that actually connects them, so it can run
against real ingested data instead of hand-written demo predictions.

Known approximations, flagged rather than hidden:

- `data/reference/lng_carriers.csv` (M2) does not carry laden/ballast
  draught reference points, and no other free source was found for them
  (docs/data_sources.md). This module approximates a vessel's ballast/laden
  draught as the min/max of its own observed MaximumStaticDraught readings
  across the ingested dataset. This only works once enough history has been
  ingested to see a vessel in both conditions; with too little history it
  will underestimate the draught range and understate delivered volume.
- GIE ALSI facility EIC codes are mapped to
  data/reference/terminal_geofences.geojson terminal names via
  `FACILITY_TO_TERMINAL` below, derived from `lng.ingest.alsi.KNOWN_FACILITIES`
  (the single source of truth for facility identifiers, verified against a
  real GET /api/about?show=listing response on 2026-07-22). Covers all 32
  currently active European LNG terminals reporting to ALSI as of that date
  (excludes decommissioned facilities and post-Brexit UK terminals, which
  stopped reporting to the EU ALSI platform).
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import pyarrow.dataset as ds

from lng.events.detect import ArrivalEvent, PositionSample, detect_arrivals
from lng.events.geofence import TerminalGeofence, load_terminal_geofences
from lng.ingest.aisstream import assert_ship_static_data_schema
from lng.ingest.alsi import KNOWN_FACILITIES
from lng.nowcast.backtest import AlsiVintage, BacktestFold, run_backtest, write_metrics_parquet
from lng.nowcast.model import aggregate_daily_nowcast, estimate_delivered_volume_cbm
from lng.vessels.registry import VesselRecord, VesselRegistry

# Derived from lng.ingest.alsi.KNOWN_FACILITIES (the single source of truth
# for facility EIC codes) so the two never drift apart.
FACILITY_TO_TERMINAL = {v["facility"]: k for k, v in KNOWN_FACILITIES.items()}


@dataclass(frozen=True)
class StaticDataReading:
    received_at: datetime
    imo: int
    draught_m: float


def load_raw_ais_rows(raw_dir: Path) -> list[dict[str, Any]]:
    """Reads every row from raw/source=aisstream/** Parquet partitions."""
    source_dir = raw_dir / "source=aisstream"
    if not source_dir.exists():
        return []
    dataset = ds.dataset(str(source_dir), format="parquet")
    return dataset.to_table().to_pylist()  # type: ignore[no-any-return]


def load_raw_ais_rows_motherduck(
    motherduck_token: str, database: str = "lng_nowcasting", table: str = "raw_aisstream"
) -> list[dict[str, Any]]:
    """Reads every row from the MotherDuck raw_aisstream table.

    This is the GitHub-Actions-reachable equivalent of load_raw_ais_rows:
    since GitHub Actions runners have no access to the VPS's local disk,
    reading from the dual-written MotherDuck copy (see
    src/lng/ingest/aisstream.py::write_rows_motherduck) is how the
    orchestrator runs there instead of requiring an SSH-based file sync.
    """
    import duckdb

    con = duckdb.connect(f"md:{database}?motherduck_token={motherduck_token}")
    try:
        tables = {row[0] for row in con.execute("SHOW TABLES").fetchall()}
        if table not in tables:
            return []
        result: list[dict[str, Any]] = con.execute(f"SELECT * FROM {table}").df().to_dict(  # noqa: S608
            "records"
        )
    finally:
        con.close()
    return result


def extract_static_data(rows: list[dict[str, Any]]) -> dict[int, list[StaticDataReading]]:
    """Builds a per-MMSI, time-ordered list of static draught readings.

    Raises via assert_ship_static_data_schema if a ShipStaticData message is
    missing a required field (schema-drift guard, per docs/risks.md),
    rather than silently skipping it.
    """
    readings: dict[int, list[StaticDataReading]] = {}
    for row in rows:
        if row["message_type"] != "ShipStaticData":
            continue
        envelope = json.loads(row["raw_json"])
        body = envelope["Message"]["ShipStaticData"]
        assert_ship_static_data_schema(body)
        reading = StaticDataReading(
            received_at=datetime.fromisoformat(row["received_at"]),
            imo=int(body["ImoNumber"]),
            draught_m=float(body["MaximumStaticDraught"]),
        )
        readings.setdefault(row["mmsi"], []).append(reading)

    for mmsi_readings in readings.values():
        mmsi_readings.sort(key=lambda r: r.received_at)
    return readings


def build_position_samples(rows: list[dict[str, Any]]) -> dict[int, list[PositionSample]]:
    """Builds a per-MMSI, time-ordered list of PositionReport samples."""
    samples: dict[int, list[PositionSample]] = {}
    for row in rows:
        if row["message_type"] != "PositionReport":
            continue
        if row["latitude"] is None or row["longitude"] is None:
            continue
        sample = PositionSample(
            timestamp=datetime.fromisoformat(row["received_at"]),
            longitude=row["longitude"],
            latitude=row["latitude"],
        )
        samples.setdefault(row["mmsi"], []).append(sample)

    for mmsi_samples in samples.values():
        mmsi_samples.sort(key=lambda s: s.timestamp)
    return samples


def match_lng_carriers(
    static_data: dict[int, list[StaticDataReading]], registry: VesselRegistry
) -> dict[int, VesselRecord]:
    """Returns the subset of observed MMSIs whose IMO is a known LNG carrier."""
    matched: dict[int, VesselRecord] = {}
    for mmsi, readings in static_data.items():
        if not readings:
            continue
        record = registry.lookup(readings[0].imo)
        if record is not None:
            matched[mmsi] = record
    return matched


def compute_vessel_tracker_rows(
    rows: list[dict[str, Any]], registry: VesselRegistry | None = None
) -> list[dict[str, Any]]:
    """Builds one row per matched LNG carrier with its most recently observed
    position, for the live tracker map (dashboard/pages/tracker.md).

    Recomputes static_data/positions/matched directly from raw rows rather
    than reusing _run_pipeline_core's internals, so this stays decoupled
    from run_pipeline()'s existing tested return contract (list[BacktestFold]).
    """
    registry = registry or VesselRegistry()
    static_data = extract_static_data(rows)
    positions = build_position_samples(rows)
    matched = match_lng_carriers(static_data, registry)

    tracker_rows = []
    for mmsi, vessel in matched.items():
        mmsi_positions = positions.get(mmsi, [])
        if not mmsi_positions:
            continue
        last = mmsi_positions[-1]
        tracker_rows.append(
            {
                "mmsi": mmsi,
                "imo": vessel.imo,
                "vessel_name": vessel.name,
                "latitude": last.latitude,
                "longitude": last.longitude,
                "received_at": last.timestamp.isoformat(),
            }
        )
    return tracker_rows


def write_vessel_tracker_motherduck(
    tracker_rows: list[dict[str, Any]],
    motherduck_token: str,
    database: str = "lng_nowcasting",
    table: str = "vessel_positions",
) -> int:
    """Replaces the vessel_positions table with this run's tracker rows.

    Unlike the append-only raw_aisstream/raw_alsi/backtest_metrics tables,
    this is a current-snapshot table (where each known vessel is right now),
    so every call fully replaces the contents rather than accumulating
    history, matching write_parquet()'s overwrite semantics rather than
    append_parquet_partition()'s.
    """
    import duckdb
    import pandas as pd

    df = pd.DataFrame(tracker_rows)  # noqa: F841 -- used by name in the SQL scan below

    con = duckdb.connect(f"md:{database}?motherduck_token={motherduck_token}")
    try:
        con.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {table} (
                mmsi BIGINT,
                imo BIGINT,
                vessel_name VARCHAR,
                latitude DOUBLE,
                longitude DOUBLE,
                received_at VARCHAR,
                updated_at TIMESTAMP DEFAULT current_timestamp
            )
            """
        )
        con.execute(f"DELETE FROM {table}")  # noqa: S608
        if tracker_rows:
            con.execute(
                f"INSERT INTO {table} (mmsi, imo, vessel_name, latitude, longitude, received_at) "  # noqa: S608
                "SELECT mmsi, imo, vessel_name, latitude, longitude, received_at FROM df"
            )
    finally:
        con.close()

    return len(tracker_rows)


def _draught_near(readings: list[StaticDataReading], when: datetime, *, before: bool) -> float:
    """Returns the closest draught reading before/after `when`, or the
    overall closest reading if none exists strictly on that side.
    """
    if before:
        candidates = [r for r in readings if r.received_at <= when]
        if candidates:
            return candidates[-1].draught_m
    else:
        candidates = [r for r in readings if r.received_at >= when]
        if candidates:
            return candidates[0].draught_m
    return readings[0].draught_m


def estimate_arrival_deliveries(
    mmsi: int,
    terminal: TerminalGeofence,
    arrivals: list[ArrivalEvent],
    static_readings: list[StaticDataReading],
    vessel: VesselRecord,
) -> list[dict[str, Any]]:
    """Estimates delivered cargo volume for each confirmed arrival.

    Ballast/laden draught reference points are approximated as the min/max
    of this vessel's own observed draught readings (see module docstring's
    caveat); requires at least two distinct readings to produce a
    meaningful non-zero estimate.
    """
    if len(static_readings) < 2:
        return []

    laden_draught_m = max(r.draught_m for r in static_readings)
    ballast_draught_m = min(r.draught_m for r in static_readings)
    if laden_draught_m <= ballast_draught_m:
        return []

    deliveries = []
    for arrival in arrivals:
        draught_before = _draught_near(static_readings, arrival.entered_berth_at, before=True)
        draught_after = _draught_near(static_readings, arrival.confirmed_at, before=False)
        volume_cbm = estimate_delivered_volume_cbm(
            capacity_cbm=vessel.cargo_capacity_cbm,
            draught_before_m=draught_before,
            draught_after_m=draught_after,
            laden_draught_m=laden_draught_m,
            ballast_draught_m=ballast_draught_m,
        )
        deliveries.append(
            {
                "terminal": terminal.name,
                "gas_day": arrival.confirmed_at.date().isoformat(),
                "volume_cbm": volume_cbm,
                "mmsi": mmsi,
            }
        )
    return deliveries


def load_alsi_vintages_by_terminal(
    alsi_dir: Path, facility_to_terminal: dict[str, str] | None = None
) -> dict[str, list[AlsiVintage]]:
    """Reads every source=alsi/built_at=*/data.parquet snapshot and groups
    rows into per-terminal AlsiVintage lists, using facility_to_terminal to
    map ALSI facility codes to terminal names (see module docstring).
    """
    mapping = facility_to_terminal or FACILITY_TO_TERMINAL
    source_dir = alsi_dir / "source=alsi"
    vintages_by_terminal: dict[str, list[AlsiVintage]] = {}
    if not source_dir.exists():
        return vintages_by_terminal

    for snapshot_dir in sorted(source_dir.glob("built_at=*")):
        stamp = snapshot_dir.name.removeprefix("built_at=")
        built_at = datetime.strptime(stamp, "%Y%m%dT%H%M%SZ")
        table = ds.dataset(str(snapshot_dir), format="parquet").to_table()
        rows_by_terminal: dict[str, list[dict[str, Any]]] = {}
        for row in table.to_pylist():
            terminal = mapping.get(row["facility"])
            if terminal is None:
                continue
            rows_by_terminal.setdefault(terminal, []).append(row)

        for terminal, rows in rows_by_terminal.items():
            vintages_by_terminal.setdefault(terminal, []).append(
                AlsiVintage(built_at=built_at, rows=rows)
            )

    return vintages_by_terminal


def load_alsi_vintages_from_motherduck(
    motherduck_token: str,
    database: str = "lng_nowcasting",
    table: str = "raw_alsi",
    facility_to_terminal: dict[str, str] | None = None,
) -> dict[str, list[AlsiVintage]]:
    """Reads every row from the MotherDuck raw_alsi table and groups them
    into per-terminal AlsiVintage lists, mirroring
    load_alsi_vintages_by_terminal's local-file logic but sourced from the
    dual-written MotherDuck copy (see
    src/lng/ingest/alsi.py::write_vintage_motherduck).
    """
    import duckdb

    mapping = facility_to_terminal or FACILITY_TO_TERMINAL
    con = duckdb.connect(f"md:{database}?motherduck_token={motherduck_token}")
    try:
        tables = {row[0] for row in con.execute("SHOW TABLES").fetchall()}
        if table not in tables:
            return {}
        rows: list[dict[str, Any]] = con.execute(f"SELECT * FROM {table}").df().to_dict(  # noqa: S608
            "records"
        )
    finally:
        con.close()

    by_built_at: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_built_at.setdefault(row["built_at"], []).append(row)

    vintages_by_terminal: dict[str, list[AlsiVintage]] = {}
    for built_at_str, vintage_rows in by_built_at.items():
        built_at = datetime.fromisoformat(built_at_str)
        rows_by_terminal: dict[str, list[dict[str, Any]]] = {}
        for row in vintage_rows:
            terminal = mapping.get(row["facility"])
            if terminal is None:
                continue
            rows_by_terminal.setdefault(terminal, []).append(row)

        for terminal, terminal_rows in rows_by_terminal.items():
            vintages_by_terminal.setdefault(terminal, []).append(
                AlsiVintage(built_at=built_at, rows=terminal_rows)
            )

    return vintages_by_terminal


def _run_pipeline_core(
    rows: list[dict[str, Any]],
    vintages_by_terminal: dict[str, list[AlsiVintage]],
    now: datetime,
    registry: VesselRegistry | None = None,
    terminals: list[TerminalGeofence] | None = None,
) -> list[BacktestFold]:
    """Shared pipeline body: matches vessels, detects arrivals, estimates
    delivered volume, and backtests against ALSI vintages, regardless of
    whether the raw rows/vintages came from local files or MotherDuck.
    """
    registry = registry or VesselRegistry()
    all_terminals = terminals or load_terminal_geofences()
    real_terminals = [t for t in all_terminals if not t.name.startswith("TEST_")]

    static_data = extract_static_data(rows)
    positions = build_position_samples(rows)
    matched = match_lng_carriers(static_data, registry)

    deliveries: list[dict[str, Any]] = []
    for mmsi, vessel in matched.items():
        mmsi_positions = positions.get(mmsi, [])
        if not mmsi_positions:
            continue
        for terminal in real_terminals:
            arrivals = detect_arrivals(mmsi, terminal, mmsi_positions)
            deliveries.extend(
                estimate_arrival_deliveries(
                    mmsi, terminal, arrivals, static_data[mmsi], vessel
                )
            )

    nowcast = aggregate_daily_nowcast(deliveries)
    predictions = [
        {"terminal": terminal, "gas_day": gas_day, "as_of": now, "predicted_gwh": gwh}
        for (terminal, gas_day), gwh in nowcast.items()
    ]
    predictions = [p for p in predictions if p["terminal"] in vintages_by_terminal]

    return run_backtest(predictions, vintages_by_terminal)


def run_pipeline(
    raw_ais_dir: Path,
    alsi_dir: Path,
    now: datetime,
    registry: VesselRegistry | None = None,
    terminals: list[TerminalGeofence] | None = None,
) -> list[BacktestFold]:
    """Runs the full ingestion-to-backtest pipeline against already-ingested
    local raw AIS and ALSI vintage Parquet files.
    """
    rows = load_raw_ais_rows(raw_ais_dir)
    vintages_by_terminal = load_alsi_vintages_by_terminal(alsi_dir)
    return _run_pipeline_core(rows, vintages_by_terminal, now, registry, terminals)


def run_pipeline_motherduck(
    motherduck_token: str,
    now: datetime,
    registry: VesselRegistry | None = None,
    terminals: list[TerminalGeofence] | None = None,
) -> list[BacktestFold]:
    """Runs the full ingestion-to-backtest pipeline reading raw AIS and ALSI
    data from MotherDuck instead of local files. This is what lets
    .github/workflows/nowcast-build.yml run the orchestrator on a GitHub
    Actions runner, which has no access to the VPS's local disk.
    """
    rows = load_raw_ais_rows_motherduck(motherduck_token)
    vintages_by_terminal = load_alsi_vintages_from_motherduck(motherduck_token)
    return _run_pipeline_core(rows, vintages_by_terminal, now, registry, terminals)


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint: runs the full pipeline and writes a metrics snapshot.

    Not exercised against real ingested data by any test (tests/test_pipeline_orchestrate.py
    exercises run_pipeline()/run_pipeline_motherduck() directly against
    synthetic fixtures). Wired into .github/workflows/nowcast-build.yml.

    --source local reads raw/ais and raw/alsi from local Parquet (VPS usage,
    where the ingesters write directly to disk). --source motherduck reads
    the same data from the dual-written MotherDuck tables instead, which is
    what lets this run on a GitHub Actions runner with no access to the
    VPS's local disk or any SSH-based file sync (see ADR 0001 Decision 5).

    With --motherduck, also appends the run's metrics to the hosted
    MotherDuck table the dashboard reads from live; without it, only the
    local Parquet snapshot is written (only meaningful with --source local,
    since --source motherduck has no local raw data to write metrics next to
    anyway unless --out is also given).
    """
    import os

    from lng.nowcast.backtest import write_metrics_motherduck

    parser = argparse.ArgumentParser(
        description="Run the full ingestion-to-backtest pipeline and write a metrics snapshot."
    )
    parser.add_argument(
        "--source",
        choices=["local", "motherduck"],
        default="local",
        help="Where to read raw AIS/ALSI data from (default: local).",
    )
    parser.add_argument("--raw-ais-dir", type=Path, help="Required when --source local")
    parser.add_argument("--alsi-dir", type=Path, help="Required when --source local")
    parser.add_argument("--out", type=Path, help="marts/backtest output directory")
    parser.add_argument("--run-id", required=True)
    parser.add_argument(
        "--motherduck",
        action="store_true",
        help="Also append metrics to MotherDuck (reads MOTHERDUCK_TOKEN env var).",
    )
    args = parser.parse_args(argv)

    if args.source == "local":
        if args.raw_ais_dir is None or args.alsi_dir is None:
            parser.error("--source local requires --raw-ais-dir and --alsi-dir")
        rows = load_raw_ais_rows(args.raw_ais_dir)
        vintages_by_terminal = load_alsi_vintages_by_terminal(args.alsi_dir)
    else:
        token = os.environ.get("MOTHERDUCK_TOKEN")
        if not token:
            parser.error("--source motherduck requires the MOTHERDUCK_TOKEN environment variable")
        rows = load_raw_ais_rows_motherduck(token)
        vintages_by_terminal = load_alsi_vintages_from_motherduck(token)

    folds = _run_pipeline_core(rows, vintages_by_terminal, now=datetime.now())

    if args.motherduck:
        token = os.environ.get("MOTHERDUCK_TOKEN")
        if not token:
            parser.error("--motherduck requires the MOTHERDUCK_TOKEN environment variable")
        tracker_rows = compute_vessel_tracker_rows(rows)
        n_tracker = write_vessel_tracker_motherduck(tracker_rows, token)
        print(f"tracker_vessels_written={n_tracker}")

    if not folds:
        print("No backtest folds produced: no matched LNG carrier arrivals or ALSI vintages.")
        return 1

    if args.out is not None:
        path = write_metrics_parquet(folds, args.run_id, args.out)
        print(f"folds={len(folds)} metrics={path}")
    else:
        print(f"folds={len(folds)}")

    if args.motherduck:
        token = os.environ.get("MOTHERDUCK_TOKEN")
        if not token:
            parser.error("--motherduck requires the MOTHERDUCK_TOKEN environment variable")
        n = write_metrics_motherduck(folds, args.run_id, token)
        print(f"motherduck_rows_written={n}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
