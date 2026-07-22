from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from lng.quality.checks import (
    DataQualityError,
    assert_ais_freshness,
    assert_alsi_schema,
    assert_marts_row_count,
    check_ais_freshness,
)

NOW = datetime(2024, 3, 1, 12, 0, 0)
MAX_STALENESS = timedelta(hours=6)


def test_check_ais_freshness_returns_only_stale_vessels() -> None:
    last_seen = {
        111: NOW - timedelta(hours=1),  # fresh
        222: NOW - timedelta(hours=10),  # stale
    }
    stale = check_ais_freshness(last_seen, NOW, MAX_STALENESS)
    assert stale == [222]


def test_assert_ais_freshness_passes_when_all_fresh() -> None:
    last_seen = {111: NOW - timedelta(hours=1), 222: NOW - timedelta(minutes=30)}
    assert_ais_freshness(last_seen, NOW, MAX_STALENESS)  # must not raise


def test_assert_ais_freshness_raises_on_stale_vessel() -> None:
    last_seen = {111: NOW - timedelta(hours=1), 222: NOW - timedelta(hours=48)}
    with pytest.raises(DataQualityError, match="exceed max staleness"):
        assert_ais_freshness(last_seen, NOW, MAX_STALENESS)


def _valid_alsi_row() -> dict[str, object]:
    return {
        "facility": "21Z0000000000082X",
        "name": "Gate terminal",
        "gasDayStart": "2024-03-01",
        "inventory": 145.32,
        "sendOut": 210.5,
        "dtmi": 180.0,
        "dtrs": 300.0,
        "status": "C",
    }


def test_assert_alsi_schema_passes_on_valid_rows() -> None:
    assert_alsi_schema([_valid_alsi_row()])  # must not raise


def test_assert_alsi_schema_raises_on_malformed_row() -> None:
    malformed = dict(_valid_alsi_row())
    del malformed["dtmi"]
    with pytest.raises(DataQualityError, match="failed schema validation"):
        assert_alsi_schema([malformed])


def test_assert_marts_row_count_passes_when_at_or_above_minimum() -> None:
    assert_marts_row_count(20, minimum=20)  # must not raise


def test_assert_marts_row_count_raises_when_below_minimum() -> None:
    with pytest.raises(DataQualityError, match="below the minimum threshold"):
        assert_marts_row_count(5, minimum=20)
