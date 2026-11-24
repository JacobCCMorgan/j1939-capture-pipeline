import csv

from bus import truck

from j1939cap.cli import main
from j1939cap.core.capture import Frame, write_recorder_csv, write_summary
from j1939cap.core.ids import build_id
from j1939cap.deid.capture import deidentify_file, write_manifest
from j1939cap.deid.pseudonym import Pseudonymizer
from j1939cap.summarize import COLUMNS, collect, render_markdown, summarize

P = Pseudonymizer(bytes.fromhex("000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f"))


def publish(tmp_path):
    """Two made-up trucks and a recording with nothing in it, de-identified into one directory."""
    raw, out = tmp_path / "raw", tmp_path / "captures"
    raw.mkdir()
    quiet = [f for f in truck(seconds=8) if f.ts_ms < 5000]  # the recorder says eight seconds; frames stop at five
    proprietary = [Frame(ms, build_id(6, 0xFF21, 0x17), bytes(8)) for ms in range(5, 10_000, 100)]
    captures = [
        ("AB00000001", "2026-03-02 14:05:09", "10000", sorted(truck(seconds=10) + proprietary, key=lambda f: f.ts_ms)),
        ("AB00000002", "2026-03-01 09:00:00", "8000", quiet),
        ("AB00000003", "2026-03-03 00:00:00", "5000", []),
    ]
    entries = []
    for serial, start, duration, frames in captures:
        write_recorder_csv(raw / f"{serial}.csv", frames)
        fields = {"sn": serial, "fw_version": "v9.9.9", "request_time_sec": str(int(duration) // 1000), "start_time": start}
        write_summary(raw / f"{serial}_summary.txt", {**fields, "duration_ms": duration, "st_drop_count": "0", "complete": "true"})
        entries.append(deidentify_file(raw / f"{serial}.csv", out, P))
    write_manifest(out, entries)
    return out


def test_one_row_per_capture_oldest_first_with_keys_from_the_manifest(tmp_path):
    rows = collect(publish(tmp_path))
    assert [r["started_at"] for r in rows] == ["2026-03-01T09:00:00Z", "2026-03-02T14:05:09Z", "2026-03-03T00:00:00Z"]
    quiet, full, empty = rows
    assert full["device_key"] == P.device("AB00000001") and full["vehicle_key"].startswith("VIN-")
    assert (full["make"], full["model_year"], full["vin_on_bus"], full["vin_check_digit_ok"]) == ("Freightliner", 2020, True, True)
    assert (full["speed_sources"], full["distance_sources"], full["distance_spread_km"]) == ("00", "00 17", 312.4)
    assert (full["engine_hours"], full["km_per_engine_hour"]) == (12345.5, "00 40.5; 17 40.5")
    assert (empty["engine_hours"], empty["km_per_engine_hour"]) == (None, "")
    assert (full["transport_announced"], full["transport_complete"], full["missing_pct"]) == (2, 2, 0.0)
    assert full["proprietary_pct"] == 8.0 and full["max_speed_kmh"] == 89.5  # 100 proprietary frames in 1,250
    assert (full["sources_with_active_codes"], full["active_codes"], full["negative_acknowledgments"]) == (1, 1, 1)
    # the recorder ran eight seconds and says so; the last frame is at five
    assert (quiet["requested_s"], quiet["duration_s"], quiet["last_frame_s"], quiet["recorder_complete"]) == (8, 8.0, 5.0, True)
    assert (empty["frames"], empty["last_frame_s"], empty["make"], empty["speed_sources"]) == (0, None, None, "")


def test_files_are_written_and_a_second_run_gives_the_same_bytes(tmp_path, capsys):
    captures = publish(tmp_path)
    assert main(["summarize", str(captures), "--out", str(tmp_path / "one")]) == 0
    assert "3 captures" in capsys.readouterr().out
    summarize(captures, tmp_path / "two")
    for name in ("captures.csv", "captures.md"):
        assert (tmp_path / "one" / name).read_bytes() == (tmp_path / "two" / name).read_bytes()
    with open(tmp_path / "one" / "captures.csv", newline="") as fh:
        table = list(csv.DictReader(fh))
    assert tuple(table[0]) == COLUMNS and len(table) == 3
    assert table[2]["frames"] == "0" and table[2]["make"] == "" and table[1]["recorder_complete"] == "yes"


def test_markdown_says_what_the_tables_say(tmp_path):
    captures = publish(tmp_path)
    text = render_markdown(collect(captures))
    assert text.startswith("# Captures side by side\n\n3 captures from 3 devices, 1,826 frames.")  # 1,250 and 576
    assert "1 holds no frames at all." in text and "| Freightliner 2020 |" in text
    assert "| 00 17 | 312.4 |" in text and "2 of 2 (100.0%)" in text
    assert "What the published files contain" not in text  # only with a manifest

    summarize(captures, tmp_path / "out")
    with_manifest = (tmp_path / "out" / "captures.md").read_text()
    assert "What the published files contain" in with_manifest and "were rewritten" in with_manifest
