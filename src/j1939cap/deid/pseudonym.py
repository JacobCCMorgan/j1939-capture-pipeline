"""Keyed pseudonyms for identifiers.

An identifier is replaced by the first ten hex digits of HMAC-SHA256 over `namespace|VALUE`, behind a prefix that
says what it stands for (`DEV-` for a device serial number, `VIN-` for a vehicle identification number). The same
value under the same key always gives the same pseudonym, so tables de-identified at different times, or in
different projects, still join on it; without the key a pseudonym cannot be turned back or recomputed.

The key lives in a file outside the repository. Its path comes from the environment.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
from pathlib import Path

KEY_FILE_VARIABLE = "J1939CAP_DEID_KEY_FILE"
DIGEST_CHARS = 10
DEVICE = ("device", "DEV-")
VEHICLE = ("vin", "VIN-")


class Pseudonymizer:
    def __init__(self, key: bytes) -> None:
        if len(key) < 16:
            raise ValueError("the de-identification key must be at least 16 bytes")
        self._key = key

    @classmethod
    def from_file(cls, path: str | os.PathLike) -> Pseudonymizer:
        """A key file holds hex digits or, failing that, the key as text. Surrounding white space is ignored."""
        text = Path(path).read_text(encoding="utf-8").strip()
        return cls(bytes.fromhex(text) if re.fullmatch(r"(?:[0-9a-fA-F]{2})+", text) else text.encode())

    @classmethod
    def from_env(cls, variable: str = KEY_FILE_VARIABLE) -> Pseudonymizer:
        path = os.environ.get(variable)
        if not path:
            raise RuntimeError(f"set {variable} to the path of the de-identification key file")
        return cls.from_file(path)

    def one(self, kind: tuple[str, str], value: str) -> str:
        namespace, prefix = kind
        message = f"{namespace}|{value.strip().upper()}".encode()
        return prefix + hmac.new(self._key, message, hashlib.sha256).hexdigest()[:DIGEST_CHARS]

    def device(self, serial: str) -> str:
        return self.one(DEVICE, serial)

    def vehicle(self, vin: str) -> str:
        return self.one(VEHICLE, vin)
