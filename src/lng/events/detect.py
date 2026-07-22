"""Arrival event detection: a dwell-time state machine over classified positions.

Per docs/risks.md, a vessel merely passing through or briefly loitering near
a terminal must not count as an arrival. A vessel is only confirmed as
"arrived" once it has been continuously classified as being in the berth
zone (with no intervening "approach" or "outside" observation) for at least
`dwell_threshold`. Missing samples (AIS coverage gaps, see docs/risks.md) do
not reset this: only an actual observation outside the berth zone resets the
dwell timer, so a gap between two berth observations is tolerated.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from lng.events.geofence import TerminalGeofence, classify_point

DEFAULT_DWELL_THRESHOLD = timedelta(hours=2)


@dataclass(frozen=True)
class PositionSample:
    timestamp: datetime
    longitude: float
    latitude: float


@dataclass(frozen=True)
class ArrivalEvent:
    terminal: str
    mmsi: int
    entered_berth_at: datetime
    confirmed_at: datetime


def detect_arrivals(
    mmsi: int,
    terminal: TerminalGeofence,
    samples: list[PositionSample],
    dwell_threshold: timedelta = DEFAULT_DWELL_THRESHOLD,
) -> list[ArrivalEvent]:
    """Returns one ArrivalEvent per continuous berth dwell period that meets
    the dwell threshold, in chronological order of the input samples.
    """
    events: list[ArrivalEvent] = []
    entered_berth_at: datetime | None = None
    confirmed = False

    for sample in samples:
        zone = classify_point(sample.longitude, sample.latitude, terminal)

        if zone == "berth":
            if entered_berth_at is None:
                entered_berth_at = sample.timestamp
                confirmed = False
            elif not confirmed and (sample.timestamp - entered_berth_at) >= dwell_threshold:
                events.append(
                    ArrivalEvent(
                        terminal=terminal.name,
                        mmsi=mmsi,
                        entered_berth_at=entered_berth_at,
                        confirmed_at=sample.timestamp,
                    )
                )
                confirmed = True
        else:
            entered_berth_at = None
            confirmed = False

    return events
