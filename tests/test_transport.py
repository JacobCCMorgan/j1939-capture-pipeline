from j1939cap.core.ids import PGN_TP_CM, PGN_TP_DT, build_id, parse_id
from j1939cap.core.transport import Reassembler

VIN = b"1M8GDM9AXKP042788"
PGN_VIN = 0xFEEC


def announce(control, pgn, size, sa, da=0xFF):
    packets = -(-size // 7)
    data = bytes([control, size & 0xFF, size >> 8, packets, 0xFF, pgn & 0xFF, (pgn >> 8) & 0xFF, pgn >> 16])
    return parse_id(build_id(7, PGN_TP_CM, sa, da)), data


def packets_of(payload, sa, da=0xFF):
    jid = parse_id(build_id(7, PGN_TP_DT, sa, da))
    out = []
    for n in range(0, len(payload), 7):
        chunk = payload[n : n + 7]
        out.append((jid, bytes([n // 7 + 1]) + chunk + b"\xff" * (7 - len(chunk))))
    return out


def run(reassembler, frames, start_ms=0, step_ms=50):
    done = []
    for n, (jid, data) in enumerate(frames):
        done += reassembler.feed(start_ms + n * step_ms, jid, data)
    return done


def test_broadcast_message_is_reassembled_and_trimmed_to_its_size():
    r = Reassembler()
    done = run(r, [announce(0x20, PGN_VIN, len(VIN), sa=0x00), *packets_of(VIN, sa=0x00)])
    assert len(done) == 1
    m = done[0]
    assert (m.kind, m.sa, m.da, m.pgn, m.size, m.packets, m.received, m.status) == ("BAM", 0x00, 0xFF, PGN_VIN, 17, 3, 3, "complete")
    assert m.payload == VIN and (m.ts_start, m.ts_end) == (0, 150)
    assert r.flush(200) == [] and r.orphan_data_frames == 0


def test_a_missing_packet_leaves_the_message_incomplete_when_the_next_one_starts():
    r = Reassembler()
    first = [announce(0x20, PGN_VIN, len(VIN), sa=0x00), *packets_of(VIN, sa=0x00)]
    del first[2]  # the second data packet never arrives
    done = run(r, first + [announce(0x20, PGN_VIN, len(VIN), sa=0x00), *packets_of(VIN, sa=0x00)])
    assert [(m.status, m.received) for m in done] == [("incomplete", 2), ("complete", 3)]
    assert done[0].payload is None


def test_senders_are_followed_independently():
    r = Reassembler()
    a = [announce(0x20, PGN_VIN, len(VIN), sa=0x00), *packets_of(VIN, sa=0x00)]
    b = [announce(0x20, 0xFECA, 10, sa=0x3D), *packets_of(bytes(range(10)), sa=0x3D)]
    mixed = [a[0], b[0], a[1], b[1], a[2], b[2], a[3]]
    done = run(r, mixed)
    assert {(m.sa, m.pgn, m.status) for m in done} == {(0x00, PGN_VIN, "complete"), (0x3D, 0xFECA, "complete")}
    assert next(m for m in done if m.sa == 0x3D).payload == bytes(range(10))


def test_addressed_transfer_with_flow_control():
    r = Reassembler()
    payload = bytes(range(20))
    cts = (parse_id(build_id(7, PGN_TP_CM, 0xF9, 0x00)), bytes([0x11, 3, 1, 0xFF, 0xFF, 0xEC, 0xFE, 0x00]))
    ack = (parse_id(build_id(7, PGN_TP_CM, 0xF9, 0x00)), bytes([0x13, 20, 0, 3, 0xFF, 0xEC, 0xFE, 0x00]))
    done = run(r, [announce(0x10, PGN_VIN, 20, sa=0x00, da=0xF9), cts, *packets_of(payload, sa=0x00, da=0xF9), ack])
    assert [(m.kind, m.sa, m.da, m.status, m.payload) for m in done] == [("RTS", 0x00, 0xF9, "complete", payload)]


def test_abort_from_the_receiver_closes_the_transfer():
    r = Reassembler()
    abort = (parse_id(build_id(7, PGN_TP_CM, 0xF9, 0x00)), bytes([0xFF, 1, 0xFF, 0xFF, 0xFF, 0xEC, 0xFE, 0x00]))
    done = run(r, [announce(0x10, PGN_VIN, 20, sa=0x00, da=0xF9), packets_of(bytes(20), sa=0x00, da=0xF9)[0], abort])
    assert [(m.status, m.received) for m in done] == [("aborted", 1)]


def test_data_without_an_announcement_is_counted():
    r = Reassembler()
    assert run(r, packets_of(VIN, sa=0x17)) == []
    assert r.orphan_data_frames == 3


def test_silence_longer_than_the_timeout_ends_the_message():
    r = Reassembler(timeout_ms=1250)
    frames = [announce(0x20, PGN_VIN, len(VIN), sa=0x00), *packets_of(VIN, sa=0x00)]
    done = r.feed(0, *frames[0]) + r.feed(50, *frames[1]) + r.feed(5000, *frames[2])
    assert [(m.status, m.received, m.ts_end) for m in done] == [("incomplete", 1, 50)]
    assert r.orphan_data_frames == 1


def test_capture_end_distinguishes_a_message_in_flight_from_a_stalled_one():
    r = Reassembler()
    frames = [announce(0x20, PGN_VIN, len(VIN), sa=0x00), *packets_of(VIN, sa=0x00)]
    r.feed(0, *frames[0])
    r.feed(50, *frames[1])
    assert [m.status for m in r.flush(100)] == ["open_at_end"]
    r.feed(0, *frames[0])
    assert [m.status for m in r.flush(10_000)] == ["incomplete"]


def test_an_announcement_that_cannot_be_true_is_counted_and_ignored():
    r = Reassembler()
    jid, _ = announce(0x20, PGN_VIN, len(VIN), sa=0x00)
    bad = (jid, bytes([0x20, 40, 0, 2, 0xFF, 0xEC, 0xFE, 0x00]))  # forty bytes cannot fit in two packets
    assert r.feed(0, *bad) == [] and r.malformed_announcements == 1


def test_a_packet_number_that_does_not_move_forward_ends_the_message_whatever_its_bytes():
    r = Reassembler()
    frames = [announce(0x20, PGN_VIN, len(VIN), sa=0x00), *packets_of(VIN, sa=0x00)]
    done = run(r, [frames[0], frames[1], frames[1], frames[2], frames[3]])
    assert [(m.status, m.received) for m in done] == [("incomplete", 1)]
    assert r.orphan_data_frames == 3


def test_two_broadcasts_of_the_same_message_are_not_stitched_into_one():
    """The first loses its last packet, the second its announcement; neither was received whole."""
    r = Reassembler()
    announcement, first, second, third = [announce(0x20, PGN_VIN, len(VIN), sa=0x00), *packets_of(VIN, sa=0x00)]
    done = run(r, [announcement, first, second, first, second, third])
    assert [(m.status, m.received) for m in done] == [("incomplete", 2)]
    assert r.orphan_data_frames == 3 and r.flush(10_000) == []


def test_the_receiver_may_ask_for_packets_again():
    r = Reassembler()
    payload = bytes(range(20))
    back = parse_id(build_id(7, PGN_TP_CM, 0xF9, 0x00))
    first_two = (back, bytes([0x11, 2, 1, 0xFF, 0xFF, 0xEC, 0xFE, 0x00]))
    again_from_two = (back, bytes([0x11, 2, 2, 0xFF, 0xFF, 0xEC, 0xFE, 0x00]))
    one, two, three = packets_of(payload, sa=0x00, da=0xF9)
    frames = [announce(0x10, PGN_VIN, 20, sa=0x00, da=0xF9), first_two, one, two, again_from_two, two, three]
    done = []
    for row, (jid, data) in enumerate(frames):
        done += r.feed(50 * row, jid, data, ref=row)
    assert [(m.status, m.payload) for m in done] == [("complete", payload)]
    assert done[0].frames == ((0, 0), (2, 1), (3, 2), (5, 2), (6, 3)) and r.orphan_data_frames == 0


def test_packets_of_an_unannounced_message_do_not_fill_the_gaps_of_the_previous_one():
    r = Reassembler()
    first = [announce(0x20, PGN_VIN, len(VIN), sa=0x00), *packets_of(VIN, sa=0x00)]
    other = packets_of(b"2NP3LJ0X9KM000000", sa=0x00)  # its announcement was lost by the recorder
    done = run(r, [first[0], first[1], *other])
    assert [(m.status, m.received) for m in done] == [("incomplete", 1)]
    assert r.orphan_data_frames == 3 and r.flush(1000) == []


def test_a_packet_number_past_the_announced_count_ends_the_message():
    r = Reassembler()
    frames = [announce(0x20, PGN_VIN, len(VIN), sa=0x00), *packets_of(bytes(35), sa=0x00)]
    done = run(r, [frames[0], frames[1], frames[5]])
    assert [(m.status, m.received) for m in done] == [("incomplete", 1)]


def test_a_message_lists_the_frames_it_was_built_from():
    r = Reassembler()
    first, second, third = packets_of(VIN, sa=0x00)
    stray = (parse_id(build_id(7, PGN_TP_DT, 0x21, 0xFF)), bytes([1]) + b"ABCDEFG")
    frames = [announce(0x20, PGN_VIN, len(VIN), sa=0x00), first, stray, second, third]
    done = []
    for row, (jid, data) in enumerate(frames):
        done += r.feed(50 * row, jid, data, ref=row)
    assert len(done) == 1 and done[0].status == "complete"
    assert done[0].frames == ((0, 0), (1, 1), (3, 2), (4, 3))  # row 2 belongs to another sender


def test_frames_are_not_tracked_unless_references_are_given():
    r = Reassembler()
    done = run(r, [announce(0x20, PGN_VIN, len(VIN), sa=0x00), *packets_of(VIN, sa=0x00)])
    assert done[0].frames == ()
