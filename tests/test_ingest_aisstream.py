from __future__ import annotations

import json
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from lng.ingest.aisstream import (
    SHIP_STATIC_DATA_REQUIRED_FIELDS,
    ConnectionClosed,
    SchemaDriftError,
    assert_ship_static_data_schema,
    dedupe_rows,
    message_to_row,
    parse_message,
    payload_hash,
    replay_file,
    stream_with_reconnect,
    write_parquet,
)

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "aisstream_sample.jsonl"


def _valid_line(mmsi: int = 111222333) -> str:
    return json.dumps(
        {
            "MessageType": "PositionReport",
            "Message": {"PositionReport": {"Latitude": 51.9, "Longitude": 4.1}},
            "MetaData": {
                "MMSI": mmsi,
                "ShipName": "TEST SHIP",
                "latitude": 51.9,
                "longitude": 4.1,
                "time_utc": "2024-03-01 00:00:07.000000000 +0000 UTC",
            },
        }
    )


def test_parse_message_valid() -> None:
    parsed = parse_message(_valid_line())
    assert parsed is not None
    assert parsed.mmsi == 111222333
    assert parsed.message_type == "PositionReport"
    assert parsed.received_at.year == 2024
    assert parsed.received_at.hour == 0


def test_parse_message_malformed_json_returns_none() -> None:
    assert parse_message("{not valid json") is None


def test_parse_message_missing_required_keys_returns_none() -> None:
    assert parse_message(json.dumps({"MessageType": "PositionReport"})) is None


def test_parse_message_missing_mmsi_returns_none() -> None:
    line = json.dumps(
        {
            "MessageType": "PositionReport",
            "Message": {},
            "MetaData": {"time_utc": "2024-03-01 00:00:00.000000000 +0000 UTC"},
        }
    )
    assert parse_message(line) is None


def test_dedupe_rows_drops_exact_repeats() -> None:
    line = _valid_line()
    parsed = parse_message(line)
    assert parsed is not None
    row = message_to_row(parsed)
    deduped = dedupe_rows([row, row, row])
    assert len(deduped) == 1


def test_dedupe_rows_keeps_distinct_messages() -> None:
    parsed_a = parse_message(_valid_line(mmsi=111))
    parsed_b = parse_message(_valid_line(mmsi=222))
    assert parsed_a is not None
    assert parsed_b is not None
    deduped = dedupe_rows([message_to_row(parsed_a), message_to_row(parsed_b)])
    assert len(deduped) == 2


def test_replay_file_skips_malformed_and_parses_fixture() -> None:
    messages, malformed = replay_file(FIXTURE_PATH)
    assert malformed >= 3
    assert len(messages) > 2900


def test_write_parquet_partitions_by_date_and_hour(tmp_path: Path) -> None:
    messages, _ = replay_file(FIXTURE_PATH)
    rows = dedupe_rows([message_to_row(m) for m in messages])
    row_count = write_parquet(rows, tmp_path)
    assert row_count == len(rows)

    partition_dirs = list((tmp_path / "source=aisstream").glob("ingest_date=*/hour=*"))
    assert len(partition_dirs) > 1
    for partition_dir in partition_dirs:
        table = pq.read_table(partition_dir / "data.parquet")
        assert table.num_rows > 0


def test_write_parquet_is_idempotent_across_repeated_calls(tmp_path: Path) -> None:
    messages, _ = replay_file(FIXTURE_PATH)
    rows = dedupe_rows([message_to_row(m) for m in messages])

    first_count = write_parquet(rows, tmp_path)
    second_count = write_parquet(rows, tmp_path)

    assert first_count == second_count

    total_rows = sum(pq.read_table(p).num_rows for p in tmp_path.rglob("data.parquet"))
    assert total_rows == first_count


def test_resumability_no_duplicates_after_simulated_restart(tmp_path: Path) -> None:
    """Simulates a process restart mid-stream: a first run writes only part of
    the fixture (as if the process was killed partway through), then the
    process is "restarted" and re-ingests the full fixture from scratch
    (AISStream has no resume/backfill, so a restart always reprocesses
    everything it can still see, per ADR 0001 Decision 3). No duplicate
    (MMSI, message_type, payload_hash) rows may exist in the final output.
    """
    messages, _ = replay_file(FIXTURE_PATH)
    rows = dedupe_rows([message_to_row(m) for m in messages])

    partial_rows = rows[: len(rows) // 2]
    write_parquet(partial_rows, tmp_path)  # first run, killed mid-stream

    write_parquet(rows, tmp_path)  # restart: full re-ingest from the fixture

    all_rows: list[dict[str, object]] = []
    for parquet_path in tmp_path.rglob("data.parquet"):
        all_rows.extend(pq.read_table(parquet_path).to_pylist())

    keys = [(row["mmsi"], row["message_type"], row["payload_hash"]) for row in all_rows]
    assert len(keys) == len(set(keys))
    assert len(all_rows) == len(rows)


def test_schema_drift_fixture_ship_static_data_has_required_fields() -> None:
    """Guards against AISStream schema drift (docs/risks.md): every
    ShipStaticData message in the recorded fixture must contain every field
    the ingestion pipeline commits to reading by name.
    """
    messages, _ = replay_file(FIXTURE_PATH)
    static_messages = [m for m in messages if m.message_type == "ShipStaticData"]
    assert len(static_messages) > 0

    raw_static_bodies = []
    for line in FIXTURE_PATH.read_text().splitlines():
        if not line.strip():
            continue
        try:
            envelope = json.loads(line)
        except json.JSONDecodeError:
            continue
        if envelope.get("MessageType") == "ShipStaticData":
            raw_static_bodies.append(envelope["Message"]["ShipStaticData"])

    assert len(raw_static_bodies) > 0
    for body in raw_static_bodies:
        assert_ship_static_data_schema(body)


def test_schema_drift_raises_loudly_on_missing_field() -> None:
    incomplete_body = {
        field: "placeholder"
        for field in SHIP_STATIC_DATA_REQUIRED_FIELDS
        if field != "MaximumStaticDraught"
    }
    with pytest.raises(SchemaDriftError):
        assert_ship_static_data_schema(incomplete_body)


def test_payload_hash_is_stable_for_identical_input() -> None:
    line = _valid_line()
    assert payload_hash(line) == payload_hash(line)


def test_payload_hash_differs_for_different_input() -> None:
    assert payload_hash(_valid_line(mmsi=1)) != payload_hash(_valid_line(mmsi=2))


def test_stream_with_reconnect_resumes_after_connection_closed() -> None:
    call_count = {"n": 0}

    def flaky_connect() -> list[str]:
        call_count["n"] += 1
        if call_count["n"] == 1:

            def gen() -> list[str]:
                yield "line-1"
                yield "line-2"
                raise ConnectionClosed("dropped")

            return gen()  # type: ignore[return-value]
        return iter(["line-3", "line-4"])

    lines = list(stream_with_reconnect(flaky_connect, max_reconnects=3))
    assert lines == ["line-1", "line-2", "line-3", "line-4"]
    assert call_count["n"] == 2


def test_stream_with_reconnect_gives_up_after_max_reconnects() -> None:
    def always_drops() -> list[str]:
        def gen() -> list[str]:
            yield "line-1"
            raise ConnectionClosed("dropped again")

        return gen()  # type: ignore[return-value]

    with pytest.raises(ConnectionClosed):
        list(stream_with_reconnect(always_drops, max_reconnects=2))
