"""Fails loudly (non-zero exit) if the dashboard's required marts data is
missing or malformed, per docs/milestones/M6.md's acceptance criteria.

Run before the Evidence.dev dashboard build (wired as dashboard/package.json's
"prebuild" script) so a missing or invalid backtest metrics table stops the
build. Zero rows is treated as a valid (if unfinished) state, not a failure,
since it just means no LNG carrier arrival has been matched yet.

Reads from MotherDuck (the dashboard's live data source, per the
docs/decisions/0001-architecture.md dashboard-hosting follow-up), not the
local marts/backtest/*.parquet files used before that migration.
"""

from __future__ import annotations

import os
import sys

import duckdb
import pandera.pandas as pa

DATABASE = "lng_nowcasting"
TABLE = "backtest_metrics"

METRICS_SCHEMA = pa.DataFrameSchema(
    {
        "run_id": pa.Column(str, nullable=False),
        "terminal": pa.Column(str, nullable=False),
        "gas_day": pa.Column(str, nullable=False),
        "predicted_gwh": pa.Column(float, nullable=False),
        "actual_gwh": pa.Column(float, nullable=False),
        "abs_error_gwh": pa.Column(float, checks=pa.Check.ge(0)),
        "mae": pa.Column(float, checks=pa.Check.ge(0)),
        "mape": pa.Column(float, checks=pa.Check.ge(0)),
    }
)


def main() -> int:
    token = os.environ.get("MOTHERDUCK_TOKEN")
    if not token:
        print("ERROR: MOTHERDUCK_TOKEN environment variable is not set.", file=sys.stderr)
        return 1

    con = duckdb.connect(f"md:{DATABASE}?motherduck_token={token}")
    try:
        tables = {row[0] for row in con.execute("SHOW TABLES").fetchall()}
        if TABLE not in tables:
            print(
                f"ERROR: table {TABLE!r} does not exist in MotherDuck database {DATABASE!r}. "
                "Run the orchestrator (src/lng/pipeline/orchestrate.py --motherduck) at least "
                "once before building the dashboard.",
                file=sys.stderr,
            )
            return 1

        df = con.execute(f"SELECT * FROM {TABLE}").df()  # noqa: S608 -- fixed identifiers, no user input
    finally:
        con.close()

    if len(df) == 0:
        print(
            f"WARNING: table {TABLE!r} exists but has zero rows (no matched LNG carrier "
            "arrival observed yet). The backtest accuracy page will render empty until a "
            "real fold is produced; the vessel tracker map is unaffected."
        )
        return 0

    try:
        METRICS_SCHEMA.validate(df)
    except pa.errors.SchemaError as exc:
        print(f"ERROR: backtest metrics failed schema validation: {exc}", file=sys.stderr)
        return 1

    print(f"OK: validated {TABLE!r}, {len(df)} total rows.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
