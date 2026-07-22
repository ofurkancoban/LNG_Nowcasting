"""Estimates delivered LNG cargo volume from an arrival event's draught change.

Simplification, flagged rather than hidden: this assumes cargo volume scales
linearly between a vessel's ballast (empty) and laden (full) draught, given
explicit ballast/laden draught reference points supplied by the caller.
data/reference/lng_carriers.csv (M2) does not yet carry ballast/laden
draught columns, so those must come from elsewhere until the reference
table is extended; this keeps M5 self-contained rather than silently
expanding M2's already-committed schema.

The energy-content conversion factor used to make cargo volume comparable to
GIE ALSI's GWh/d send-out figures is an approximate constant, not a
calibrated value, and is flagged as needing real calibration before this
model is trusted for anything beyond exercising the backtest harness.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

# Approximate LNG energy density: roughly 21-23 MJ/kg at ~450 kg/m3 gives on
# the order of 6 GWh per 1,000 cbm. This is a rough constant for exercising
# the pipeline end to end, not a calibrated figure.
APPROXIMATE_GWH_PER_CBM = 0.0060


def estimate_delivered_volume_cbm(
    capacity_cbm: float,
    draught_before_m: float,
    draught_after_m: float,
    laden_draught_m: float,
    ballast_draught_m: float,
) -> float:
    """Estimates cargo volume delivered from a draught change during an arrival.

    Fraction delivered is (draught_before - draught_after) / (laden - ballast),
    clamped to [0, 1] so noisy draught readings cannot produce a negative or
    over-100%-of-capacity estimate.
    """
    draught_range = laden_draught_m - ballast_draught_m
    if draught_range <= 0:
        raise ValueError("laden_draught_m must be greater than ballast_draught_m")

    fraction_delivered = (draught_before_m - draught_after_m) / draught_range
    fraction_delivered = max(0.0, min(1.0, fraction_delivered))
    return capacity_cbm * fraction_delivered


def cbm_to_gwh(volume_cbm: float) -> float:
    """Converts an LNG volume in cubic meters to an approximate energy content
    in GWh, using APPROXIMATE_GWH_PER_CBM.
    """
    return volume_cbm * APPROXIMATE_GWH_PER_CBM


def aggregate_daily_nowcast(
    deliveries: list[dict[str, Any]],
) -> dict[tuple[str, str], float]:
    """Sums delivered volume (in GWh-equivalent) per (terminal, gas_day).

    Each delivery dict must have "terminal", "gas_day", and "volume_cbm" keys.
    """
    totals: dict[tuple[str, str], float] = defaultdict(float)
    for delivery in deliveries:
        key = (delivery["terminal"], delivery["gas_day"])
        totals[key] += cbm_to_gwh(delivery["volume_cbm"])
    return dict(totals)
