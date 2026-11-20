import json

from bus import truck

from j1939cap.cli import main


def write_capture(folder, name, frames):
    path = folder / name
    rows = "".join(f"{f.ts_ms},{f.can_id:08X},{f.data.hex().upper()}\n" for f in frames)
    path.write_text("timestamp_ms,can_id,data\n" + rows)
    return path


def test_json_report_reads_the_summary_next_to_the_capture(tmp_path, capsys):
    capture = write_capture(tmp_path, "demo.csv", truck(seconds=6))
    (tmp_path / "demo_summary.txt").write_text("request_time_sec=6\nduration_ms=6000\ncomplete=true\nst_drop_count=0\n")
    assert main(["inspect", str(capture), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["report_version"] == 1 and report["capture"]["file"] == "demo.csv"
    assert report["capture"]["requested_s"] == 6 and report["capture"]["complete"] is True
    assert report["integrity"]["recorder_reported_drops"] == 0


def test_text_report(tmp_path, capsys):
    capture = write_capture(tmp_path, "demo.csv", truck(seconds=6))
    assert main(["inspect", str(capture)]) == 0
    text = capsys.readouterr().out
    assert "no summary file next to the capture" in text
    assert "sources disagree by 312.400 km" in text and "check digit ok, Freightliner" in text and "SPN 1569 FMI 31 x2" in text


def test_an_empty_capture_prints_a_report_without_blanks(tmp_path, capsys):
    capture = write_capture(tmp_path, "empty.csv", [])
    assert main(["inspect", str(capture)]) == 0
    text = capsys.readouterr().out
    assert "0 frames" in text and "not on the bus" in text and "None" not in text


def test_deidentify_writes_captures_and_a_manifest(tmp_path, capsys, monkeypatch):
    from j1939cap.core.capture import write_summary
    from j1939cap.deid.capture import read_manifest
    from j1939cap.deid.pseudonym import KEY_FILE_VARIABLE

    key = tmp_path / "key"
    key.write_text("000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f")
    capture = write_capture(tmp_path, "canraw_AB12345678_1.csv", truck(seconds=6))
    summary = {"sn": "AB12345678", "start_time": "2026-03-02 14:05:09", "duration_ms": "6000"}
    write_summary(tmp_path / "canraw_AB12345678_1_summary.txt", summary)

    assert main(["deidentify", str(capture), "--out", str(tmp_path / "out")]) == 1  # no key named
    assert KEY_FILE_VARIABLE in capsys.readouterr().err and not (tmp_path / "out").exists()

    monkeypatch.setenv(KEY_FILE_VARIABLE, str(key))
    assert main(["deidentify", str(capture), "--out", str(tmp_path / "out")]) == 0
    # 600 engine speed, 60 vehicle speed, 24 once-a-second frames, two VIN broadcasts of four frames, a request, a reply
    assert "DEV-cafba9262c_20260302T140509Z: 694 rows, 6 changed" in capsys.readouterr().out
    (row,) = read_manifest(tmp_path / "out")
    assert row["capture"] == "DEV-cafba9262c_20260302T140509Z" and row["vehicle_key"].startswith("VIN-")
    counted = [row[name] for name in ("rows", "kept", "vin_rewritten", "transport_control", "request_trimmed")]
    assert counted == ["694", "685", "6", "2", "1"]
    assert int(row["bytes"]) == (tmp_path / "out" / "DEV-cafba9262c_20260302T140509Z.csv.gz").stat().st_size

    assert main(["inspect", str(tmp_path / "out" / "DEV-cafba9262c_20260302T140509Z.csv.gz")]) == 0
    text = capsys.readouterr().out
    assert "check digit ok, Freightliner, 2020" in text and "requested None" not in text
