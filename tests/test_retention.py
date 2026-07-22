from __future__ import annotations

import os
import time
from pathlib import Path

from lng.quality.retention import delete_stale_files, find_stale_files

THREE_DAYS_SECONDS = 3 * 24 * 60 * 60


def _touch_with_age(path: Path, age_seconds: float, now: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"fake parquet content")
    mtime = now - age_seconds
    os.utime(path, (mtime, mtime))


def test_find_stale_files_returns_only_files_older_than_max_age(tmp_path: Path) -> None:
    now = time.time()
    fresh = tmp_path / "fresh.parquet"
    stale = tmp_path / "stale.parquet"
    _touch_with_age(fresh, age_seconds=60, now=now)  # 1 minute old
    _touch_with_age(stale, age_seconds=THREE_DAYS_SECONDS + 3600, now=now)  # just over 3 days

    result = find_stale_files(tmp_path, max_age_seconds=THREE_DAYS_SECONDS, now=now)

    assert result == [stale]


def test_find_stale_files_ignores_non_parquet_files(tmp_path: Path) -> None:
    now = time.time()
    stale_txt = tmp_path / "stale.txt"
    _touch_with_age(stale_txt, age_seconds=THREE_DAYS_SECONDS + 3600, now=now)

    result = find_stale_files(tmp_path, max_age_seconds=THREE_DAYS_SECONDS, now=now)

    assert result == []


def test_find_stale_files_returns_empty_list_for_missing_root(tmp_path: Path) -> None:
    result = find_stale_files(tmp_path / "does_not_exist", max_age_seconds=THREE_DAYS_SECONDS)
    assert result == []


def test_find_stale_files_recurses_into_partitioned_subdirectories(tmp_path: Path) -> None:
    now = time.time()
    nested = tmp_path / "source=aisstream" / "ingest_date=2026-07-01" / "hour=00" / "part.parquet"
    _touch_with_age(nested, age_seconds=THREE_DAYS_SECONDS + 3600, now=now)

    result = find_stale_files(tmp_path, max_age_seconds=THREE_DAYS_SECONDS, now=now)

    assert result == [nested]


def test_delete_stale_files_removes_files_and_returns_count(tmp_path: Path) -> None:
    now = time.time()
    stale_1 = tmp_path / "a.parquet"
    stale_2 = tmp_path / "b.parquet"
    _touch_with_age(stale_1, age_seconds=THREE_DAYS_SECONDS + 3600, now=now)
    _touch_with_age(stale_2, age_seconds=THREE_DAYS_SECONDS + 3600, now=now)

    deleted = delete_stale_files([stale_1, stale_2])

    assert deleted == 2
    assert not stale_1.exists()
    assert not stale_2.exists()


def test_delete_stale_files_removes_empty_parent_directories(tmp_path: Path) -> None:
    now = time.time()
    nested = tmp_path / "source=aisstream" / "ingest_date=2026-07-01" / "hour=00" / "part.parquet"
    _touch_with_age(nested, age_seconds=THREE_DAYS_SECONDS + 3600, now=now)

    delete_stale_files([nested])

    assert not nested.parent.exists()


def test_delete_stale_files_keeps_directory_with_remaining_fresh_file(tmp_path: Path) -> None:
    now = time.time()
    partition_dir = tmp_path / "source=aisstream" / "ingest_date=2026-07-01" / "hour=00"
    stale = partition_dir / "old.parquet"
    fresh = partition_dir / "new.parquet"
    _touch_with_age(stale, age_seconds=THREE_DAYS_SECONDS + 3600, now=now)
    _touch_with_age(fresh, age_seconds=60, now=now)

    delete_stale_files([stale])

    assert not stale.exists()
    assert fresh.exists()
    assert partition_dir.exists()
