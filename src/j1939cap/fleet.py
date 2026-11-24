"""The trucks side by side: one row per device, one row per make.

`summarize` looks at captures one by one and `pair` sets a capture next to its device log. This module asks two
questions of the whole set:

- For a device: is its capture usable, is there a log, and which of the five values an ELD has to record (VIN,
  vehicle speed, engine speed, engine hours, distance) could the device not read while the truck was moving?
- For a device again, from the events of its log: how many reading cycles did it have to retry, and how many
  error codes did it raise? These do not come from the readings, so they check what the readings show.
- For a make: which controllers send speed and distance on its bus, how many trucks broadcast a VIN, how many
  carry two distances that disagree, and how many devices read all five values?
- For the trucks whose distance sources are far apart: which controller's distance still fits the engine hours?
  The trucks whose sources agree give the band a distance per engine hour normally falls in, and each controller
  of a truck that disagrees is held against it. Engine hours are a counter of the engine controller as well, so
  the check can fail on both sides; it is evidence, not a ruling.

Nothing is labelled by hand. A value counts as unreadable by a rule on the log extract, and the rule's numbers are
the constants below. `sensitivity` recomputes the counts with each number halved and doubled, so a reader can see
which conclusions lean on a threshold.

Make and model year come from `devices.csv`. For a truck that broadcasts a VIN they can be read off the stand-in
(manufacturer code and model-year character are kept), and the table says `vin`. For a truck that sends none, or
one the manufacturer table does not know, they come from the fleet's records and the table says `records`.

Groups of fewer than five trucks are not tabulated: the make row shows `<5` and nothing else.
"""

from __future__ import annotations

import csv
import re
from collections import Counter
from datetime import timedelta
from pathlib import Path
from statistics import quantiles

from . import summarize
from .devicelog import tables
from .devicelog.tables import DeviceLog, Reading

USABLE_FRAMES = 10_000  # a switched-off or nearly silent bus leaves fewer than this in 240 seconds
MOVING_KMH = 20.0  # GPS speed from which the truck is certainly under way and the engine certainly running
UNREADABLE_SHARE = 0.2  # share of moving readings without the value
ENOUGH_READINGS = 20  # moving readings a log needs before anything is said about it, ten minutes of driving
BEFORE_CAPTURE = timedelta(seconds=30)  # around a capture the device is out of its reading loop (see pairing)
AFTER_CAPTURE = timedelta(minutes=12)
APART_KM = 1.0  # two distance sources further apart than this disagree
FAR_APART_KM = 100_000.0  # further apart than any difference in calibration: one of the counters was reset or replaced
BAND_TENTHS = 10  # the band is the middle eight tenths of the trucks whose sources agree; fewer trucks than this give none
SMALL_GROUP = 5
ENGINE = "00"
DRIVING = "Driving"  # the device's name for the state in which it reads the engine data
BELOW, INSIDE, ABOVE = "below", "inside", "above"

ITEMS = (("vin", "vin"), ("speed", "speed_kmh"), ("engine_speed", "rpm"), ("engine_hours", "engine_hours"), ("distance", "distance_km"))
NOT_ENOUGH = "n/a"
MAKE_KEYS = ("speed_from", "distance_from", "vin_on_bus", "two_distance_sources", "sources_apart", "with_log", "reads_all_five")
TROUBLED_KEYS = ("device_key", "make", "model_year", "moving_readings", "unreadable", "retry_pct", "error_pct")
TROUBLED_KEYS += ("distance_sources", "frames")
MAKE_HEADER = ["make", "trucks", "speed sent by", "distance sent by", "VIN on the bus", "two distance sources", "more than 1 km apart"]
DEVICE_HEADER = ["device", "make", "model year", "known from", "captures", "frames", "usable", "speed from", "distance from", "spread km"]
DEVICE_HEADER += ["engine hours", "km per engine hour"]
VIN, RECORDS = "vin", "records"

DEVICE_TABLE_HEADER = ("device_key", "make", "model_year", "known_from")
COLUMNS = (
    "device_key",
    "make",
    "model_year",
    "known_from",
    "captures",
    "frames",
    "capture_usable",
    "speed_sources",
    "distance_sources",
    "distance_spread_km",
    "engine_hours",
    "km_per_engine_hour",
    "vin_on_bus",
    "logs",
    "moving_readings",
    "unreadable",
    "retry_pct",
    "error_pct",
)


class DeviceTableError(Exception):
    """`devices.csv` is not the table this module expects."""


def read_devices(path: str | Path) -> dict[str, dict[str, str]]:
    with open(path, encoding="ascii", newline="") as fh:
        rows = list(csv.reader(fh))
    if not rows or tuple(rows[0]) != DEVICE_TABLE_HEADER:
        raise DeviceTableError(f"{path}: the header is not {','.join(DEVICE_TABLE_HEADER)}")
    out: dict[str, dict[str, str]] = {}
    for n, row in enumerate(rows[1:], start=2):
        cells = dict(zip(DEVICE_TABLE_HEADER, row, strict=False))
        ok = (
            len(row) == len(DEVICE_TABLE_HEADER)
            and re.fullmatch(r"DEV-[0-9a-f]{10}", cells["device_key"])
            and re.fullmatch(r"[A-Za-z][A-Za-z ]{0,23}", cells["make"])
            and re.fullmatch(r"(?:19|20)\d\d|", cells["model_year"])
            and cells["known_from"] in (VIN, RECORDS)
            and cells["device_key"] not in out
        )
        if not ok:
            raise DeviceTableError(f"{path}, line {n}: not a device row")
        out[cells["device_key"]] = cells
    return out


def _away_from_captures(log: DeviceLog):
    """A test for a moment of a log: is it clear of every capture the log holds?"""
    captures = [e.time for e in log.events if e.event == "capture_requested"]
    return lambda moment: not any(start - BEFORE_CAPTURE <= moment <= start + AFTER_CAPTURE for start in captures)


def moving_readings(log: DeviceLog, moving_kmh: float = MOVING_KMH) -> list[Reading]:
    """Readings taken with the truck under way, away from any capture."""
    away = _away_from_captures(log)
    return [r for r in log.readings if (r.gps_speed_kmh or 0.0) >= moving_kmh and away(r.time)]


def unreadable(
    logs: list[DeviceLog], share: float = UNREADABLE_SHARE, moving_kmh: float = MOVING_KMH
) -> tuple[int, tuple[str, ...] | None]:
    """Moving readings in a device's logs, and the values missing in more than `share` of them.

    None instead of the values when the logs hold too few moving readings to say.
    """
    readings = [r for log in logs for r in moving_readings(log, moving_kmh)]
    if len(readings) < ENOUGH_READINGS:
        return len(readings), None
    missing = Counter(name for r in readings for name, column in ITEMS if getattr(r, column) in (None, ""))
    return len(readings), tuple(name for name, _ in ITEMS if missing[name] > share * len(readings))


def own_account(logs: list[DeviceLog]) -> tuple[float | None, float | None]:
    """What the device itself notes while driving, away from any capture, per 100 reading cycles: the cycles it had
    to retry, and the error codes it raised. None when the logs hold too few driving cycles to say.

    Both are counted from the events of a log and not from its readings, so they are a second and a third look at
    what the readings show.
    """
    cycles = retried = errors = 0
    for log in logs:
        away = _away_from_captures(log)
        cycles += sum(1 for r in log.readings if r.state == DRIVING and away(r.time))
        for e in log.events:
            if e.values.get("state") != DRIVING or not away(e.time):
                continue
            retried += e.event == "reading_retry" and e.values.get("attempt") == "1"
            errors += e.event == "error_sent"
    if cycles < ENOUGH_READINGS:
        return None, None
    return round(100 * retried / cycles, 1), round(100 * errors / cycles, 1)


def device_rows(captures: list[dict], logs: list[DeviceLog], devices: dict[str, dict[str, str]]) -> list[dict]:
    """One row per device that has a capture, a log or a line in the device table. `captures` are summarize rows."""
    keys = sorted({c["capture"].split("_")[0] for c in captures} | {log.device_key for log in logs} | set(devices))
    rows = []
    for key in keys:
        own = [c for c in captures if c["capture"].split("_")[0] == key]
        best = max(own, key=lambda c: (c["frames"], c["capture"]), default=None)
        own_logs = [log for log in logs if log.device_key == key]
        count, values = unreadable(own_logs) if own_logs else (None, None)
        retried, errors = own_account(own_logs) if own_logs else (None, None)
        known = devices.get(key, {})
        rows.append(
            {
                "device_key": key,
                "make": known.get("make", ""),
                "model_year": known.get("model_year", ""),
                "known_from": known.get("known_from", ""),
                "captures": len(own),
                "frames": best["frames"] if best else None,
                "capture_usable": bool(best) and best["frames"] >= USABLE_FRAMES,
                "speed_sources": best["speed_sources"] if best else "",
                "distance_sources": best["distance_sources"] if best else "",
                "distance_spread_km": best["distance_spread_km"] if best else None,
                "engine_hours": best["engine_hours"] if best else None,
                "km_per_engine_hour": best["km_per_engine_hour"] if best else "",
                "vin_on_bus": best["vin_on_bus"] if best else None,
                "logs": len(own_logs),
                "moving_readings": count,
                "unreadable": "" if not own_logs else NOT_ENOUGH if values is None else " ".join(values),
                "retry_pct": retried,
                "error_pct": errors,
            }
        )
    return rows


def _patterns(values: list[str]) -> str:
    counted = Counter(value.replace(" ", "+") or "none" for value in values)
    return ", ".join(f"{pattern} x{n}" for pattern, n in sorted(counted.items(), key=lambda item: (-item[1], item[0])))


def make_rows(rows: list[dict]) -> list[dict]:
    """One row per make over the trucks with a usable capture; makes with fewer than five such trucks only named."""
    usable = [r for r in rows if r["capture_usable"]]
    out = []
    for make in sorted({r["make"] or "unknown" for r in usable}):
        trucks = [r for r in usable if (r["make"] or "unknown") == make]
        if len(trucks) < SMALL_GROUP:
            out.append({"make": make, "trucks": f"<{SMALL_GROUP}"})
            continue
        two = [r for r in trucks if len(r["distance_sources"].split()) > 1]
        checked = [r for r in trucks if r["logs"] and r["unreadable"] != NOT_ENOUGH]
        out.append(
            {
                "make": make,
                "trucks": len(trucks),
                "speed_from": _patterns([r["speed_sources"] for r in trucks]),
                "distance_from": _patterns([r["distance_sources"] for r in trucks]),
                "vin_on_bus": sum(1 for r in trucks if r["vin_on_bus"]),
                "two_distance_sources": len(two),
                "sources_apart": sum(1 for r in two if (r["distance_spread_km"] or 0.0) > APART_KM),
                "with_log": len(checked),
                "reads_all_five": sum(1 for r in checked if not r["unreadable"]),
            }
        )
    return out


def per_hour(row: dict) -> dict[str, float]:
    """`00 18.4; 31 44.9` as {"00": 18.4, "31": 44.9}."""
    return {address: float(value) for address, value in (part.split() for part in row["km_per_engine_hour"].split("; ") if part)}


def distance_check(rows: list[dict]) -> dict:
    """Distance per engine hour of the trucks whose sources are far apart, against the trucks whose sources agree.

    `band` is the middle eight tenths of the agreeing trucks, None when there are too few. Each truck in `far` is its
    device row with `positions`: for every controller, whether its figure is below, inside or above the band.
    """
    usable = [r for r in rows if r["capture_usable"] and per_hour(r)]
    agreeing = [max(per_hour(r).values()) for r in usable if (r["distance_spread_km"] or 0.0) <= APART_KM]
    band = None
    if len(agreeing) >= BAND_TENTHS:
        tenths = quantiles(agreeing, n=BAND_TENTHS)
        band = (tenths[0], tenths[-1])
    far = []
    for row in usable:
        if band is None or (row["distance_spread_km"] or 0.0) <= FAR_APART_KM:
            continue
        positions = {a: BELOW if v < band[0] else ABOVE if v > band[1] else INSIDE for a, v in per_hour(row).items()}
        far.append({**row, "positions": positions})
    engine_below = [r for r in far if r["positions"].get(ENGINE) == BELOW]
    return {
        "agreeing": len(agreeing),
        "band": band,
        "far": far,
        "engine_below": len(engine_below),
        "other_inside": sum(1 for r in engine_below if INSIDE in [p for a, p in r["positions"].items() if a != ENGINE]),
        "engine_inside_other_outside": sum(
            1 for r in far if r["positions"].get(ENGINE) == INSIDE and INSIDE not in [p for a, p in r["positions"].items() if a != ENGINE]
        ),
    }


def sensitivity(captures: list[dict], logs: list[DeviceLog]) -> list[dict]:
    """The counts that rest on a threshold, with the threshold halved, as set, and doubled."""
    best: dict[str, int] = {}
    for c in captures:
        key = c["capture"].split("_")[0]
        best[key] = max(best.get(key, 0), c["frames"])
    by_device: dict[str, list[DeviceLog]] = {}
    for log in logs:
        by_device.setdefault(log.device_key, []).append(log)

    def with_unreadable(share: float, moving_kmh: float) -> str:
        results = [unreadable(own, share, moving_kmh)[1] for own in by_device.values()]
        return f"{sum(1 for values in results if values)} of {sum(1 for values in results if values is not None)}"

    out = [
        {
            "threshold": "frames for a usable capture",
            "set_to": USABLE_FRAMES,
            "counted": "devices with a usable capture",
            "halved": sum(1 for frames in best.values() if frames >= USABLE_FRAMES / 2),
            "as_set": sum(1 for frames in best.values() if frames >= USABLE_FRAMES),
            "doubled": sum(1 for frames in best.values() if frames >= USABLE_FRAMES * 2),
        },
        {
            "threshold": "share of moving readings without a value",
            "set_to": UNREADABLE_SHARE,
            "counted": "devices with an unreadable value, of devices with enough readings",
            "halved": with_unreadable(UNREADABLE_SHARE / 2, MOVING_KMH),
            "as_set": with_unreadable(UNREADABLE_SHARE, MOVING_KMH),
            "doubled": with_unreadable(UNREADABLE_SHARE * 2, MOVING_KMH),
        },
        {
            "threshold": "GPS speed that counts as moving, km/h",
            "set_to": MOVING_KMH,
            "counted": "devices with an unreadable value, of devices with enough readings",
            "halved": with_unreadable(UNREADABLE_SHARE, MOVING_KMH / 2),
            "as_set": with_unreadable(UNREADABLE_SHARE, MOVING_KMH),
            "doubled": with_unreadable(UNREADABLE_SHARE, MOVING_KMH * 2),
        },
    ]
    return out


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


def render_markdown(rows: list[dict], makes: list[dict], thresholds: list[dict]) -> str:
    usable = [r for r in rows if r["capture_usable"]]
    checked = [r for r in rows if r["logs"] and r["unreadable"] != NOT_ENOUGH]
    troubled = [r for r in checked if r["unreadable"]]
    out = ["# Devices and makes", ""]
    out.append(
        f"{len(rows)} devices, {len(usable)} with a usable capture ({USABLE_FRAMES:,} frames or more). "
        f"{len(checked)} have a log with at least {ENOUGH_READINGS} readings taken while moving; "
        f"in {len(troubled)} of those the device could not read one or more of the five values."
    )
    out += [
        "",
        "## By make",
        "",
        "Trucks with a usable capture. Addresses are J1939 source addresses in hex: `00+17 x9` means nine trucks on which"
        " both the engine (00) and the instrument cluster (17) send the value. These are counts of the trucks in the input,"
        " not rates for a make.",
        "",
    ]
    out += _table(
        [*MAKE_HEADER, "with a log", "device reads all five"],
        [[m["make"], m["trucks"]] + [m.get(key) for key in MAKE_KEYS] for m in makes],
    )
    check = distance_check(rows)
    if check["band"] is not None:
        low, high = check["band"]
        out += [
            "",
            "## Distance per engine hour",
            "",
            "Each controller's distance divided by the engine hours of the same bus: the truck's average speed over its life"
            f" with the engine running. The {check['agreeing']} trucks whose distance sources agree within {_text(APART_KM)} km, or"
            f" that have one source, put the middle eight tenths of that figure between {_text(low)} and {_text(high)} km per"
            " engine hour.",
            "",
        ]
        if check["far"]:
            out += [
                f"Of the {len(check['far'])} trucks whose sources are more than {FAR_APART_KM:,.0f} km apart, the engine's distance"
                f" is below that band on {check['engine_below']}, and on {check['other_inside']} of those another controller's is"
                f" inside it. On {check['engine_inside_other_outside']} the engine's is inside and the other controller's is not."
                " Engine hours are a counter of the engine controller too, so the check can fail on both sides.",
                "",
            ]
            out += _table(
                ["device", "make", "model year", "spread km", "engine hours", "km per engine hour, against the band"],
                [
                    [r["device_key"], r["make"], r["model_year"], r["distance_spread_km"], r["engine_hours"]]
                    + ["; ".join(f"{a} {_text(v)} {r['positions'][a]}" for a, v in per_hour(r).items())]
                    for r in sorted(check["far"], key=lambda r: r["distance_spread_km"])
                ],
            )
    out += ["", "## Devices that could not read a value", ""]
    fine = [r for r in checked if not r["unreadable"]]
    retried = [r["retry_pct"] for r in fine if r["retry_pct"] is not None]
    errors = [r["error_pct"] for r in fine if r["error_pct"] is not None]
    if troubled and retried and errors:
        out += [
            "The device's own log gives two more looks at the same devices, both counted per 100 reading cycles while driving"
            " and away from a capture: the cycles the device had to retry, and the error codes it raised. The"
            f" {len(fine)} devices that read all five values retry at most {_text(max(retried))} cycles in 100 and raise at most"
            f" {_text(max(errors))} codes per 100.",
            "",
        ]
    header = ["device", "make", "model year", "moving readings", "not read by the device", "retried cycles per 100"]
    header += ["error codes per 100 cycles", "distance on the bus from", "frames in the capture"]
    out += _table(header, [[r[key] for key in TROUBLED_KEYS] for r in troubled])
    out += ["", "## How much the thresholds matter", ""]
    out += _table(
        ["threshold", "set to", "what is counted", "halved", "as set", "doubled"],
        [[t["threshold"], t["set_to"], t["counted"], t["halved"], t["as_set"], t["doubled"]] for t in thresholds],
    )
    out += ["", "## Every device", ""]
    out += _table(
        [*DEVICE_HEADER, "VIN on bus", "logs", "moving readings", "not read", "retried per 100", "error codes per 100"],
        [[r[column] for column in COLUMNS] for r in rows],
    )
    return "\n".join(out) + "\n"


def write_reports(captures_dir: str | Path, logs_dir: str | Path, devices_path: str | Path, out_dir: str | Path) -> list[dict]:
    captures = summarize.collect(captures_dir)
    logs = tables.read_logs(logs_dir)
    rows = device_rows(captures, logs, read_devices(devices_path))
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    write_csv(out_dir / "devices.csv", rows)
    text = render_markdown(rows, make_rows(rows), sensitivity(captures, logs))
    (out_dir / "devices.md").write_text(text, encoding="utf-8", newline="\n")
    return rows
