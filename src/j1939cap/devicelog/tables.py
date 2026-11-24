"""Reading a published extract.

Two CSV files per log, `<name>_readings.csv` and `<name>_events.csv`, and one `MANIFEST.csv` per directory. The
reader is strict: the header must be the schema's, and every cell must be empty or fit the type of its column.
"""

from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

from . import schema

MANIFEST_NAME = "MANIFEST.csv"
READINGS_SUFFIX = "_readings.csv"
EVENTS_SUFFIX = "_events.csv"
TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


class TableError(Exception):
    """A file is not an extract: wrong header, or a cell that does not fit its column."""


class Reading(NamedTuple):
    time: datetime
    state: str
    protocol: str
    vin: str
    speed_kmh: float | None
    rpm: float | None
    distance_km: float | None
    engine_hours: float | None
    distance_group: int | None
    distance_source: int | None
    configured_source: int | None
    gps_valid: bool | None
    gps_speed_kmh: float | None
    sources: tuple[tuple[int, float | None], ...]
    uploaded: bool


class Event(NamedTuple):
    time: datetime
    event: str
    values: dict[str, str]


class DeviceLog(NamedTuple):
    name: str
    device_key: str
    first_time: datetime
    last_time: datetime
    readings: list[Reading]
    events: list[Event]


def parse_time(text: str) -> datetime:
    return datetime.strptime(text, TIME_FORMAT).replace(tzinfo=timezone.utc)


def format_time(moment: datetime) -> str:
    return moment.strftime(TIME_FORMAT)


def _rows(path: str | Path, header: tuple[str, ...]) -> list[list[str]]:
    with open(path, encoding="ascii", newline="") as fh:
        rows = list(csv.reader(fh))
    if not rows or tuple(rows[0]) != header:
        raise TableError(f"{path}: the header is not {','.join(header)}")
    if any(len(row) != len(header) for row in rows[1:]):
        raise TableError(f"{path}: a row does not have {len(header)} cells")
    return rows[1:]


def read_reading_rows(path: str | Path) -> list[dict[str, str]]:
    """The rows as text, checked against the schema."""
    out = []
    for n, row in enumerate(_rows(path, schema.READING_HEADER), start=2):
        cells = dict(zip(schema.READING_HEADER, row, strict=True))
        for column, kind in schema.READING_COLUMNS:
            if (cells[column] or column == "time") and not schema.fits(kind, cells[column]):
                raise TableError(f"{path}, line {n}: {column} does not hold a {kind}")
        out.append(cells)
    return out


def read_event_rows(path: str | Path) -> list[tuple[str, str, dict[str, str]]]:
    out = []
    for n, (time, event, text) in enumerate(_rows(path, schema.EVENT_HEADER), start=2):
        if not schema.fits(schema.TIME, time) or event not in schema.EVENTS:
            raise TableError(f"{path}, line {n}: not an event of the schema")
        try:
            values = schema.parse_event_values(text)
        except ValueError:
            raise TableError(f"{path}, line {n}: values are not key=value pairs") from None
        allowed = schema.EVENTS[event]
        if any(key not in allowed or not schema.fits(allowed[key], value) for key, value in values.items()):
            raise TableError(f"{path}, line {n}: a value does not belong to {event}")
        if text != schema.event_values(event, values):
            raise TableError(f"{path}, line {n}: values are not in the order of the schema")
        out.append((time, event, values))
    return out


def _number(text: str) -> float | None:
    return float(text) if text else None


def _byte(text: str) -> int | None:
    return int(text, 16) if text else None


def read_readings(path: str | Path) -> list[Reading]:
    out = []
    for cells in read_reading_rows(path):
        out.append(
            Reading(
                time=parse_time(cells["time"]),
                state=cells["state"],
                protocol=cells["protocol"],
                vin=cells["vin"],
                speed_kmh=_number(cells["speed_kmh"]),
                rpm=_number(cells["rpm"]),
                distance_km=_number(cells["distance_km"]),
                engine_hours=_number(cells["engine_hours"]),
                distance_group=int(cells["distance_group"]) if cells["distance_group"] else None,
                distance_source=_byte(cells["distance_source"]),
                configured_source=_byte(cells["configured_source"]),
                gps_valid=None if not cells["gps_valid"] else cells["gps_valid"] != "00",
                gps_speed_kmh=_number(cells["gps_speed_kmh"]),
                sources=tuple(schema.parse_pairs(cells["sources"])),
                uploaded=cells["uploaded"] == "1",
            )
        )
    return out


def read_events(path: str | Path) -> list[Event]:
    return [Event(parse_time(time), event, values) for time, event, values in read_event_rows(path)]


def read_manifest(directory: str | Path) -> list[dict[str, str]]:
    with open(Path(directory) / MANIFEST_NAME, encoding="ascii", newline="") as fh:
        return list(csv.DictReader(fh))


def log_names(directory: str | Path) -> list[str]:
    return sorted(path.name[: -len(READINGS_SUFFIX)] for path in Path(directory).glob(f"*{READINGS_SUFFIX}"))


def read_log(directory: str | Path, name: str, entry: dict[str, str] | None = None) -> DeviceLog:
    """One log of a directory. Its first and last time come from the manifest entry, which knows the whole log;
    without one they are the times of the first and last row."""
    directory = Path(directory)
    readings = read_readings(directory / f"{name}{READINGS_SUFFIX}")
    events = read_events(directory / f"{name}{EVENTS_SUFFIX}")
    if entry is not None:
        first, last = parse_time(entry["first_time"]), parse_time(entry["last_time"])
    else:
        times = [row.time for row in readings] + [row.time for row in events]
        if not times:
            raise TableError(f"{name}: an extract without rows and without a manifest entry has no time span")
        first, last = min(times), max(times)
    return DeviceLog(name, name.rsplit("_", 1)[0], first, last, readings, events)


def read_logs(directory: str | Path) -> list[DeviceLog]:
    directory = Path(directory)
    entries = {entry["log"]: entry for entry in read_manifest(directory)} if (directory / MANIFEST_NAME).is_file() else {}
    return [read_log(directory, name, entries.get(name)) for name in log_names(directory)]
