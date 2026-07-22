from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import pandera.pandas as pa
import pyarrow.parquet as pq
import pytest

from lng.ingest.alsi import (
    AlsiPoller,
    PollingRateLimitError,
    fetch_all_pages,
    rows_from_response,
    write_vintage_snapshot,
)

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "alsi_sample_response.json"


@pytest.fixture
def fixture_response() -> dict[str, object]:
    return json.loads(FIXTURE_PATH.read_text())


def test_rows_from_response_parses_fixture(fixture_response: dict[str, object]) -> None:
    rows = rows_from_response(fixture_response)
    assert len(rows) == 2
    assert rows[0]["facility"] == "21Z0000000000082X"
    assert rows[0]["gasDayStart"] == "2024-03-01"
    assert rows[0]["inventory"] == 145.32
    assert rows[0]["status"] == "C"


def test_rows_from_response_raises_on_missing_field() -> None:
    broken = {
        "last_page": 1,
        "data": [
            {
                "name": "Broken terminal",
                "code": "21Z0000000000000Z",
                "gasDayStart": "2024-03-01",
                "inventory": 10.0,
                "sendOut": 5.0,
                # dtmi and dtrs and status intentionally missing
            }
        ],
    }
    with pytest.raises(ValueError, match="missing required fields"):
        rows_from_response(broken)


def test_fetch_all_pages_loops_over_multiple_pages() -> None:
    page_1 = {"last_page": 2, "data": [{"facility": "A"}]}
    page_2 = {"last_page": 2, "data": [{"facility": "B"}]}

    def fetch_page(page: int) -> dict[str, object]:
        return page_1 if page == 1 else page_2

    rows = fetch_all_pages(fetch_page)
    assert rows == [{"facility": "A"}, {"facility": "B"}]


def test_poller_blocks_second_poll_same_day_same_facility() -> None:
    poller = AlsiPoller(clock=lambda: date(2024, 3, 1))
    call_count = {"n": 0}

    def fetcher() -> dict[str, object]:
        call_count["n"] += 1
        return {"ok": True}

    poller.poll("21Z0000000000082X", fetcher)
    with pytest.raises(PollingRateLimitError):
        poller.poll("21Z0000000000082X", fetcher)
    assert call_count["n"] == 1


def test_poller_allows_different_facility_same_day() -> None:
    poller = AlsiPoller(clock=lambda: date(2024, 3, 1))
    poller.poll("facility-a", lambda: {"ok": True})
    poller.poll("facility-b", lambda: {"ok": True})  # must not raise


def test_poller_allows_same_facility_next_calendar_day() -> None:
    days = iter([date(2024, 3, 1), date(2024, 3, 2)])
    poller = AlsiPoller(clock=lambda: next(days))
    poller.poll("21Z0000000000082X", lambda: {"ok": True})
    poller.poll("21Z0000000000082X", lambda: {"ok": True})  # must not raise, new day


def test_write_vintage_snapshot_never_overwrites_and_both_stay_queryable(
    tmp_path: Path, fixture_response: dict[str, object]
) -> None:
    rows_v1 = rows_from_response(fixture_response)

    revised_response = json.loads(FIXTURE_PATH.read_text())
    revised_response["data"][0]["inventory"] = 999.0  # simulates a retroactive correction
    rows_v2 = rows_from_response(revised_response)

    built_at_1 = datetime(2024, 3, 2, 19, 30, 0)
    built_at_2 = datetime(2024, 3, 3, 19, 30, 0)

    snapshot_1 = write_vintage_snapshot(rows_v1, tmp_path, built_at_1)
    snapshot_2 = write_vintage_snapshot(rows_v2, tmp_path, built_at_2)

    assert snapshot_1 != snapshot_2
    assert snapshot_1.exists()
    assert snapshot_2.exists()

    table_1 = pq.read_table(snapshot_1 / "data.parquet")
    table_2 = pq.read_table(snapshot_2 / "data.parquet")

    inventory_1 = table_1.to_pylist()[0]["inventory"]
    inventory_2 = table_2.to_pylist()[0]["inventory"]

    assert inventory_1 == 145.32
    assert inventory_2 == 999.0


def test_write_vintage_snapshot_raises_if_built_at_reused(
    tmp_path: Path, fixture_response: dict[str, object]
) -> None:
    rows = rows_from_response(fixture_response)
    built_at = datetime(2024, 3, 2, 19, 30, 0)
    write_vintage_snapshot(rows, tmp_path, built_at)
    with pytest.raises(FileExistsError):
        write_vintage_snapshot(rows, tmp_path, built_at)


def test_ingested_rows_schema(fixture_response: dict[str, object]) -> None:
    rows = rows_from_response(fixture_response)
    df = pd.DataFrame(rows)

    schema = pa.DataFrameSchema(
        {
            "facility": pa.Column(str, nullable=False),
            "name": pa.Column(str, nullable=False),
            "gasDayStart": pa.Column(str, nullable=False),
            "inventory": pa.Column(float, nullable=False),
            "sendOut": pa.Column(float, nullable=False),
            "dtmi": pa.Column(float, nullable=False),
            "dtrs": pa.Column(float, nullable=False),
            "status": pa.Column(str, nullable=False),
        }
    )
    schema.validate(df)
