"""Ingests GIE ALSI daily facility reports as vintaged, immutable ground truth.

Per docs/data_sources.md, ALSI publishes new data once per calendar day
(19:30 CET, with a second pass at 23:00 CET for late reporters); polling more
often than once per day per facility can never return new information and is
blocked here in code, not merely documented. Per ADR 0001 Decision 4, every
ingestion run writes a new built_at-stamped vintage snapshot rather than
overwriting a previous one, because GIE allows SSOs/LSOs to retroactively
correct historical data.

Schema note: as of GIE API manual v009 (December 2023 / January 2024
changelog), `inventory` and `dtmi` changed from flat string fields to
objects with `lng` (10^3 m3 LNG) and `gwh` (energy units) sub-fields. This
project's earlier v007-based assumption of flat float fields was wrong;
verified against a real live API response on 2026-07-22 and corrected here.
"""

from __future__ import annotations

import argparse
import os
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx
import pyarrow as pa
import pyarrow.parquet as pq

BASE_URL = "https://alsi.gie.eu/api"
TEST_BASE_URL = "https://alsitest.gie.eu/api"

# Known facility identifiers for the terminals this project's geofences and
# FACILITY_TO_TERMINAL mapping (src/lng/pipeline/orchestrate.py) track,
# verified via a live GET /api/about?show=listing call on 2026-07-22. The
# EU-wide aggregate (--type eu) never returns per-facility rows, so any
# per-terminal backtest requires fetching these individually.
KNOWN_FACILITIES = {
    "Zeebrugge": {
        "country": "BE",
        "company": "21X000000001006T",
        "facility": "21W0000000001245",
    },
    "Krk": {
        "country": "HR",
        "company": "31X-LNG-HR-----7",
        "facility": "31W-0000-G-000-Z",
    },
    "Inkoo": {
        "country": "FI",
        "company": "66X000000000027Z",
        "facility": "21W000000000130A",
    },
    "Hamina": {
        "country": "FI",
        "company": "66X-00000000024H",
        "facility": "66W000000000001U",
    },
    "Fos Tonkin": {
        "country": "FR",
        "company": "21X0000000010679",
        "facility": "63W179356656691A",
    },
    "Montoir de Bretagne": {
        "country": "FR",
        "company": "21X0000000010679",
        "facility": "63W631527814486R",
    },
    "Dunkerque": {
        "country": "FR",
        "company": "21X000000001331I",
        "facility": "21W0000000000451",
    },
    "Fos Cavaou": {
        "country": "FR",
        "company": "21X000000001070K",
        "facility": "63W943693783886F",
    },
    "Wilhelmshaven": {
        "country": "DE",
        "company": "21X000000001403J",
        "facility": "21W000000000129W",
    },
    "Brunsbuettel": {
        "country": "DE",
        "company": "21X000000001403J",
        "facility": "37W000000000107A",
    },
    "Stade": {
        "country": "DE",
        "company": "21X000000001403J",
        "facility": "37W000000000110L",
    },
    "Wilhelmshaven 2": {
        "country": "DE",
        "company": "21X000000001403J",
        "facility": "37W000000000111J",
    },
    "Mukran": {
        "country": "DE",
        "company": "37X000000000265F",
        "facility": "37W000000000114D",
    },
    "Alexandroupolis": {
        "country": "GR",
        "company": "21X738265265081N",
        "facility": "21W0000000001318",
    },
    "Revythoussa": {
        "country": "GR",
        "company": "21X-GR-A-A0A0A-G",
        "facility": "21W000000000040B",
    },
    "OLT Toscana": {
        "country": "IT",
        "company": "21X000000001109G",
        "facility": "21W0000000000443",
    },
    "Piombino": {
        "country": "IT",
        "company": "59XFSRUITALIASTY",
        "facility": "59WFSRUGOLARTUNH",
    },
    "Ravenna": {
        "country": "IT",
        "company": "59XFSRUITALIASTY",
        "facility": "59WBWSINGAPORERX",
    },
    "Panigaglia": {
        "country": "IT",
        "company": "59XFSRUITALIASTY",
        "facility": "59W0000000000011",
    },
    "Rovigo": {
        "country": "IT",
        "company": "21X000000001360B",
        "facility": "21W000000000082W",
    },
    "Klaipeda": {
        "country": "LT",
        "company": "21X0000000013740",
        "facility": "21W0000000001253",
    },
    "EemsEnergy": {
        "country": "NL",
        "company": "52X000000000088H",
        "facility": "52W000000000001W",
    },
    "Gate Rotterdam": {
        "country": "NL",
        "company": "21X000000001063H",
        "facility": "21W0000000000079",
    },
    "Swinoujscie": {
        "country": "PL",
        "company": "21X-PL-A-A0A0A-B",
        "facility": "21W000000000096L",
    },
    "Sines": {
        "country": "PT",
        "company": "21X0000000013619",
        "facility": "16WTGNL01------O",
    },
    "Bilbao": {
        "country": "ES",
        "company": "21X000000001352A",
        "facility": "21W0000000000362",
    },
    "Barcelona": {
        "country": "ES",
        "company": "21X000000001254A",
        "facility": "21W000000000039X",
    },
    "Huelva": {
        "country": "ES",
        "company": "21X000000001254A",
        "facility": "21W0000000000370",
    },
    "Cartagena": {
        "country": "ES",
        "company": "21X000000001254A",
        "facility": "21W000000000038Z",
    },
    "El Musel": {
        "country": "ES",
        "company": "21X000000000134P",
        "facility": "21W0000000000346",
    },
    "Sagunto": {
        "country": "ES",
        "company": "18XTGPRS-12345-G",
        "facility": "21W0000000000354",
    },
    "Mugardos": {
        "country": "ES",
        "company": "18XRGNSA-12345-V",
        "facility": "21W0000000000338",
    },
}

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

    `inventory` and `dtmi` are nested {"lng": ..., "gwh": ...} objects in the
    real API (see module docstring); this extracts both units into separate
    flat columns rather than picking just one.

    Raises ValueError loudly if a required field is missing from any entry,
    rather than writing a partial/null row.
    """
    rows = []
    for entry in response["data"]:
        missing = [field for field in REQUIRED_FIELDS if field not in entry]
        if missing:
            raise ValueError(f"ALSI entry missing required fields: {missing}")

        inventory = entry["inventory"]
        dtmi = entry["dtmi"]
        if not isinstance(inventory, dict) or not isinstance(dtmi, dict):
            raise ValueError(
                "ALSI entry's inventory/dtmi fields are not the expected "
                "{'lng': ..., 'gwh': ...} object shape"
            )

        rows.append(
            {
                "facility": entry.get("code"),
                "name": entry.get("name"),
                "gasDayStart": entry["gasDayStart"],
                "inventory_lng": _parse_alsi_float(inventory["lng"]),
                "inventory_gwh": _parse_alsi_float(inventory["gwh"]),
                "sendOut": _parse_alsi_float(entry["sendOut"]),
                "dtmi_lng": _parse_alsi_float(dtmi["lng"]),
                "dtmi_gwh": _parse_alsi_float(dtmi["gwh"]),
                "dtrs": _parse_alsi_float(entry["dtrs"]),
                "status": entry["status"],
            }
        )
    return rows


def _parse_alsi_float(value: Any) -> float | None:
    """Parses an ALSI numeric field, treating "-" (GIE's placeholder for a
    missing/no-data gas day, status "N") as a real, expected absence rather
    than a parsing error.
    """
    if value == "-":
        return None
    return float(value)


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


def write_vintage_motherduck(
    rows: list[dict[str, Any]],
    built_at: datetime,
    motherduck_token: str,
    database: str = "lng_nowcasting",
    table: str = "raw_alsi",
) -> int:
    """Appends this vintage's ALSI rows to a MotherDuck table, mirroring the
    local Parquet vintage snapshot. This is the permanent, queryable copy;
    local vintage snapshots are a rotating buffer only (see
    scripts/cleanup_local_raw.py). Never overwrites a prior vintage: each
    row carries its own built_at, so retroactive ALSI corrections remain
    reconstructible per ADR 0001 Decision 4.
    """
    import duckdb
    import pandas as pd

    stamp = built_at.isoformat()
    rows_with_vintage = [{**row, "built_at": stamp} for row in rows]
    df = pd.DataFrame(rows_with_vintage)  # noqa: F841 -- used by name in the SQL scan below

    con = duckdb.connect(f"md:{database}?motherduck_token={motherduck_token}")
    try:
        con.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {table} (
                facility VARCHAR,
                name VARCHAR,
                gasDayStart VARCHAR,
                inventory_lng DOUBLE,
                inventory_gwh DOUBLE,
                sendOut DOUBLE,
                dtmi_lng DOUBLE,
                dtmi_gwh DOUBLE,
                dtrs DOUBLE,
                status VARCHAR,
                built_at VARCHAR
            )
            """
        )
        con.execute(f"INSERT INTO {table} SELECT * FROM df")  # noqa: S608
    finally:
        con.close()

    return len(rows)


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint: fetches one ALSI report and writes a new vintage snapshot.

    Reads the API key from the ALSI_API_KEY environment variable. Not
    exercised by any test (would require a live network call); wired into
    .github/workflows/ingest-alsi.yml for scheduled production use.
    """
    parser = argparse.ArgumentParser(
        description="Ingest a GIE ALSI report into a vintage snapshot."
    )
    parser.add_argument(
        "--type", choices=["eu", "ne", "ai"], help="ALSI aggregate report type parameter"
    )
    parser.add_argument(
        "--known-facilities",
        action="store_true",
        help="Fetch each terminal in KNOWN_FACILITIES individually instead of an aggregate.",
    )
    parser.add_argument("--out", required=True, type=Path, help="Output directory for snapshots")
    parser.add_argument(
        "--base-url", default=BASE_URL, help="ALSI API base URL (use TEST_BASE_URL to rehearse)"
    )
    parser.add_argument(
        "--from-date",
        dest="from_date",
        help="ALSI 'from' date filter (YYYY-MM-DD). Omitting pulls full history, which is slow.",
    )
    parser.add_argument("--to-date", dest="to_date", help="ALSI 'to' date filter (YYYY-MM-DD).")
    parser.add_argument(
        "--motherduck",
        action="store_true",
        help="Also dual-write raw rows to MotherDuck (reads MOTHERDUCK_TOKEN env var).",
    )
    args = parser.parse_args(argv)

    if not args.type and not args.known_facilities:
        parser.error("either --type or --known-facilities is required")

    api_key = os.environ.get("ALSI_API_KEY")
    if not api_key:
        parser.error("ALSI_API_KEY environment variable must be set")

    motherduck_token = None
    if args.motherduck:
        motherduck_token = os.environ.get("MOTHERDUCK_TOKEN")
        if not motherduck_token:
            parser.error("--motherduck requires the MOTHERDUCK_TOKEN environment variable")

    date_params: dict[str, Any] = {}
    if args.from_date:
        date_params["from"] = args.from_date
    if args.to_date:
        date_params["to"] = args.to_date

    rows: list[dict[str, Any]] = []
    if args.known_facilities:
        for params in KNOWN_FACILITIES.values():
            fetch_page = make_httpx_page_fetcher(
                args.base_url, api_key, {**params, **date_params, "size": 300}
            )
            raw_entries = fetch_all_pages(fetch_page)
            rows.extend(rows_from_response({"data": raw_entries}))
    else:
        fetch_page = make_httpx_page_fetcher(
            args.base_url, api_key, {"type": args.type, **date_params, "size": 300}
        )
        raw_entries = fetch_all_pages(fetch_page)
        rows = rows_from_response({"data": raw_entries})

    built_at = datetime.now(UTC)
    snapshot_dir = write_vintage_snapshot(rows, args.out, built_at)
    print(f"rows_written={len(rows)} snapshot={snapshot_dir}")

    if motherduck_token:
        n = write_vintage_motherduck(rows, built_at, motherduck_token)
        print(f"motherduck_rows_written={n}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
