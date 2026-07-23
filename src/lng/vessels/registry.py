"""Vessel registry: IMO-based LNG carrier identification.

Per ADR 0001 and docs/risks.md, LNG carrier identification is a curated
IMO-number allowlist lookup against data/reference/lng_carriers.csv, not an
AIS ship-type heuristic. AIS ship type codes 80-89 cover all tankers, not
just LNG carriers, and are insufficient on their own.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

DEFAULT_REFERENCE_PATH = (
    Path(__file__).resolve().parents[3] / "data" / "reference" / "lng_carriers.csv"
)


@dataclass(frozen=True)
class VesselRecord:
    imo: int
    name: str
    cargo_capacity_cbm: float
    build_year: int
    propulsion_type: str
    source_url: str
    specs_verified: bool = True
    """False for vessels added from a bulk name+IMO list (e.g. a terminal's
    "compatible vessels" list) where cargo_capacity_cbm/build_year could not
    be individually verified per ship and are placeholders. Consumers that
    compute delivered cargo volume (src/lng/pipeline/orchestrate.py) must
    check this flag and decline to estimate a volume for unverified vessels
    rather than silently using a placeholder capacity as if it were real.
    """


class VesselRegistry:
    """In-memory IMO -> VesselRecord lookup loaded from a reference CSV."""

    def __init__(self, reference_path: Path = DEFAULT_REFERENCE_PATH) -> None:
        self._by_imo: dict[int, VesselRecord] = {}
        with reference_path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                record = VesselRecord(
                    imo=int(row["imo"]),
                    name=row["name"],
                    cargo_capacity_cbm=float(row["cargo_capacity_cbm"]),
                    build_year=int(row["build_year"]),
                    propulsion_type=row["propulsion_type"],
                    source_url=row["source_url"],
                    specs_verified=row.get("specs_verified", "True").strip().lower() == "true",
                )
                self._by_imo[record.imo] = record

    def is_lng_carrier(self, imo: int) -> bool:
        return imo in self._by_imo

    def lookup(self, imo: int) -> VesselRecord | None:
        return self._by_imo.get(imo)

    def __len__(self) -> int:
        return len(self._by_imo)
