from __future__ import annotations

from datetime import datetime, timedelta

from lng.events.detect import DEFAULT_DWELL_THRESHOLD, PositionSample, detect_arrivals
from lng.events.geofence import TerminalGeofence

GATE = TerminalGeofence(
    name="Gate Rotterdam",
    approach_polygon=[
        (3.900, 51.850),
        (4.140, 51.850),
        (4.140, 52.040),
        (3.900, 52.040),
        (3.900, 51.850),
    ],
    berth_polygon=[
        (4.000, 51.930),
        (4.040, 51.930),
        (4.040, 51.960),
        (4.000, 51.960),
        (4.000, 51.930),
    ],
)

BERTH_POINT = (4.020, 51.945)
APPROACH_POINT = (3.950, 51.900)
OUTSIDE_POINT = (5.500, 53.000)

T0 = datetime(2024, 3, 1, 0, 0, 0)


def test_vessel_dwells_past_threshold_confirms_arrival() -> None:
    samples = [
        PositionSample(T0, *BERTH_POINT),
        PositionSample(T0 + timedelta(hours=1), *BERTH_POINT),
        PositionSample(T0 + DEFAULT_DWELL_THRESHOLD, *BERTH_POINT),
    ]
    events = detect_arrivals(mmsi=123456789, terminal=GATE, samples=samples)
    assert len(events) == 1
    assert events[0].entered_berth_at == T0
    assert events[0].confirmed_at == T0 + DEFAULT_DWELL_THRESHOLD


def test_vessel_leaves_approach_before_dwell_threshold_no_arrival() -> None:
    samples = [
        PositionSample(T0, *BERTH_POINT),
        PositionSample(T0 + timedelta(minutes=30), *APPROACH_POINT),
        PositionSample(T0 + timedelta(hours=3), *APPROACH_POINT),
    ]
    events = detect_arrivals(mmsi=123456789, terminal=GATE, samples=samples)
    assert events == []


def test_vessel_never_enters_berth_no_arrival() -> None:
    samples = [
        PositionSample(T0, *OUTSIDE_POINT),
        PositionSample(T0 + timedelta(hours=1), *APPROACH_POINT),
    ]
    events = detect_arrivals(mmsi=123456789, terminal=GATE, samples=samples)
    assert events == []


def test_coverage_gap_during_dwell_period_is_tolerated() -> None:
    # Only two samples exist: entering berth, and a much later sample still in
    # berth. The multi-hour gap between them (an AIS coverage dropout) must
    # not reset the dwell timer; elapsed time is measured between the actual
    # observations received.
    samples = [
        PositionSample(T0, *BERTH_POINT),
        PositionSample(T0 + timedelta(hours=5), *BERTH_POINT),  # large gap, still berth
    ]
    events = detect_arrivals(mmsi=123456789, terminal=GATE, samples=samples)
    assert len(events) == 1
    assert events[0].entered_berth_at == T0


def test_arrival_confirmed_only_once_per_continuous_dwell() -> None:
    samples = [
        PositionSample(T0, *BERTH_POINT),
        PositionSample(T0 + DEFAULT_DWELL_THRESHOLD, *BERTH_POINT),
        PositionSample(T0 + DEFAULT_DWELL_THRESHOLD + timedelta(hours=1), *BERTH_POINT),
    ]
    events = detect_arrivals(mmsi=123456789, terminal=GATE, samples=samples)
    assert len(events) == 1


def test_leaving_and_returning_to_berth_produces_a_second_arrival_if_redwelled() -> None:
    samples = [
        PositionSample(T0, *BERTH_POINT),
        PositionSample(T0 + DEFAULT_DWELL_THRESHOLD, *BERTH_POINT),  # arrival 1
        PositionSample(  # departs
            T0 + DEFAULT_DWELL_THRESHOLD + timedelta(hours=1), *OUTSIDE_POINT
        ),
        PositionSample(T0 + timedelta(hours=10), *BERTH_POINT),  # returns
        PositionSample(  # arrival 2
            T0 + timedelta(hours=10) + DEFAULT_DWELL_THRESHOLD, *BERTH_POINT
        ),
    ]
    events = detect_arrivals(mmsi=123456789, terminal=GATE, samples=samples)
    assert len(events) == 2
