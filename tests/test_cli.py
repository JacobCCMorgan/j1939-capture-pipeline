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
