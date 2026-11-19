"""A small made-up bus for the tests: every value in it is invented."""

from j1939cap.core.capture import Frame
from j1939cap.core.ids import PGN_TP_CM, PGN_TP_DT, build_id
from j1939cap.core.vin import check_digit

VIN = "1FUAB1CD0LE000001"
VIN = VIN[:8] + check_digit(VIN) + VIN[9:]


def le(value, size):
    return round(value).to_bytes(size, "little")


def bam(ts, pgn, payload, sa):
    packets = -(-len(payload) // 7)
    head = bytes([0x20]) + le(len(payload), 2) + bytes([packets, 0xFF]) + le(pgn, 3)
    frames = [Frame(ts, build_id(7, PGN_TP_CM, sa, 0xFF), head)]
    for n in range(packets):
        chunk = payload[7 * n : 7 * n + 7]
        frames.append(Frame(ts + 50 * (n + 1), build_id(7, PGN_TP_DT, sa, 0xFF), bytes([n + 1]) + chunk + b"\xff" * (7 - len(chunk))))
    return frames


def truck(seconds=20):
    """Twenty seconds of a small bus: an engine, an instrument cluster that disagrees about distance, a brake controller."""
    frames = []
    for ms in range(0, seconds * 1000, 10):
        frames.append(Frame(ms, build_id(3, 0xF004, 0x00), bytes(3) + le(1200 * 8, 2) + bytes(3)))
        if ms % 100 == 0:
            frames.append(Frame(ms, build_id(6, 0xFEF1, 0x00), b"\xff" + le(round(89.5 * 256), 2) + b"\xff" * 5))
        if ms % 1000 == 0:
            km = 500_000 + ms / 1000 * 0.025
            frames.append(Frame(ms, build_id(6, 0xFEC1, 0x00), le(km / 0.005, 4) + b"\xff" * 4))
            frames.append(Frame(ms + 1, build_id(6, 0xFEC1, 0x17), le((km - 312.4) / 0.005, 4) + b"\xff" * 4))
            frames.append(Frame(ms + 2, build_id(6, 0xFEE5, 0x00), le(12_345.5 / 0.05, 4) + b"\xff" * 4))
            frames.append(Frame(ms + 3, build_id(6, 0xFECA, 0x0B), bytes.fromhex("04FF21061F02FFFF")))
        if ms % 5000 == 0:
            frames += bam(ms + 4, 0xFEEC, VIN.encode() + b"*", sa=0x00)
    frames.append(Frame(700, build_id(6, 0xEA00, 0xF9, 0x00), le(0xFEEC, 3)))
    frames.append(Frame(710, build_id(6, 0xE800, 0x0B, 0xFF), bytes([1, 0xFF, 0xFF, 0xFF, 0xF9]) + le(0xFEDA, 3)))
    return sorted(frames, key=lambda f: f.ts_ms)
