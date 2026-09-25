"""HCI ログ (Android の btsnoop_hci.log / iOS・macOS の PacketLogger .pklg) を読んで
ATT レイヤの通信を時系列に並べる。

Wireshark なしで「アプリがどの handle に何を書き、何が notify されたか」を一覧にするのが目的。
対応: btsnoop v1 / datalink 1001 (HCI unencapsulated), 1002 (HCI UART H4)、
      Apple PacketLogger (.pklg, big/little endian 自動判定)。
L2CAP の分割再構成、LE Connection Complete による handle -> アドレス対応、
Read By Type 応答からの value handle -> characteristic UUID 対応も行う。
"""

from __future__ import annotations

import json
import struct
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import BinaryIO

from .hexutil import hexstr
from .uuids import short_uuid, uuid_name

BTSNOOP_MAGIC = b"btsnoop\x00"
DATALINK_HCI_UNENCAP = 1001
DATALINK_HCI_H4 = 1002
# btsnoop のタイムスタンプは西暦 0 年 1 月 1 日からのマイクロ秒
_BTSNOOP_EPOCH_OFFSET_US = 0x00E03AB44A676000

H4_CMD, H4_ACL, H4_SCO, H4_EVT = 0x01, 0x02, 0x03, 0x04
CID_ATT = 0x0004

ATT_OPCODES: dict[int, str] = {
    0x01: "Error Rsp",
    0x02: "MTU Req",
    0x03: "MTU Rsp",
    0x04: "Find Info Req",
    0x05: "Find Info Rsp",
    0x06: "Find By Type Value Req",
    0x07: "Find By Type Value Rsp",
    0x08: "Read By Type Req",
    0x09: "Read By Type Rsp",
    0x0A: "Read Req",
    0x0B: "Read Rsp",
    0x0C: "Read Blob Req",
    0x0D: "Read Blob Rsp",
    0x0E: "Read Multiple Req",
    0x0F: "Read Multiple Rsp",
    0x10: "Read By Group Type Req",
    0x11: "Read By Group Type Rsp",
    0x12: "Write Req",
    0x13: "Write Rsp",
    0x16: "Prepare Write Req",
    0x17: "Prepare Write Rsp",
    0x18: "Execute Write Req",
    0x19: "Execute Write Rsp",
    0x1B: "Notification",
    0x1D: "Indication",
    0x1E: "Confirmation",
    0x52: "Write Cmd",
    0xD2: "Signed Write Cmd",
}

ATT_ERRORS: dict[int, str] = {
    0x01: "Invalid Handle",
    0x02: "Read Not Permitted",
    0x03: "Write Not Permitted",
    0x04: "Invalid PDU",
    0x05: "Insufficient Authentication",
    0x06: "Request Not Supported",
    0x07: "Invalid Offset",
    0x08: "Insufficient Authorization",
    0x09: "Prepare Queue Full",
    0x0A: "Attribute Not Found",
    0x0B: "Attribute Not Long",
    0x0C: "Insufficient Encryption Key Size",
    0x0D: "Invalid Attribute Value Length",
    0x0E: "Unlikely Error",
    0x0F: "Insufficient Encryption",
    0x10: "Unsupported Group Type",
    0x11: "Insufficient Resources",
}

# 手動解析で重要な「データが動く」opcode
DATA_OPCODES = {0x0B, 0x0D, 0x12, 0x16, 0x1B, 0x1D, 0x52, 0xD2}


@dataclass
class AttEvent:
    index: int
    ts: datetime
    direction: str  # "TX" (host -> controller = スマホが送った) / "RX" (受信)
    conn_handle: int
    peer: str | None
    opcode: int
    handle: int | None
    value: bytes
    char_uuid: str | None = None
    detail: str = ""

    @property
    def opcode_name(self) -> str:
        return ATT_OPCODES.get(self.opcode, f"op 0x{self.opcode:02x}")

    def to_dict(self) -> dict:
        return {
            "i": self.index,
            "ts": self.ts.isoformat(timespec="microseconds"),
            "dir": self.direction,
            "conn": self.conn_handle,
            "peer": self.peer,
            "opcode": self.opcode,
            "op": self.opcode_name,
            "handle": self.handle,
            "uuid": self.char_uuid,
            "hex": self.value.hex(),
            "detail": self.detail,
        }


@dataclass
class _ConnState:
    peer: str | None = None
    reassembly: dict[str, bytearray] = field(default_factory=dict)  # per direction
    expected: dict[str, int] = field(default_factory=dict)
    last_read_handle: dict[str, int] = field(default_factory=dict)
    last_read_by_type_uuid: int | None = None
    last_read_by_type_range: tuple[int, int] | None = None
    handle_uuid: dict[int, str] = field(default_factory=dict)  # value handle -> char uuid


def _uuid_from_bytes(b: bytes) -> str:
    if len(b) == 2:
        return f"0000{struct.unpack('<H', b)[0]:04x}-0000-1000-8000-00805f9b34fb"
    if len(b) == 16:
        # ATT 上の 128bit UUID はリトルエンディアン
        h = b[::-1].hex()
        return f"{h[0:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"
    return b.hex()


def _ts(us: int) -> datetime:
    return datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(microseconds=us - _BTSNOOP_EPOCH_OFFSET_US)


def iter_records(fp: BinaryIO) -> Iterator[tuple[int, int, datetime, bytes]]:
    """(record index, flags, timestamp, H4 packet bytes) を順に返す。

    flags は btsnoop 流儀: bit0 = 1 なら受信 (controller -> host)。
    パケットは常に H4 (先頭 1 バイトが 01=cmd / 02=ACL / 04=event) に正規化する。
    """
    head = fp.read(8)
    fp.seek(0)
    if head == BTSNOOP_MAGIC:
        yield from _iter_btsnoop(fp)
    else:
        yield from _iter_pklg(fp)


def _iter_btsnoop(fp: BinaryIO) -> Iterator[tuple[int, int, datetime, bytes]]:
    header = fp.read(16)
    if len(header) < 16 or header[:8] != BTSNOOP_MAGIC:
        raise ValueError("btsnoop 形式ではありません (magic 不一致)。Android の btsnooz 形式なら AOSP の btsnooz.py で展開してください。")
    version, datalink = struct.unpack(">II", header[8:16])
    if version != 1:
        raise ValueError(f"未対応の btsnoop version {version}")
    if datalink not in (DATALINK_HCI_UNENCAP, DATALINK_HCI_H4):
        raise ValueError(f"未対応の datalink {datalink} (1001/1002 のみ対応)")
    idx = 0
    while True:
        rec = fp.read(24)
        if len(rec) < 24:
            return
        orig_len, incl_len, flags, _drops, ts_us = struct.unpack(">IIIIq", rec)
        data = fp.read(incl_len)
        if len(data) < incl_len:
            return
        if datalink == DATALINK_HCI_UNENCAP:
            # flags bit1: 1 = command/event, 0 = ACL data。方向 bit0: 0 = sent, 1 = received
            if flags & 0x02:
                ptype = H4_EVT if flags & 0x01 else H4_CMD
            else:
                ptype = H4_ACL
            data = bytes([ptype]) + data
        yield idx, flags, _ts(ts_us), data
        idx += 1


# PacketLogger のレコード種別 -> (H4 type, 受信フラグ)
_PKLG_TYPES: dict[int, tuple[int, int]] = {
    0x00: (H4_CMD, 0),  # HCI command (host -> controller)
    0x01: (H4_EVT, 1),  # HCI event
    0x02: (H4_ACL, 0),  # ACL sent
    0x03: (H4_ACL, 1),  # ACL received
}
_PKLG_MAX_LEN = 1 << 20


def _pklg_endian(data: bytes) -> str:
    """先頭数レコードを両エンディアンで歩いて、辻褄が合う方を選ぶ。"""
    best, best_n = ">", -1
    for endian in (">", "<"):
        pos, n = 0, 0
        while pos + 13 <= len(data) and n < 8:
            ln = struct.unpack(endian + "I", data[pos : pos + 4])[0]
            if ln < 9 or ln > _PKLG_MAX_LEN or pos + 4 + ln > len(data):
                break
            n += 1
            pos += 4 + ln
        if n > best_n:
            best, best_n = endian, n
    if best_n <= 0:
        raise ValueError("btsnoop でも PacketLogger (.pklg) でもないファイルです")
    return best


def _iter_pklg(fp: BinaryIO) -> Iterator[tuple[int, int, datetime, bytes]]:
    """Apple PacketLogger: [len u32][ts_sec u32][ts_usec u32][type u8][data]。len は type 以降 + 8。"""
    data = fp.read()
    endian = _pklg_endian(data)
    pos, idx = 0, 0
    while pos + 13 <= len(data):
        ln, sec, usec = struct.unpack(endian + "III", data[pos : pos + 12])
        ptype = data[pos + 12]
        if ln < 9 or pos + 4 + ln > len(data):
            return
        body = data[pos + 13 : pos + 4 + ln]
        pos += 4 + ln
        if ptype in _PKLG_TYPES:
            h4, rx = _PKLG_TYPES[ptype]
            ts = datetime.fromtimestamp(sec, tz=timezone.utc) + timedelta(microseconds=usec)
            yield idx, rx, ts, bytes([h4]) + body
            idx += 1


class SnoopParser:
    def __init__(self) -> None:
        self.conns: dict[int, _ConnState] = {}
        self.events: list[AttEvent] = []
        self.addresses: dict[int, str] = {}

    # ---- HCI ----
    def feed(self, idx: int, flags: int, ts: datetime, pkt: bytes) -> None:
        if not pkt:
            return
        direction = "RX" if flags & 0x01 else "TX"
        ptype = pkt[0]
        body = pkt[1:]
        if ptype == H4_EVT:
            self._on_event(body)
        elif ptype == H4_ACL and len(body) >= 4:
            self._on_acl(idx, ts, direction, body)

    def _on_event(self, body: bytes) -> None:
        if len(body) < 2:
            return
        code, plen = body[0], body[1]
        params = body[2 : 2 + plen]
        if code == 0x3E and params:  # LE Meta Event
            sub = params[0]
            if sub in (0x01, 0x0A) and len(params) >= 12:  # (Enhanced) Connection Complete
                status, handle = params[1], struct.unpack("<H", params[2:4])[0]
                addr_type, addr = params[5], params[6:12]
                if status == 0:
                    mac = ":".join(f"{b:02x}" for b in addr[::-1])
                    kind = "public" if addr_type == 0 else "random"
                    self.addresses[handle] = f"{mac} ({kind})"
                    self.conns.setdefault(handle, _ConnState()).peer = self.addresses[handle]
        elif code == 0x05 and len(params) >= 3:  # Disconnection Complete
            handle = struct.unpack("<H", params[1:3])[0]
            self.conns.pop(handle, None)

    def _on_acl(self, idx: int, ts: datetime, direction: str, body: bytes) -> None:
        hf, length = struct.unpack("<HH", body[:4])
        conn = hf & 0x0FFF
        pb = (hf >> 12) & 0x3
        payload = body[4 : 4 + length]
        st = self.conns.setdefault(conn, _ConnState())
        if pb in (0x00, 0x02):  # first fragment
            if len(payload) < 4:
                return
            l2len = struct.unpack("<H", payload[:2])[0]
            st.reassembly[direction] = bytearray(payload)
            st.expected[direction] = l2len + 4
        elif pb == 0x01:  # continuation
            if direction not in st.reassembly:
                return
            st.reassembly[direction] += payload
        else:
            return
        buf = st.reassembly[direction]
        if len(buf) >= st.expected.get(direction, 1 << 30):
            del st.reassembly[direction]
            self._on_l2cap(idx, ts, direction, conn, st, bytes(buf))

    def _on_l2cap(self, idx: int, ts: datetime, direction: str, conn: int, st: _ConnState, pdu: bytes) -> None:
        l2len, cid = struct.unpack("<HH", pdu[:4])
        if cid != CID_ATT:
            return
        self._on_att(idx, ts, direction, conn, st, pdu[4 : 4 + l2len])

    # ---- ATT ----
    def _on_att(self, idx: int, ts: datetime, direction: str, conn: int, st: _ConnState, att: bytes) -> None:
        if not att:
            return
        op = att[0]
        p = att[1:]
        handle: int | None = None
        value = b""
        detail = ""
        u16 = lambda b: struct.unpack("<H", b)[0]  # noqa: E731

        if op in (0x12, 0x52, 0xD2, 0x1B, 0x1D) and len(p) >= 2:
            handle, value = u16(p[:2]), p[2:]
        elif op == 0x0A and len(p) >= 2:
            handle = u16(p[:2])
            st.last_read_handle[direction] = handle
        elif op == 0x0C and len(p) >= 4:
            handle = u16(p[:2])
            st.last_read_handle[direction] = handle
            detail = f"offset={u16(p[2:4])}"
        elif op in (0x0B, 0x0D):
            # 応答は逆方向の Read Req に対応する
            other = "RX" if direction == "TX" else "TX"
            handle, value = st.last_read_handle.get(other), p
        elif op == 0x16 and len(p) >= 4:
            handle, value = u16(p[:2]), p[4:]
            detail = f"offset={u16(p[2:4])}"
        elif op == 0x01 and len(p) >= 4:
            handle = u16(p[1:3])
            detail = f"req={ATT_OPCODES.get(p[0], hex(p[0]))} err={ATT_ERRORS.get(p[3], hex(p[3]))}"
        elif op in (0x02, 0x03) and len(p) >= 2:
            detail = f"mtu={u16(p[:2])}"
        elif op == 0x08 and len(p) >= 6:
            uuid = _uuid_from_bytes(p[4:])
            st.last_read_by_type_uuid = u16(p[4:6]) if len(p) == 6 else None
            detail = f"range=0x{u16(p[:2]):04x}-0x{u16(p[2:4]):04x} type={short_uuid(uuid)} {uuid_name(uuid)}"
        elif op == 0x09 and len(p) >= 1:
            each = p[0]
            entries = []
            for off in range(1, len(p) - each + 1, each):
                h = u16(p[off : off + 2])
                v = p[off + 2 : off + each]
                if st.last_read_by_type_uuid == 0x2803 and len(v) >= 3:
                    props, vh, cu = v[0], u16(v[1:3]), _uuid_from_bytes(v[3:])
                    st.handle_uuid[vh] = cu
                    entries.append(f"decl@0x{h:04x} value=0x{vh:04x} props=0x{props:02x} {short_uuid(cu)} {uuid_name(cu)}")
                else:
                    entries.append(f"0x{h:04x}={v.hex()}")
            detail = "; ".join(entries)
        elif op == 0x10 and len(p) >= 6:
            uuid = _uuid_from_bytes(p[4:])
            detail = f"range=0x{u16(p[:2]):04x}-0x{u16(p[2:4]):04x} group={short_uuid(uuid)}"
        elif op == 0x11 and len(p) >= 1:
            each = p[0]
            entries = []
            for off in range(1, len(p) - each + 1, each):
                s, e = u16(p[off : off + 2]), u16(p[off + 2 : off + 4])
                su = _uuid_from_bytes(p[off + 4 : off + each])
                entries.append(f"svc 0x{s:04x}-0x{e:04x} {short_uuid(su)} {uuid_name(su)}")
            detail = "; ".join(entries)
        elif op == 0x05 and len(p) >= 1:
            fmt = p[0]
            size = 4 if fmt == 1 else 18
            entries = []
            for off in range(1, len(p) - size + 1, size):
                h = u16(p[off : off + 2])
                du = _uuid_from_bytes(p[off + 2 : off + size])
                entries.append(f"0x{h:04x}={short_uuid(du)}")
            detail = "; ".join(entries)

        ev = AttEvent(
            index=idx,
            ts=ts,
            direction=direction,
            conn_handle=conn,
            peer=st.peer or self.addresses.get(conn),
            opcode=op,
            handle=handle,
            value=bytes(value),
            char_uuid=st.handle_uuid.get(handle) if handle is not None else None,
            detail=detail,
        )
        self.events.append(ev)


def parse_file(path: Path | str) -> SnoopParser:
    parser = SnoopParser()
    with open(path, "rb") as fp:
        for idx, flags, ts, pkt in iter_records(fp):
            parser.feed(idx, flags, ts, pkt)
    return parser


def format_event(ev: AttEvent, t0: datetime | None = None) -> str:
    rel = f"{(ev.ts - t0).total_seconds():9.3f}" if t0 else ev.ts.strftime("%H:%M:%S.%f")
    arrow = "->" if ev.direction == "TX" else "<-"
    h = f"0x{ev.handle:04x}" if ev.handle is not None else "      "
    u = f"{short_uuid(ev.char_uuid)} {uuid_name(ev.char_uuid)}" if ev.char_uuid else ""
    val = hexstr(ev.value)
    tail = "  ".join(x for x in (val, ev.detail) if x)
    return f"[{rel}] {arrow} c{ev.conn_handle} {ev.opcode_name:<24} {h} {u:<40} {tail}"


def summarize(events: list[AttEvent]) -> str:
    from collections import Counter

    by_handle: Counter[tuple[int | None, str]] = Counter()
    for ev in events:
        if ev.opcode in DATA_OPCODES:
            by_handle[(ev.handle, ev.opcode_name)] += 1
    lines = ["handle  op                       count  uuid"]
    uuid_of: dict[int | None, str | None] = {ev.handle: ev.char_uuid for ev in events}
    for (h, name), n in sorted(by_handle.items(), key=lambda kv: (kv[0][0] or 0, kv[0][1])):
        hs = f"0x{h:04x}" if h is not None else "?"
        cu = uuid_of.get(h)
        us = f"{short_uuid(cu)} {uuid_name(cu)}" if cu else ""
        lines.append(f"{hs:<7} {name:<24} {n:>5}  {us}")
    return "\n".join(lines)


def events_to_jsonl(events: list[AttEvent]) -> str:
    return "\n".join(json.dumps(ev.to_dict(), ensure_ascii=False) for ev in events)
