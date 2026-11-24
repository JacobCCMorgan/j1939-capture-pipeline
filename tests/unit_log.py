"""A small made-up device log for the tests, and the rules that read it. Wording, serial number and values are invented.

The log belongs to the truck of `bus.truck()`: the unit reads its distance from the instrument cluster at address
0x17, which is 312.4 km behind the engine, and it goes quiet for six minutes around a capture.
"""

from datetime import datetime, timedelta, timezone

from bus import VIN

SERIAL = "AB123456"
START = datetime(2031, 3, 4, 10, 0, 0, tzinfo=timezone.utc)
CAPTURE_AT = START + timedelta(minutes=10)
CAPTURE_SECONDS = 20

RULES_DOCUMENT = {
    "line": r"^(?P<time>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) (?P<body>.*)$",
    "time_format": "%Y-%m-%d %H:%M:%S",
    "serial": r"unit (?P<serial>[A-Z]{2}\d{6})",
    "cycle_start": r"^cycle begin",
    "missing": ["N/A", ""],
    "fields": [
        {"column": "protocol", "pattern": r"^\[(?P<state>\w+)\] protocol=(?P<value>.*)$"},
        {"column": "vin", "pattern": r"^\[(?P<state>\w+)\] vin=(?P<value>.*)$"},
        {"column": "speed_kmh", "pattern": r"^\[(?P<state>\w+)\] speed=(?P<value>.*)$"},
        {"column": "rpm", "pattern": r"^\[(?P<state>\w+)\] rpm=(?P<value>.*)$"},
        {"column": "distance_km", "pattern": r"^\[(?P<state>\w+)\] distance=(?P<value>.*)$"},
        {"column": "engine_hours", "pattern": r"^\[(?P<state>\w+)\] hours=(?P<value>.*)$"},
        {"column": "distance_group", "pattern": r"^\[(?P<state>\w+)\] group=(?P<value>.*)$"},
        {"column": "distance_source", "pattern": r"^\[(?P<state>\w+)\] source=(?P<value>.*)$"},
        {"column": "gps_valid", "pattern": r"^\[(?P<state>\w+)\] gps fix=(?P<value>.*)$"},
        {"column": "gps_speed_kmh", "pattern": r"^\[(?P<state>\w+)\] gps speed=(?P<value>.*)$"},
        {"column": "sources", "pattern": r"^\[(?P<state>\w+)\] sources=(?P<value>.*)$"},
        {"column": "uploaded", "pattern": r"^\[(?P<state>\w+)\] sent to server"},
    ],
    "events": [
        {"event": "capture_requested", "pattern": r"^capture asked: group (?P<group>\d+), (?P<seconds>\d+) s"},
        {"event": "capture_started", "pattern": r"^capture recording (?P<seconds>\d+) s"},
        {
            "event": "capture_summary",
            "pattern": r"^capture summary \S+ time=(?P<seconds>\d+)s duration=(?P<duration_ms>\d+)ms written=(?P<written>\d+) "
            r"queue_dropped=(?P<queue_dropped>\d+) stop_ok=(?P<stop_ok>\w+) drop_count=(?P<drop_count>\d+)",
        },
        {"event": "capture_uploaded", "pattern": r"^capture uploaded"},
        {"event": "capture_ended", "pattern": r"^capture over, group (?P<group>\d+)"},
        {
            "event": "error_sent",
            "pattern": r"\[(?P<state>\w+)\] gps speed \((?P<gps_speed_kmh>\d+)\) but engine data missing, raising (?P<code>E\d+)",
        },
    ],
}


def stamp(moment):
    return moment.strftime("%Y-%m-%d %H:%M:%S")


def cycle(moment, *, km, state="Driving", vin=VIN, speed="88", hours="12345.50", sent=True):
    at = stamp(moment)
    lines = [
        f"{at} cycle begin",
        f"{at} [{state}] protocol=01",
        f"{at} [{state}] vin={vin}",
        f"{at} [{state}] speed={speed}",
        f"{at} [{state}] rpm=1200",
        f"{at} [{state}] distance={km}",
        f"{at} [{state}] hours={hours}",
        f"{at} [{state}] group=65217",
        f"{at} [{state}] source=17",
        f"{at} [{state}] gps fix=01",
        f"{at} [{state}] gps speed=87",
        f"{at} [{state}] sources=17,{km},00,N/A",
        f"{at} chatter that no rule asks for, driver 4711 at 34.0522,-118.2437",
    ]
    return lines + ([f"{at} [{state}] sent to server"] if sent else [])


def log_lines(rows_written=1000, km_after="499688.20"):
    """Twenty minutes: a reading every thirty seconds, a capture at minute ten, readings again from minute sixteen."""
    lines = [f"{stamp(START)} unit {SERIAL} up"]
    for n in range(20):  # 10:00:00 to 10:09:30
        lines += cycle(START + timedelta(seconds=30 * n), km=f"{499687.10 + 0.01 * n:.2f}")
    at = stamp(CAPTURE_AT)
    done = stamp(CAPTURE_AT + timedelta(seconds=CAPTURE_SECONDS + 1))
    lines += [
        f"{at} capture asked: group 2, {CAPTURE_SECONDS} s",
        f"{at} [Driving] leaving the loop for a capture",
        f"{at} capture mode true",
        f"{at} capture recording {CAPTURE_SECONDS} s -> /data/capture_{SERIAL}.csv",
        f"{stamp(CAPTURE_AT + timedelta(seconds=7))} source.go:100: link checksum failed",
        f"{done} capture mode false",
        f"{done} capture summary /data/capture_{SERIAL}.csv: time={CAPTURE_SECONDS}s duration=20000ms written={rows_written} "
        "queue_dropped=0 stop_ok=true drop_count=0",
        f"{stamp(CAPTURE_AT + timedelta(seconds=50))} capture uploaded",
        f"{stamp(CAPTURE_AT + timedelta(seconds=51))} capture over, group 2",
        f"{stamp(CAPTURE_AT + timedelta(seconds=52))} source.go:822: [Driving] gps speed (61) but engine data missing, raising E005",
        f"{stamp(CAPTURE_AT + timedelta(seconds=53))} holding back E005",
    ]
    lines += cycle(CAPTURE_AT + timedelta(seconds=55), km="N/A", speed="N/A", hours="N/A")
    for n in range(8):  # 10:16:00 to 10:19:30
        lines += cycle(CAPTURE_AT + timedelta(minutes=6, seconds=30 * n), km=f"{float(km_after) + 0.01 * n:.2f}", hours="12345.55")
    return lines


def write_log(path, lines=None):
    path.write_text("\n".join(lines if lines is not None else log_lines()) + "\n", encoding="utf-8")
    return path
