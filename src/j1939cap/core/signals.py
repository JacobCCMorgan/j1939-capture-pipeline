"""A small catalog of broadcast signals and how to turn their bytes into engineering units.

The catalog is hand-written and deliberately short: the five values an electronic logging device has to read
(vehicle speed, engine speed, distance, engine hours and, through the transport protocol, the VIN) plus fuel.
Every entry is a little-endian unsigned field on byte boundaries, which is all these parameters need.

J1939 reserves the top of each field's range. For a field of any width, a most significant byte of 0xFF means the
sender does not have the parameter (not available), 0xFE means it has a fault reading it (error) and 0xFB to 0xFD
are reserved. Those raw values must never be scaled into a number.
"""

from __future__ import annotations

from typing import NamedTuple

VALID = "valid"
ERROR = "error"
NOT_AVAILABLE = "not_available"
RESERVED = "reserved"


class SignalDef(NamedTuple):
    name: str
    pgn: int
    spn: int
    start: int  # zero-based index of the first byte
    length: int  # bytes
    scale: float
    offset: float
    unit: str


class Reading(NamedTuple):
    name: str
    value: float | None
    state: str


CATALOG: tuple[SignalDef, ...] = (
    SignalDef("engine_speed", 0xF004, 190, 3, 2, 0.125, 0.0, "rpm"),
    SignalDef("wheel_based_vehicle_speed", 0xFEF1, 84, 1, 2, 1 / 256, 0.0, "km/h"),
    SignalDef("total_vehicle_distance_hr", 0xFEC1, 917, 0, 4, 0.005, 0.0, "km"),
    SignalDef("trip_distance_hr", 0xFEC1, 918, 4, 4, 0.005, 0.0, "km"),
    SignalDef("trip_distance", 0xFEE0, 244, 0, 4, 0.125, 0.0, "km"),
    SignalDef("total_vehicle_distance", 0xFEE0, 245, 4, 4, 0.125, 0.0, "km"),
    SignalDef("engine_total_hours", 0xFEE5, 247, 0, 4, 0.05, 0.0, "h"),
    SignalDef("engine_trip_fuel", 0xFEE9, 182, 0, 4, 0.5, 0.0, "L"),
    SignalDef("engine_total_fuel_used", 0xFEE9, 250, 4, 4, 0.5, 0.0, "L"),
    SignalDef("engine_trip_fuel_hr", 0xFD09, 5053, 0, 4, 0.001, 0.0, "L"),
    SignalDef("engine_total_fuel_used_hr", 0xFD09, 5054, 4, 4, 0.001, 0.0, "L"),
    SignalDef("engine_fuel_rate", 0xFEF2, 183, 0, 2, 0.05, 0.0, "L/h"),
)

BY_PGN: dict[int, tuple[SignalDef, ...]] = {}
for _signal in CATALOG:
    BY_PGN[_signal.pgn] = BY_PGN.get(_signal.pgn, ()) + (_signal,)
BY_NAME: dict[str, SignalDef] = {s.name: s for s in CATALOG}


def raw_state(raw: int, length: int) -> str:
    top = raw >> (8 * (length - 1))
    if top == 0xFF:
        return NOT_AVAILABLE
    if top == 0xFE:
        return ERROR
    if top >= 0xFB:
        return RESERVED
    return VALID


def decode(pgn: int, data: bytes) -> list[Reading]:
    """Readings for every cataloged signal of this parameter group. A payload too short for a field skips it."""
    out: list[Reading] = []
    for signal in BY_PGN.get(pgn, ()):
        chunk = data[signal.start : signal.start + signal.length]
        if len(chunk) < signal.length:
            continue
        raw = int.from_bytes(chunk, "little")
        state = raw_state(raw, signal.length)
        value = raw * signal.scale + signal.offset if state == VALID else None
        out.append(Reading(signal.name, value, state))
    return out
