"""Reading a capture from disk.

The recorder format is a CSV with one frame per row, `timestamp_ms,can_id,data`: milliseconds since the capture
started, the identifier as eight hex digits, the payload as hex. A sidecar text file with one `key=value` per line
sits next to it and says what was asked for and what the recorder believes it wrote.

Rows are returned as they are. A row whose identifier is wider than 29 bits is still a row here; deciding whether
it belongs to the capture is the job of `integrity`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple


class Frame(NamedTuple):
    ts_ms: int
    can_id: int
    data: bytes


class ReadResult(NamedTuple):
    frames: list[Frame]
    malformed_rows: int


def _parse_row(line: str) -> Frame | None:
    parts = line.split(",")
    if len(parts) != 3:
        return None
    ts, can_id, data = (p.strip() for p in parts)
    if not (1 <= len(can_id) <= 8 and len(data) <= 16 and len(data) % 2 == 0):
        return None
    try:
        return Frame(int(ts), int(can_id, 16), bytes.fromhex(data))
    except ValueError:
        return None


def read_recorder_csv(path: str | Path) -> ReadResult:
    frames: list[Frame] = []
    malformed = 0
    with open(path, encoding="ascii", errors="replace") as fh:
        for n, line in enumerate(fh):
            line = line.strip()
            if not line:
                continue
            frame = _parse_row(line)
            if frame is None:
                if n == 0 and line.lower().startswith("timestamp"):
                    continue
                malformed += 1
            else:
                frames.append(frame)
    return ReadResult(frames, malformed)


@dataclass(frozen=True)
class CaptureSummary:
    """The recorder's own account of a capture. Unknown keys are kept in `fields`."""

    fields: dict[str, str]

    def _int(self, key: str) -> int | None:
        value = self.fields.get(key, "")
        return int(value) if value.lstrip("-").isdigit() else None

    def _time(self, key: str) -> datetime | None:
        try:
            return datetime.strptime(self.fields.get(key, ""), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        except ValueError:
            return None

    @property
    def requested_s(self) -> int | None:
        return self._int("request_time_sec")

    @property
    def duration_ms(self) -> int | None:
        return self._int("duration_ms")

    @property
    def records_written(self) -> int | None:
        return self._int("records_written")

    @property
    def complete(self) -> bool | None:
        value = self.fields.get("complete", "").lower()
        return {"true": True, "false": False}.get(value)

    @property
    def reported_drops(self) -> int | None:
        """Sum of every drop counter the recorder reports, or None when it reports none."""
        counts = [self._int(k) for k in self.fields if k.endswith("dropped") or k.endswith("drop_count")]
        counts = [c for c in counts if c is not None]
        return sum(counts) if counts else None

    @property
    def started_at(self) -> datetime | None:
        return self._time("start_time")

    @property
    def ended_at(self) -> datetime | None:
        return self._time("end_time")


def read_summary(path: str | Path) -> CaptureSummary:
    fields: dict[str, str] = {}
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            key, sep, value = line.strip().partition("=")
            if sep and key:
                fields[key.strip()] = value.strip()
    return CaptureSummary(fields)


def find_summary(capture_path: str | Path) -> Path | None:
    """`name.csv` is described by `name_summary.txt` in the same directory."""
    capture_path = Path(capture_path)
    candidate = capture_path.with_name(capture_path.stem + "_summary.txt")
    return candidate if candidate.is_file() else None
