import pytest
from bus import VIN, bam, le, truck
from test_integrity import slipped_capture

from j1939cap.core import integrity
from j1939cap.core import vin as vin_rules
from j1939cap.core.capture import Frame, read_recorder_csv, read_summary, write_recorder_csv, write_summary
from j1939cap.core.ids import PGN_TP_DT, build_id, parse_id
from j1939cap.core.report import analyze
from j1939cap.deid import capture as deid
from j1939cap.deid import rules
from j1939cap.deid.pseudonym import Pseudonymizer

P = Pseudonymizer(bytes.fromhex("000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f"))
NEW_VIN = rules.rewrite_vin(VIN, P.vehicle(VIN))
MASKED = b"\xff" * 8


def by_group(rows, pgn):
    return [f for f in rows if f.can_id < 1 << 29 and parse_id(f.can_id).pgn == pgn]


def test_numeric_groups_are_untouched_and_the_report_is_the_same_apart_from_the_vin():
    rows = truck()
    outcome = deid.deidentify_rows(rows, 20_000, P)
    assert len(outcome.rows) == len(rows)
    for pgn in (0xF004, 0xFEF1, 0xFEC1, 0xFEE5, 0xFECA, 0xE800):
        assert by_group(outcome.rows, pgn) == by_group(rows, pgn)

    before, after = analyze(rows), analyze(outcome.rows)
    assert [v["vin"] for v in before["vin"]] == [VIN] and [v["vin"] for v in after["vin"]] == [NEW_VIN]
    for report in (before, after):
        report["vin"][0]["vin"] = ""
    assert before == after
    assert outcome.vehicle_keys == (P.vehicle(VIN),)


def test_the_vin_is_rewritten_across_its_packets_and_nothing_of_it_is_left():
    outcome = deid.deidentify_rows(truck(), 20_000, P)
    packets = by_group(outcome.rows, PGN_TP_DT)
    assert [p.data[0] for p in packets[:3]] == [1, 2, 3]
    assert b"".join(p.data[1:] for p in packets[:3]) == NEW_VIN.encode() + b"*" + b"\xff" * 3
    everything = b"".join(f.data for f in outcome.rows)
    assert not any(VIN[n : n + 5].encode() in everything for n in range(3, 13))
    assert vin_rules.is_valid(NEW_VIN) and vin_rules.manufacturer(NEW_VIN) == "Freightliner"
    assert outcome.counts[rules.VIN] == 12 and outcome.counts[deid.TRANSPORT_CONTROL] == 4


def extra_frames():
    """A proprietary frame, a position, a request with buffer residue, free text over transport, and loose packets."""
    position = b"".join(round((v + 210) * 1e7).to_bytes(4, "little") for v in (34.0522, -118.2437))
    component = bam(300, 0xFEEB, b"MAKER*MODEL-9000*SERIAL1234567*UNIT42*", sa=0x00)
    cut_short = bam(900, 0xFEEC, VIN.encode() + b"*", sa=0x0B)[:2]  # announcement and first packet only
    loose = Frame(1400, build_id(7, PGN_TP_DT, 0x21, 0xFF), b"\x02PRIVATE")
    return [
        Frame(100, build_id(6, 0xFF21, 0x17), bytes.fromhex("0102030405060708")),
        Frame(110, build_id(6, 0xFEF3, 0x1C), position),
        Frame(120, build_id(6, 0xEA00, 0xF9, 0x00), le(0xFEEC, 3) + b"SECRT"),
        Frame(130, build_id(6, 0xFD11, 0x00), b"UNREVIEW"),
        Frame(140, build_id(6, 0xEE00, 0x00), (0x1ABCDE | (0x2F5 << 21)).to_bytes(8, "little")),
        *component,
        *cut_short,
        loose,
    ]


def test_each_kind_of_frame_gets_its_rule():
    rows = sorted(truck(seconds=2) + extra_frames(), key=lambda f: f.ts_ms)
    outcome = deid.deidentify_rows(rows, 2_000, P)
    out = outcome.rows
    assert by_group(out, 0xFF21)[0].data == MASKED and by_group(out, 0xFD11)[0].data == MASKED
    assert by_group(out, 0xFEF3)[0].data == rules.coarsen_position(by_group(rows, 0xFEF3)[0].data) != by_group(rows, 0xFEF3)[0].data
    assert [f.data for f in by_group(out, 0xEA00)] == [le(0xFEEC, 3) + b"\xff" * 5, le(0xFEEC, 3)]
    assert int.from_bytes(by_group(out, 0xEE00)[0].data, "little") == 0x2F5 << 21

    packets = {(parse_id(f.can_id).sa, f.ts_ms): f.data for f in by_group(out, PGN_TP_DT)}
    component = [data for (sa, ts), data in packets.items() if sa == 0x00 and 300 < ts < 700]
    assert len(component) == 6 and all(data[1:] == b"\xff" * 7 for data in component)
    assert [data[0] for data in component] == [1, 2, 3, 4, 5, 6]  # sequence numbers stay, so the message still counts as complete
    assert packets[(0x0B, 950)] == b"\x01" + b"\xff" * 7  # a VIN that did not arrive whole cannot be rewritten
    assert packets[(0x21, 1400)] == b"\x02" + b"\xff" * 7  # no announcement: nobody knows what it carried

    everything = b"".join(f.data for f in out)
    for secret in (b"SERIAL", b"MODEL", b"UNIT42", b"PRIVATE", b"SECRT", b"UNREVIEW"):
        assert secret not in everything
    assert outcome.counts[rules.MASK] == 2 and outcome.counts[rules.POSITION] == 1 and outcome.counts[rules.NAME] == 1
    assert outcome.counts[deid.TRANSPORT_MASKED] == 8

    before, after = analyze(rows)["transport"], analyze(out)["transport"]
    assert before == after and before["complete"] == 2


def test_a_run_of_rows_cut_at_the_wrong_offset_reads_back_the_same_with_only_recovered_frames_left_in_it():
    truth, rows = slipped_capture()
    truth = [Frame(f.ts_ms, f.can_id, f.data) for f in truth]
    before = integrity.check(rows, 2000)
    outcome = deid.deidentify_rows(rows, 2000, P)
    after = integrity.check(outcome.rows, 2000)
    run = before.runs[0]
    assert [(r.first_row, r.rows, r.offsets) for r in after.runs] == [(run.first_row, run.rows, run.offsets)]
    assert after.recovered == before.recovered and outcome.counts[deid.OUT_OF_FRAME] == run.rows

    stream = integrity.to_stream(outcome.rows[run.first_row : run.first_row + run.rows])
    covered = set()
    for offset in run.offsets:
        covered |= set(range(offset, offset + integrity.RECORD_BYTES))
    loose = [n for n in range(len(stream)) if n not in covered and stream[n] != 0xFF]
    # the only bytes left outside recovered records are the timestamp and identifier that follow the last one
    last = run.offsets[-1] + integrity.RECORD_BYTES
    assert set(loose) <= set(range(last, last + deid.HEAD_BYTES))


def test_frames_recovered_from_a_run_follow_the_rules_too():
    truth, rows = slipped_capture()
    proprietary = build_id(6, 0xFF21, 0x17)
    swap = {0x18FEC117: proprietary}
    rows = [Frame(f.ts_ms, swap.get(f.can_id, f.can_id), f.data) for f in rows]
    stream = bytearray(integrity.to_stream(rows[10:21]))
    old, new = (0x18FEC117).to_bytes(4, "big"), proprietary.to_bytes(4, "big")
    rows[10:21] = integrity.from_stream(bytes(stream.replace(old, new)))
    outcome = deid.deidentify_rows(rows, 2000, P)
    recovered = integrity.check(outcome.rows, 2000).recovered
    assert recovered and all(f.data == MASKED for f in recovered if f.can_id == proprietary)
    assert any(f.can_id == proprietary for f in recovered)


def test_output_that_does_not_read_back_is_refused(monkeypatch):
    _, rows = slipped_capture()
    monkeypatch.setattr(deid, "from_stream", lambda stream: integrity.from_stream(b"\x00" * len(stream)))
    with pytest.raises(deid.DeidError, match="read back"):
        deid.deidentify_rows(rows, 2000, P)


def test_files_are_named_by_pseudonym_and_start_time(tmp_path):
    source = tmp_path / "raw"
    source.mkdir()
    write_recorder_csv(source / "canraw_AB12345678_20260302140509.csv", truck(seconds=2))
    write_summary(
        source / "canraw_AB12345678_20260302140509_summary.txt",
        {"sn": "AB12345678", "fw_version": "v9.9.9", "start_time": "2026-03-02 14:05:09", "duration_ms": "2000", "operator": "someone"},
    )
    done = deid.deidentify_file(source / "canraw_AB12345678_20260302140509.csv", tmp_path / "out", P)
    name = "DEV-cafba9262c_20260302T140509Z"
    assert done["capture"] == name and done["device_key"] == "DEV-cafba9262c" and done["summary_keys_dropped"] == ["operator"]
    assert sorted(p.name for p in (tmp_path / "out").iterdir()) == [f"{name}.csv.gz", f"{name}_summary.txt"]
    summary = read_summary(tmp_path / "out" / f"{name}_summary.txt")
    assert summary.fields == {"sn": "DEV-cafba9262c", "fw_version": "v9.9.9", "start_time": "2026-03-02 14:05:09", "duration_ms": "2000"}
    rows, malformed = read_recorder_csv(tmp_path / "out" / f"{name}.csv.gz")
    assert malformed == 0 and len(rows) == done["rows"] == len(truck(seconds=2))
    for path in (tmp_path / "out").iterdir():
        assert b"AB12345678" not in path.read_bytes()


def test_a_capture_without_a_summary_or_with_rows_that_are_not_frames_is_refused(tmp_path):
    lonely = tmp_path / "lonely.csv"
    write_recorder_csv(lonely, truck(seconds=1))
    with pytest.raises(deid.DeidError, match="summary"):
        deid.deidentify_file(lonely, tmp_path / "out", P)

    odd = tmp_path / "odd.csv"
    odd.write_text("timestamp_ms,can_id,data\n0,0CF00400,F07D7D401F00F0FF\nserial AB12345678 rebooted\n")
    write_summary(tmp_path / "odd_summary.txt", {"sn": "AB12345678", "start_time": "2026-03-02 14:05:09"})
    with pytest.raises(deid.DeidError, match="not frames"):
        deid.deidentify_file(odd, tmp_path / "out", P)
    assert not (tmp_path / "out").exists()
