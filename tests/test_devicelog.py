import copy
import json

import pytest
from bus import VIN
from unit_log import CAPTURE_AT, RULES_DOCUMENT, SERIAL, START, cycle, log_lines, stamp, write_log

from j1939cap.deid import rules as deid_rules
from j1939cap.deid.pseudonym import Pseudonymizer
from j1939cap.devicelog import extract, schema, tables
from j1939cap.devicelog import rules as log_rules

P = Pseudonymizer(bytes.fromhex("000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f"))
RULES = log_rules.parse(RULES_DOCUMENT)


def changed(**parts):
    document = copy.deepcopy(RULES_DOCUMENT)
    document.update(parts)
    return document


def test_rules_can_only_choose_among_what_the_schema_allows():
    refused = [
        changed(fields=[{"column": "driver", "pattern": r"driver (?P<value>\d+)"}]),
        changed(fields=[{"column": "time", "pattern": r"(?P<value>.*)"}]),
        changed(fields=[{"column": "speed_kmh", "pattern": r"speed=\d+"}]),  # no value group
        changed(events=[{"event": "driver_login", "pattern": r"login"}]),
        changed(events=[{"event": "capture_uploaded", "pattern": r"uploaded to (?P<host>\S+)"}]),
        changed(line=r"^(?P<body>.*)$"),
        changed(serial=r"unit \w+"),
        changed(cycle_start="("),
        ["not", "an", "object"],
    ]
    for document in refused:
        with pytest.raises(log_rules.RulesError):
            log_rules.parse(document)


def test_rules_are_loaded_from_a_file(tmp_path):
    path = tmp_path / "rules.json"
    path.write_text(json.dumps(RULES_DOCUMENT), encoding="utf-8")
    assert len(log_rules.load(path).fields) == len(RULES_DOCUMENT["fields"])
    with pytest.raises(log_rules.RulesError):
        log_rules.load(tmp_path / "missing.json")


def test_a_reading_is_one_cycle_with_typed_values(tmp_path):
    raw = extract.read_log(write_log(tmp_path / "unit.log"), RULES)
    assert (raw.serial, raw.first_time, len(raw.readings)) == (SERIAL, START, 29)
    first = raw.readings[0]
    assert first == {
        "time": "2031-03-04T10:00:00Z",
        "state": "Driving",
        "protocol": "01",
        "vin": VIN,
        "speed_kmh": "88",
        "rpm": "1200",
        "distance_km": "499687.10",
        "engine_hours": "12345.50",
        "distance_group": "65217",
        "distance_source": "17",
        "gps_valid": "01",
        "gps_speed_kmh": "87",
        "sources": "17=499687.10|00=",
        "uploaded": "1",
    }
    quiet = raw.readings[20]  # the first cycle after the capture: the unit says "N/A"
    assert (quiet["speed_kmh"], quiet["distance_km"], quiet["engine_hours"], quiet["sources"]) == ("", "", "", "17=|00=")
    assert not raw.rejected


def test_a_value_that_does_not_fit_is_dropped_and_counted_and_a_later_one_replaces_an_earlier(tmp_path):
    lines = [f"{stamp(START)} unit {SERIAL} up", f"{stamp(START)} [Driving] speed=1"]  # a field before any cycle
    lines += cycle(START, km="12.50")
    at = stamp(START)
    lines += [f"{at} [Driving] speed=fast", f"{at} [Driving] source=zz", f"{at} [Driving] rpm=900", f"{at} [Driving] sources=17,1.0,00"]
    raw = extract.read_log(write_log(tmp_path / "unit.log", lines), RULES)
    assert len(raw.readings) == 1
    assert (raw.readings[0]["speed_kmh"], raw.readings[0]["distance_source"], raw.readings[0]["rpm"]) == ("88", "17", "900")
    assert raw.rejected == {"before_first_cycle": 1, "speed_kmh": 1, "distance_source": 1, "sources": 1}


def test_events_carry_the_values_the_schema_lists_for_them(tmp_path):
    raw = extract.read_log(write_log(tmp_path / "unit.log", log_lines(rows_written=4321)), RULES)
    events = [(event, schema.event_values(event, values)) for _, event, values in raw.events]
    assert events == [
        ("capture_requested", "seconds=20;group=2"),
        ("capture_started", "seconds=20"),
        ("capture_summary", "seconds=20;duration_ms=20000;written=4321;queue_dropped=0;stop_ok=true;drop_count=0"),
        ("capture_uploaded", ""),
        ("capture_ended", "group=2"),
        ("error_sent", "code=E005;state=Driving;gps_speed_kmh=61"),
    ]  # the log has more lines about the capture, a checksum line and a held-back code; the schema has no event for them
    assert raw.events[0][0] == tables.format_time(CAPTURE_AT)


def test_published_files_are_named_by_pseudonym_and_hold_no_identifier(tmp_path):
    entry = extract.extract_file(write_log(tmp_path / "unit.log"), RULES, tmp_path / "out", P)
    name = f"{P.device(SERIAL)}_20310304T100000Z"
    assert (entry["log"], entry["device_key"], entry["vehicle_key"]) == (name, P.device(SERIAL), P.vehicle(VIN))
    assert (entry["readings"], entry["events"], entry["values_rejected"], entry["lines_repeated"]) == (29, 6, 0, 0)
    assert entry["first_time"] == "2031-03-04T10:00:00Z" and entry["last_time"] == "2031-03-04T10:19:30Z"

    files = sorted(p.name for p in (tmp_path / "out").iterdir())
    assert files == [f"{name}_events.csv", f"{name}_readings.csv"]
    text = "".join((tmp_path / "out" / f).read_text(encoding="ascii") for f in files)
    assert SERIAL not in text and VIN not in text and "4711" not in text and "34.0522" not in text

    manifest = extract.write_manifest(tmp_path / "out", [entry])
    assert tables.read_manifest(tmp_path / "out")[0]["readings_sha256"] == entry["readings_sha256"]
    assert manifest.name == tables.MANIFEST_NAME


def test_the_vin_column_holds_the_stand_in_a_capture_of_the_same_truck_carries(tmp_path):
    extract.extract_file(write_log(tmp_path / "unit.log"), RULES, tmp_path / "out", P)
    log = tables.read_logs(tmp_path / "out")[0]
    assert {r.vin for r in log.readings} == {deid_rules.rewrite_vin(VIN, P.vehicle(VIN))}
    assert log.device_key == P.device(SERIAL) and (log.first_time, len(log.events)) == (START, 6)
    reading = log.readings[0]
    assert (reading.distance_km, reading.distance_group, reading.distance_source, reading.uploaded) == (499687.10, 65217, 0x17, True)
    assert reading.sources == ((0x17, 499687.10), (0x00, None)) and reading.gps_valid is True


def test_an_extract_that_still_holds_an_identifier_is_refused_and_removed(tmp_path, monkeypatch):
    monkeypatch.setattr(extract, "_stand_in", lambda raw, pseudonymizer: (raw, None))
    with pytest.raises(extract.ExtractError):
        extract.extract_file(write_log(tmp_path / "unit.log"), RULES, tmp_path / "out", P)
    assert list((tmp_path / "out").iterdir()) == []


def test_a_log_written_twice_gives_its_rows_once(tmp_path):
    once = extract.read_log(write_log(tmp_path / "once.log"), RULES)
    twice = extract.read_log(write_log(tmp_path / "twice.log", log_lines() + log_lines()), RULES)
    assert (twice.readings, twice.events) == (once.readings, once.events)
    assert twice.lines_repeated == once.lines_used and twice.lines == 2 * once.lines


def test_logs_that_cannot_be_trusted_are_refused(tmp_path):
    with pytest.raises(extract.ExtractError):  # nothing in the layout of the rules
        extract.read_log(write_log(tmp_path / "other.log", ["free text", "more of it"]), RULES)
    with pytest.raises(extract.ExtractError):  # no serial number
        extract.read_log(write_log(tmp_path / "anonymous.log", cycle(START, km="1.00")), RULES)
    mixed = [f"{stamp(START)} unit {SERIAL} up", f"{stamp(START)} unit ZZ999999 up", *cycle(START, km="1.00")]
    with pytest.raises(extract.ExtractError):  # two serial numbers, neither clearly its own
        extract.read_log(write_log(tmp_path / "mixed.log", mixed), RULES)


def test_the_strict_reader_refuses_what_is_not_an_extract(tmp_path):
    entry = extract.extract_file(write_log(tmp_path / "unit.log"), RULES, tmp_path / "out", P)
    readings, events = tmp_path / "out" / f"{entry['log']}_readings.csv", tmp_path / "out" / f"{entry['log']}_events.csv"
    good_readings, good_events = readings.read_text(encoding="ascii"), events.read_text(encoding="ascii")

    for bad in (
        good_readings.replace("time,state", "when,state", 1),
        good_readings.replace(",Driving,", ",Dr1ving,", 1),
        good_readings + "x,y\n",
    ):
        readings.write_text(bad, encoding="ascii")
        with pytest.raises(tables.TableError):
            tables.read_readings(readings)
    for bad in (
        good_events.replace("capture_uploaded", "driver_login", 1),
        good_events.replace("seconds=20;group=2", "group=2;seconds=20", 1),
        good_events.replace("code=E005", "code=secret text", 1),
    ):
        events.write_text(bad, encoding="ascii")
        with pytest.raises(tables.TableError):
            tables.read_events(events)


def test_pairs_of_address_and_value():
    missing = frozenset({"N/A", ""})
    assert schema.pairs("17,512345.5,00,N/A", missing) == "17=512345.5|00="
    assert schema.pairs("", missing) == "" and schema.parse_pairs("") == []
    assert schema.pairs("17,512345.5,00", missing) is None and schema.pairs("1G,5", missing) is None
    assert schema.parse_pairs("17=512345.5|00=") == [(0x17, 512345.5), (0x00, None)]
