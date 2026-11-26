"""The device log extracts and the device table in data/input are what their manifest and the schema say."""

import hashlib
from pathlib import Path

import pytest

from j1939cap import fleet
from j1939cap.deid.capture import read_manifest as read_capture_manifest
from j1939cap.devicelog import tables

INPUT = Path(__file__).resolve().parent.parent / "data" / "input"
LOGS = INPUT / "device_logs"
LOG_MANIFEST = tables.read_manifest(LOGS) if (LOGS / tables.MANIFEST_NAME).is_file() else []


def test_every_extract_is_listed_and_every_listed_extract_is_there():
    listed = {entry["log"] for entry in LOG_MANIFEST}
    assert listed == set(tables.log_names(LOGS))
    assert all((LOGS / f"{name}{tables.EVENTS_SUFFIX}").is_file() for name in listed)
    assert len(list(LOGS.iterdir())) == 2 * len(listed) + 1


@pytest.mark.parametrize("entry", LOG_MANIFEST, ids=lambda entry: entry["log"])
def test_extract_matches_its_manifest_line(entry):
    for suffix, column in ((tables.READINGS_SUFFIX, "readings"), (tables.EVENTS_SUFFIX, "events")):
        blob = (LOGS / f"{entry['log']}{suffix}").read_bytes()
        assert (len(blob), hashlib.sha256(blob).hexdigest()) == (int(entry[f"{column}_bytes"]), entry[f"{column}_sha256"])

    log = tables.read_log(LOGS, entry["log"], entry)  # the strict reader: every cell fits its type
    assert (len(log.readings), len(log.events)) == (int(entry["readings"]), int(entry["events"]))
    assert log.device_key == entry["device_key"] == entry["log"].rsplit("_", 1)[0]
    assert entry["log"].endswith(f"{log.first_time:%Y%m%dT%H%M%SZ}")
    assert all(log.first_time <= row.time <= log.last_time for row in [*log.readings, *log.events])
    if entry["vehicle_key"]:
        assert any(reading.vin for reading in log.readings)


def test_the_device_table_names_every_device_with_a_capture_or_a_log_and_no_other():
    devices = fleet.read_devices(INPUT / "devices.csv")
    captures = read_capture_manifest(INPUT / "captures")
    with_data = {entry["device_key"] for entry in captures} | {entry["device_key"] for entry in LOG_MANIFEST}
    assert set(devices) == with_data
    with_vin = {entry["device_key"] for entry in [*captures, *LOG_MANIFEST] if entry["vehicle_key"]}
    assert {key for key, row in devices.items() if row["known_from"] == fleet.VIN} <= with_vin
