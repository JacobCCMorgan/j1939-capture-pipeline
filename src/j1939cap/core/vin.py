"""Vehicle identification number: clean-up, check digit, manufacturer and model year.

A VIN is 17 characters from the digits and the capital letters except I, O and Q. On the bus it arrives as ASCII
ending in an asterisk, usually spread over three transport packets.

Position 9 is a check digit computed from the other sixteen. Positions 1 to 3 identify the manufacturer and
position 10 the model year, on a thirty-year cycle.
"""

from __future__ import annotations

from datetime import date

_ALPHABET = "0123456789ABCDEFGHJKLMNPRSTUVWXYZ"
_VALUES = dict(zip("ABCDEFGHJKLMNPRSTUVWXYZ", (1, 2, 3, 4, 5, 6, 7, 8, 1, 2, 3, 4, 5, 7, 9, 2, 3, 4, 5, 6, 7, 8, 9), strict=True))
_VALUES.update({str(d): d for d in range(10)})
_WEIGHTS = (8, 7, 6, 5, 4, 3, 2, 10, 0, 9, 8, 7, 6, 5, 4, 3, 2)
_YEAR_CODES = "ABCDEFGHJKLMNPRSTVWXY123456789"  # A is 1980, 2010, 2040

# World manufacturer identifiers of common North American heavy trucks.
WMI = {
    "1FU": "Freightliner",
    "1FV": "Freightliner",
    "3AK": "Freightliner",
    "3AL": "Freightliner",
    "4V4": "Volvo",
    "1XK": "Kenworth",
    "2XK": "Kenworth",
    "3WK": "Kenworth",
    "1XP": "Peterbilt",
    "2XP": "Peterbilt",
    "1NP": "Peterbilt",
    "2NP": "Peterbilt",
    "1HT": "International",
    "1HS": "International",
    "2HS": "International",
    "3HS": "International",
    "3HA": "International",
    "1M1": "Mack",
    "1M2": "Mack",
    "5KJ": "Western Star",
    "5KK": "Western Star",
}


def clean(raw: bytes | str) -> str:
    """Text of a VIN payload: everything before the asterisk, upper case, VIN characters only."""
    text = raw.decode("latin-1") if isinstance(raw, bytes) else raw
    text = text.split("*", 1)[0].upper()
    return "".join(ch for ch in text if ch in _ALPHABET)


def check_digit(vin: str) -> str:
    if len(vin) != 17 or any(ch not in _ALPHABET for ch in vin):
        raise ValueError("a VIN is 17 characters without I, O or Q")
    remainder = sum(_VALUES[ch] * weight for ch, weight in zip(vin, _WEIGHTS, strict=True)) % 11
    return "X" if remainder == 10 else str(remainder)


def is_valid(vin: str) -> bool:
    try:
        return check_digit(vin) == vin[8]
    except ValueError:
        return False


def manufacturer(vin: str) -> str | None:
    return WMI.get(vin[:3])


def model_year(vin: str, not_after: int | None = None) -> int | None:
    """Model year from position 10: the latest year of the thirty-year cycle that is not past `not_after`.

    `not_after` defaults to next calendar year, since model years run ahead of the calendar.
    """
    if len(vin) != 17 or vin[9] not in _YEAR_CODES:
        return None
    if not_after is None:
        not_after = date.today().year + 1
    year = 1980 + _YEAR_CODES.index(vin[9])
    while year + 30 <= not_after:
        year += 30
    return year if year <= not_after else None
