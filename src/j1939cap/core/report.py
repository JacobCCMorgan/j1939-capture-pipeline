"""One capture in, one report out.

`analyze` runs every check in the core over a capture and returns a plain dictionary that can be printed, written
as JSON or loaded into a table. Nothing else in the project decodes a capture a second way: the command line, the
pipeline and the web tool all start from this report.
"""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from itertools import pairwise
from pathlib import Path

from . import vin as vin_rules
from .capture import CaptureSummary, Frame, find_summary, read_recorder_csv, read_summary
from .dtc import decode_dm
from .ids import (
    PGN_ACKNOWLEDGMENT,
    PGN_DM1,
    PGN_DM2,
    PGN_REQUEST,
    PGN_TP_CM,
    PGN_TP_DT,
    PGN_VEHICLE_IDENTIFICATION,
    is_proprietary,
    parse_id,
)
from .integrity import check
from .signals import BY_NAME, ERROR, NOT_AVAILABLE, VALID, decode
from .transport import Reassembler, TpMessage

REPORT_VERSION = 1
DISTANCE_SIGNALS = ("total_vehicle_distance_hr", "total_vehicle_distance")
ELD_SIGNALS = {
    "vehicle_speed": ("wheel_based_vehicle_speed",),
    "engine_speed": ("engine_speed",),
    "engine_hours": ("engine_total_hours",),
}
DISTANCE_CONFLICT_KM = 1.0
_NOT_BROADCAST = (PGN_TP_CM, PGN_TP_DT, PGN_REQUEST, PGN_ACKNOWLEDGMENT)
_ACK_KINDS = {0: "positive", 1: "negative", 2: "access_denied", 3: "cannot_respond"}


def cadence(timestamps: list[int], min_frames: int = 50, max_hole: int = 5) -> dict:
    """Period and gaps of one identifier, and how many frames a steady broadcast is missing.

    The period is the median gap. When nine gaps in ten sit on a whole multiple of it the identifier is treated as a
    steady broadcast. A gap of k periods then counts as k - 1 missing frames as long as k is small; a longer silence
    is a pause in the broadcast, which some senders do on purpose, and is counted separately.
    """
    out: dict = {"frames": len(timestamps), "period_ms": None, "max_gap_ms": None, "missing": None, "pauses": None}
    gaps = [b - a for a, b in pairwise(timestamps)]
    if not gaps:
        return out
    out["max_gap_ms"] = max(gaps)
    if len(timestamps) < min_frames:
        return out
    period = float(statistics.median(gaps))
    out["period_ms"] = period
    if period < 5:
        return out
    on_grid = missing = pauses = 0
    for gap in gaps:
        k = round(gap / period)
        if k >= 1 and abs(gap - k * period) <= 0.3 * period:
            on_grid += 1
            if k > max_hole:
                pauses += 1
            else:
                missing += k - 1
    if on_grid >= 0.9 * len(gaps):
        out["missing"], out["pauses"] = missing, pauses
    return out


def _quantile(values: list[float], q: float) -> float | None:
    """A recorder that loses frames loses them from every broadcast alike, so the capture's loss is taken as the
    median over identifiers; one sender with odd timing cannot move it."""
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int(q * len(ordered)))], 2)


def _signal_stats() -> dict:
    return {VALID: 0, ERROR: 0, NOT_AVAILABLE: 0, "first": None, "last": None, "min": None, "max": None}


def _transport_section(messages: list[TpMessage], reassembler: Reassembler) -> dict:
    by_status = Counter(f"{m.kind}:{m.status}" for m in messages)
    per_group: dict[tuple[int, int], Counter] = defaultdict(Counter)
    for m in messages:
        per_group[(m.pgn, m.sa)][m.status] += 1
    groups = [{"pgn": pgn, "sa": sa, **dict(counts)} for (pgn, sa), counts in sorted(per_group.items())]
    announced = len(messages)
    complete = sum(1 for m in messages if m.status == "complete")
    return {
        "announced": announced,
        "complete": complete,
        "complete_pct": round(100 * complete / announced, 2) if announced else None,
        "by_kind_and_status": dict(sorted(by_status.items())),
        "orphan_data_frames": reassembler.orphan_data_frames,
        "malformed_announcements": reassembler.malformed_announcements,
        "groups": groups,
    }


def _eld_section(signals: dict, vins: list[dict], requests: Counter) -> dict:
    def sources(names: tuple[str, ...]) -> list[dict]:
        found = []
        for (name, sa), stats in sorted(signals.items()):
            if name in names and stats[VALID]:
                found.append({"sa": sa, "signal": name, "valid": stats[VALID], "last": stats["last"]})
        return found

    distance = sources(DISTANCE_SIGNALS)
    lasts = [s["last"] for s in distance]
    spread = round(max(lasts) - min(lasts), 3) if lasts else None
    section = {}
    for key, names in ELD_SIGNALS.items():
        found = sources(names)
        section[key] = {"available": bool(found), "sources": found}
    section["distance"] = {
        "available": bool(distance),
        "sources": distance,
        "spread_km": spread,
        "sources_disagree": spread is not None and spread > DISTANCE_CONFLICT_KM,
    }
    section["vin"] = {
        "available": bool(vins),
        "check_digit_ok": any(v["valid"] for v in vins),
        "requested": sum(n for (_, _, pgn), n in requests.items() if pgn == PGN_VEHICLE_IDENTIFICATION),
    }
    return section


def analyze(frames: list[Frame], summary: CaptureSummary | None = None, *, repair: bool = True) -> dict:
    duration_ms = summary.duration_ms if summary else None
    integrity = check(frames, duration_ms, repair=repair)
    clean = integrity.frames

    timestamps: dict[int, list[int]] = defaultdict(list)
    signals: dict[tuple[str, int], dict] = {}
    diagnostics: list[tuple[int, int, int, bytes]] = []
    vin_texts: dict[tuple[int, str], Counter] = defaultdict(Counter)
    requests: Counter = Counter()
    acknowledgments: Counter = Counter()
    reassembler = Reassembler()
    messages: list[TpMessage] = []

    for frame in clean:
        jid = parse_id(frame.can_id)
        timestamps[frame.can_id].append(frame.ts_ms)
        if jid.pgn in (PGN_TP_CM, PGN_TP_DT):
            messages += reassembler.feed(frame.ts_ms, jid, frame.data)
            continue
        for reading in decode(jid.pgn, frame.data):
            stats = signals.setdefault((reading.name, jid.sa), _signal_stats())
            stats[reading.state] = stats.get(reading.state, 0) + 1
            if reading.value is not None:
                if stats["first"] is None:
                    stats["first"] = stats["min"] = stats["max"] = reading.value
                stats["last"] = reading.value
                stats["min"] = min(stats["min"], reading.value)
                stats["max"] = max(stats["max"], reading.value)
        if jid.pgn in (PGN_DM1, PGN_DM2) and len(frame.data) >= 6:
            diagnostics.append((frame.ts_ms, jid.pgn, jid.sa, frame.data))
        elif jid.pgn == PGN_VEHICLE_IDENTIFICATION:
            vin_texts[(jid.sa, "frame")][vin_rules.clean(frame.data)] += 1
        elif jid.pgn == PGN_REQUEST and len(frame.data) >= 3:
            requests[(jid.sa, jid.da, int.from_bytes(frame.data[:3], "little"))] += 1
        elif jid.pgn == PGN_ACKNOWLEDGMENT and frame.data:
            acknowledgments[(jid.sa, _ACK_KINDS.get(frame.data[0], "other"))] += 1
    last_ts = clean[-1].ts_ms if clean else 0
    messages += reassembler.flush(last_ts)

    for m in messages:
        if m.status != "complete":
            continue
        if m.pgn in (PGN_DM1, PGN_DM2):
            diagnostics.append((m.ts_end, m.pgn, m.sa, m.payload))
        elif m.pgn == PGN_VEHICLE_IDENTIFICATION:
            vin_texts[(m.sa, "transport")][vin_rules.clean(m.payload)] += 1

    groups, missing_total, loss_by_identifier = [], 0, []
    sources: dict[int, dict] = {}
    for can_id, ts in sorted(timestamps.items()):
        jid = parse_id(can_id)
        stats = cadence(ts)
        groups.append({"can_id": can_id, "pgn": jid.pgn, "sa": jid.sa, "proprietary": is_proprietary(jid.pgn), **stats})
        if stats["missing"] is not None and jid.pgn not in _NOT_BROADCAST:
            missing_total += stats["missing"]
            loss_by_identifier.append(100 * stats["missing"] / (stats["frames"] + stats["missing"]))
        source = sources.setdefault(jid.sa, {"sa": jid.sa, "frames": 0, "groups": 0, "proprietary_frames": 0})
        source["frames"] += len(ts)
        source["groups"] += 1
        source["proprietary_frames"] += len(ts) if is_proprietary(jid.pgn) else 0

    vins = []
    for (sa, via), texts in sorted(vin_texts.items()):
        text, count = texts.most_common(1)[0]
        vins.append(
            {
                "sa": sa,
                "via": via,
                "vin": text,
                "messages": count,
                "valid": vin_rules.is_valid(text),
                "manufacturer": vin_rules.manufacturer(text),
                "model_year": vin_rules.model_year(text),
            }
        )

    latest: dict[tuple[int, int], tuple[int, int, bytes]] = {}
    for ts, pgn, sa, payload in sorted(diagnostics, key=lambda d: d[0]):
        count = latest[(pgn, sa)][1] + 1 if (pgn, sa) in latest else 1
        latest[(pgn, sa)] = (ts, count, payload)
    diagnostic_rows = []
    for (pgn, sa), (ts, count, payload) in sorted(latest.items()):
        message = decode_dm(payload)
        diagnostic_rows.append(
            {
                "message": "DM1" if pgn == PGN_DM1 else "DM2",
                "sa": sa,
                "messages": count,
                "last_ts_ms": ts,
                "lamps": message.lamps,
                "dtcs": [d._asdict() for d in message.dtcs],
            }
        )

    span_ms = duration_ms if duration_ms else last_ts
    proprietary = sum(s["proprietary_frames"] for s in sources.values())
    return {
        "report_version": REPORT_VERSION,
        "capture": {
            "rows": integrity.rows,
            "frames": len(clean),
            "duration_ms": span_ms,
            "first_frame_ms": clean[0].ts_ms if clean else None,
            "last_frame_ms": last_ts if clean else None,
            "frames_per_second": round(len(clean) / (span_ms / 1000), 1) if span_ms else None,
            "requested_s": summary.requested_s if summary else None,
            "complete": summary.complete if summary else None,
            "started_at": summary.started_at.strftime("%Y-%m-%dT%H:%M:%SZ") if summary and summary.started_at else None,
            "firmware": (summary.fields.get("fw_version") or None) if summary else None,
            "can_firmware": (summary.fields.get("st_version") or None) if summary else None,
            "sources": len(sources),
            "identifiers": len(timestamps),
            "proprietary_pct": round(100 * proprietary / len(clean), 2) if clean else None,
        },
        "integrity": {
            "rows_out_of_frame": integrity.rows_out_of_frame,
            "runs": [run._asdict() for run in integrity.runs],
            "frames_recovered": len(integrity.recovered),
            "steady_identifiers": len(loss_by_identifier),
            "steady_frames_missing": missing_total,
            "missing_pct": _quantile(loss_by_identifier, 0.5),
            "missing_pct_p25": _quantile(loss_by_identifier, 0.25),
            "missing_pct_p75": _quantile(loss_by_identifier, 0.75),
            "recorder_reported_drops": summary.reported_drops if summary else None,
        },
        "sources": sorted(sources.values(), key=lambda s: -s["frames"]),
        "groups": groups,
        "transport": _transport_section(messages, reassembler),
        "signals": [
            {"signal": name, "sa": sa, "unit": BY_NAME[name].unit, **stats} for (name, sa), stats in sorted(signals.items())
        ],
        "eld": _eld_section(signals, vins, requests),
        "vin": vins,
        "diagnostics": diagnostic_rows,
        "requests": [{"sa": sa, "da": da, "pgn": pgn, "count": n} for (sa, da, pgn), n in sorted(requests.items())],
        "acknowledgments": [{"sa": sa, "kind": kind, "count": n} for (sa, kind), n in sorted(acknowledgments.items())],
    }


def analyze_file(path: str | Path, summary_path: str | Path | None = None, *, repair: bool = True) -> dict:
    """Read a recorder CSV, pick up its sidecar when there is one, and analyze it."""
    frames, malformed = read_recorder_csv(path)
    if summary_path is None:
        summary_path = find_summary(path)
    summary = read_summary(summary_path) if summary_path else None
    report = analyze(frames, summary, repair=repair)
    report["capture"] = {"file": Path(path).name, "malformed_rows": malformed, **report["capture"]}
    return report
