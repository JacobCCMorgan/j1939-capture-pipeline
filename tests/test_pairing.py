from datetime import timedelta

from bus import truck
from unit_log import CAPTURE_AT, CAPTURE_SECONDS, RULES_DOCUMENT, SERIAL, log_lines, write_log

from j1939cap import pairing
from j1939cap.core.capture import CaptureSummary
from j1939cap.core.report import analyze
from j1939cap.deid import capture as deid
from j1939cap.deid.pseudonym import Pseudonymizer
from j1939cap.devicelog import extract, tables
from j1939cap.devicelog import rules as log_rules

P = Pseudonymizer(bytes.fromhex("000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f"))
RULES = log_rules.parse(RULES_DOCUMENT)
ROWS = truck(seconds=CAPTURE_SECONDS)
SUMMARY = {
    "sn": SERIAL,
    "request_time_sec": str(CAPTURE_SECONDS),
    "start_time": f"{CAPTURE_AT:%Y-%m-%d %H:%M:%S}",
    "duration_ms": str(CAPTURE_SECONDS * 1000),
    "records_written": str(len(ROWS)),
    "complete": "true",
}


def published_capture():
    """The report of the de-identified capture, as someone without the raw file would compute it."""
    return analyze(deid.deidentify_rows(ROWS, CAPTURE_SECONDS * 1000, P).rows, CaptureSummary(dict(SUMMARY)))


def published_log(tmp_path, **changes):
    out = tmp_path / "logs"
    entry = extract.extract_file(write_log(tmp_path / "unit.log", log_lines(rows_written=len(ROWS), **changes)), RULES, out, P)
    extract.write_manifest(out, [entry])
    return tables.read_logs(out)[0]


def test_a_log_that_spans_the_capture_agrees_with_it(tmp_path):
    row = pairing.pair(published_capture(), published_log(tmp_path))
    assert (row["covered"], row["minutes_before"], row["request_offset_s"], row["rows_agree"]) == (True, 10.0, 0, True)
    # the unit reads the instrument cluster, 312.4 km behind the engine
    assert (row["device_group"], row["device_source"]) == (65217, "17")
    bracket = (row["distance_before_km"], row["bus_first_km"], row["bus_last_km"], row["distance_after_km"])
    assert bracket == (499687.29, 499687.6, 499688.075, 499688.2)
    assert (row["distance_check"], row["hours_check"], row["vin_check"]) == (pairing.BETWEEN, pairing.BETWEEN, pairing.SAME)
    assert row["bus_distance_sources"] == "00 VDHR 500000.475; 17 VDHR 499688.075" and row["bus_spread_km"] == 312.4
    # last whole reading 10:09:30, first whole one 10:16:00; the reading at 10:10:55 was sent but held no values
    assert (row["reading_before_s"], row["reading_after_s"], row["no_reading_s"], row["no_upload_s"]) == (30, 340, 390, 85)
    # 500,000 km and 499,688 km over 12,345.5 engine hours
    assert row["bus_km_per_engine_hour"] == "00 40.5; 17 40.5"
    # the steps of the capture, in seconds from its start; the first complete reading is the one at minute sixteen
    steps = ("recording_started_s", "recording_ended_s", "file_uploaded_s", "capture_closed_s", "reading_back_s")
    assert tuple(row[step] for step in steps) == (0, 21, 50, 51, 360)


def test_a_distance_the_readings_do_not_bracket_is_outside(tmp_path):
    row = pairing.pair(published_capture(), published_log(tmp_path, km_after="499600.00"))
    assert (row["distance_check"], row["hours_check"]) == (pairing.OUTSIDE, pairing.BETWEEN)


def test_what_is_missing_on_one_side_is_named(tmp_path):
    log = published_log(tmp_path)
    no_engine_data = analyze([f for f in ROWS if f.can_id & 0xFF not in (0x00, 0x17)], CaptureSummary(dict(SUMMARY)))
    row = pairing.pair(no_engine_data, log)
    assert (row["distance_check"], row["hours_check"], row["vin_check"]) == (pairing.NOT_ON_BUS, pairing.NOT_ON_BUS, pairing.NOT_ON_BUS)

    no_vin = log._replace(readings=[r._replace(vin="", distance_km=None, engine_hours=None) for r in log.readings])
    row = pairing.pair(published_capture(), no_vin)
    assert (row["distance_check"], row["hours_check"], row["vin_check"]) == (pairing.NOT_ON_BUS, pairing.NO_READING, pairing.NOT_IN_LOG)
    assert row["device_source"] is None


def test_a_log_is_matched_by_device_and_time(tmp_path):
    log = published_log(tmp_path)
    assert pairing.find_log(log.device_key, CAPTURE_AT, [log]) is log
    assert pairing.find_log("DEV-0000000000", CAPTURE_AT, [log]) is None
    later = pairing.find_log(log.device_key, CAPTURE_AT + timedelta(hours=5), [log])  # its own log, though it ended hours before
    report = published_capture()
    report["capture"]["started_at"] = tables.format_time(CAPTURE_AT + timedelta(hours=5))
    row = pairing.pair(report, later)
    assert (row["log"], row["covered"], row["request_offset_s"], row["distance_check"]) == (log.name, False, None, pairing.NO_READING)
    assert pairing.pair(published_capture(), None)["log"] is None
