from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pandera.pandas as pa
import pytest

from lng.vessels.capacity import full_cargo_capacity_cbm
from lng.vessels.registry import DEFAULT_REFERENCE_PATH, VesselRegistry

LABELED_FIXTURE_PATH = (
    Path(__file__).parent / "fixtures" / "vessel_registry_labeled_sample.json"
)


@pytest.fixture
def registry() -> VesselRegistry:
    return VesselRegistry()


@pytest.fixture
def labeled_sample() -> list[dict[str, object]]:
    return json.loads(LABELED_FIXTURE_PATH.read_text())


def test_registry_loads_reference_csv(registry: VesselRegistry) -> None:
    assert len(registry) == 326


def test_known_lng_carrier_is_identified(registry: VesselRegistry) -> None:
    assert registry.is_lng_carrier(9337755) is True


def test_unknown_imo_is_not_lng_carrier(registry: VesselRegistry) -> None:
    assert registry.is_lng_carrier(9999901) is False


def test_lookup_returns_none_for_unknown_imo(registry: VesselRegistry) -> None:
    assert registry.lookup(9999901) is None


def test_full_cargo_capacity_cbm_known_vessel(registry: VesselRegistry) -> None:
    assert full_cargo_capacity_cbm(9337755, registry) == 266253.0


def test_full_cargo_capacity_cbm_unknown_vessel(registry: VesselRegistry) -> None:
    assert full_cargo_capacity_cbm(9999901, registry) is None


def test_classification_precision_and_recall_meet_thresholds(
    registry: VesselRegistry, labeled_sample: list[dict[str, object]]
) -> None:
    true_positives = 0
    false_positives = 0
    false_negatives = 0

    for entry in labeled_sample:
        imo = int(entry["imo"])  # type: ignore[arg-type]
        expected = bool(entry["expected_lng_carrier"])
        predicted = registry.is_lng_carrier(imo)

        if predicted and expected:
            true_positives += 1
        elif predicted and not expected:
            false_positives += 1
        elif not predicted and expected:
            false_negatives += 1

    precision = true_positives / (true_positives + false_positives)
    recall = true_positives / (true_positives + false_negatives)

    assert precision >= 0.95
    assert recall >= 0.90


def test_reference_csv_schema_capacity_never_null() -> None:
    schema = pa.DataFrameSchema(
        {
            "imo": pa.Column(int, checks=pa.Check.greater_than(0), unique=True),
            "name": pa.Column(str, nullable=False),
            "cargo_capacity_cbm": pa.Column(
                float, checks=pa.Check.greater_than(0), nullable=False
            ),
            "build_year": pa.Column(int, checks=pa.Check.in_range(1950, 2100)),
            "propulsion_type": pa.Column(str, nullable=False),
            "source_url": pa.Column(str, nullable=False),
            "specs_verified": pa.Column(bool, nullable=False),
        }
    )
    df = pd.read_csv(DEFAULT_REFERENCE_PATH)
    df["cargo_capacity_cbm"] = df["cargo_capacity_cbm"].astype(float)
    schema.validate(df)


def test_unverified_vessel_has_specs_verified_false(registry: VesselRegistry) -> None:
    # Adam LNG (IMO 9501186) comes from the bulk SLNG compatible-vessels list,
    # added without individually verified capacity/build year.
    record = registry.lookup(9501186)
    assert record is not None
    assert record.specs_verified is False


def test_curated_vessel_has_specs_verified_true(registry: VesselRegistry) -> None:
    record = registry.lookup(9337755)  # Mozah, individually verified
    assert record is not None
    assert record.specs_verified is True
