"""What may leave with a frame.

Every frame keeps its timestamp and its identifier. Its data bytes are published only when the parameter group is
on a short reviewed list of numeric signal groups: engine, transmission, brakes, aftertreatment, electrical,
ambient, distance, hours, fuel, time and dash groups whose eight bytes are scaled numbers or switch states, plus
the diagnostic messages with their trouble codes. A group that is not on the list has its data replaced by 0xFF,
which is how J1939 writes "not available". That covers the manufacturers' proprietary groups, the groups that carry
free text (component and software identification) and any group nobody has reviewed yet.

Four groups get their own treatment:

- vehicle identification: the VIN is rewritten, and so is text in its place that is not quite a VIN (see
  `rewrite_identification`);
- vehicle position: latitude and longitude are moved to a 0.05 degree grid;
- address claimed: the identity number inside the NAME is cleared, manufacturer and function stay;
- request: only the three bytes that name the requested group are kept. The recorder stores eight bytes for every
  frame, and after a short frame the rest are whatever was in its buffer.
"""

from __future__ import annotations

from itertools import cycle

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

# What a controller sent where the VIN belongs.
WHOLE_VIN = "whole"  # seventeen VIN characters
MISTYPED_VIN = "mistyped"  # seventeen characters, up to three of them not VIN characters
FRAGMENT = "fragment"  # anything else that holds a VIN character: a few digits, padded text
NO_TEXT = "nothing"  # padding only

MISTYPED_AT_MOST = 3

_GRID = 500_000  # 0.05 degree in units of 1e-7 degree
_POSITION_OFFSET = 2_100_000_000  # 210 degrees
_FILL = "Z"
_PADDING = frozenset(b"\x00 \xff")
_NEVER_IN_A_VIN = frozenset(b"IOQioq")
_UNKNOWN = ord("?")


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


def _is_vin_byte(byte: int) -> bool:
    return vin_rules.is_vin_character(chr(byte))


def _passes(byte: int) -> int:
    return byte if byte in _PADDING or byte in _NEVER_IN_A_VIN or byte == ord("*") else _UNKNOWN


def _upper(head: bytes) -> str:
    """One character per byte; only ASCII letters change case, so positions hold."""
    return "".join(chr(byte).upper() if byte < 0x80 else chr(byte) for byte in head)


def classify_identification(payload: bytes) -> tuple[str, str]:
    """What kind of text a vehicle identification payload holds, and the text that names the vehicle.

    The text is what stands before the asterisk, without padding, letters in upper case. Its pseudonym is the
    vehicle key, so a VIN stored with the same typing error elsewhere still gets the same key.
    """
    head = payload.split(b"*", 1)[0]
    marks = [_is_vin_byte(byte) for byte in head]
    text = _upper(bytes(byte for byte in head if byte not in _PADDING))
    if len(head) >= 17 and all(marks[:17]) and not any(marks[17:]):
        return WHOLE_VIN, text[:17]
    if not any(marks):
        return NO_TEXT, ""
    if len(head) == 17 and marks.count(False) <= MISTYPED_AT_MOST:
        return MISTYPED_VIN, text
    return FRAGMENT, text


def rewrite_identification(payload: bytes, vehicle_key: str) -> bytes:
    """A vehicle identification payload of the same length with no character of the vehicle's own left in it.

    A whole VIN becomes its stand-in (`rewrite_vin`). A mistyped one, seventeen characters of which a few cannot be
    part of a VIN, gets the same layout: manufacturer and model year stay, pseudonym digits and `ZZ` fill the rest,
    and each character that is not a VIN character stays where it was, so the text is still not a VIN and for the
    same reason. In a fragment every VIN character becomes a pseudonym digit: with only some of a VIN, or text that
    was never one, there is no telling which characters are safe to keep.

    Outside the VIN characters only padding (0x00, space, 0xFF), the asterisk and the letters I, O and Q pass.
    Any other byte becomes a question mark, and so does everything else behind the asterisk.
    """
    digits = vehicle_key.rsplit("-", 1)[-1].upper()
    if len(digits) != 10:
        raise ValueError("expected a pseudonym ending in ten hex digits")
    head, star, tail = payload.partition(b"*")
    kind, _ = classify_identification(payload)
    text = _upper(head)
    if kind == WHOLE_VIN:
        layout = rewrite_vin(text[:17], vehicle_key)
    elif kind == MISTYPED_VIN:
        layout = text[:3] + digits[:5] + "0" + text[9] + digits[5:] + _FILL * 2
    else:
        layout = ""
    filler = cycle(digits)
    out = bytearray()
    for position, byte in enumerate(head):
        if not _is_vin_byte(byte):
            out.append(_passes(byte))
        elif position < len(layout):
            out.append(ord(layout[position]))
        else:
            out.append(ord(next(filler)))
    out += star
    out += bytes(_UNKNOWN if _is_vin_byte(byte) else _passes(byte) for byte in tail)
    return bytes(out)
