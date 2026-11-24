"""Every capture of a directory side by side.

`summarize` analyzes each capture with the same report the command line prints for one, keeps the figures that are
worth comparing across vehicles, and writes them twice: `captures.csv` for programs and `captures.md` for people.
Nothing in the output depends on when or where it was produced, so the files can be committed and a fresh run can
be compared with them byte for byte.
"""

from __future__ import annotations

import csv
from pathlib import Path

from .core.capture import capture_name
from .core.report import analyze_file
from .core.signals import VALID
from .deid.capture import MANIFEST_NAME, read_manifest

COLUMNS = (
    "capture",
    "device_key",
    "vehicle_key",
    "started_at",
    "firmware",
    "can_firmware",
    "requested_s",
    "duration_s",
    "last_frame_s",
    "recorder_complete",
    "recorder_reported_drops",
    "rows",
    "frames",
    "frames_per_second",
    "sources",
    "identifiers",
    "proprietary_pct",
    "rows_out_of_frame",
    "runs",
    "frames_recovered",
    "steady_identifiers",
    "missing_pct",
    "missing_pct_p25",
    "missing_pct_p75",
    "transport_announced",
    "transport_complete",
    "transport_complete_pct",
    "transport_orphan_frames",
    "make",
    "model_year",
    "vin_on_bus",
    "vin_check_digit_ok",
    "vin_requests",
    "speed_sources",
    "engine_speed_sources",
    "distance_sources",
    "distance_spread_km",
    "hours_sources",
    "max_speed_kmh",
    "sources_with_active_codes",
    "active_codes",
    "negative_acknowledgments",
)


def _sources(item: dict) -> str:
    return " ".join(sorted({f"{s['sa']:02X}" for s in item["sources"]}))


def row_of(report: dict, keys: dict[str, str]) -> dict:
    cap, integ, tp, eld = report["capture"], report["integrity"], report["transport"], report["eld"]
    vin = next((v for v in report["vin"] if v["vin"]), None)
    speeds = [s["max"] for s in report["signals"] if s["signal"] == "wheel_based_vehicle_speed" and s[VALID]]
    active = [d for d in report["diagnostics"] if d["message"] == "DM1" and d["dtcs"]]
    return {
        "capture": capture_name(cap["file"]),
        "device_key": keys.get("device_key", ""),
        "vehicle_key": keys.get("vehicle_key", ""),
        "started_at": cap["started_at"],
        "firmware": cap["firmware"],
        "can_firmware": cap["can_firmware"],
        "requested_s": cap["requested_s"],
        "duration_s": round(cap["duration_ms"] / 1000, 1) if cap["duration_ms"] else None,
        "last_frame_s": round(cap["last_frame_ms"] / 1000, 1) if cap["last_frame_ms"] is not None else None,
        "recorder_complete": cap["complete"],
        "recorder_reported_drops": integ["recorder_reported_drops"],
        "rows": cap["rows"],
        "frames": cap["frames"],
        "frames_per_second": cap["frames_per_second"],
        "sources": cap["sources"],
        "identifiers": cap["identifiers"],
        "proprietary_pct": cap["proprietary_pct"],
        "rows_out_of_frame": integ["rows_out_of_frame"],
        "runs": len(integ["runs"]),
        "frames_recovered": integ["frames_recovered"],
        "steady_identifiers": integ["steady_identifiers"],
        "missing_pct": integ["missing_pct"],
        "missing_pct_p25": integ["missing_pct_p25"],
        "missing_pct_p75": integ["missing_pct_p75"],
        "transport_announced": tp["announced"],
        "transport_complete": tp["complete"],
        "transport_complete_pct": tp["complete_pct"],
        "transport_orphan_frames": tp["orphan_data_frames"],
        "make": vin["manufacturer"] if vin else None,
        "model_year": vin["model_year"] if vin else None,
        "vin_on_bus": eld["vin"]["available"],
        "vin_check_digit_ok": eld["vin"]["check_digit_ok"] if eld["vin"]["available"] else None,
        "vin_requests": eld["vin"]["requested"],
        "speed_sources": _sources(eld["vehicle_speed"]),
        "engine_speed_sources": _sources(eld["engine_speed"]),
        "distance_sources": _sources(eld["distance"]),
        "distance_spread_km": eld["distance"]["spread_km"],
        "hours_sources": _sources(eld["engine_hours"]),
        "max_speed_kmh": round(max(speeds), 1) if speeds else None,
        "sources_with_active_codes": len(active),
        "active_codes": sum(len(d["dtcs"]) for d in active),
        "negative_acknowledgments": sum(a["count"] for a in report["acknowledgments"] if a["kind"] == "negative"),
    }


def collect(directory: str | Path) -> list[dict]:
    """One row per capture file in the directory, oldest first."""
    directory = Path(directory)
    keys: dict[str, dict[str, str]] = {}
    if (directory / MANIFEST_NAME).is_file():
        keys = {entry["capture"]: entry for entry in read_manifest(directory)}
    paths = sorted(p for p in directory.iterdir() if p.name.lower().endswith((".csv", ".csv.gz")) and p.name != MANIFEST_NAME)
    rows = [row_of(analyze_file(path), keys.get(capture_name(path), {})) for path in paths]
    return sorted(rows, key=lambda r: (r["started_at"] or "", r["capture"]))


def _text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
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


def _vehicle(row: dict) -> str:
    if row["make"]:
        return f"{row['make']} {row['model_year']}"
    return "VIN on the bus, maker not in the table" if row["vin_on_bus"] else "no VIN on the bus"


def _share(part: int, whole: int) -> str:
    return f"{100 * part / whole:.1f}%" if whole else "-"


def _pct(value: float | None) -> str | None:
    return None if value is None else f"{value:.1f}%"


def _missing(row: dict) -> str | None:
    if row["missing_pct"] is None:
        return None
    return f"{row['missing_pct']:.1f}% ({row['missing_pct_p25']:.1f} to {row['missing_pct_p75']:.1f})"


def _long_messages(row: dict) -> str | None:
    if not row["transport_announced"]:
        return None
    return f"{row['transport_complete']} of {row['transport_announced']} ({row['transport_complete_pct']:.1f}%)"


def render_markdown(rows: list[dict], manifest: list[dict[str, str]] | None = None) -> str:
    live = [r for r in rows if r["frames"]]
    devices = {r["device_key"] or r["capture"] for r in rows}
    empty = len(rows) - len(live)
    lines = [
        "# Captures side by side",
        "",
        f"{len(rows)} captures from {len(devices)} devices, {sum(r['frames'] for r in rows):,} frames. "
        f"{empty} hold{'s' if empty == 1 else ''} no frames at all.",
        "",
        "Written by `j1939cap summarize`; `captures.csv` has every column. Source addresses are hexadecimal.",
        "",
        "## What was recorded",
        "",
        "The recorder is asked for a number of seconds and reports how long it ran. Frames end at is the timestamp of the",
        "last frame in the file.",
        "",
    ]
    header = ["capture", "vehicle", "asked for (s)", "ran (s)", "frames end at (s)", "frames", "sources", "proprietary"]
    header.append("top speed (km/h)")
    lines += _table(
        header,
        [
            [
                r["capture"],
                _vehicle(r) if r["frames"] else None,
                r["requested_s"],
                r["duration_s"],
                r["last_frame_s"],
                f"{r['frames']:,}",
                r["sources"],
                _pct(r["proprietary_pct"]),
                r["max_speed_kmh"],
            ]
            for r in rows
        ],
    )
    lines += [
        "",
        "## Is the recording intact",
        "",
        "The first two columns are the recorder's own account. Rows out of frame were cut at the wrong byte offset;",
        "frames recovered were read back out of them. Frames missing is an estimate: the median, over every steadily",
        "repeating identifier, of the share of its frames that are absent, with the quartiles in brackets. Long messages",
        "complete counts the multi-packet messages that arrived whole out of those announced.",
        "",
    ]
    header = ["capture", "says dropped", "says complete", "rows out of frame", "frames recovered", "frames missing"]
    header.append("long messages complete")
    lines += _table(
        header,
        [
            [
                r["capture"],
                r["recorder_reported_drops"],
                r["recorder_complete"],
                r["rows_out_of_frame"],
                r["frames_recovered"],
                _missing(r),
                _long_messages(r),
            ]
            for r in rows
        ],
    )
    lines += [
        "",
        "## What an electronic logging device needs",
        "",
        "Which source addresses send each item. Distance spread is the gap between the last readings of the sources.",
        "",
    ]
    header = ["capture", "vehicle speed", "engine speed", "distance", "distance spread (km)", "engine hours", "VIN broadcast"]
    header.append("VIN requests")
    lines += _table(
        header,
        [
            [
                r["capture"],
                r["speed_sources"],
                r["engine_speed_sources"],
                r["distance_sources"],
                r["distance_spread_km"],
                r["hours_sources"],
                r["vin_on_bus"],
                r["vin_requests"],
            ]
            for r in live
        ],
    )
    lines += ["", "## Trouble codes and refusals", ""]
    lines += _table(
        ["capture", "sources with active codes", "active codes", "negative acknowledgments"],
        [[r["capture"], r["sources_with_active_codes"], r["active_codes"], r["negative_acknowledgments"]] for r in live],
    )
    if manifest:
        lines += ["", "## What the published files contain", "", *_published(manifest)]
    return "\n".join(lines) + "\n"


def _published(manifest: list[dict[str, str]]) -> list[str]:
    def total(*names: str) -> int:
        return sum(int(entry[name]) for entry in manifest for name in names)

    kept = total("kept", "transport_control", "transport_data_kept")
    masked = total("masked", "transport_data_masked")
    rewritten = total("vin_rewritten", "position_coarsened", "request_trimmed", "identity_cleared")
    frames = kept + masked + rewritten
    in_runs = total("rows_out_of_frame")
    recovered = frames - (total("rows") - in_runs)
    return [
        f"Of {frames:,} frames, {kept:,} ({_share(kept, frames)}) carry their original data bytes, {masked:,} ({_share(masked, frames)})",
        f"keep timestamp and identifier with the data filled with 0xFF, and {rewritten:,} ({_share(rewritten, frames)}) were rewritten:",
        f"VIN, position, request or address claim. The {in_runs} rows of the out-of-frame runs hold {recovered} of those frames; what is",
        "left of those rows is 0xFF, apart from the timestamp and identifier that follow a recovered frame.",
    ]


def summarize(directory: str | Path, out_dir: str | Path) -> list[dict]:
    rows = collect(directory)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = read_manifest(directory) if (Path(directory) / MANIFEST_NAME).is_file() else None
    write_csv(out_dir / "captures.csv", rows)
    (out_dir / "captures.md").write_text(render_markdown(rows, manifest), encoding="utf-8", newline="\n")
    return rows
