from j1939cap.core import integrity
from j1939cap.core.capture import Frame
from j1939cap.core.integrity import RECORD_BYTES, check, recover

IDS = (0x0CF00400, 0x18FEF100, 0x18FEC117)


def make_frames(n, start_ms=1000, step_ms=10):
    return [Frame(start_ms + step_ms * i, IDS[i % 3], bytes([i] * 8)) for i in range(n)]


def to_stream(frames):
    return b"".join(f.ts_ms.to_bytes(3, "big") + f.can_id.to_bytes(4, "big") + f.data for f in frames)


def to_rows(stream):
    rows = []
    for p in range(0, len(stream) - RECORD_BYTES + 1, RECORD_BYTES):
        rec = stream[p : p + RECORD_BYTES]
        rows.append(Frame(int.from_bytes(rec[:3], "big"), int.from_bytes(rec[3:7], "big"), rec[7:]))
    return rows


def slipped_capture(n=40, lost_in=10, resync_after=20, lost_bytes=5):
    """Drop bytes from inside one record and pad after a later one, as a recorder that loses and regains its place."""
    truth = make_frames(n)
    stream = bytearray(to_stream(truth))
    cut = lost_in * RECORD_BYTES + 4  # inside the identifier
    del stream[cut : cut + lost_bytes]
    pad_at = (resync_after + 1) * RECORD_BYTES - lost_bytes
    stream[pad_at:pad_at] = b"\xaa" * lost_bytes
    return truth, to_rows(bytes(stream))


def test_an_intact_capture_has_no_runs():
    frames = make_frames(50)
    result = check(frames, duration_ms=2000)
    assert result.frames == frames and result.runs == [] and result.rows_out_of_frame == 0


def test_a_slip_is_found_and_the_frames_inside_it_are_read_back():
    truth, rows = slipped_capture()
    assert len(rows) == len(truth)
    result = check(rows, duration_ms=2000)
    assert len(result.runs) == 1
    run = result.runs[0]
    assert (run.first_row, run.rows) == (10, 11)
    assert (run.ts_before, run.ts_after) == (truth[9].ts_ms, truth[21].ts_ms)
    # record 10 lost bytes; record 20 is followed by padding, so nothing vouches for it
    assert result.recovered == truth[11:20]
    assert result.frames == truth[:10] + truth[11:20] + truth[21:]


def test_without_repair_the_run_is_only_set_aside():
    truth, rows = slipped_capture()
    result = check(rows, duration_ms=2000, repair=False)
    assert result.recovered == [] and result.runs[0].recovered == 0
    assert result.frames == truth[:10] + truth[21:]


def test_the_record_that_straddles_the_loss_is_not_recovered():
    truth = make_frames(12)
    stream = bytearray(to_stream(truth))
    del stream[4 * RECORD_BYTES + 9 : 4 * RECORD_BYTES + 12]  # three payload bytes of record 4
    rows = to_rows(bytes(stream) + b"\x00" * 3)
    got = recover(rows[4:], truth[3].ts_ms, truth[-1].ts_ms + 100, set(IDS))
    assert truth[4] not in got and all(f in truth for f in got)
    assert got[:3] == truth[5:8]


def test_slips_are_found_without_a_known_duration():
    truth, rows = slipped_capture()
    result = check(rows)
    assert result.runs[0].rows == 11 and result.frames[:10] == truth[:10]


def test_bus_silence_is_not_a_slip():
    frames = make_frames(20) + make_frames(20, start_ms=60_000)
    result = check(frames, duration_ms=70_000)
    assert result.runs == [] and len(result.frames) == 40


def test_a_lone_row_far_ahead_is_a_slip():
    frames = make_frames(20)
    frames.insert(10, Frame(1_500_000, IDS[0], bytes(8)))
    result = check(frames)
    assert [(r.first_row, r.rows) for r in result.runs] == [(10, 1)]


def test_a_row_that_steps_backwards_is_a_slip():
    frames = make_frames(20)
    frames.insert(10, Frame(5, 0x0AAAAAAA, bytes(8)))
    result = check(frames, duration_ms=2000)
    assert [(r.first_row, r.rows) for r in result.runs] == [(10, 1)] and len(result.frames) == 20


def test_a_run_says_where_its_recovered_records_sit_in_the_byte_stream():
    truth, rows = slipped_capture()
    run = check(rows, duration_ms=2000).runs[0]
    stream = integrity.to_stream(rows[run.first_row : run.first_row + run.rows])
    assert len(run.offsets) == run.recovered > 0
    for offset in run.offsets:
        record = integrity.from_stream(stream[offset : offset + RECORD_BYTES])[0]
        assert record in truth
    assert integrity.from_stream(stream) == rows[run.first_row : run.first_row + run.rows]
