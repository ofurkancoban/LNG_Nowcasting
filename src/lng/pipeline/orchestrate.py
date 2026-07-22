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
  `FACILITY_TO_TERMINAL` below. Verified against a real
  GET /api/about?show=listing response on 2026-07-22: Rotterdam Gate
  Terminal is `21W0000000000079`, Zeebrugge LNG Terminal is
  `21W0000000001245`. Only covers these two terminals; extend this mapping
  before scoring against additional European LNG terminals.
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
from lng.nowcast.backtest import AlsiVintage, BacktestFold, run_backtest, write_metrics_parquet
from lng.nowcast.model import aggregate_daily_nowcast, estimate_delivered_volume_cbm
from lng.vessels.registry import VesselRecord, VesselRegistry

# Provisional, project-defined mapping from GIE ALSI facility EIC code to a
# terminal name in data/reference/terminal_geofences.geojson. Only covers
# the fixture codes used in tests; must be extended with real EIC codes
# before use against real ALSI data (see module docstring).
FACILITY_TO_TERMINAL = {
    "21W0000000000079": "Gate Rotterdam",  # Rotterdam Gate Terminal, verified via
    "21W0000000001245": "Zeebrugge",  # /api/about?show=listing on 2026-07-22
}


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


def run_pipeline(
    raw_ais_dir: Path,
    alsi_dir: Path,
    now: datetime,
    registry: VesselRegistry | None = None,
    terminals: list[TerminalGeofence] | None = None,
) -> list[BacktestFold]:
    """Runs the full ingestion-to-backtest pipeline against already-ingested
    raw AIS and ALSI vintage data, returning the resulting backtest folds.
    """
    registry = registry or VesselRegistry()
    all_terminals = terminals or load_terminal_geofences()
    real_terminals = [t for t in all_terminals if not t.name.startswith("TEST_")]

    rows = load_raw_ais_rows(raw_ais_dir)
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

    vintages_by_terminal = load_alsi_vintages_by_terminal(alsi_dir)
    predictions = [p for p in predictions if p["terminal"] in vintages_by_terminal]

    return run_backtest(predictions, vintages_by_terminal)


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint: runs the full pipeline and writes a metrics snapshot.

    Not exercised against real ingested data by any test (tests/test_pipeline_orchestrate.py
    exercises run_pipeline() directly against synthetic fixtures); this is the
    entrypoint .github/workflows/nowcast-build.yml's placeholder step should
    eventually call once real raw AIS and ALSI data exist on disk.
    """
    parser = argparse.ArgumentParser(
        description="Run the full ingestion-to-backtest pipeline and write a metrics snapshot."
    )
    parser.add_argument("--raw-ais-dir", required=True, type=Path)
    parser.add_argument("--alsi-dir", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path, help="marts/backtest output directory")
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args(argv)

    folds = run_pipeline(args.raw_ais_dir, args.alsi_dir, now=datetime.now())
    if not folds:
        print("No backtest folds produced: no matched LNG carrier arrivals or ALSI vintages.")
        return 1

    path = write_metrics_parquet(folds, args.run_id, args.out)
    print(f"folds={len(folds)} metrics={path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
