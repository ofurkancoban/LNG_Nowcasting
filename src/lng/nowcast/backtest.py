"""Walk-forward backtest harness comparing the nowcast model against vintaged
GIE ALSI ground truth.

Per ADR 0001 Decision 4, GIE ALSI data is retroactively corrected over time,
so scoring a historical prediction against "current" ALSI values would leak
hindsight the model could never have had in real time. select_vintage_as_of
enforces that every fold is scored only against the ALSI vintage that
actually existed as of that prediction's "as of" date.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq


@dataclass(frozen=True)
class AlsiVintage:
    built_at: datetime
    rows: list[dict[str, Any]]


@dataclass(frozen=True)
class BacktestFold:
    terminal: str
    gas_day: str
    as_of: datetime
    predicted_gwh: float
    actual_gwh: float
    vintage_built_at: datetime


def select_vintage_as_of(vintages: list[AlsiVintage], as_of: datetime) -> AlsiVintage:
    """Returns the latest vintage with built_at <= as_of.

    Raises ValueError if no such vintage exists, rather than silently
    falling back to a future (hindsight-leaking) vintage.
    """
    eligible = [v for v in vintages if v.built_at <= as_of]
    if not eligible:
        raise ValueError(f"no ALSI vintage available at or before {as_of}")
    return max(eligible, key=lambda v: v.built_at)


def run_backtest(
    predictions: list[dict[str, Any]],
    vintages_by_terminal: dict[str, list[AlsiVintage]],
) -> list[BacktestFold]:
    """Scores each prediction against the ALSI vintage available as of its date.

    Each prediction dict must have "terminal", "gas_day", "as_of" (datetime),
    and "predicted_gwh" keys.
    """
    folds: list[BacktestFold] = []
    for prediction in predictions:
        terminal = prediction["terminal"]
        vintages = vintages_by_terminal[terminal]
        vintage = select_vintage_as_of(vintages, prediction["as_of"])

        actual_row = next(
            row for row in vintage.rows if row["gasDayStart"] == prediction["gas_day"]
        )
        if actual_row["sendOut"] is None:
            # GIE reported no data for this gas day (status "N"); skip rather
            # than scoring a prediction against a missing ground truth value.
            continue

        folds.append(
            BacktestFold(
                terminal=terminal,
                gas_day=prediction["gas_day"],
                as_of=prediction["as_of"],
                predicted_gwh=prediction["predicted_gwh"],
                actual_gwh=actual_row["sendOut"],
                vintage_built_at=vintage.built_at,
            )
        )
    return folds


def build_metrics_rows(folds: list[BacktestFold], run_id: str) -> list[dict[str, Any]]:
    """Builds one output row per terminal-day fold, with aggregate MAE/MAPE
    columns denormalized across every row for convenient schema validation.
    """
    n = len(folds)
    if n == 0:
        raise ValueError("cannot build metrics from zero backtest folds")

    abs_errors = [abs(fold.predicted_gwh - fold.actual_gwh) for fold in folds]
    mae = sum(abs_errors) / n

    pct_errors = [
        abs(fold.predicted_gwh - fold.actual_gwh) / fold.actual_gwh
        for fold in folds
        if fold.actual_gwh != 0
    ]
    mape = (sum(pct_errors) / len(pct_errors) * 100) if pct_errors else float("nan")

    rows = []
    for fold, abs_error in zip(folds, abs_errors, strict=True):
        rows.append(
            {
                "run_id": run_id,
                "terminal": fold.terminal,
                "gas_day": fold.gas_day,
                "predicted_gwh": fold.predicted_gwh,
                "actual_gwh": fold.actual_gwh,
                "abs_error_gwh": abs_error,
                "mae": mae,
                "mape": mape,
            }
        )
    return rows


def write_metrics_parquet(folds: list[BacktestFold], run_id: str, out_dir: Path) -> Path:
    rows = build_metrics_rows(folds, run_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"metrics_{run_id}.parquet"
    table = pa.Table.from_pylist(rows)
    pq.write_table(table, path)
    return path


def write_metrics_motherduck(
    folds: list[BacktestFold],
    run_id: str,
    motherduck_token: str,
    database: str = "lng_nowcasting",
    table: str = "backtest_metrics",
) -> int:
    """Appends this run's metrics rows to a MotherDuck table.

    Never overwrites prior runs: each call inserts new rows keyed by
    run_id, so the dashboard's live query can always read the latest state
    without any local Parquet file or rebuild step, per the decision to
    move the dashboard's data source to a hosted database (see
    docs/decisions/0001-architecture.md follow-up on live dashboard hosting).
    Creates the table on first use if it does not already exist.
    """
    import duckdb
    import pandas as pd

    rows = build_metrics_rows(folds, run_id)
    df = pd.DataFrame(rows)  # noqa: F841 -- referenced by name in the SQL replacement scan below

    con = duckdb.connect(f"md:{database}?motherduck_token={motherduck_token}")
    try:
        con.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {table} (
                run_id VARCHAR,
                terminal VARCHAR,
                gas_day VARCHAR,
                predicted_gwh DOUBLE,
                actual_gwh DOUBLE,
                abs_error_gwh DOUBLE,
                mae DOUBLE,
                mape DOUBLE,
                written_at TIMESTAMP DEFAULT current_timestamp
            )
            """
        )
        con.execute(
            f"""
            INSERT INTO {table}
                (run_id, terminal, gas_day, predicted_gwh, actual_gwh, abs_error_gwh, mae, mape)
            SELECT run_id, terminal, gas_day, predicted_gwh, actual_gwh, abs_error_gwh, mae, mape
            FROM df
            """
        )
    finally:
        con.close()

    return len(rows)
