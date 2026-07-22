"""Fails loudly (non-zero exit) if the dashboard's required marts data is
missing or malformed, per docs/milestones/M6.md's acceptance criteria.

Run before the Evidence.dev dashboard build (wired as dashboard/package.json's
"prebuild" script) so a missing or invalid backtest metrics artifact stops the
build instead of silently rendering an empty page.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pandera.pandas as pa

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKTEST_METRICS_GLOB = "marts/backtest/metrics_*.parquet"

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
    matches = sorted(REPO_ROOT.glob(BACKTEST_METRICS_GLOB))
    if not matches:
        print(
            f"ERROR: no backtest metrics files found matching {BACKTEST_METRICS_GLOB}. "
            "Run the M5 backtest harness (src/lng/nowcast/backtest.py) before building "
            "the dashboard.",
            file=sys.stderr,
        )
        return 1

    df = pd.concat([pd.read_parquet(path) for path in matches], ignore_index=True)

    try:
        METRICS_SCHEMA.validate(df)
    except pa.errors.SchemaError as exc:
        print(f"ERROR: backtest metrics failed schema validation: {exc}", file=sys.stderr)
        return 1

    print(f"OK: validated {len(matches)} metrics file(s), {len(df)} total rows.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
