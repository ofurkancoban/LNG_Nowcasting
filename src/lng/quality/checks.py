"""Data quality assertions run before trusting a pipeline stage's output.

Every check here raises DataQualityError loudly on failure rather than
logging a warning and continuing, per the project's "fail loudly" pattern
established in M1's schema-drift guard and M6's dashboard prebuild check.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import pandas as pd
import pandera.pandas as pa

ALSI_SCHEMA = pa.DataFrameSchema(
    {
        "facility": pa.Column(str, nullable=False),
        "name": pa.Column(str, nullable=False),
        "gasDayStart": pa.Column(str, nullable=False),
        "inventory_lng": pa.Column(float, nullable=False),
        "inventory_gwh": pa.Column(float, nullable=False),
        "sendOut": pa.Column(float, nullable=False),
        "dtmi_lng": pa.Column(float, nullable=False),
        "dtmi_gwh": pa.Column(float, nullable=False),
        "dtrs": pa.Column(float, nullable=False),
        "status": pa.Column(str, nullable=False),
    }
)


class DataQualityError(Exception):
    """Raised when ingested or built data fails a quality check."""


def check_ais_freshness(
    last_seen: dict[int, datetime],
    now: datetime,
    max_staleness: timedelta,
) -> list[int]:
    """Returns the MMSIs whose last observed position exceeds max_staleness.

    Does not raise itself; callers decide whether any staleness is
    acceptable via assert_ais_freshness, since a single stale vessel amid
    many fresh ones may be an expected AIS coverage gap (docs/risks.md)
    rather than a pipeline failure.
    """
    return [mmsi for mmsi, last in last_seen.items() if (now - last) > max_staleness]


def assert_ais_freshness(
    last_seen: dict[int, datetime],
    now: datetime,
    max_staleness: timedelta,
) -> None:
    """Raises DataQualityError if any tracked vessel exceeds max_staleness."""
    stale = check_ais_freshness(last_seen, now, max_staleness)
    if stale:
        raise DataQualityError(
            f"{len(stale)} vessel(s) exceed max staleness of {max_staleness}: {sorted(stale)}"
        )


def assert_alsi_schema(rows: list[dict[str, Any]]) -> None:
    """Raises DataQualityError if ingested ALSI rows fail schema validation."""
    df = pd.DataFrame(rows)
    try:
        ALSI_SCHEMA.validate(df)
    except pa.errors.SchemaError as exc:
        raise DataQualityError(f"ALSI rows failed schema validation: {exc}") from exc


def assert_marts_row_count(row_count: int, minimum: int) -> None:
    """Raises DataQualityError if a marts table has fewer than `minimum` rows."""
    if row_count < minimum:
        raise DataQualityError(
            f"marts row count {row_count} is below the minimum threshold of {minimum}"
        )
