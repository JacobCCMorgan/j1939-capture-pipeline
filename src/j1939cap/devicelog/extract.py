"""From a device log to the two tables that can be published.

A log is read line by line. A line that matches the cycle-start rule opens a reading; field rules fill its
columns until the next cycle starts, a later value replacing an earlier one, as the device itself retries a
reading it is not happy with. Event rules give one event row each. Whatever no rule asks for is never looked at.

A download can write the same stretch of a log twice. Once time steps back, lines that were already used are
passed over and counted, until the log is past the point it had reached.

Values are checked against their types on the way in. Then the two identifiers are replaced: the serial number
names the files through its pseudonym, and the VIN column gets the stand-in a capture of the same truck carries,
so the two can still be compared. Before anything counts as written the files are read back with the strict
reader, compared with what was meant, and searched for the serial number and the VINs. If any of that fails the
files are removed and nothing is returned.
"""

from __future__ import annotations

import csv
import hashlib
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from ..deid import rules as deid_rules
from ..deid.pseudonym import Pseudonymizer
from . import schema, tables
from .rules import Rules

MANIFEST_COLUMNS = (
    "log",
    "device_key",
    "vehicle_key",
    "first_time",
    "last_time",
    "lines",
    "lines_used",
    "lines_repeated",
    "readings",
    "events",
    "values_rejected",
    "readings_bytes",
    "readings_sha256",
    "events_bytes",
    "events_sha256",
)
SERIAL_SHARE = 0.9  # of all serial numbers found in a log, the share its own must have


class ExtractError(Exception):
    """The log cannot be turned into an extract with confidence; nothing was written."""


@dataclass
class RawExtract:
    """What the rules found, identifiers still in it. Never written anywhere."""

    serial: str
    first_time: datetime
    last_time: datetime
    lines: int
    lines_used: int
    lines_repeated: int
    readings: list[dict[str, str]]
    events: list[tuple[str, str, dict[str, str]]]
    rejected: Counter = field(default_factory=Counter)


def _typed(kind: str, raw: str, missing: frozenset[str]) -> str | None:
    """The value as it will be written, an empty string for "not available", None when it does not fit."""
    text = raw.strip()
    if kind == schema.PAIRS:
        return schema.pairs(text, missing)
    if text in missing:
        return ""
    if kind == schema.HEX_BYTE:
        text = text.upper()
    elif kind == schema.BOOLEAN:
        text = text.lower()
    elif kind == schema.VIN:
        return text  # rewritten later; its type is checked on the stand-in
    return text if schema.fits(kind, text) else None


def read_log(path: str | Path, rules: Rules) -> RawExtract:
    serials: Counter = Counter()
    rejected: Counter = Counter()
    readings: list[dict[str, str]] = []
    events: list[tuple[str, str, dict[str, str]]] = []
    current: dict[str, str] | None = None
    first_stamp = last_stamp = None
    lines = used = repeated = 0
    latest: datetime | None = None
    replay = False
    seen: set[str] = set()

    def moment_of(text: str) -> datetime:
        return datetime.strptime(text, rules.time_format).replace(tzinfo=timezone.utc)

    with open(path, encoding="utf-8", errors="replace") as fh:
        for text in fh:
            lines += 1
            for found in rules.serial.finditer(text):
                serials[found["serial"]] += 1
            line = rules.line.match(text.rstrip("\r\n"))
            if line is None:
                continue
            last_stamp = line["time"]
            first_stamp = first_stamp or last_stamp
            body = line["body"]
            if rules.any_rule.search(body) is None:
                continue
            try:
                moment = moment_of(last_stamp)
            except ValueError:
                continue
            if latest is not None and moment < latest:
                replay = True
            if replay:
                if text in seen:
                    repeated += 1
                    continue
                replay = moment <= latest
            seen.add(text)
            latest = moment if latest is None else max(latest, moment)
            stamp = tables.format_time(moment)
            used += 1
            if rules.cycle_start.search(body):
                current = {"time": stamp}
                readings.append(current)
            for rule in rules.fields:
                match = rule.pattern.search(body)
                if match is None:
                    continue
                if current is None:
                    rejected["before_first_cycle"] += 1
                    continue
                groups = match.groupdict()
                state = groups.get("state")
                if state and schema.fits(schema.WORD, state):
                    current.setdefault("state", state)
                kind = schema.READING_TYPES[rule.column]
                value = "1" if kind == schema.FLAG else _typed(kind, groups["value"], rules.missing)
                if value is None:
                    rejected[rule.column] += 1
                else:
                    current[rule.column] = value
            for rule in rules.events:
                match = rule.pattern.search(body)
                if match is None:
                    continue
                values = {}
                for key, raw in match.groupdict().items():
                    if raw is None:
                        continue
                    value = _typed(schema.EVENTS[rule.event][key], raw, frozenset())
                    if value:
                        values[key] = value
                    else:
                        rejected[f"{rule.event}.{key}"] += 1
                events.append((stamp, rule.event, values))

    name = Path(path).name
    if first_stamp is None or last_stamp is None:
        raise ExtractError(f"{name}: no line has the layout the rules describe")
    if not serials:
        raise ExtractError(f"{name}: no serial number found")
    serial, count = serials.most_common(1)[0]
    if count < SERIAL_SHARE * sum(serials.values()):
        raise ExtractError(f"{name}: more than one serial number, none of them clearly its own")
    try:
        first, last = moment_of(first_stamp), moment_of(last_stamp)
    except ValueError:
        raise ExtractError(f"{name}: the first or the last line carries no readable time") from None
    return RawExtract(serial, first, last, lines, used, repeated, [r for r in readings if len(r) > 1], events, rejected)


def _stand_in(raw: str, pseudonymizer: Pseudonymizer) -> tuple[str | None, str | None]:
    """Stand-in text and vehicle key for what the device read as the VIN; (None, None) when it cannot be written."""
    try:
        payload = raw.encode("ascii")
    except UnicodeEncodeError:
        return None, None
    kind, text = deid_rules.classify_identification(payload)
    if kind == deid_rules.NO_TEXT:
        return "", None
    key = pseudonymizer.vehicle(text)
    stand_in = deid_rules.rewrite_identification(payload, key).decode("ascii")
    if not schema.fits(schema.VIN, stand_in):
        return None, None
    return stand_in, key if kind != deid_rules.FRAGMENT else None


def _write(path: Path, header: tuple[str, ...], rows: list[list[str]]) -> bytes:
    with open(path, "w", encoding="ascii", newline="") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(header)
        writer.writerows(rows)
    return path.read_bytes()


def publish(raw: RawExtract, out_dir: str | Path, pseudonymizer: Pseudonymizer) -> dict:
    """Write `<device key>_<first time>_readings.csv` and `_events.csv` into `out_dir`; return the manifest entry."""
    device_key = pseudonymizer.device(raw.serial)
    rejected = Counter(raw.rejected)
    vehicle_keys: Counter = Counter()
    stand_ins: dict[str, tuple[str | None, str | None]] = {}
    secrets = {raw.serial}
    reading_rows = []
    for reading in raw.readings:
        cells = dict(reading)
        vin = cells.get("vin", "")
        if vin:
            if vin not in stand_ins:
                stand_ins[vin] = _stand_in(vin, pseudonymizer)
            stand_in, key = stand_ins[vin]
            secrets.add(vin)
            if stand_in is None:
                rejected["vin"] += 1
                stand_in = ""
            if key:
                vehicle_keys[key] += 1
            cells["vin"] = stand_in
        reading_rows.append([cells.get(column, "") for column in schema.READING_HEADER])
    event_rows = [[time, event, schema.event_values(event, values)] for time, event, values in raw.events]

    name = f"{device_key}_{raw.first_time:%Y%m%dT%H%M%SZ}"
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    readings_path, events_path = out_dir / f"{name}{tables.READINGS_SUFFIX}", out_dir / f"{name}{tables.EVENTS_SUFFIX}"
    try:
        readings_blob = _write(readings_path, schema.READING_HEADER, reading_rows)
        events_blob = _write(events_path, schema.EVENT_HEADER, event_rows)
        back = [[cells[column] for column in schema.READING_HEADER] for cells in tables.read_reading_rows(readings_path)]
        back_events = [[time, event, schema.event_values(event, values)] for time, event, values in tables.read_event_rows(events_path)]
        if back != reading_rows or back_events != event_rows:
            raise ExtractError("the extract does not read back the way it was written")
        published = (readings_blob + events_blob).decode("ascii").upper() + name.upper()
        if any(len(secret) >= 8 and secret.upper() in published for secret in secrets):
            raise ExtractError("an identifier of the device or the vehicle is still in the extract")
    except (ExtractError, tables.TableError, UnicodeError) as error:
        readings_path.unlink(missing_ok=True)
        events_path.unlink(missing_ok=True)
        raise ExtractError(f"{name}: {error}") from None

    keys = [key for key, _ in vehicle_keys.most_common()]
    return {
        "log": name,
        "device_key": device_key,
        "vehicle_key": keys[0] if keys else "",
        "vehicle_keys": keys,
        "first_time": tables.format_time(raw.first_time),
        "last_time": tables.format_time(raw.last_time),
        "lines": raw.lines,
        "lines_used": raw.lines_used,
        "lines_repeated": raw.lines_repeated,
        "readings": len(reading_rows),
        "events": len(event_rows),
        "values_rejected": sum(rejected.values()),
        "rejected": dict(sorted(rejected.items())),
        "readings_bytes": len(readings_blob),
        "readings_sha256": hashlib.sha256(readings_blob).hexdigest(),
        "events_bytes": len(events_blob),
        "events_sha256": hashlib.sha256(events_blob).hexdigest(),
    }


def extract_file(log_path: str | Path, rules: Rules, out_dir: str | Path, pseudonymizer: Pseudonymizer) -> dict:
    return publish(read_log(log_path, rules), out_dir, pseudonymizer)


def write_manifest(out_dir: str | Path, entries: list[dict]) -> Path:
    path = Path(out_dir) / tables.MANIFEST_NAME
    with open(path, "w", encoding="ascii", newline="") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(MANIFEST_COLUMNS)
        for entry in sorted(entries, key=lambda e: e["log"]):
            writer.writerow([entry[column] for column in MANIFEST_COLUMNS])
    return path
