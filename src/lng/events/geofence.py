"""Two-stage terminal geofencing: a wide approach zone and a tight berth polygon.

Per docs/risks.md, a single geofence around a terminal risks classifying
vessels merely transiting a busy shipping lane as "arrivals". The two-stage
approach zone / berth polygon split, combined with a dwell-time threshold in
src/lng/events/detect.py, mitigates that.

Polygon coordinates that cross the antimeridian (longitude 180/-180) are
normalized before the point-in-polygon test: any polygon whose longitude
span exceeds 180 degrees is assumed to cross the dateline the "short way",
and both the polygon and the query point have negative longitudes shifted
by +360 before testing, per docs/milestones/M3.md.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from shapely.geometry import Point, Polygon, shape

DEFAULT_GEOFENCE_PATH = (
    Path(__file__).resolve().parents[3] / "data" / "reference" / "terminal_geofences.geojson"
)

Coordinate = tuple[float, float]


@dataclass(frozen=True)
class TerminalGeofence:
    name: str
    approach_polygon: list[Coordinate]
    berth_polygon: list[Coordinate]


def _crosses_antimeridian(coords: list[Coordinate]) -> bool:
    lons = [lon for lon, _lat in coords]
    return (max(lons) - min(lons)) > 180


def _normalized_polygon(coords: list[Coordinate]) -> tuple[Polygon, bool]:
    crosses = _crosses_antimeridian(coords)
    if not crosses:
        return Polygon(coords), False
    shifted = [(lon + 360 if lon < 0 else lon, lat) for lon, lat in coords]
    return Polygon(shifted), True


def point_in_ring(longitude: float, latitude: float, coords: list[Coordinate]) -> bool:
    """Point-in-polygon test that tolerates antimeridian-crossing rings.

    Uses `covers` rather than `contains` so points exactly on the boundary
    count as inside.
    """
    polygon, crosses = _normalized_polygon(coords)
    query_lon = longitude + 360 if (crosses and longitude < 0) else longitude
    return bool(polygon.covers(Point(query_lon, latitude)))


def classify_point(longitude: float, latitude: float, terminal: TerminalGeofence) -> str:
    """Returns "berth", "approach", or "outside" for a point against a terminal."""
    if point_in_ring(longitude, latitude, terminal.berth_polygon):
        return "berth"
    if point_in_ring(longitude, latitude, terminal.approach_polygon):
        return "approach"
    return "outside"


def load_terminal_geofences(path: Path = DEFAULT_GEOFENCE_PATH) -> list[TerminalGeofence]:
    """Loads terminal approach/berth polygons from a GeoJSON FeatureCollection.

    Each terminal must have exactly one "approach" zone feature and one
    "berth" zone feature, grouped by the "terminal" property.
    """
    raw = json.loads(path.read_text())
    by_terminal: dict[str, dict[str, list[Coordinate]]] = {}
    for feature in raw["features"]:
        # shape() validates the geometry is well-formed GeoJSON; raises on malformed input.
        geometry = shape(feature["geometry"])
        if not geometry.is_valid:
            raise ValueError(f"invalid polygon geometry for {feature['properties']}")
        terminal = feature["properties"]["terminal"]
        zone = feature["properties"]["zone"]
        coords = [(float(lon), float(lat)) for lon, lat in geometry.exterior.coords]
        by_terminal.setdefault(terminal, {})[zone] = coords

    geofences = []
    for terminal, zones in by_terminal.items():
        geofences.append(
            TerminalGeofence(
                name=terminal,
                approach_polygon=zones["approach"],
                berth_polygon=zones["berth"],
            )
        )
    return geofences
