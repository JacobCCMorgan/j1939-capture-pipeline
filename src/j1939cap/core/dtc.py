"""Diagnostic messages DM1 (active trouble codes) and DM2 (previously active).

Both share one payload: two bytes of lamp status, then four bytes per trouble code. With one code or none the
message fits a single frame; with more it travels over the transport protocol.

A trouble code is a suspect parameter number (19 bits), a failure mode identifier (5 bits), an occurrence count
(7 bits) and a conversion method bit. The first two bytes hold the low 16 bits of the parameter number; the top
three bits of the third byte complete it. An occurrence count of 127 means the sender does not keep one.
"""

from __future__ import annotations

from typing import NamedTuple

LAMP_STATES = ("off", "on", "error", "not_available")
LAMPS = ("protect", "amber_warning", "red_stop", "malfunction_indicator")  # byte 1, low bit pair first
OCCURRENCE_NOT_AVAILABLE = 0x7F


class Dtc(NamedTuple):
    spn: int
    fmi: int
    occurrence: int | None
    conversion_method: int


class DiagnosticMessage(NamedTuple):
    lamps: dict[str, str]
    dtcs: tuple[Dtc, ...]

    @property
    def any_lamp_on(self) -> bool:
        return "on" in self.lamps.values()


def decode_dtc(chunk: bytes) -> Dtc:
    if len(chunk) != 4:
        raise ValueError("a trouble code is four bytes")
    spn = chunk[0] | (chunk[1] << 8) | ((chunk[2] >> 5) << 16)
    count = chunk[3] & 0x7F
    return Dtc(spn, chunk[2] & 0x1F, None if count == OCCURRENCE_NOT_AVAILABLE else count, chunk[3] >> 7)


def decode_dm(payload: bytes) -> DiagnosticMessage:
    """Decode a DM1 or DM2 payload. An all-zero code means "no trouble codes"; trailing 0xFF is padding."""
    if len(payload) < 2:
        raise ValueError("a diagnostic message has at least the two lamp bytes")
    lamps = {name: LAMP_STATES[(payload[0] >> (2 * n)) & 0x3] for n, name in enumerate(LAMPS)}
    dtcs: list[Dtc] = []
    for n in range(2, len(payload) - 3, 4):
        chunk = payload[n : n + 4]
        if chunk in (b"\x00\x00\x00\x00", b"\xff\xff\xff\xff"):
            continue
        dtcs.append(decode_dtc(chunk))
    return DiagnosticMessage(lamps, tuple(dtcs))
