"""What an extract of a device log may contain.

The telematics device that records a capture also keeps a text log of its own work. About every thirty seconds
while the truck is moving it reads the engine data it reports to the fleet, and it writes
down what it got: speed, engine speed, distance, engine hours, the VIN, and which parameter group and source
address the distance came from. Next to that, the log notes when a capture was asked for, started and closed, and
when the device raised one of its own error codes.

The log itself is free text and stays private. An extract keeps two tables and nothing else:

- readings: one row per reading cycle, with the columns in `READING_COLUMNS`;
- events: one row per event named in `EVENTS`, with the values listed for it.

Every value has a type, and a value that does not fit its type is dropped and counted, never passed on. Text can
only enter through the VIN column, which is rewritten by the same rule as the VIN of a capture.
"""

from __future__ import annotations

import re

TIME = "time"  # UTC, to the second
WORD = "word"  # a state name or the like: letters only
CODE = "code"  # an error code: capital letters followed by digits
NUMBER = "number"
INTEGER = "integer"
HEX_BYTE = "hex_byte"
BOOLEAN = "boolean"
FLAG = "flag"  # 1 when the line was seen
VIN = "vin"  # a stand-in, see deid.rules.rewrite_identification
PAIRS = "pairs"  # address=value|address=value: the distance each source address reports

GRAMMAR = {
    TIME: re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z"),
    WORD: re.compile(r"[A-Za-z]{1,16}"),
    CODE: re.compile(r"[A-Z]{1,6}\d{1,4}"),
    NUMBER: re.compile(r"-?\d{1,9}(?:\.\d{1,3})?"),
    INTEGER: re.compile(r"\d{1,10}"),
    HEX_BYTE: re.compile(r"[0-9A-F]{2}"),
    BOOLEAN: re.compile(r"true|false"),
    FLAG: re.compile(r"1"),
    VIN: re.compile(r"[0-9A-Z?]{1,24}"),
    PAIRS: re.compile(r"[0-9A-F]{2}=(?:-?\d{1,9}(?:\.\d{1,3})?)?(?:\|[0-9A-F]{2}=(?:-?\d{1,9}(?:\.\d{1,3})?)?){0,15}"),
}

READING_COLUMNS = (
    ("time", TIME),
    ("state", WORD),
    ("protocol", HEX_BYTE),
    ("vin", VIN),
    ("speed_kmh", NUMBER),
    ("rpm", NUMBER),
    ("distance_km", NUMBER),
    ("engine_hours", NUMBER),
    ("distance_group", INTEGER),
    ("distance_source", HEX_BYTE),
    ("configured_source", HEX_BYTE),
    ("gps_valid", HEX_BYTE),
    ("gps_speed_kmh", NUMBER),
    ("sources", PAIRS),
    ("uploaded", FLAG),
)
READING_TYPES = dict(READING_COLUMNS)
READING_HEADER = tuple(name for name, _ in READING_COLUMNS)

EVENTS = {
    # a capture, in the order the device goes through it
    "capture_requested": {"seconds": INTEGER, "group": INTEGER},
    "capture_started": {"seconds": INTEGER},
    "capture_summary": {
        "seconds": INTEGER,
        "duration_ms": INTEGER,
        "written": INTEGER,
        "queue_dropped": INTEGER,
        "stop_ok": BOOLEAN,
        "drop_count": INTEGER,
    },
    "capture_uploaded": {},
    "capture_ended": {"group": INTEGER},
    # the reading loop
    "state_entered": {"state": WORD},
    "reading_retry": {"state": WORD, "attempt": INTEGER, "of": INTEGER},
    "engine_not_detected": {},
    # error codes the device raises
    "error_sent": {"code": CODE, "state": WORD, "gps_speed_kmh": NUMBER, "current": NUMBER, "previous": NUMBER, "difference": NUMBER},
    # the link to the server
    "acknowledgment_timeout": {},
    "server_reconnect": {"count": INTEGER},
}
EVENT_HEADER = ("time", "event", "values")


def fits(kind: str, text: str) -> bool:
    return GRAMMAR[kind].fullmatch(text) is not None


def pairs(raw: str, missing: frozenset[str]) -> str | None:
    """`17,512345.5,00,N/A` becomes `17=512345.5|00=`; None when the list is not address, value, address, value."""
    parts = [part.strip() for part in raw.split(",")] if raw.strip() else []
    if len(parts) % 2:
        return None
    out = []
    for address, value in zip(parts[::2], parts[1::2], strict=True):
        address = address.upper()
        value = "" if value in missing else value
        if not fits(HEX_BYTE, address) or (value and not fits(NUMBER, value)):
            return None
        out.append(f"{address}={value}")
    return "|".join(out)


def parse_pairs(text: str) -> list[tuple[int, float | None]]:
    if not text:
        return []
    out = []
    for item in text.split("|"):
        address, _, value = item.partition("=")
        out.append((int(address, 16), float(value) if value else None))
    return out


def event_values(event: str, values: dict[str, str]) -> str:
    """`key=value;key=value` in the order the event declares its values."""
    return ";".join(f"{key}={values[key]}" for key in EVENTS[event] if key in values)


def parse_event_values(text: str) -> dict[str, str]:
    return dict(item.split("=", 1) for item in text.split(";")) if text else {}
