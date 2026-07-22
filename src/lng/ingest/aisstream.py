"""Ingests AISStream.io messages into append-only, date/hour partitioned Parquet.

Raw AIS timestamp format follows AISStream's MetaData.time_utc convention:
"YYYY-MM-DD HH:MM:SS.nnnnnnnnn +0000 UTC". Only the leading 19 characters are
parsed; the rest is nanosecond precision and timezone label we do not need.

Schema note: the envelope's metadata key is `MetaData` (capital D), not
`Metadata` as this project originally assumed from the documentation prose.
Verified against real live traffic on 2026-07-22; the earlier assumption
caused every single message to be silently classified as malformed. Also
corrected: ShipStaticData's ship-type field is named `Type`, not `ShipType`.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import uuid
from collections.abc import AsyncIterator, Callable, Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import websockets

AISSTREAM_WS_URL = "wss://stream.aisstream.io/v0/stream"

# Rough bounding box covering the North Sea / Northwest European coast, where
# the terminals in data/reference/terminal_geofences.geojson are located.
# [[lat, lon], [lat, lon]] per AISStream's subscription format.
DEFAULT_BOUNDING_BOXES: list[list[list[float]]] = [[[48.0, -6.0], [62.0, 10.0]]]

logger = logging.getLogger(__name__)

REQUIRED_ENVELOPE_KEYS = ("MessageType", "Message", "MetaData")

# Fields the ingestion pipeline commits to reading by name from a ShipStaticData
# (AIS Type 5) message body, per docs/data_sources.md. VERIFIED against real
# live AISStream traffic on 2026-07-22. This list is the schema-drift contract:
# if AISStream renames or drops one of these fields, ingestion must fail loudly
# rather than silently writing nulls.
SHIP_STATIC_DATA_REQUIRED_FIELDS = (
    "ImoNumber",
    "CallSign",
    "Type",
    "Dimension",
    "MaximumStaticDraught",
    "Destination",
    "Eta",
)


class SchemaDriftError(Exception):
    """Raised when a ShipStaticData message is missing a field ingestion depends on."""


def assert_ship_static_data_schema(message: dict[str, Any]) -> None:
    """Fails loudly if a ShipStaticData message body is missing a required field.

    This is the concrete guard against the AISStream schema-drift risk
    documented in docs/risks.md: ingestion must never silently treat a renamed
    or dropped field as an absent value.
    """
    missing = [field for field in SHIP_STATIC_DATA_REQUIRED_FIELDS if field not in message]
    if missing:
        raise SchemaDriftError(f"ShipStaticData message missing expected fields: {missing}")


class ConnectionClosed(Exception):
    """Raised by a stream source when the underlying connection drops."""


@dataclass(frozen=True)
class ParsedMessage:
    mmsi: int
    message_type: str
    raw_line: str
    payload_hash: str
    received_at: datetime
    latitude: float | None
    longitude: float | None


def _parse_time_utc(value: str) -> datetime:
    prefix = value[:19]
    return datetime.strptime(prefix, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)


def payload_hash(raw_line: str) -> str:
    return hashlib.sha256(raw_line.encode("utf-8")).hexdigest()


def parse_message(raw_line: str) -> ParsedMessage | None:
    """Parses one raw AISStream JSON line. Returns None on any malformed input.

    Malformed inputs are logged and skipped rather than raising, since a single
    bad line must never take down ingestion of the rest of the stream.
    """
    try:
        envelope: Any = json.loads(raw_line)
    except json.JSONDecodeError:
        logger.warning("skipping malformed AIS line: invalid JSON")
        return None

    if not isinstance(envelope, dict):
        logger.warning("skipping malformed AIS line: envelope is not an object")
        return None

    if any(key not in envelope for key in REQUIRED_ENVELOPE_KEYS):
        logger.warning("skipping malformed AIS line: missing required envelope key")
        return None

    metadata = envelope["MetaData"]
    message_type = envelope["MessageType"]
    if not isinstance(metadata, dict) or not isinstance(message_type, str):
        logger.warning("skipping malformed AIS line: MetaData/MessageType wrong type")
        return None

    mmsi = metadata.get("MMSI")
    time_utc = metadata.get("time_utc")
    if not isinstance(mmsi, int) or not isinstance(time_utc, str):
        logger.warning("skipping malformed AIS line: missing MMSI or time_utc")
        return None

    try:
        received_at = _parse_time_utc(time_utc)
    except ValueError:
        logger.warning("skipping malformed AIS line: unparseable time_utc")
        return None

    latitude = metadata.get("latitude")
    longitude = metadata.get("longitude")
    latitude = latitude if isinstance(latitude, (int, float)) else None
    longitude = longitude if isinstance(longitude, (int, float)) else None

    return ParsedMessage(
        mmsi=mmsi,
        message_type=message_type,
        raw_line=raw_line,
        payload_hash=payload_hash(raw_line),
        received_at=received_at,
        latitude=float(latitude) if latitude is not None else None,
        longitude=float(longitude) if longitude is not None else None,
    )


def message_to_row(message: ParsedMessage) -> dict[str, Any]:
    return {
        "mmsi": message.mmsi,
        "message_type": message.message_type,
        "payload_hash": message.payload_hash,
        "ingest_date": message.received_at.date().isoformat(),
        "hour": message.received_at.hour,
        "received_at": message.received_at.isoformat(),
        "latitude": message.latitude,
        "longitude": message.longitude,
        "raw_json": message.raw_line,
    }


def dedupe_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drops rows with a repeated (mmsi, message_type, payload_hash) key.

    First occurrence wins; order is preserved.
    """
    seen: set[tuple[int, str, str]] = set()
    deduped: list[dict[str, Any]] = []
    for row in rows:
        key = (row["mmsi"], row["message_type"], row["payload_hash"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped


def write_parquet(rows: list[dict[str, Any]], out_dir: Path) -> int:
    """Writes rows partitioned by source/ingest_date/hour, per ADR 0001.

    Each call fully recomputes and overwrites the aisstream partitions under
    out_dir from the given rows, rather than appending, so repeated calls
    with the same input are idempotent (same row count every time).
    """
    source_dir = out_dir / "source=aisstream"
    if source_dir.exists():
        for path in sorted(source_dir.rglob("*.parquet"), reverse=True):
            path.unlink()
        for path in sorted(source_dir.rglob("*"), reverse=True):
            if path.is_dir():
                try:
                    path.rmdir()
                except OSError:
                    pass

    partitions: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for row in rows:
        key = (row["ingest_date"], row["hour"])
        partitions.setdefault(key, []).append(row)

    total_written = 0
    for (ingest_date, hour), partition_rows in partitions.items():
        partition_dir = source_dir / f"ingest_date={ingest_date}" / f"hour={hour:02d}"
        partition_dir.mkdir(parents=True, exist_ok=True)
        table = pa.Table.from_pylist(partition_rows)
        pq.write_table(table, partition_dir / "data.parquet")
        total_written += len(partition_rows)

    return total_written


def append_parquet_partition(rows: list[dict[str, Any]], out_dir: Path) -> int:
    """Appends rows as a new uniquely-named file per date/hour partition.

    Unlike write_parquet() (which recomputes and overwrites, appropriate for
    a full fixture replay per ADR 0001 Decision 3), this never deletes
    existing partition files: each call adds one new file, which is the
    safe pattern for continuous live-stream flushing where each flush's
    rows are disjoint in time from prior flushes.
    """
    partitions: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for row in rows:
        key = (row["ingest_date"], row["hour"])
        partitions.setdefault(key, []).append(row)

    total_written = 0
    for (ingest_date, hour), partition_rows in partitions.items():
        partition_dir = (
            out_dir / "source=aisstream" / f"ingest_date={ingest_date}" / f"hour={hour:02d}"
        )
        partition_dir.mkdir(parents=True, exist_ok=True)
        filename = f"part-{uuid.uuid4().hex}.parquet"
        table = pa.Table.from_pylist(partition_rows)
        pq.write_table(table, partition_dir / filename)
        total_written += len(partition_rows)

    return total_written


async def _live_message_lines(
    api_key: str, bounding_boxes: list[list[list[float]]]
) -> AsyncIterator[str]:
    """Yields raw text lines from a live AISStream subscription.

    Not exercised by any test: doing so would require a live network
    connection, which the project's test rules forbid. This exists for the
    production deployment described in deploy/README.md and
    deploy/systemd/lng-ingest-aisstream.service, and has not itself been
    run against the real AISStream service this session (see
    docs/data_sources.md's UNVERIFIED note on AISStream's exact schema).
    """
    try:
        async with websockets.connect(AISSTREAM_WS_URL, open_timeout=30) as connection:
            await connection.send(
                json.dumps({"APIKey": api_key, "BoundingBoxes": bounding_boxes})
            )
            async for message in connection:
                yield message if isinstance(message, str) else message.decode("utf-8")
    except (websockets.exceptions.WebSocketException, TimeoutError, OSError) as exc:
        raise ConnectionClosed(str(exc)) from exc


async def run_live_async(
    api_key: str,
    out_dir: Path,
    bounding_boxes: list[list[list[float]]] | None = None,
    flush_every: int = 500,
) -> None:
    """Continuously ingests a live AISStream subscription into raw Parquet.

    Reconnects on ConnectionClosed indefinitely (AISStream has no
    resume/backfill, per docs/data_sources.md, so a reconnect only resumes
    from whatever the service sends next). Flushes every `flush_every`
    messages using the append-only writer, never the overwrite-recompute one.
    """
    boxes = bounding_boxes or DEFAULT_BOUNDING_BOXES
    buffer: list[str] = []

    while True:
        try:
            async for line in _live_message_lines(api_key, boxes):
                buffer.append(line)
                if len(buffer) >= flush_every:
                    _flush_live_buffer(buffer, out_dir)
                    buffer = []
        except ConnectionClosed as exc:
            logger.warning("AIS live connection closed (%s), reconnecting in 5s", exc)
            await asyncio.sleep(5)
            continue


def _flush_live_buffer(lines: list[str], out_dir: Path) -> None:
    messages = []
    for line in lines:
        parsed = parse_message(line)
        if parsed is not None:
            messages.append(parsed)
    rows = dedupe_rows([message_to_row(message) for message in messages])
    if rows:
        append_parquet_partition(rows, out_dir)


def run_live(
    api_key: str,
    out_dir: Path,
    bounding_boxes: list[list[list[float]]] | None = None,
    flush_every: int = 500,
) -> None:
    """Synchronous entrypoint wrapping run_live_async, used by the CLI."""
    asyncio.run(run_live_async(api_key, out_dir, bounding_boxes, flush_every))


def stream_with_reconnect(
    connect: Callable[[], Iterable[str]],
    max_reconnects: int = 3,
) -> Iterator[str]:
    """Yields lines produced by connect(), reconnecting on ConnectionClosed.

    connect() is called again (up to max_reconnects times) whenever the
    iterable it returns raises ConnectionClosed mid-stream. Lines already
    yielded before a drop are not re-yielded, since connect() is expected to
    resume from wherever the underlying source currently is (AISStream has no
    replay capability, so a reconnect only ever gets new/future messages).
    """
    reconnects = 0
    while True:
        try:
            yield from connect()
            return
        except ConnectionClosed:
            reconnects += 1
            if reconnects > max_reconnects:
                raise
            logger.warning("AIS stream connection closed, reconnecting (attempt %d)", reconnects)
            continue


def replay_file(path: Path) -> tuple[list[ParsedMessage], int]:
    """Parses every line of a recorded fixture file.

    Returns the successfully parsed messages and a count of malformed lines
    skipped.
    """
    messages: list[ParsedMessage] = []
    malformed = 0
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        parsed = parse_message(line)
        if parsed is None:
            malformed += 1
            continue
        messages.append(parsed)
    return messages, malformed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Ingest AISStream.io messages, either replayed from a fixture or live."
    )
    parser.add_argument(
        "--replay", type=Path, help="Path to a recorded jsonl fixture to replay"
    )
    parser.add_argument(
        "--out", required=True, type=Path, help="Output directory for Parquet partitions"
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Connect to the live AISStream.io WebSocket instead of replaying a fixture. "
        "Reads the API key from the AISSTREAM_API_KEY environment variable.",
    )
    args = parser.parse_args(argv)

    if args.live:
        api_key = os.environ.get("AISSTREAM_API_KEY")
        if not api_key:
            parser.error("--live requires the AISSTREAM_API_KEY environment variable to be set")
        run_live(api_key, args.out)
        return 0

    if args.replay is None:
        parser.error("either --replay <fixture> or --live is required")

    messages, malformed = replay_file(args.replay)
    if malformed:
        logger.warning("skipped %d malformed lines from %s", malformed, args.replay)

    rows = [message_to_row(message) for message in messages]
    deduped = dedupe_rows(rows)
    row_count = write_parquet(deduped, args.out)
    print(f"rows_written={row_count}")
    return row_count


if __name__ == "__main__":
    main()
