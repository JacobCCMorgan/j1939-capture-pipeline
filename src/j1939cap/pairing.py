"""A capture next to the device's own log of the same hours.

The capture is what the device recorded from the bus. The log extract is what the device's firmware decoded from
the same bus at the same time and decided to report. Both pass through the same receiver, so their agreement
checks two decoders against each other; it cannot show what never reached the device. Put side by side they
answer four things a capture cannot answer alone:

1. Is this log about this capture? The log has to span it, and its capture events have to agree with the
   capture's own summary: the request at the start time, the same number of rows written.
2. Does the device report what the bus says? Distance and engine hours only go up, so the values on the bus
   during the capture must lie between the device's last reading before it and its first reading after it.
   The VIN must be the same text.
3. Where does the device take the distance from? It names a parameter group and a source address. Other
   controllers on the bus may send a distance of their own, and it need not be the same number.
4. How long does the device pause? While it records, it leaves its reading loop: seconds without a complete
   reading, seconds between two uploads, and when each step of the capture happens, counted from its start.

Nothing here judges which distance is right. The pairing says what the device chose and what else was there, and
gives each controller's distance per engine hour as a check on it.
"""

from __future__ import annotations

import csv
from datetime import datetime, timedelta
from pathlib import Path
from statistics import median

from .core import vin as vin_rules
from .core.capture import capture_name
from .core.report import analyze_file, per_engine_hour_text
from .devicelog import tables
from .devicelog.tables import DeviceLog, Reading

DISTANCE_GROUPS = {65217: "total_vehicle_distance_hr", 65248: "total_vehicle_distance"}
HOURS_SIGNAL = "engine_total_hours"
SHORT = {"total_vehicle_distance_hr": "VDHR", "total_vehicle_distance": "VD"}
DISTANCE_TOLERANCE_KM = 0.5
HOURS_TOLERANCE = 0.06
REQUEST_WINDOW_S = 600
AFTERMATH = timedelta(minutes=15)
ENGINE = "00"
# the steps of a capture as the log notes them: column, event, name in the report
STEPS = (
    ("recording_started_s", "capture_started", "recording starts"),
    ("recording_ended_s", "capture_summary", "recording ends"),
    ("file_uploaded_s", "capture_uploaded", "file uploaded"),
    ("capture_closed_s", "capture_ended", "capture closed"),
)

BETWEEN = "between"
OUTSIDE = "outside"
NOT_ON_BUS = "not on the bus"
NO_READING = "no reading"
SAME = "same"
DIFFERENT = "different"
NOT_IN_LOG = "not in the log"

COLUMNS = (
    "capture",
    "log",
    "covered",
    "minutes_before",
    "minutes_after",
    "request_offset_s",
    "rows_agree",
    "device_group",
    "device_source",
    "reading_before_s",
    "reading_after_s",
    "distance_before_km",
    "bus_first_km",
    "bus_last_km",
    "distance_after_km",
    "distance_check",
    "hours_check",
    "vin_check",
    "bus_distance_sources",
    "bus_spread_km",
    "bus_km_per_engine_hour",
    "no_reading_s",
    "no_upload_s",
    "recording_started_s",
    "recording_ended_s",
    "file_uploaded_s",
    "capture_closed_s",
    "reading_back_s",
)


def _span(report: dict) -> tuple[datetime, datetime] | None:
    started = report["capture"]["started_at"]
    if started is None:
        return None
    start = tables.parse_time(started)
    return start, start + timedelta(milliseconds=report["capture"]["duration_ms"] or 0)


def find_log(device_key: str, start: datetime, logs: list[DeviceLog]) -> DeviceLog | None:
    """The log of this device that spans the start of the capture; failing that, its log nearest in time."""
    own = [log for log in logs if log.device_key == device_key]
    spanning = [log for log in own if log.first_time <= start <= log.last_time]
    if spanning:
        return spanning[0]
    return min(own, key=lambda log: min(abs(log.first_time - start), abs(log.last_time - start)), default=None)


def _complete(reading: Reading) -> bool:
    return None not in (reading.speed_kmh, reading.rpm, reading.distance_km, reading.engine_hours)


def _around(readings: list[Reading], start: datetime, end: datetime, wanted) -> tuple[Reading | None, Reading | None]:
    before = [r for r in readings if r.time < start and wanted(r)]
    after = [r for r in readings if r.time > end and wanted(r)]
    return (before[-1] if before else None), (after[0] if after else None)


def _bracket(low: float | None, first: float | None, last: float | None, high: float | None, tolerance: float) -> str:
    if first is None or last is None:
        return NOT_ON_BUS
    if low is None or high is None:
        return NO_READING
    return BETWEEN if low - tolerance <= first and last <= high + tolerance else OUTSIDE


def _signal(report: dict, name: str, sa: int | None = None) -> dict | None:
    found = [s for s in report["signals"] if s["signal"] == name and s["valid"] and (sa is None or s["sa"] == sa)]
    return found[0] if found else None


def pair(report: dict, log: DeviceLog | None) -> dict:
    row = dict.fromkeys(COLUMNS)
    row["capture"] = report["capture"].get("file", "")
    span = _span(report)
    distance = report["eld"]["distance"]
    row["bus_distance_sources"] = "; ".join(f"{s['sa']:02X} {SHORT[s['signal']]} {s['last']:.3f}" for s in distance["sources"])
    row["bus_spread_km"] = distance["spread_km"]
    row["bus_km_per_engine_hour"] = per_engine_hour_text(report)
    if log is None or span is None:
        return row
    start, end = span
    row["log"] = log.name
    row["covered"] = log.first_time <= start and end <= log.last_time
    row["minutes_before"] = round((start - log.first_time).total_seconds() / 60, 1)
    row["minutes_after"] = round((log.last_time - end).total_seconds() / 60, 1)

    requests = [e for e in log.events if e.event == "capture_requested" and abs((e.time - start).total_seconds()) <= REQUEST_WINDOW_S]
    if requests:
        nearest = min(requests, key=lambda e: abs((e.time - start).total_seconds()))
        row["request_offset_s"] = int((nearest.time - start).total_seconds())
    summaries = [e for e in log.events if e.event == "capture_summary" and start <= e.time <= end + AFTERMATH]
    if summaries and "written" in summaries[0].values:
        row["rows_agree"] = int(summaries[0].values["written"]) == report["capture"]["rows"]

    before, after = _around(log.readings, start, end, lambda r: r.distance_km is not None)
    chosen = before or after
    if chosen is not None and chosen.distance_source is not None:
        row["device_group"], row["device_source"] = chosen.distance_group, f"{chosen.distance_source:02X}"
    if before is not None:
        row["reading_before_s"], row["distance_before_km"] = int((start - before.time).total_seconds()), before.distance_km
    if after is not None:
        row["reading_after_s"], row["distance_after_km"] = int((after.time - end).total_seconds()), after.distance_km
    on_bus = None
    if chosen is not None and chosen.distance_group in DISTANCE_GROUPS and chosen.distance_source is not None:
        on_bus = _signal(report, DISTANCE_GROUPS[chosen.distance_group], chosen.distance_source)
    if on_bus is not None:
        row["bus_first_km"], row["bus_last_km"] = on_bus["first"], on_bus["last"]
    row["distance_check"] = _bracket(
        row["distance_before_km"], row["bus_first_km"], row["bus_last_km"], row["distance_after_km"], DISTANCE_TOLERANCE_KM
    )

    hours = _signal(report, HOURS_SIGNAL)
    low, high = _around(log.readings, start, end, lambda r: r.engine_hours is not None)
    row["hours_check"] = _bracket(
        low.engine_hours if low else None,
        hours["first"] if hours else None,
        hours["last"] if hours else None,
        high.engine_hours if high else None,
        HOURS_TOLERANCE,
    )

    on_the_bus = {v["vin"] for v in report["vin"] if v["vin"]}
    in_the_log = {vin_rules.clean(r.vin) for r in log.readings if r.vin}  # the report holds the text cleaned the same way
    if not on_the_bus:
        row["vin_check"] = NOT_ON_BUS
    elif not in_the_log:
        row["vin_check"] = NOT_IN_LOG
    else:
        row["vin_check"] = SAME if on_the_bus & in_the_log else DIFFERENT

    whole_before, whole_after = _around(log.readings, start, end, _complete)
    if whole_before and whole_after:
        row["no_reading_s"] = int((whole_after.time - whole_before.time).total_seconds())
    sent_before, sent_after = _around(log.readings, start, end, lambda r: r.uploaded)
    if sent_before and sent_after:
        row["no_upload_s"] = int((sent_after.time - sent_before.time).total_seconds())
    for column, event, _ in STEPS:
        earliest = start - timedelta(seconds=REQUEST_WINDOW_S)
        noted = [e.time for e in log.events if e.event == event and earliest <= e.time <= end + AFTERMATH]
        if noted:
            row[column] = int((noted[0] - start).total_seconds())
    if whole_after:
        row["reading_back_s"] = int((whole_after.time - start).total_seconds())
    return row


def pair_directories(captures_dir: str | Path, logs_dir: str | Path) -> list[dict]:
    """One row per capture, oldest first. A capture whose device has no log still gets its row."""
    logs = tables.read_logs(logs_dir)
    rows = []
    for path in sorted(Path(captures_dir).iterdir()):
        if not path.name.lower().endswith((".csv", ".csv.gz")) or path.name == tables.MANIFEST_NAME:
            continue
        report = analyze_file(path)
        name = capture_name(path)
        span = _span(report)
        log = find_log(name.split("_")[0], span[0], logs) if span else None
        row = pair(report, log)
        row["capture"] = name
        rows.append(row)
    return sorted(rows, key=lambda r: (r["capture"].split("_")[-1], r["capture"]))


def _text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:.3f}".rstrip("0").rstrip(".")
    return str(value)


def write_csv(path: str | Path, rows: list[dict]) -> None:
    with open(path, "w", encoding="ascii", newline="") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(COLUMNS)
        writer.writerows([_text(row[column]) for column in COLUMNS] for row in rows)


def _table(header: list[str], lines: list[list]) -> list[str]:
    out = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    out += ["| " + " | ".join(_text(cell) or "-" for cell in line) + " |" for line in lines]
    return out


def _source(row: dict) -> str:
    if row["device_source"] is None:
        return ""
    group = SHORT.get(DISTANCE_GROUPS.get(row["device_group"], ""), str(row["device_group"]))
    return f"{row['device_source']} {group}"


def render_markdown(rows: list[dict]) -> str:
    with_log = [r for r in rows if r["log"]]
    covered = [r for r in with_log if r["covered"]]
    out = ["# Captures and device logs", ""]
    out.append(f"{len(rows)} captures, {len(with_log)} with a log of the same device, {len(covered)} of those logs span the capture.")
    out.append("")
    if covered:
        checked = [r for r in covered if r["distance_check"] in (BETWEEN, OUTSIDE)]
        hours = [r for r in covered if r["hours_check"] in (BETWEEN, OUTSIDE)]
        vins = [r for r in covered if r["vin_check"] in (SAME, DIFFERENT)]
        requested = [r for r in covered if r["request_offset_s"] is not None]
        out += [
            "## What the pairs show",
            "",
            f"- The log has the capture request within {max((abs(r['request_offset_s']) for r in requested), default=0)} s of the "
            f"capture's start time in {len(requested)} of {len(covered)} pairs, and the row count in the log's capture summary "
            f"equals the rows of the file in {sum(1 for r in covered if r['rows_agree'])} of "
            f"{sum(1 for r in covered if r['rows_agree'] is not None)}.",
            f"- Distance: the bus values lie between the device's readings before and after in "
            f"{sum(1 for r in checked if r['distance_check'] == BETWEEN)} of {len(checked)} pairs that can be checked. "
            f"Engine hours: {sum(1 for r in hours if r['hours_check'] == BETWEEN)} of {len(hours)}. "
            f"VIN: the same text in {sum(1 for r in vins if r['vin_check'] == SAME)} of {len(vins)}.",
        ]
        named = [r for r in covered if r["device_source"] is not None]
        other = [r for r in named if r["device_source"] != ENGINE]
        engine_too = [r for r in other if any(part.startswith(ENGINE + " ") for part in r["bus_distance_sources"].split("; "))]
        if named:
            out.append(
                f"- Distance source: the device reports a controller other than the engine (address {ENGINE}) in {len(other)} of "
                f"{len(named)} pairs; in {len(engine_too)} of those the engine sends a distance too."
            )
        gaps = [r["no_reading_s"] for r in covered if r["no_reading_s"] is not None]
        uploads = [r["no_upload_s"] for r in covered if r["no_upload_s"] is not None]
        if gaps and uploads:
            out.append(
                f"- While it records, the device pauses its reading loop: {min(gaps)} to {max(gaps)} s without a complete reading "
                f"(median {int(median(gaps))}), {min(uploads)} to {max(uploads)} s between uploads (median {int(median(uploads))})."
            )
        out.append("")
    out += ["## Is the log about the capture", ""]
    out += _table(
        ["capture", "log spans it", "min before", "min after", "request offset s", "rows agree"],
        [[r["capture"], r["covered"], r["minutes_before"], r["minutes_after"], r["request_offset_s"], r["rows_agree"]] for r in with_log],
    )
    out += ["", "## What the device reports against what the bus says", ""]
    out += _table(
        ["capture", "device source", "before km", "bus first km", "bus last km", "after km", "distance", "hours", "vin"],
        [
            [r["capture"], _source(r), r["distance_before_km"], r["bus_first_km"], r["bus_last_km"], r["distance_after_km"]]
            + [r["distance_check"], r["hours_check"], r["vin_check"]]
            for r in covered
        ],
    )
    out += [
        "",
        "## Distance sources on the bus and the one the device uses",
        "",
        "Km per engine hour is each controller's distance divided by the engine hours on the bus, by source address.",
        "",
    ]
    out += _table(
        ["capture", "device source", "on the bus (address, group, last km)", "spread km", "km per engine hour"],
        [[r["capture"], _source(r), r["bus_distance_sources"], r["bus_spread_km"], r["bus_km_per_engine_hour"]] for r in covered],
    )
    out += ["", "## The pause around a capture", "", "Seconds after the start of the capture at which the log notes each step.", ""]
    steps = [(name, [r[column] for r in covered if r[column] is not None]) for column, _, name in STEPS]
    steps.append(("first complete reading", [r["reading_back_s"] for r in covered if r["reading_back_s"] is not None]))
    out += _table(
        ["step", "pairs", "median s", "earliest s", "latest s"],
        [[name, len(seconds), int(median(seconds)), min(seconds), max(seconds)] for name, seconds in steps if seconds],
    )
    out += [""]
    out += _table(
        ["capture", "s without a reading", "s between uploads", "recording ends", "file uploaded", "capture closed", "reading back"],
        [
            [r["capture"], r["no_reading_s"], r["no_upload_s"], r["recording_ended_s"], r["file_uploaded_s"], r["capture_closed_s"]]
            + [r["reading_back_s"]]
            for r in covered
        ],
    )
    return "\n".join(out) + "\n"


def write_reports(captures_dir: str | Path, logs_dir: str | Path, out_dir: str | Path) -> list[dict]:
    rows = pair_directories(captures_dir, logs_dir)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    write_csv(out_dir / "pairing.csv", rows)
    (out_dir / "pairing.md").write_text(render_markdown(rows), encoding="utf-8", newline="\n")
    return rows
