"""Cargo capacity lookup for identified LNG carriers.

M2 capacity comes directly from the curated registry
(data/reference/lng_carriers.csv). Estimating the volume of a partial cargo
from observed draught change is a nowcast-modeling concern and is out of
scope until M5.
"""

from __future__ import annotations

from lng.vessels.registry import VesselRegistry


def full_cargo_capacity_cbm(imo: int, registry: VesselRegistry) -> float | None:
    """Returns the vessel's full LNG cargo capacity in cubic meters, or None
    if the IMO number is not in the registry.
    """
    record = registry.lookup(imo)
    if record is None:
        return None
    return record.cargo_capacity_cbm
