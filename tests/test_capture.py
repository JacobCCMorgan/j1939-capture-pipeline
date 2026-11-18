from datetime import datetime, timezone

from j1939cap.core.capture import Frame, find_summary, read_recorder_csv, read_summary

CSV = """timestamp_ms,can_id,data
0,0CF00400,F07D7D401F00F0FF
12,18FEF100,FFFFFF0000000000
not a row
25,18FEF1,00
30,0CF00400,F07D
3998033,9511FF04,0018F00011223344
31,18FEF100,ZZ
"""

SUMMARY = """fw_version=v2.0.0
request_time_sec=240
start_time=2026-03-02 14:05:09
end_time=2026-03-02 14:09:10
duration_ms=241000
early_stop=
records_written=200000
iot_queue_dropped=0
st_drop_count=0
complete=true
"""


def test_rows_are_parsed_and_bad_rows_are_counted(tmp_path):
    path = tmp_path / "capture.csv"
    path.write_text(CSV)
    frames, malformed = read_recorder_csv(path)
    assert malformed == 2  # the text line and the non-hex payload; the header is not an error
    assert frames[0] == Frame(0, 0x0CF00400, bytes.fromhex("F07D7D401F00F0FF"))
    assert frames[2] == Frame(25, 0x18FEF1, b"\x00")  # short identifiers and short payloads are legal rows
    assert frames[3].data == b"\xf0\x7d"
    assert len(frames) == 5


def test_a_row_written_out_of_frame_is_still_returned(tmp_path):
    path = tmp_path / "capture.csv"
    path.write_text(CSV)
    frames, _ = read_recorder_csv(path)
    assert frames[4].can_id == 0x9511FF04 and frames[4].ts_ms == 3998033


def test_summary_fields(tmp_path):
    path = tmp_path / "capture_summary.txt"
    path.write_text(SUMMARY)
    summary = read_summary(path)
    assert (summary.requested_s, summary.duration_ms, summary.records_written) == (240, 241000, 200000)
    assert summary.complete is True and summary.reported_drops == 0
    assert summary.started_at == datetime(2026, 3, 2, 14, 5, 9, tzinfo=timezone.utc)
    assert summary.fields["early_stop"] == ""


def test_summary_with_missing_values(tmp_path):
    path = tmp_path / "s.txt"
    path.write_text("duration_ms=\ncomplete=maybe\nno separator\n")
    summary = read_summary(path)
    assert summary.duration_ms is None and summary.complete is None
    assert summary.reported_drops is None and summary.started_at is None


def test_summary_is_found_next_to_the_capture(tmp_path):
    capture = tmp_path / "canraw_demo_20260302.csv"
    capture.write_text(CSV)
    assert find_summary(capture) is None
    sidecar = tmp_path / "canraw_demo_20260302_summary.txt"
    sidecar.write_text(SUMMARY)
    assert find_summary(capture) == sidecar
