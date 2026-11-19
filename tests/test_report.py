import pytest
from bus import VIN, bam, le, truck

from j1939cap.core.capture import Frame
from j1939cap.core.ids import build_id
from j1939cap.core.report import analyze, cadence


def test_cadence_counts_frames_missing_from_a_steady_broadcast():
    steady = list(range(0, 10_000, 100))
    assert cadence(steady) == {"frames": 100, "period_ms": 100, "max_gap_ms": 100, "missing": 0, "pauses": 0}
    holes = [t for t in steady if t not in (1000, 1100, 5000)]
    got = cadence(holes)
    assert (got["frames"], got["period_ms"], got["max_gap_ms"], got["missing"], got["pauses"]) == (97, 100, 300, 3, 0)


def test_a_sender_that_pauses_on_purpose_is_not_missing_frames():
    bursts = [start + 50 * n for start in range(0, 60_000, 6000) for n in range(20)]
    got = cadence(bursts)
    assert (got["period_ms"], got["missing"], got["pauses"]) == (50, 0, 9)


def test_cadence_does_not_guess_for_irregular_or_short_series():
    assert cadence([0, 7, 300, 310, 2000])["missing"] is None
    irregular = [n * n for n in range(80)]
    assert cadence(irregular)["missing"] is None
    assert cadence([5])["max_gap_ms"] is None


def test_report_of_a_healthy_bus():
    report = analyze(truck())
    cap, integ = report["capture"], report["integrity"]
    assert cap["rows"] == cap["frames"] and cap["sources"] == 4
    assert integ["rows_out_of_frame"] == 0 and integ["steady_frames_missing"] == 0 and integ["missing_pct"] == 0.0
    assert integ["steady_identifiers"] == 2  # engine speed and vehicle speed; the rest is too sparse to judge
    assert report["transport"]["announced"] == 4 and report["transport"]["complete_pct"] == 100.0
    speed = report["eld"]["vehicle_speed"]["sources"]
    assert [(s["sa"], s["last"]) for s in speed] == [(0x00, 89.5)]
    assert report["eld"]["engine_speed"]["sources"][0]["last"] == 1200.0
    assert report["eld"]["engine_hours"]["sources"][0]["last"] == pytest.approx(12_345.5)


def test_two_distance_sources_that_disagree_are_reported():
    distance = analyze(truck())["eld"]["distance"]
    assert [s["sa"] for s in distance["sources"]] == [0x00, 0x17]
    assert distance["spread_km"] == pytest.approx(312.4, abs=0.01) and distance["sources_disagree"]


def test_vin_diagnostics_requests_and_acknowledgments():
    report = analyze(truck())
    assert report["vin"] == [
        {"sa": 0, "via": "transport", "vin": VIN, "messages": 4, "valid": True, "manufacturer": "Freightliner", "model_year": 2020}
    ]
    assert report["eld"]["vin"] == {"available": True, "check_digit_ok": True, "requested": 1}
    dm1 = report["diagnostics"][0]
    assert (dm1["message"], dm1["sa"], dm1["messages"], dm1["lamps"]["amber_warning"]) == ("DM1", 0x0B, 20, "on")
    assert dm1["dtcs"] == [{"spn": 1569, "fmi": 31, "occurrence": 2, "conversion_method": 0}]
    assert report["requests"] == [{"sa": 0xF9, "da": 0x00, "pgn": 0xFEEC, "count": 1}]
    assert report["acknowledgments"] == [{"sa": 0x0B, "kind": "negative", "count": 1}]


def test_frames_lost_by_the_recorder_show_up_in_every_steady_broadcast():
    kept = [f for n, f in enumerate(truck()) if n % 10 != 9]  # the recorder loses one row in ten, whoever sent it
    integ = analyze(kept)["integrity"]
    assert integ["steady_identifiers"] == 2
    assert 8 < integ["missing_pct_p25"] <= integ["missing_pct"] <= integ["missing_pct_p75"] < 12
    assert integ["rows_out_of_frame"] == 0


def test_the_latest_diagnostic_message_wins_whether_it_came_in_one_frame_or_several():
    one_code = Frame(1000, build_id(6, 0xFECA, 0x00), bytes.fromhex("04FF21061F02FFFF"))
    two_codes = bam(3000, 0xFECA, bytes.fromhex("04FF" + "21061F02" + "6E000003"), sa=0x00)
    cleared = Frame(6000, build_id(6, 0xFECA, 0x00), bytes.fromhex("00FF00000000FFFF"))

    dm1 = analyze([one_code, *two_codes])["diagnostics"][0]
    assert [code["spn"] for code in dm1["dtcs"]] == [1569, 110] and dm1["last_ts_ms"] == 3100

    dm1 = analyze([one_code, *two_codes, cleared])["diagnostics"][0]
    assert dm1["dtcs"] == [] and dm1["messages"] == 3 and dm1["lamps"]["amber_warning"] == "off"


def test_distance_comes_from_the_standard_field_when_the_high_resolution_one_is_not_available():
    frames = []
    for second in range(5):
        frames.append(Frame(1000 * second, build_id(6, 0xFEC1, 0x00), b"\xff" * 8))
        frames.append(Frame(1000 * second + 1, build_id(6, 0xFEE0, 0x00), b"\xff" * 4 + le(123_456 / 0.125, 4)))
    distance = analyze(frames)["eld"]["distance"]
    assert distance["available"] and not distance["sources_disagree"]
    assert [(s["signal"], s["last"]) for s in distance["sources"]] == [("total_vehicle_distance", 123_456.0)]


def test_an_empty_capture_still_gives_a_report():
    report = analyze([])
    assert report["capture"]["frames"] == 0 and report["integrity"]["missing_pct"] is None
    assert not report["eld"]["vehicle_speed"]["available"] and report["vin"] == []
