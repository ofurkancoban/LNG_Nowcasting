"""CLI wrapper for lng.quality.retention: deletes stale local Parquet files.

Run on a schedule (see deploy/systemd/lng-cleanup-local-raw.timer) rather
than tied to any single ingestion process, so cleanup happens even if an
ingester is temporarily down.
"""

from __future__ import annotations

import argparse

from lng.quality.retention import delete_stale_files, find_stale_files


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Delete local raw/marts Parquet files older than the retention window."
    )
    parser.add_argument(
        "--root",
        type=str,
        required=True,
        help="Directory to scan recursively for *.parquet files (e.g. raw/ or marts/).",
    )
    parser.add_argument(
        "--max-age-days",
        type=float,
        default=3.0,
        help="Delete files whose mtime is older than this many days (default: 3).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List files that would be deleted without deleting them.",
    )
    args = parser.parse_args(argv)

    from pathlib import Path

    max_age_seconds = args.max_age_days * 24 * 60 * 60
    stale = find_stale_files(Path(args.root), max_age_seconds)

    if args.dry_run:
        for path in stale:
            print(f"would delete: {path}")
        print(f"dry_run=true would_delete={len(stale)}")
        return 0

    deleted = delete_stale_files(stale)
    print(f"deleted={deleted}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
