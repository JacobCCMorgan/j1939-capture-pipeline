"""Is the recording itself intact?

The recorder stores a frame as fifteen bytes: three of timestamp, four of identifier, eight of payload, and the
capture file is that byte stream cut into rows. When bytes go missing the cut lands in the wrong place and a run of
rows comes out with nonsense timestamps and identifiers until the recorder finds its place again. The frames inside
the run are not damaged, only cut at the wrong offset, so most of them can be read back by putting the rows' bytes
together again and looking for records that make sense.

`check` separates the rows that are in frame from the runs that are not, and optionally recovers frames from each run.
"""

from __future__ import annotations

from collections import Counter
from typing import NamedTuple

from .capture import Frame
from .ids import MAX_EXTENDED_ID

RECORD_BYTES = 15


class SlipRun(NamedTuple):
    first_row: int
    rows: int
    ts_before: int | None
    ts_after: int | None
    recovered: int


class IntegrityResult(NamedTuple):
    frames: list[Frame]
    recovered: list[Frame]
    runs: list[SlipRun]
    rows: int

    @property
    def rows_out_of_frame(self) -> int:
        return sum(run.rows for run in self.runs)


def _plausible(frame: Frame, duration_ms: int | None, slack_ms: int) -> bool:
    if frame.can_id > MAX_EXTENDED_ID or frame.ts_ms < 0:
        return False
    return duration_ms is None or frame.ts_ms <= duration_ms + slack_ms


def _traffic_continues(frames: list[Frame], start: int, duration_ms: int | None, slack_ms: int, max_step_ms: int, lookahead: int) -> bool:
    """A jump in time is real bus silence when the rows after it carry on from the new time."""
    prev = frames[start].ts_ms
    for frame in frames[start + 1 : start + 1 + lookahead]:
        if not _plausible(frame, duration_ms, slack_ms) or not 0 <= frame.ts_ms - prev <= max_step_ms:
            return False
        prev = frame.ts_ms
    return True


def recover(rows: list[Frame], ts_low: int, ts_high: int, known_ids: set[int]) -> list[Frame]:
    """Read frames back out of a run of rows that were cut at the wrong offset.

    A record is accepted when its timestamp fits between the frames around the run, does not go backwards, its
    identifier is one the capture uses elsewhere, and the record after it is acceptable too. The last condition drops
    the record that straddles the point where bytes were lost: its head looks right but its payload is not its own.
    """
    stream = bytearray()
    for row in rows:
        if not (0 <= row.ts_ms < 1 << 24 and 0 <= row.can_id < 1 << 32 and len(row.data) == 8):
            return []
        stream += row.ts_ms.to_bytes(3, "big") + row.can_id.to_bytes(4, "big") + row.data

    def record_at(pos: int, not_before: int) -> Frame | None:
        if pos + RECORD_BYTES > len(stream):
            return None
        ts = int.from_bytes(stream[pos : pos + 3], "big")
        can_id = int.from_bytes(stream[pos + 3 : pos + 7], "big")
        if max(ts_low, not_before) <= ts <= ts_high and can_id in known_ids:
            return Frame(ts, can_id, bytes(stream[pos + 7 : pos + RECORD_BYTES]))
        return None

    out: list[Frame] = []
    pos, last_ts = 0, ts_low
    while pos + RECORD_BYTES <= len(stream):
        frame = record_at(pos, last_ts)
        if frame is not None:
            after = pos + RECORD_BYTES
            if after == len(stream) or record_at(after, frame.ts_ms) is not None:
                out.append(frame)
                last_ts = frame.ts_ms
                pos = after
                continue
        pos += 1
    return out


def check(
    frames: list[Frame],
    duration_ms: int | None = None,
    *,
    max_step_ms: int = 5000,
    slack_ms: int = 2000,
    lookahead: int = 3,
    repair: bool = True,
) -> IntegrityResult:
    """Split a capture into in-frame rows and out-of-frame runs.

    A row is in frame when its identifier fits in 29 bits, its timestamp is inside the capture (when the duration
    is known) and it does not step backwards or jump ahead of the row before it. A jump ahead is accepted when the
    following rows continue from it. With `repair`, frames recovered from a run are merged back in time order.
    """
    flagged = [False] * len(frames)
    last: int | None = None
    for i, frame in enumerate(frames):
        ok = _plausible(frame, duration_ms, slack_ms)
        if ok:
            step = 0 if last is None else frame.ts_ms - last
            if step < 0:
                ok = False
            elif last is None or step > max_step_ms:
                ok = _traffic_continues(frames, i, duration_ms, slack_ms, max_step_ms, lookahead)
        if ok:
            last = frame.ts_ms
        else:
            flagged[i] = True

    # A row can keep a believable timestamp and still be cut wrong. Next to a run, an identifier that the capture
    # uses nowhere else gives it away.
    seen = Counter(f.can_id for f, bad in zip(frames, flagged, strict=True) if not bad)
    changed = True
    while changed:
        changed = False
        for i, frame in enumerate(frames):
            if flagged[i] or seen[frame.can_id] != 1:
                continue
            if (i > 0 and flagged[i - 1]) or (i + 1 < len(frames) and flagged[i + 1]):
                flagged[i] = True
                changed = True

    known_ids = {f.can_id for f, bad in zip(frames, flagged, strict=True) if not bad}
    out: list[Frame] = []
    recovered_all: list[Frame] = []
    runs: list[SlipRun] = []
    i = 0
    while i < len(frames):
        if not flagged[i]:
            out.append(frames[i])
            i += 1
            continue
        j = i
        while j < len(frames) and flagged[j]:
            j += 1
        ts_before = out[-1].ts_ms if out else None
        ts_after = frames[j].ts_ms if j < len(frames) else None
        recovered: list[Frame] = []
        if repair:
            low = ts_before if ts_before is not None else 0
            high = ts_after if ts_after is not None else low + max_step_ms
            recovered = recover(frames[i:j], low, high, known_ids)
            out.extend(recovered)
            recovered_all.extend(recovered)
        runs.append(SlipRun(i, j - i, ts_before, ts_after, len(recovered)))
        i = j
    return IntegrityResult(out, recovered_all, runs, len(frames))
