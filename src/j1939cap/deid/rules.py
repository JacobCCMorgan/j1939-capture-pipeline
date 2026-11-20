"""What may leave with a frame.

Every frame keeps its timestamp and its identifier. Its data bytes are published only when the parameter group is
on a short reviewed list of numeric signal groups: engine, transmission, brakes, aftertreatment, electrical,
ambient, distance, hours, fuel, time and dash groups whose eight bytes are scaled numbers or switch states, plus
the diagnostic messages with their trouble codes. A group that is not on the list has its data replaced by 0xFF,
which is how J1939 writes "not available". That covers the manufacturers' proprietary groups, the groups that carry
free text (component and software identification) and any group nobody has reviewed yet.

Four groups get their own treatment:

- vehicle identification: the VIN is rewritten (see `rewrite_vin`);
- vehicle position: latitude and longitude are moved to a 0.05 degree grid;
- address claimed: the identity number inside the NAME is cleared, manufacturer and function stay;
- request: only the three bytes that name the requested group are kept. The recorder stores eight bytes for every
  frame, and after a short frame the rest are whatever was in its buffer.
"""

from __future__ import annotations

from ..core import vin as vin_rules
from ..core.ids import PGN_ADDRESS_CLAIMED, PGN_REQUEST, PGN_VEHICLE_IDENTIFICATION

KEEP = "kept"
MASK = "masked"
VIN = "vin_rewritten"
POSITION = "position_coarsened"
NAME = "identity_cleared"
REQUEST = "request_trimmed"

PGN_VEHICLE_POSITION = 0xFEF3

KEPT_GROUPS = frozenset(
    int(group, 16)
    for group in """
    0000 0100 0400 D000 D200 E000 E800
    F000 F001 F002 F003 F004 F005 F006 F007 F009 F00A F00E F00F F01A
    FD09 FD3E FD7B FD7C FD92 FD94 FDA4 FDB2 FDB3 FDB4 FDC4 FDCC FDCD FDE5
    FE41 FE4A FE4F FE56 FE58 FE59 FE5A FE5B FE69 FE6E FE6F FE70 FE8D FE92 FE96 FE97 FE98 FE99 FE9A
    FEA4 FEAE FEB1 FEB3 FEBD FEBF FEC1 FEC3 FECA FECB FEDB FEDC FEDD FEDF
    FEE0 FEE1 FEE3 FEE4 FEE5 FEE6 FEE7 FEE8 FEE9 FEED FEEE FEEF
    FEF0 FEF1 FEF2 FEF4 FEF5 FEF6 FEF7 FEF8 FEF9 FEFA FEFC FEFF
    """.split()
)

_SPECIAL = {
    PGN_VEHICLE_IDENTIFICATION: VIN,
    PGN_VEHICLE_POSITION: POSITION,
    PGN_ADDRESS_CLAIMED: NAME,
    PGN_REQUEST: REQUEST,
}

_GRID = 500_000  # 0.05 degree in units of 1e-7 degree
_POSITION_OFFSET = 2_100_000_000  # 210 degrees
_FILL = "Z"


def action(pgn: int) -> str:
    """How the data of a frame in this parameter group is treated."""
    if pgn in _SPECIAL:
        return _SPECIAL[pgn]
    if pgn in KEPT_GROUPS:
        return KEEP
    return MASK


def mask(data: bytes) -> bytes:
    return b"\xff" * len(data)


def trim_request(data: bytes) -> bytes:
    return data[:3] + b"\xff" * max(0, len(data) - 3)


def _coarsen(raw: int) -> int:
    if raw >> 24 >= 0xFB:  # not available, error or reserved: there is no position to move
        return raw
    cells = (raw - _POSITION_OFFSET + _GRID // 2) // _GRID
    return _POSITION_OFFSET + cells * _GRID


def coarsen_position(data: bytes) -> bytes:
    """Latitude in bytes 1 to 4, longitude in bytes 5 to 8, both 1e-7 degree per bit with a -210 degree offset."""
    if len(data) != 8:
        return mask(data)
    latitude = _coarsen(int.from_bytes(data[:4], "little"))
    longitude = _coarsen(int.from_bytes(data[4:], "little"))
    return latitude.to_bytes(4, "little") + longitude.to_bytes(4, "little")


def clear_identity(data: bytes) -> bytes:
    """A NAME is 64 bits, low byte first; the identity number is its lowest 21 bits."""
    if len(data) != 8:
        return mask(data)
    name = int.from_bytes(data, "little") & ~((1 << 21) - 1)
    return name.to_bytes(8, "little")


def rewrite_vin(vin: str, vehicle_key: str) -> str:
    """A stand-in VIN that keeps what an analysis needs and nothing that points at the vehicle.

    Positions 1 to 3 (manufacturer) and 10 (model year) are kept. Positions 4 to 8 and 11 to 15 are the ten hex
    digits of the vehicle's pseudonym, positions 16 and 17 are `ZZ`, where a real VIN has digits. The check digit
    is recomputed so that it is right when the original was right and wrong when the original was wrong.
    """
    digits = vehicle_key.rsplit("-", 1)[-1].upper()
    if len(vin) != 17 or len(digits) != 10:
        raise ValueError("expected a 17-character VIN and a pseudonym ending in ten hex digits")
    body = vin[:3] + digits[:5] + "0" + vin[9] + digits[5:] + _FILL * 2
    right = vin_rules.check_digit(body)
    if vin_rules.is_valid(vin):
        check = right
    else:
        check = "1" if right == "0" else "0"
    return body[:8] + check + body[9:]
