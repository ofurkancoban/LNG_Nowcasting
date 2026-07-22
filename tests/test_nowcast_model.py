from __future__ import annotations

import pytest

from lng.nowcast.model import (
    aggregate_daily_nowcast,
    cbm_to_gwh,
    estimate_delivered_volume_cbm,
)


def test_full_delivery_from_laden_to_ballast() -> None:
    volume = estimate_delivered_volume_cbm(
        capacity_cbm=170000,
        draught_before_m=12.0,
        draught_after_m=8.0,
        laden_draught_m=12.0,
        ballast_draught_m=8.0,
    )
    assert volume == pytest.approx(170000.0)


def test_partial_delivery_is_proportional() -> None:
    volume = estimate_delivered_volume_cbm(
        capacity_cbm=100000,
        draught_before_m=12.0,
        draught_after_m=10.0,
        laden_draught_m=12.0,
        ballast_draught_m=8.0,
    )
    # (12 - 10) / (12 - 8) = 0.5
    assert volume == pytest.approx(50000.0)


def test_delivery_clamped_to_zero_when_draught_increases() -> None:
    volume = estimate_delivered_volume_cbm(
        capacity_cbm=100000,
        draught_before_m=8.0,
        draught_after_m=9.0,  # draught increased: noisy reading, not a real delivery
        laden_draught_m=12.0,
        ballast_draught_m=8.0,
    )
    assert volume == 0.0


def test_delivery_clamped_to_capacity_when_draught_change_exceeds_range() -> None:
    volume = estimate_delivered_volume_cbm(
        capacity_cbm=100000,
        draught_before_m=13.0,  # beyond laden_draught_m, noisy reading
        draught_after_m=7.0,  # below ballast_draught_m
        laden_draught_m=12.0,
        ballast_draught_m=8.0,
    )
    assert volume == 100000.0


def test_invalid_draught_range_raises() -> None:
    with pytest.raises(ValueError, match="laden_draught_m must be greater"):
        estimate_delivered_volume_cbm(
            capacity_cbm=100000,
            draught_before_m=10.0,
            draught_after_m=9.0,
            laden_draught_m=8.0,
            ballast_draught_m=8.0,
        )


def test_cbm_to_gwh_is_positive_and_scales_linearly() -> None:
    assert cbm_to_gwh(1000) == pytest.approx(cbm_to_gwh(500) * 2)
    assert cbm_to_gwh(1000) > 0


def test_aggregate_daily_nowcast_sums_per_terminal_and_day() -> None:
    deliveries = [
        {"terminal": "Gate Rotterdam", "gas_day": "2024-03-01", "volume_cbm": 100000},
        {"terminal": "Gate Rotterdam", "gas_day": "2024-03-01", "volume_cbm": 50000},
        {"terminal": "Gate Rotterdam", "gas_day": "2024-03-02", "volume_cbm": 20000},
        {"terminal": "Zeebrugge", "gas_day": "2024-03-01", "volume_cbm": 30000},
    ]
    totals = aggregate_daily_nowcast(deliveries)

    assert totals[("Gate Rotterdam", "2024-03-01")] == pytest.approx(cbm_to_gwh(150000))
    assert totals[("Gate Rotterdam", "2024-03-02")] == pytest.approx(cbm_to_gwh(20000))
    assert totals[("Zeebrugge", "2024-03-01")] == pytest.approx(cbm_to_gwh(30000))
