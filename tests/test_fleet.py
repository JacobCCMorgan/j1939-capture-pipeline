import json
from datetime import datetime, timedelta, timezone

import pytest
from bus import truck
from unit_log import CAPTURE_AT, CAPTURE_SECONDS, RULES_DOCUMENT, SERIAL, cycle, stamp, write_log

from j1939cap import fleet
from j1939cap.cli import main
from j1939cap.core.capture import write_recorder_csv, write_summary
from j1939cap.deid.pseudonym import Pseudonymizer
from j1939cap.devicelog.tables import DeviceLog, Event, Reading

P = Pseudonymizer(bytes.fromhex("000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f"))
T0 = datetime(2031, 3, 4, 10, 0, 0, tzinfo=timezone.utc)


def reading(n, **changes):
    values = dict(
        time=T0 + timedelta(seconds=30 * n),
        state="Driving",
        protocol="01",
        vin="1FU0A1B20LC3D4EZZ",
        speed_kmh=88.0,
        rpm=1200.0,
        distance_km=500000.0 + n,
        engine_hours=12345.5,
        distance_group=65217,
        distance_source=0x17,
        configured_source=None,
        gps_valid=True,
        gps_speed_kmh=87.0,
        sources=(),
        uploaded=True,
    )
    values.update(changes)
    return Reading(**values)


def log_of(key, readings, events=()):
    return DeviceLog(f"{key}_20310304T100000Z", key, T0, T0 + timedelta(hours=8), list(readings), list(events))


def capture_row(key, frames=250_000, speed="00", distance="00 17", spread=312.4, vin=True, hours=20_000.0, per_hour="00 55.0; 17 55.0"):
    return {
        "capture": f"{key}_20310304T101000Z",
        "frames": frames,
        "speed_sources": speed,
        "distance_sources": distance,
        "distance_spread_km": spread,
        "engine_hours": hours,
        "km_per_engine_hour": per_hour,
        "vin_on_bus": vin,
    }


def key_of(n):
    return f"DEV-{n:010x}"


def test_the_device_table_is_read_strictly(tmp_path):
    path = tmp_path / "devices.csv"
    lines = ["device_key,make,model_year,known_from", "DEV-00000000a1,Freightliner,2021,vin", "DEV-00000000a2,Western Star,,records"]
    path.write_text("\n".join(lines) + "\n", encoding="ascii")
    table = fleet.read_devices(path)
    assert table["DEV-00000000a1"] == dict(zip(fleet.DEVICE_TABLE_HEADER, lines[1].split(","), strict=True))
    assert table["DEV-00000000a2"]["model_year"] == ""
    good = path.read_text(encoding="ascii")
    for bad in (
        good.replace("known_from", "source", 1),
        good.replace("DEV-00000000a2", "AB123456"),
        good.replace(",vin\n", ",guess\n"),
        good.replace("2021", "21"),
        good.replace("DEV-00000000a2", "DEV-00000000a1"),  # the same device twice
        good + "DEV-00000000a3,Volvo\n",
    ):
        path.write_text(bad, encoding="ascii")
        with pytest.raises(fleet.DeviceTableError):
            fleet.read_devices(path)


def test_a_value_is_unreadable_when_it_is_missing_in_enough_moving_readings():
    whole = log_of(key_of(1), [reading(n) for n in range(40)])
    assert fleet.unreadable([whole]) == (40, ())
    no_distance = log_of(key_of(2), [reading(n, distance_km=None, vin="") for n in range(40)])
    assert fleet.unreadable([no_distance]) == (40, ("vin", "distance"))
    now_and_then = log_of(key_of(3), [reading(n, engine_hours=None if n % 10 == 0 else 1.0) for n in range(40)])  # 4 of 40
    assert fleet.unreadable([now_and_then]) == (40, ())
    assert fleet.unreadable([now_and_then], share=0.05) == (40, ("engine_hours",))


def test_readings_at_rest_or_around_a_capture_do_not_count():
    parked = log_of(key_of(1), [reading(n, gps_speed_kmh=0.0, speed_kmh=None, rpm=None) for n in range(40)])
    assert fleet.unreadable([parked]) == (0, None)  # nothing can be said about a truck that never moved
    capture = Event(T0 + timedelta(minutes=10), "capture_requested", {"seconds": "240"})
    quiet = [reading(n, speed_kmh=None, distance_km=None) if 19 <= n < 44 else reading(n) for n in range(60)]
    around = log_of(key_of(2), quiet, [capture])
    assert len(fleet.moving_readings(around)) == 60 - 26  # from thirty seconds before to twelve minutes after, ends included
    assert fleet.unreadable([around]) == (34, ())
    assert fleet.unreadable([log_of(key_of(3), [reading(n) for n in range(19)])]) == (19, None)


def test_device_rows_join_capture_log_and_table():
    empty = capture_row(key_of(2), frames=720, speed="", distance="", spread=None, vin=False)
    captures = [capture_row(key_of(1)), capture_row(key_of(1), frames=900), empty]
    logs = [log_of(key_of(1), [reading(n, rpm=None) for n in range(40)]), log_of(key_of(3), [reading(n) for n in range(5)])]
    table = {
        key_of(1): {"make": "Kenworth", "model_year": "2022", "known_from": "vin"},
        key_of(4): {"make": "Mack", "model_year": "", "known_from": "records"},
    }
    rows = {row["device_key"]: row for row in fleet.device_rows(captures, logs, table)}
    assert list(rows) == [key_of(n) for n in (1, 2, 3, 4)]
    first = rows[key_of(1)]
    assert (first["make"], first["captures"], first["frames"], first["capture_usable"]) == ("Kenworth", 2, 250_000, True)
    assert (first["distance_sources"], first["logs"], first["moving_readings"]) == ("00 17", 1, 40)
    assert first["unreadable"] == "engine_speed"
    assert (rows[key_of(2)]["capture_usable"], rows[key_of(2)]["make"], rows[key_of(2)]["unreadable"]) == (False, "", "")
    assert (rows[key_of(3)]["captures"], rows[key_of(3)]["frames"], rows[key_of(3)]["unreadable"]) == (0, None, fleet.NOT_ENOUGH)
    assert (rows[key_of(4)]["make"], rows[key_of(4)]["captures"], rows[key_of(4)]["logs"]) == ("Mack", 0, 0)


def test_a_make_with_fewer_than_five_trucks_is_only_named():
    captures = [
        capture_row(key_of(n), distance="00 17" if n < 4 else "00", spread=300.0 if n < 2 else 0.1, vin=n == 0) for n in range(6)
    ]
    captures += [capture_row(key_of(n)) for n in (10, 11)] + [capture_row(key_of(20), frames=50)]
    logs = [log_of(key_of(n), [reading(m, vin="" if n == 0 else "X") for m in range(40)]) for n in range(3)]
    table = {key_of(n): {"make": "Kenworth", "model_year": "2022", "known_from": "vin"} for n in range(6)}
    table.update({key_of(n): {"make": "Mack", "model_year": "", "known_from": "records"} for n in (10, 11, 20)})
    makes = fleet.make_rows(fleet.device_rows(captures, logs, table))
    assert makes == [
        {
            "make": "Kenworth",
            "trucks": 6,
            "speed_from": "00 x6",
            "distance_from": "00+17 x4, 00 x2",
            "vin_on_bus": 1,
            "two_distance_sources": 4,
            "sources_apart": 2,
            "with_log": 3,
            "reads_all_five": 2,
        },
        {"make": "Mack", "trucks": "<5"},
    ]


def test_a_distance_far_from_the_others_is_held_against_the_trucks_that_agree():
    # ten trucks whose sources agree, at 40 to 67 km per engine hour, give the band
    captures = [capture_row(key_of(n), spread=0.1, per_hour=f"00 {40 + 3 * n}.0; 17 {40 + 3 * n}.0") for n in range(10)]
    captures += [
        capture_row(key_of(20), spread=1_200_000.0, per_hour="00 2.2; 17 45.0"),  # the engine's counter started again
        capture_row(key_of(21), spread=400_000.0, per_hour="00 50.0; 31 30.0"),  # here the other controller's did
        capture_row(key_of(22), spread=900_000.0, per_hour="00 10.9; 17 204.7"),  # neither fits
        capture_row(key_of(23), spread=5_000.0, per_hour="00 60.0; 17 61.0"),  # apart, but not far apart
        capture_row(key_of(24), frames=50, spread=2_000_000.0, per_hour="00 1.0; 17 50.0"),  # not a usable capture
    ]
    check = fleet.distance_check(fleet.device_rows(captures, [], {}))
    low, high = check["band"]
    assert check["agreeing"] == 10 and 40.0 <= low < 43.5 and 64.0 < high <= 67.0
    assert [r["device_key"] for r in check["far"]] == [key_of(20), key_of(21), key_of(22)]
    assert check["far"][0]["positions"] == {"00": fleet.BELOW, "17": fleet.INSIDE}
    assert check["far"][2]["positions"] == {"00": fleet.BELOW, "17": fleet.ABOVE}
    assert (check["engine_below"], check["other_inside"], check["engine_inside_other_outside"]) == (2, 1, 1)
    assert fleet.distance_check(fleet.device_rows(captures[:5], [], {}))["band"] is None  # too few trucks to say


def test_the_events_of_a_log_say_which_cycles_were_retried_and_which_raised_a_code():
    readings = [reading(n) for n in range(80)]
    retry = lambda n, attempt, state="Driving": Event(  # noqa: E731
        T0 + timedelta(seconds=30 * n + 5), "reading_retry", {"state": state, "attempt": str(attempt), "of": "3"}
    )
    events = [retry(n, attempt) for n in range(10) for attempt in (1, 2)]  # ten cycles, each retried twice
    events += [Event(T0 + timedelta(seconds=30 * n + 6), "error_sent", {"code": "E005", "state": "Driving"}) for n in range(4)]
    events += [retry(40, 1, state="Sleeping")]  # not while driving
    assert fleet.own_account([log_of(key_of(1), readings, events)]) == (12.5, 5.0)
    # a capture at minute five takes readings 9 to 34 out, and the retry of cycle 9 with them
    capture = Event(T0 + timedelta(minutes=5), "capture_requested", {"seconds": "240", "group": "2"})
    assert fleet.own_account([log_of(key_of(1), readings, [*events, capture])]) == (16.7, 7.4)
    assert fleet.own_account([log_of(key_of(1), readings[:10], events)]) == (None, None)  # too few cycles to say
    row = fleet.device_rows([], [log_of(key_of(1), readings, events)], {})[0]
    assert (row["retry_pct"], row["error_pct"]) == (12.5, 5.0)


def test_sensitivity_recounts_with_each_threshold_halved_and_doubled():
    captures = [capture_row(key_of(1), frames=6_000), capture_row(key_of(2), frames=12_000), capture_row(key_of(3), frames=30_000)]
    # no engine speed below 20 km/h
    slow = [reading(n, gps_speed_kmh=15.0, rpm=None) for n in range(40)] + [reading(n) for n in range(40, 80)]
    some = [reading(n, vin="" if n % 4 == 0 else "X") for n in range(40)]  # a quarter without a VIN
    by_threshold = {t["threshold"]: t for t in fleet.sensitivity(captures, [log_of(key_of(1), slow), log_of(key_of(2), some)])}
    frames = by_threshold["frames for a usable capture"]
    assert (frames["halved"], frames["as_set"], frames["doubled"]) == (3, 2, 1)
    share = by_threshold["share of moving readings without a value"]
    assert (share["halved"], share["as_set"], share["doubled"]) == ("1 of 2", "1 of 2", "0 of 2")
    moving = by_threshold["GPS speed that counts as moving, km/h"]
    assert (moving["halved"], moving["as_set"], moving["doubled"]) == ("2 of 2", "1 of 2", "1 of 2")


def test_the_command_line_writes_the_device_and_make_report(tmp_path, monkeypatch, capsys):
    key, rules = tmp_path / "key.txt", tmp_path / "rules.json"
    key.write_text("000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f", encoding="ascii")
    rules.write_text(json.dumps(RULES_DOCUMENT), encoding="utf-8")
    monkeypatch.setenv("J1939CAP_DEID_KEY_FILE", str(key))
    monkeypatch.setenv("J1939CAP_LOG_RULES_FILE", str(rules))
    rows = truck(seconds=CAPTURE_SECONDS)
    write_recorder_csv(tmp_path / "capture.csv", rows)
    summary = {"sn": SERIAL, "start_time": f"{CAPTURE_AT:%Y-%m-%d %H:%M:%S}", "duration_ms": "20000", "records_written": str(len(rows))}
    write_summary(tmp_path / "capture_summary.txt", summary)
    monkeypatch.setattr(fleet, "USABLE_FRAMES", 100)  # twenty seconds of the made-up truck are a few hundred frames
    start = CAPTURE_AT - timedelta(hours=1)
    lines = [f"{stamp(start)} unit {SERIAL} up"]
    for n in range(30):
        lines += cycle(start + timedelta(seconds=30 * n), km=f"{499000 + n}.00")
    write_log(tmp_path / "unit.log", lines)
    root = tmp_path / "input"
    assert main(["deidentify", str(tmp_path / "capture.csv"), "--out", str(root / "captures")]) == 0
    assert main(["extract-log", str(tmp_path / "unit.log"), "--out", str(root / "device_logs")]) == 0

    assert main(["fleet", str(root), "--out", str(tmp_path / "reports")]) == 1  # no devices.csv yet
    assert "devices.csv" in capsys.readouterr().err
    table = f"device_key,make,model_year,known_from\n{P.device(SERIAL)},Freightliner,2020,vin\n"
    (root / "devices.csv").write_text(table, encoding="ascii")
    assert main(["fleet", str(root), "--out", str(tmp_path / "reports")]) == 0
    assert "1 devices, 1 with a usable capture" in capsys.readouterr().out

    lines = (tmp_path / "reports" / "devices.csv").read_text(encoding="ascii").splitlines()
    assert lines[0] == ",".join(fleet.COLUMNS)
    cells = dict(zip(fleet.COLUMNS, lines[1].split(","), strict=True))
    assert (cells["device_key"], cells["make"], cells["capture_usable"]) == (P.device(SERIAL), "Freightliner", "yes")
    assert cells["distance_sources"] == "00 17"
    assert (cells["logs"], cells["moving_readings"], cells["unreadable"], cells["vin_on_bus"]) == ("1", "30", "", "yes")
    text = (tmp_path / "reports" / "devices.md").read_text(encoding="utf-8")
    assert "1 devices, 1 with a usable capture (100 frames or more)." in text and "| Freightliner | <5 |" in text
    assert "## How much the thresholds matter" in text and SERIAL not in text
