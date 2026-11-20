"""De-identify one capture.

The capture is read the way the analysis reads it: rows that are in frame, plus the frames recovered from runs of
rows cut at the wrong offset. Each of those frames gets its data rewritten by the rules in `rules`, and the result
is written back where it came from. For a run that means rebuilding its byte stream: recovered records go back at
their offsets with their new data, and every other byte becomes 0xFF, because nobody can say what it was part of.
The one exception is the timestamp and identifier of a damaged record that directly follows a recovered one: those
seven bytes are what vouched for the record before them, so they stay and the run still reads back the same way.

Long messages are followed through the transport protocol. The data frames of a message are published only when
the message is complete and its parameter group is on the kept list; the VIN is rewritten across its packets; data
frames of an incomplete message, or with no announcement, are filled with 0xFF behind their sequence number.

Before returning, the output is read back. It must split into the same runs and give the same frames as the
original, changed only as the rules say. If it does not, nothing is returned.
"""

from __future__ import annotations

import csv
import hashlib
from collections import Counter
from pathlib import Path
from typing import NamedTuple

from ..core import vin as vin_rules
from ..core.capture import Frame, capture_name, find_summary, read_recorder_csv, read_summary, write_recorder_csv, write_summary
from ..core.ids import PGN_TP_CM, PGN_TP_DT, parse_id
from ..core.integrity import RECORD_BYTES, check, from_stream, to_stream
from ..core.transport import Reassembler
from . import rules
from .pseudonym import Pseudonymizer

SUMMARY_KEYS = (
    "fw_version",
    "st_version",
    "request_time_sec",
    "start_time",
    "end_time",
    "duration_ms",
    "early_stop",
    "records_written",
    "iot_queue_dropped",
    "st_stop_ok",
    "st_drop_count",
    "complete",
)
TRANSPORT_CONTROL = "transport_control"
TRANSPORT_KEPT = "transport_data_kept"
TRANSPORT_MASKED = "transport_data_masked"
OUT_OF_FRAME = "rows_out_of_frame"
HEAD_BYTES = 7  # timestamp and identifier of a record
HEAD_WINDOW_MS = 5000
MANIFEST_NAME = "MANIFEST.csv"
MANIFEST_COUNTS = (
    OUT_OF_FRAME,
    rules.KEEP,
    rules.MASK,
    rules.VIN,
    rules.POSITION,
    rules.REQUEST,
    rules.NAME,
    TRANSPORT_CONTROL,
    TRANSPORT_KEPT,
    TRANSPORT_MASKED,
)
MANIFEST_COLUMNS = ("capture", "device_key", "vehicle_key", "rows", *MANIFEST_COUNTS, "bytes", "sha256")


class DeidError(Exception):
    """The capture cannot be de-identified with confidence; nothing was written."""


class Outcome(NamedTuple):
    rows: list[Frame]
    counts: dict[str, int]
    vehicle_keys: tuple[str, ...]
    rows_changed: int


def _rewritten_vin_payload(payload: bytes, pseudonymizer: Pseudonymizer) -> tuple[bytes | None, str | None]:
    """New payload and vehicle pseudonym, or (None, None) when the payload does not start with a 17-character VIN."""
    text = vin_rules.clean(payload)
    if len(text) != 17 or payload[:17].decode("latin-1").upper() != text:
        return None, None
    key = pseudonymizer.vehicle(text)
    return rules.rewrite_vin(text, key).encode("ascii") + payload[17:], key


def _apply_rules(frames: list[Frame], pseudonymizer: Pseudonymizer) -> tuple[list[bytes], Counter, Counter]:
    reassembler = Reassembler()
    messages = []
    for ref, frame in enumerate(frames):
        jid = parse_id(frame.can_id)
        if jid.pgn in (PGN_TP_CM, PGN_TP_DT):
            messages += reassembler.feed(frame.ts_ms, jid, frame.data, ref)
    messages += reassembler.flush(frames[-1].ts_ms if frames else 0)

    planned: dict[int, tuple[bytes, str]] = {}
    vehicle_keys: Counter = Counter()
    for message in messages:
        payload, outcome = None, TRANSPORT_MASKED
        if message.status == "complete":
            act = rules.action(message.pgn)
            if act == rules.KEEP:
                payload, outcome = message.payload, TRANSPORT_KEPT
            elif act == rules.VIN:
                payload, key = _rewritten_vin_payload(message.payload, pseudonymizer)
                if key is not None:
                    outcome = rules.VIN
                    vehicle_keys[key] += 1
        for ref, sequence in message.frames:
            if sequence == 0:
                continue
            chunk = payload[7 * (sequence - 1) : 7 * sequence] if payload is not None else b""
            planned[ref] = (bytes([sequence]) + chunk + b"\xff" * (7 - len(chunk)), outcome)

    data: list[bytes] = []
    counts: Counter = Counter()
    for ref, frame in enumerate(frames):
        pgn = parse_id(frame.can_id).pgn
        if pgn == PGN_TP_CM:
            new, outcome = frame.data, TRANSPORT_CONTROL
        elif pgn == PGN_TP_DT:
            new, outcome = planned.get(ref, (frame.data[:1] + b"\xff" * (len(frame.data) - 1), TRANSPORT_MASKED))
        else:
            outcome = rules.action(pgn)
            if outcome == rules.KEEP:
                new = frame.data
            elif outcome == rules.POSITION:
                new = rules.coarsen_position(frame.data)
            elif outcome == rules.NAME:
                new = rules.clear_identity(frame.data)
            elif outcome == rules.REQUEST:
                new = rules.trim_request(frame.data)
            else:  # masked groups, and a VIN that is not inside a transport message
                new, outcome = rules.mask(frame.data), rules.MASK
        data.append(new)
        counts[outcome] += 1
    return data, counts, vehicle_keys


def deidentify_rows(rows: list[Frame], duration_ms: int | None, pseudonymizer: Pseudonymizer) -> Outcome:
    before = check(rows, duration_ms)
    in_run = [False] * len(rows)
    for run in before.runs:
        in_run[run.first_row : run.first_row + run.rows] = [True] * run.rows

    # Where each analysed frame came from: a row, or a recovered record of a run.
    origins: list[tuple[int, int | None]] = []
    starts = {run.first_row: n for n, run in enumerate(before.runs)}
    for row in range(len(rows)):
        if row in starts:
            origins += [(starts[row], k) for k in range(before.runs[starts[row]].recovered)]
        if not in_run[row]:
            origins.append((row, None))
    if len(origins) != len(before.frames):
        raise DeidError("the frames of the capture could not be traced back to its rows")

    data, counts, vehicle_keys = _apply_rules(before.frames, pseudonymizer)
    expected = [Frame(f.ts_ms, f.can_id, new) for f, new in zip(before.frames, data, strict=True)]

    out = list(rows)
    known_ids = {frame.can_id for frame in before.frames}
    originals: dict[int, bytes] = {}
    streams: dict[int, bytearray] = {}
    for n, run in enumerate(before.runs):
        original = to_stream(rows[run.first_row : run.first_row + run.rows])
        if original is None:
            raise DeidError("a run of out-of-frame rows does not come from fifteen-byte records")
        originals[n] = original
        streams[n] = bytearray(b"\xff" * len(original))
    for (where, k), frame in zip(origins, expected, strict=True):
        if k is None:
            out[where] = frame
            continue
        offsets = before.runs[where].offsets
        offset = offsets[k]
        streams[where][offset : offset + RECORD_BYTES] = to_stream([frame])
        head = originals[where][offset + RECORD_BYTES : offset + RECORD_BYTES + HEAD_BYTES]
        if len(head) == HEAD_BYTES and offset + RECORD_BYTES not in offsets:
            ts, can_id = int.from_bytes(head[:3], "big"), int.from_bytes(head[3:], "big")
            if can_id in known_ids and frame.ts_ms <= ts <= frame.ts_ms + HEAD_WINDOW_MS:
                streams[where][offset + RECORD_BYTES : offset + RECORD_BYTES + HEAD_BYTES] = head
    for n, run in enumerate(before.runs):
        out[run.first_row : run.first_row + run.rows] = from_stream(bytes(streams[n]))

    after = check(out, duration_ms)
    same_runs = [(r.first_row, r.rows, r.offsets) for r in after.runs] == [(r.first_row, r.rows, r.offsets) for r in before.runs]
    if not same_runs or after.frames != expected:
        raise DeidError("the de-identified capture does not read back the way the original does")

    counts[OUT_OF_FRAME] = before.rows_out_of_frame
    changed = sum(1 for old, new in zip(rows, out, strict=True) if old != new)
    return Outcome(out, dict(sorted(counts.items())), tuple(key for key, _ in vehicle_keys.most_common()), changed)


def deidentify_file(capture_path: str | Path, out_dir: str | Path, pseudonymizer: Pseudonymizer) -> dict:
    """Write `<device key>_<start time>.csv.gz` and its summary into `out_dir`; return what was done."""
    summary_path = find_summary(capture_path)
    if summary_path is None:
        raise DeidError(f"{capture_name(capture_path)}: no summary file, so no serial number and no start time to name the output")
    summary = read_summary(summary_path)
    serial, started = summary.fields.get("sn", ""), summary.started_at
    if not serial or started is None:
        raise DeidError("the summary file has no serial number or no start time")

    rows, malformed = read_recorder_csv(capture_path)
    if malformed:
        raise DeidError(f"{malformed} rows are not frames; their text cannot be checked")
    outcome = deidentify_rows(rows, summary.duration_ms, pseudonymizer)

    device_key = pseudonymizer.device(serial)
    name = f"{device_key}_{started:%Y%m%dT%H%M%SZ}"
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    write_recorder_csv(out_dir / f"{name}.csv.gz", outcome.rows)
    fields = {"sn": device_key, **{key: summary.fields[key] for key in SUMMARY_KEYS if key in summary.fields}}
    write_summary(out_dir / f"{name}_summary.txt", fields)
    dropped = sorted(set(summary.fields) - set(SUMMARY_KEYS) - {"sn"})
    written = (out_dir / f"{name}.csv.gz").read_bytes()
    return {
        "capture": name,
        "device_key": device_key,
        "vehicle_keys": list(outcome.vehicle_keys),
        "rows": len(outcome.rows),
        "rows_changed": outcome.rows_changed,
        "counts": outcome.counts,
        "summary_keys_dropped": dropped,
        "bytes": len(written),
        "sha256": hashlib.sha256(written).hexdigest(),
    }


def write_manifest(out_dir: str | Path, entries: list[dict]) -> Path:
    """One line per capture: its keys, what was done to its rows, and the checksum of the published file."""
    path = Path(out_dir) / MANIFEST_NAME
    with open(path, "w", encoding="ascii", newline="") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(MANIFEST_COLUMNS)
        for entry in sorted(entries, key=lambda e: e["capture"]):
            keys = entry["vehicle_keys"]
            counts = [entry["counts"].get(name, 0) for name in MANIFEST_COUNTS]
            vehicle_key = keys[0] if keys else ""
            writer.writerow([entry["capture"], entry["device_key"], vehicle_key, entry["rows"], *counts, entry["bytes"], entry["sha256"]])
    return path


def read_manifest(directory: str | Path) -> list[dict[str, str]]:
    with open(Path(directory) / MANIFEST_NAME, encoding="ascii", newline="") as fh:
        return list(csv.DictReader(fh))
