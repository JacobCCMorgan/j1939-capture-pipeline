"""Fields of a J1939 29-bit identifier.

Layout, most significant bit first: priority (3), extended data page (1), data page (1), PDU format (8),
PDU specific (8), source address (8). When PDU format is below 240 the message is addressed (PDU1): PDU specific
is the destination address and is not part of the parameter group number. From 240 up the message is broadcast
(PDU2) and PDU specific is the low byte of the parameter group number.
"""

from __future__ import annotations

from typing import NamedTuple

MAX_EXTENDED_ID = 0x1FFFFFFF
GLOBAL_ADDRESS = 0xFF
NULL_ADDRESS = 0xFE

PGN_ACKNOWLEDGMENT = 0xE800
PGN_REQUEST = 0xEA00
PGN_TP_DT = 0xEB00
PGN_TP_CM = 0xEC00
PGN_ADDRESS_CLAIMED = 0xEE00
PGN_PROPRIETARY_A = 0xEF00
PGN_DM1 = 0xFECA
PGN_DM2 = 0xFECB
PGN_VEHICLE_IDENTIFICATION = 0xFEEC


class J1939Id(NamedTuple):
    priority: int
    pgn: int
    sa: int
    da: int
    pdu1: bool


def parse_id(can_id: int) -> J1939Id:
    """Split a 29-bit identifier. The destination of a broadcast (PDU2) message is the global address."""
    if not 0 <= can_id <= MAX_EXTENDED_ID:
        raise ValueError(f"not a 29-bit identifier: {can_id:#x}")
    pages = (can_id >> 24) & 0x3
    pf = (can_id >> 16) & 0xFF
    ps = (can_id >> 8) & 0xFF
    if pf < 240:
        return J1939Id(can_id >> 26, (pages << 16) | (pf << 8), can_id & 0xFF, ps, True)
    return J1939Id(can_id >> 26, (pages << 16) | (pf << 8) | ps, can_id & 0xFF, GLOBAL_ADDRESS, False)


def build_id(priority: int, pgn: int, sa: int, da: int = GLOBAL_ADDRESS) -> int:
    """Inverse of parse_id. For a PDU2 group the destination is ignored."""
    if not (0 <= priority <= 7 and 0 <= pgn <= 0x3FFFF and 0 <= sa <= 0xFF and 0 <= da <= 0xFF):
        raise ValueError("field out of range")
    pf = (pgn >> 8) & 0xFF
    if pf < 240:
        if pgn & 0xFF:
            raise ValueError(f"PDU1 group {pgn:#x} cannot carry a low byte")
        return (priority << 26) | (pgn << 8) | (da << 8) | sa
    return (priority << 26) | (pgn << 8) | sa


def is_proprietary(pgn: int) -> bool:
    """Proprietary A (addressed) and Proprietary B (broadcast) groups, on either data page."""
    base = pgn & 0xFFFF
    return base == PGN_PROPRIETARY_A or 0xFF00 <= base <= 0xFFFF
