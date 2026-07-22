from __future__ import annotations

import json
from pathlib import Path

from shapely.geometry import shape

from lng.events.geofence import (
    DEFAULT_GEOFENCE_PATH,
    TerminalGeofence,
    classify_point,
    load_terminal_geofences,
)

GATE_APPROACH = [
    (3.900, 51.850),
    (4.140, 51.850),
    (4.140, 52.040),
    (3.900, 52.040),
    (3.900, 51.850),
]
GATE_BERTH = [
    (4.000, 51.930),
    (4.040, 51.930),
    (4.040, 51.960),
    (4.000, 51.960),
    (4.000, 51.930),
]
GATE = TerminalGeofence(
    name="Gate Rotterdam", approach_polygon=GATE_APPROACH, berth_polygon=GATE_BERTH
)

ANTIMERIDIAN_APPROACH = [
    (179.0, 0.0),
    (-179.0, 0.0),
    (-179.0, 1.0),
    (179.0, 1.0),
    (179.0, 0.0),
]
ANTIMERIDIAN_BERTH = [
    (179.5, 0.3),
    (-179.5, 0.3),
    (-179.5, 0.7),
    (179.5, 0.7),
    (179.5, 0.3),
]
ANTIMERIDIAN = TerminalGeofence(
    name="TEST_ANTIMERIDIAN_FIXTURE",
    approach_polygon=ANTIMERIDIAN_APPROACH,
    berth_polygon=ANTIMERIDIAN_BERTH,
)


def test_point_inside_berth_polygon() -> None:
    assert classify_point(4.020, 51.945, GATE) == "berth"


def test_point_inside_approach_zone_outside_berth() -> None:
    assert classify_point(3.950, 51.900, GATE) == "approach"


def test_point_outside_both_zones() -> None:
    assert classify_point(5.500, 53.000, GATE) == "outside"


def test_point_exactly_on_berth_boundary_is_covered() -> None:
    # (4.000, 51.945) sits exactly on the western edge of the berth polygon.
    assert classify_point(4.000, 51.945, GATE) == "berth"


def test_point_exactly_on_approach_boundary_is_covered() -> None:
    # (3.900, 51.900) sits exactly on the western edge of the approach polygon.
    assert classify_point(3.900, 51.900, GATE) == "approach"


def test_antimeridian_crossing_berth_polygon_classifies_point_inside() -> None:
    # 179.9 degrees longitude sits inside the dateline-crossing berth polygon
    # (179.5 to -179.5, i.e. 179.5 to 180 to -179.5 the short way).
    assert classify_point(179.9, 0.5, ANTIMERIDIAN) == "berth"


def test_antimeridian_crossing_point_just_past_dateline_classifies_inside() -> None:
    assert classify_point(-179.9, 0.5, ANTIMERIDIAN) == "berth"


def test_antimeridian_point_outside_geofence_entirely() -> None:
    assert classify_point(170.0, 0.5, ANTIMERIDIAN) == "outside"


def test_point_in_approach_but_far_corner_of_gate_terminal() -> None:
    assert classify_point(3.905, 52.035, GATE) == "approach"


def test_load_terminal_geofences_returns_expected_terminals() -> None:
    geofences = load_terminal_geofences()
    names = {g.name for g in geofences}
    assert "Gate Rotterdam" in names
    assert "Zeebrugge" in names
    assert "TEST_ANTIMERIDIAN_FIXTURE" in names


def test_every_feature_in_geofence_file_is_a_valid_polygon() -> None:
    raw = json.loads(Path(DEFAULT_GEOFENCE_PATH).read_text())
    assert len(raw["features"]) > 0
    for feature in raw["features"]:
        geometry = shape(feature["geometry"])
        assert geometry.geom_type == "Polygon"
        assert geometry.is_valid
