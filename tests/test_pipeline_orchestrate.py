from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from lng.events.detect import DEFAULT_DWELL_THRESHOLD
from lng.ingest.aisstream import dedupe_rows, message_to_row, parse_message, write_parquet
from lng.ingest.alsi import write_vintage_snapshot
from lng.nowcast.model import APPROXIMATE_GWH_PER_CBM
from lng.pipeline.orchestrate import (
    build_position_samples,
    extract_static_data,
    load_alsi_vintages_by_terminal,
    load_raw_ais_rows,
    match_lng_carriers,
    run_pipeline,
)
from lng.vessels.registry import VesselRegistry

MOZAH_MMSI = 227000111
MOZAH_IMO = 9337755  # real vessel in data/reference/lng_carriers.csv
MOZAH_CAPACITY_CBM = 266000.0

BERTH_POINT = (4.020, 51.945)  # inside Gate Rotterdam's berth polygon
T0 = datetime(2024, 3, 1, 0, 0, 0)


def _static_data_line(mmsi: int, imo: int, draught: float, when: datetime) -> str:
    time_utc = when.strftime("%Y-%m-%d %H:%M:%S.000000000 +0000 UTC")
    return json.dumps(
        {
            "MessageType": "ShipStaticData",
            "Message": {
                "ShipStaticData": {
                    "ImoNumber": imo,
                    "CallSign": "C1234",
                    "ShipType": 80,
                    "Dimension": {"A": 150, "B": 30, "C": 20, "D": 20},
                    "MaximumStaticDraught": draught,
                    "Destination": "ROTTERDAM",
                    "Eta": {"Month": 3, "Day": 1, "Hour": 0, "Minute": 0},
                }
            },
            "Metadata": {
                "MMSI": mmsi,
                "ShipName": "MOZAH",
                "latitude": BERTH_POINT[1],
                "longitude": BERTH_POINT[0],
                "time_utc": time_utc,
            },
        }
    )


def _position_line(mmsi: int, lon: float, lat: float, when: datetime) -> str:
    time_utc = when.strftime("%Y-%m-%d %H:%M:%S.000000000 +0000 UTC")
    return json.dumps(
        {
            "MessageType": "PositionReport",
            "Message": {"PositionReport": {"Latitude": lat, "Longitude": lon}},
            "Metadata": {
                "MMSI": mmsi,
                "ShipName": "MOZAH",
                "latitude": lat,
                "longitude": lon,
                "time_utc": time_utc,
            },
        }
    )


def _write_raw_ais_dataset(out_dir: Path) -> None:
    arrival_confirmed_at = T0 + DEFAULT_DWELL_THRESHOLD + timedelta(minutes=5)
    lines = [
        _static_data_line(MOZAH_MMSI, MOZAH_IMO, draught=12.0, when=T0 - timedelta(hours=1)),
        _position_line(MOZAH_MMSI, *BERTH_POINT, when=T0),
        _position_line(MOZAH_MMSI, *BERTH_POINT, when=T0 + timedelta(hours=1)),
        _position_line(MOZAH_MMSI, *BERTH_POINT, when=arrival_confirmed_at),
        _static_data_line(
            MOZAH_MMSI, MOZAH_IMO, draught=8.0, when=arrival_confirmed_at + timedelta(minutes=10)
        ),
    ]

    messages = [parse_message(line) for line in lines]
    assert all(m is not None for m in messages)
    rows = dedupe_rows([message_to_row(m) for m in messages if m is not None])  # type: ignore[arg-type]
    write_parquet(rows, out_dir)


def _write_alsi_vintage(out_dir: Path, gas_day: str, send_out: float, built_at: datetime) -> None:
    rows = [
        {
            "facility": "21Z0000000000082X",  # maps to "Gate Rotterdam" via FACILITY_TO_TERMINAL
            "name": "Gate terminal",
            "gasDayStart": gas_day,
            "inventory": 100.0,
            "sendOut": send_out,
            "dtmi": 180.0,
            "dtrs": 300.0,
            "status": "C",
        }
    ]
    write_vintage_snapshot(rows, out_dir, built_at)


def test_load_raw_ais_rows_reads_written_partitions(tmp_path: Path) -> None:
    _write_raw_ais_dataset(tmp_path)
    rows = load_raw_ais_rows(tmp_path)
    assert len(rows) == 5


def test_extract_static_data_parses_imo_and_draught(tmp_path: Path) -> None:
    _write_raw_ais_dataset(tmp_path)
    rows = load_raw_ais_rows(tmp_path)
    static_data = extract_static_data(rows)
    readings = static_data[MOZAH_MMSI]
    assert len(readings) == 2
    assert readings[0].draught_m == 12.0
    assert readings[1].draught_m == 8.0
    assert readings[0].imo == MOZAH_IMO


def test_build_position_samples_sorted_by_time(tmp_path: Path) -> None:
    _write_raw_ais_dataset(tmp_path)
    rows = load_raw_ais_rows(tmp_path)
    samples = build_position_samples(rows)
    assert len(samples[MOZAH_MMSI]) == 3
    timestamps = [s.timestamp for s in samples[MOZAH_MMSI]]
    assert timestamps == sorted(timestamps)


def test_match_lng_carriers_finds_real_registry_vessel(tmp_path: Path) -> None:
    _write_raw_ais_dataset(tmp_path)
    rows = load_raw_ais_rows(tmp_path)
    static_data = extract_static_data(rows)
    matched = match_lng_carriers(static_data, VesselRegistry())
    assert MOZAH_MMSI in matched
    assert matched[MOZAH_MMSI].name == "Mozah"


def test_load_alsi_vintages_by_terminal_maps_facility_to_terminal(tmp_path: Path) -> None:
    _write_alsi_vintage(tmp_path, "2024-03-01", send_out=250.0, built_at=T0)
    vintages = load_alsi_vintages_by_terminal(tmp_path)
    assert "Gate Rotterdam" in vintages
    assert vintages["Gate Rotterdam"][0].rows[0]["sendOut"] == 250.0


def test_run_pipeline_end_to_end_produces_one_backtest_fold(tmp_path: Path) -> None:
    raw_dir = tmp_path / "raw"
    alsi_dir = tmp_path / "alsi"
    _write_raw_ais_dataset(raw_dir)

    arrival_confirmed_at = T0 + DEFAULT_DWELL_THRESHOLD + timedelta(minutes=5)
    gas_day = arrival_confirmed_at.date().isoformat()
    _write_alsi_vintage(alsi_dir, gas_day, send_out=1000.0, built_at=T0)

    now = arrival_confirmed_at + timedelta(days=1)
    folds = run_pipeline(raw_dir, alsi_dir, now=now)

    assert len(folds) == 1
    fold = folds[0]
    assert fold.terminal == "Gate Rotterdam"
    assert fold.gas_day == gas_day
    assert fold.actual_gwh == 1000.0

    # Full laden (12.0m) to ballast (8.0m) delivery of Mozah's full 266,000 cbm
    # capacity, converted at the approximate energy density constant.
    expected_gwh = MOZAH_CAPACITY_CBM * APPROXIMATE_GWH_PER_CBM
    assert fold.predicted_gwh == pytest.approx(expected_gwh)


def test_run_pipeline_returns_no_folds_when_no_vessels_match(tmp_path: Path) -> None:
    raw_dir = tmp_path / "raw"
    alsi_dir = tmp_path / "alsi"

    # A vessel whose IMO is not in the reference registry produces no matches.
    line = _static_data_line(999999999, imo=1234567, draught=10.0, when=T0)
    messages = [parse_message(line)]
    rows = dedupe_rows([message_to_row(m) for m in messages if m is not None])  # type: ignore[arg-type]
    write_parquet(rows, raw_dir)

    folds = run_pipeline(raw_dir, alsi_dir, now=T0)
    assert folds == []
