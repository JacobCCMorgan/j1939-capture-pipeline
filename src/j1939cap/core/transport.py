"""Transport protocol: messages longer than eight bytes.

A long message is announced on the connection management group and then sent seven bytes at a time on the data
transfer group, each data frame starting with its sequence number. A broadcast announcement (BAM) goes to everyone
and is not acknowledged. An addressed transfer starts with a request to send and is paced by the receiver.

`Reassembler` follows every sender-to-destination link on the bus and reports one `TpMessage` per announcement,
with a status that says whether the whole message arrived:

- complete: every packet was seen.
- incomplete: the link moved on (a new announcement, packets of another message, or silence longer than the
  timeout) with packets missing.
- aborted: one side sent a connection abort.
- open_at_end: the capture stopped while packets were still arriving.

A caller that passes a reference with each frame (a row number, say) gets them back on the message: `frames` lists
the reference and sequence number of the announcement (sequence 0) and of every data frame that went into it.
"""

from __future__ import annotations

from typing import Any, NamedTuple

from .ids import PGN_TP_CM, PGN_TP_DT, J1939Id

CM_RTS = 0x10
CM_CTS = 0x11
CM_END_OF_MESSAGE_ACK = 0x13
CM_BAM = 0x20
CM_ABORT = 0xFF


class TpMessage(NamedTuple):
    kind: str
    sa: int
    da: int
    pgn: int
    size: int
    packets: int
    received: int
    status: str
    ts_start: int
    ts_end: int
    payload: bytes | None
    frames: tuple[tuple[Any, int], ...] = ()


class _Session:
    __slots__ = ("kind", "sa", "da", "pgn", "size", "packets", "ts_start", "last_ts", "last_sequence", "got", "frames")

    def __init__(self, kind: str, sa: int, da: int, pgn: int, size: int, packets: int, ts: int, ref: Any = None) -> None:
        self.kind, self.sa, self.da, self.pgn, self.size, self.packets = kind, sa, da, pgn, size, packets
        self.ts_start = self.last_ts = ts
        self.last_sequence = 0
        self.got: dict[int, bytes] = {}
        self.frames: list[tuple[Any, int]] = [] if ref is None else [(ref, 0)]

    def close(self, status: str) -> TpMessage:
        payload = None
        if status == "complete":
            payload = b"".join(self.got[n] for n in range(1, self.packets + 1))[: self.size]
        return TpMessage(
            self.kind,
            self.sa,
            self.da,
            self.pgn,
            self.size,
            self.packets,
            len(self.got),
            status,
            self.ts_start,
            self.last_ts,
            payload,
            tuple(self.frames),
        )


class Reassembler:
    def __init__(self, timeout_ms: int = 1250) -> None:
        self.timeout_ms = timeout_ms
        self.orphan_data_frames = 0
        self.malformed_announcements = 0
        self._open: dict[tuple[int, int], _Session] = {}

    def _close(self, key: tuple[int, int], status: str) -> list[TpMessage]:
        session = self._open.pop(key, None)
        return [session.close(status)] if session is not None else []

    def feed(self, ts_ms: int, jid: J1939Id, data: bytes, ref: Any = None) -> list[TpMessage]:
        """Take one frame; return the messages it finished, if any."""
        if jid.pgn == PGN_TP_CM and len(data) == 8:
            return self._control(ts_ms, jid, data, ref)
        if jid.pgn == PGN_TP_DT and data:
            return self._data(ts_ms, jid, data, ref)
        return []

    def _control(self, ts_ms: int, jid: J1939Id, data: bytes, ref: Any) -> list[TpMessage]:
        control = data[0]
        if control in (CM_RTS, CM_BAM):
            key = (jid.sa, jid.da)
            closed = self._close(key, "incomplete")
            size, packets = data[1] | (data[2] << 8), data[3]
            if packets == 0 or size <= 8 or size > 7 * packets:
                self.malformed_announcements += 1
                return closed
            pgn = data[5] | (data[6] << 8) | (data[7] << 16)
            self._open[key] = _Session("BAM" if control == CM_BAM else "RTS", jid.sa, jid.da, pgn, size, packets, ts_ms, ref)
            return closed
        if control == CM_ABORT:
            return self._close((jid.sa, jid.da), "aborted") + self._close((jid.da, jid.sa), "aborted")
        if control in (CM_CTS, CM_END_OF_MESSAGE_ACK):
            session = self._open.get((jid.da, jid.sa))  # sent by the receiver, back to the sender
            if session is not None:
                session.last_ts = ts_ms
        return []

    def _data(self, ts_ms: int, jid: J1939Id, data: bytes, ref: Any) -> list[TpMessage]:
        key = (jid.sa, jid.da)
        closed: list[TpMessage] = []
        session = self._open.get(key)
        if session is not None and ts_ms - session.last_ts > self.timeout_ms:
            closed = self._close(key, "incomplete")
            session = None
        if session is None:
            self.orphan_data_frames += 1
            return closed
        sequence = data[0]
        if sequence <= session.last_sequence and session.got.get(sequence) == data[1:8]:
            session.last_ts = ts_ms  # the same packet sent again
            if ref is not None:
                session.frames.append((ref, sequence))
            return closed
        if not session.last_sequence < sequence <= session.packets:
            # Packets arrive in order. A number that goes backwards, or past the announced count, belongs to a
            # message whose announcement was not seen; it must not be allowed to fill the gaps of this one.
            self.orphan_data_frames += 1
            return self._close(key, "incomplete")
        session.got[sequence] = data[1:8]
        session.last_sequence = sequence
        session.last_ts = ts_ms
        if ref is not None:
            session.frames.append((ref, sequence))
        if len(session.got) == session.packets:
            closed += self._close(key, "complete")
        return closed

    def flush(self, ts_ms: int) -> list[TpMessage]:
        """Close whatever is still open at the end of the capture."""
        out: list[TpMessage] = []
        for key in list(self._open):
            session = self._open[key]
            out += self._close(key, "open_at_end" if ts_ms - session.last_ts <= self.timeout_ms else "incomplete")
        return out
