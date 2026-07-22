"""Ingests GIE ALSI daily facility reports as vintaged, immutable ground truth.

Per docs/data_sources.md, ALSI publishes new data once per calendar day
(19:30 CET, with a second pass at 23:00 CET for late reporters); polling more
often than once per day per facility can never return new information and is
blocked here in code, not merely documented. Per ADR 0001 Decision 4, every
ingestion run writes a new built_at-stamped vintage snapshot rather than
overwriting a previous one, because GIE allows SSOs/LSOs to retroactively
correct historical data.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx
import pyarrow as pa
import pyarrow.parquet as pq

BASE_URL = "https://alsi.gie.eu/api"
TEST_BASE_URL = "https://alsitest.gie.eu/api"

REQUIRED_FIELDS = ("gasDayStart", "inventory", "sendOut", "dtmi", "dtrs", "status")


class PollingRateLimitError(Exception):
    """Raised when a facility is polled more than once in the same calendar day."""


class AlsiPoller:
    """Enforces at most one poll per facility per calendar day.

    `clock` is injectable so tests can simulate crossing a day boundary
    without depending on wall-clock time.
    """

    def __init__(self, clock: Callable[[], date] | None = None) -> None:
        self._clock = clock or (lambda: datetime.now(UTC).date())
        self._last_polled: dict[str, date] = {}

    def poll(self, facility: str, fetcher: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        today = self._clock()
        if self._last_polled.get(facility) == today:
            raise PollingRateLimitError(
                f"facility {facility!r} already polled today ({today}); "
                "ALSI only publishes new data once per calendar day"
            )
        result = fetcher()
        self._last_polled[facility] = today
        return result


def rows_from_response(response: dict[str, Any]) -> list[dict[str, Any]]:
    """Flattens an ALSI /api response's `data` array into ingestion rows.

    Raises ValueError loudly if a required field is missing from any entry,
    rather than writing a partial/null row.
    """
    rows = []
    for entry in response["data"]:
        missing = [field for field in REQUIRED_FIELDS if field not in entry]
        if missing:
            raise ValueError(f"ALSI entry missing required fields: {missing}")
        rows.append(
            {
                "facility": entry.get("code"),
                "name": entry.get("name"),
                "gasDayStart": entry["gasDayStart"],
                "inventory": float(entry["inventory"]),
                "sendOut": float(entry["sendOut"]),
                "dtmi": float(entry["dtmi"]),
                "dtrs": float(entry["dtrs"]),
                "status": entry["status"],
            }
        )
    return rows


def fetch_all_pages(fetch_page: Callable[[int], dict[str, Any]]) -> list[dict[str, Any]]:
    """Loops over paginated ALSI responses using the `last_page` field.

    `fetch_page(page_number)` is injected so production code can back it
    with a real HTTP call and tests can back it with fixture data, per the
    project rule that no test may hit a live network.
    """
    all_rows: list[dict[str, Any]] = []
    page = 1
    while True:
        response = fetch_page(page)
        all_rows.extend(response["data"])
        if page >= response["last_page"]:
            break
        page += 1
    return all_rows


def make_httpx_page_fetcher(
    base_url: str, api_key: str, params: dict[str, Any]
) -> Callable[[int], dict[str, Any]]:
    """Builds a real page-fetching callable backed by an HTTP GET request.

    Not exercised by any test (that would require live network access);
    tests exercise fetch_all_pages() with a fake fetch_page instead.
    """

    def fetch_page(page: int) -> dict[str, Any]:
        response = httpx.get(
            base_url,
            params={**params, "page": page},
            headers={"x-key": api_key},
            timeout=30.0,
        )
        response.raise_for_status()
        result: dict[str, Any] = response.json()
        return result

    return fetch_page


def write_vintage_snapshot(rows: list[dict[str, Any]], out_dir: Path, built_at: datetime) -> Path:
    """Writes rows to a new built_at-stamped snapshot directory.

    Never overwrites a prior vintage: each call with a distinct built_at
    produces an independent, permanently queryable snapshot, per ADR 0001
    Decision 4. Raises if the target snapshot directory already exists,
    since re-using a built_at would silently merge into a prior vintage.
    """
    stamp = built_at.strftime("%Y%m%dT%H%M%SZ")
    snapshot_dir = out_dir / "source=alsi" / f"built_at={stamp}"
    snapshot_dir.mkdir(parents=True, exist_ok=False)
    table = pa.Table.from_pylist(rows)
    pq.write_table(table, snapshot_dir / "data.parquet")
    return snapshot_dir
