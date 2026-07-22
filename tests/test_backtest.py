from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd
import pandera.pandas as pa
import pyarrow.parquet as pq
import pytest

from lng.nowcast.backtest import (
    AlsiVintage,
    build_metrics_rows,
    run_backtest,
    select_vintage_as_of,
    write_metrics_parquet,
)

TERMINAL = "Gate Rotterdam"


def _vintage(built_at: datetime, gas_day: str, send_out: float) -> AlsiVintage:
    return AlsiVintage(
        built_at=built_at,
        rows=[{"gasDayStart": gas_day, "sendOut": send_out}],
    )


def test_select_vintage_as_of_picks_latest_not_exceeding_as_of() -> None:
    vintages = [
        _vintage(datetime(2024, 3, 2, 19, 30), "2024-03-01", 100.0),
        _vintage(datetime(2024, 3, 3, 19, 30), "2024-03-01", 105.0),  # a correction
        _vintage(datetime(2024, 3, 10, 19, 30), "2024-03-01", 999.0),  # future, must not leak
    ]
    selected = select_vintage_as_of(vintages, as_of=datetime(2024, 3, 4, 0, 0))
    assert selected.built_at == datetime(2024, 3, 3, 19, 30)
    assert selected.rows[0]["sendOut"] == 105.0


def test_select_vintage_as_of_raises_when_none_available() -> None:
    vintages = [_vintage(datetime(2024, 3, 10, 19, 30), "2024-03-01", 100.0)]
    with pytest.raises(ValueError, match="no ALSI vintage available"):
        select_vintage_as_of(vintages, as_of=datetime(2024, 3, 1, 0, 0))


def test_run_backtest_never_selects_a_vintage_after_as_of() -> None:
    vintages_by_terminal = {
        TERMINAL: [
            _vintage(datetime(2024, 3, 2, 19, 30), "2024-03-01", 100.0),
            _vintage(datetime(2024, 3, 20, 19, 30), "2024-03-01", 999.0),  # future correction
        ]
    }
    predictions = [
        {
            "terminal": TERMINAL,
            "gas_day": "2024-03-01",
            "as_of": datetime(2024, 3, 5, 0, 0),
            "predicted_gwh": 98.0,
        }
    ]
    folds = run_backtest(predictions, vintages_by_terminal)
    assert len(folds) == 1
    fold = folds[0]
    # The no-lookahead guarantee: no fold's vintage may be built after its as_of date.
    assert fold.vintage_built_at <= fold.as_of
    assert fold.actual_gwh == 100.0  # not the future-corrected 999.0


def _make_bulk_predictions_and_vintages(
    n_days: int,
) -> tuple[list[dict[str, object]], dict[str, list[AlsiVintage]]]:
    predictions = []
    rows = []
    for day in range(n_days):
        gas_day = f"2024-03-{day + 1:02d}"
        rows.append({"gasDayStart": gas_day, "sendOut": 100.0 + day})
        predictions.append(
            {
                "terminal": TERMINAL,
                "gas_day": gas_day,
                "as_of": datetime(2024, 4, 1, 0, 0),
                "predicted_gwh": 100.0 + day + 2.0,
            }
        )
    vintage = AlsiVintage(built_at=datetime(2024, 3, 31, 19, 30), rows=rows)
    return predictions, {TERMINAL: [vintage]}


def test_backtest_meets_minimum_sample_size_threshold(tmp_path: Path) -> None:
    predictions, vintages_by_terminal = _make_bulk_predictions_and_vintages(n_days=25)
    folds = run_backtest(predictions, vintages_by_terminal)
    assert len(folds) >= 20

    metrics_path = write_metrics_parquet(folds, run_id="test-run", out_dir=tmp_path)
    table = pq.read_table(metrics_path)
    assert table.num_rows >= 20


def test_metrics_rows_include_mae_and_mape_columns_and_schema_is_valid(tmp_path: Path) -> None:
    predictions, vintages_by_terminal = _make_bulk_predictions_and_vintages(n_days=25)
    folds = run_backtest(predictions, vintages_by_terminal)
    rows = build_metrics_rows(folds, run_id="test-run")

    df = pd.DataFrame(rows)
    schema = pa.DataFrameSchema(
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
    schema.validate(df)

    # every fold's prediction was exactly +2.0 GWh over actual, so MAE == 2.0
    assert df["mae"].iloc[0] == pytest.approx(2.0)


def test_build_metrics_rows_raises_on_empty_folds() -> None:
    with pytest.raises(ValueError, match="zero backtest folds"):
        build_metrics_rows([], run_id="empty-run")
