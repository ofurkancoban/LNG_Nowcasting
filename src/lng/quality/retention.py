"""Local Parquet retention: find and delete files past the local buffer window.

Per docs/decisions/0001-architecture.md's dual-write addendum: every
ingestion path writes to both local Parquet (fast, network-independent) and
MotherDuck (the permanent, queryable copy). Local files are a rotating
buffer, not the permanent record, and can be safely deleted once they
exceed the retention window without losing any data — the MotherDuck copy
is authoritative.
"""

from __future__ import annotations

import time
from pathlib import Path

DEFAULT_MAX_AGE_SECONDS = 3 * 24 * 60 * 60  # 3 days


def find_stale_files(root: Path, max_age_seconds: float, now: float | None = None) -> list[Path]:
    """Returns every *.parquet file under root whose mtime exceeds max_age_seconds."""
    if not root.exists():
        return []
    reference_time = now if now is not None else time.time()
    stale = []
    for path in root.rglob("*.parquet"):
        if not path.is_file():
            continue
        age = reference_time - path.stat().st_mtime
        if age > max_age_seconds:
            stale.append(path)
    return stale


def delete_stale_files(paths: list[Path]) -> int:
    """Deletes the given files, then removes any directories left empty.

    Returns the number of files deleted. Directory removal only removes
    directories that became empty as a direct result of this cleanup, never
    directories that still contain other files.
    """
    parent_dirs: set[Path] = set()
    deleted = 0
    for path in paths:
        parent_dirs.add(path.parent)
        path.unlink()
        deleted += 1

    for directory in sorted(parent_dirs, key=lambda p: len(p.parts), reverse=True):
        try:
            directory.rmdir()
        except OSError:
            pass  # not empty, or already removed

    return deleted
