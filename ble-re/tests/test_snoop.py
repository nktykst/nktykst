"""合成した btsnoop ファイルでパーサを検証する。"""

import struct
from datetime import datetime, timezone
from io import BytesIO

from ble_re.snoop import (
    _BTSNOOP_EPOCH_OFFSET_US,
    DATALINK_HCI_H4,
    SnoopParser,
    iter_records,
    parse_file,
    summarize,
)

BASE_US = _BTSNOOP_EPOCH_OFFSET_US + 1_700_000_000 * 1_000_000  # 2023-11-14T22:13:20Z


def rec(flags: int, pkt: bytes, t_us: int) -> bytes:
    return struct.pack(">IIIIq", len(pkt), len(pkt), flags, 0, BASE_US + t_us) + pkt


def acl(conn: int, pb: int, payload: bytes) -> bytes:
    return bytes([0x02]) + struct.pack("<HH", conn | (pb << 12), len(payload)) + payload


def att(pdu: bytes) -> bytes:
    return struct.pack("<HH", len(pdu), 0x0004) + pdu


def le_conn_complete(conn: int, mac: bytes) -> bytes:
    params = bytes([0x01, 0x00]) + struct.pack("<H", conn) + bytes([0x00, 0x01]) + mac[::-1] + bytes(7)
    return bytes([0x04, 0x3E, len(params)]) + params


def build(records: list[bytes], datalink: int = DATALINK_HCI_H4) -> bytes:
    return b"btsnoop\x00" + struct.pack(">II", 1, datalink) + b"".join(records)


TX, RX = 0x00, 0x01
CHAR_UUID_128 = bytes.fromhex("6e400003b5a3f393e0a9e50e24dcca9e")


def sample() -> bytes:
    conn = 0x0040
    mac = bytes.fromhex("aabbccddeeff")
    # Read By Type Rsp (0x2803 = characteristic declaration): value handle 0x0012, notify, 128bit UUID
    decl = struct.pack("<HBH", 0x0011, 0x10, 0x0012) + CHAR_UUID_128[::-1]
    rbt_rsp = bytes([0x09, len(decl)]) + decl
    notif = bytes([0x1B]) + struct.pack("<H", 0x0012) + bytes(range(40))
    notif_l2 = att(notif)
    return build(
        [
            rec(RX | 0x02, le_conn_complete(conn, mac), 0),
            rec(TX, acl(conn, 0, att(bytes([0x02]) + struct.pack("<H", 247))), 1_000),
            rec(RX, acl(conn, 0, att(bytes([0x03]) + struct.pack("<H", 185))), 2_000),
            rec(TX, acl(conn, 0, att(bytes([0x08]) + struct.pack("<HHH", 0x0001, 0xFFFF, 0x2803))), 3_000),
            rec(RX, acl(conn, 0, att(rbt_rsp)), 4_000),
            rec(TX, acl(conn, 0, att(bytes([0x52]) + struct.pack("<H", 0x0015) + b"\x01\x02")), 5_000),
            # フラグメント化された notification (先頭 20 バイト + 残り)
            rec(RX, acl(conn, 0b10, notif_l2[:20]), 6_000),
            rec(RX, acl(conn, 0b01, notif_l2[20:]), 6_100),
            rec(TX, acl(conn, 0, att(bytes([0x0A]) + struct.pack("<H", 0x0003))), 7_000),
            rec(RX, acl(conn, 0, att(bytes([0x0B]) + b"Toy")), 8_000),
            rec(TX, acl(conn, 0, att(bytes([0x12]) + struct.pack("<H", 0x0099) + b"\xff")), 9_000),
            rec(RX, acl(conn, 0, att(bytes([0x01, 0x12]) + struct.pack("<H", 0x0099) + bytes([0x05]))), 10_000),
        ]
    )


def test_iter_records_timestamp_and_count():
    recs = list(iter_records(BytesIO(sample())))
    assert len(recs) == 12
    assert recs[0][2] == datetime(2023, 11, 14, 22, 13, 20, tzinfo=timezone.utc)


def test_parser_events(tmp_path):
    path = tmp_path / "btsnoop_hci.log"
    path.write_bytes(sample())
    p = parse_file(path)
    ops = [e.opcode_name for e in p.events]
    assert ops == [
        "MTU Req", "MTU Rsp", "Read By Type Req", "Read By Type Rsp", "Write Cmd",
        "Notification", "Read Req", "Read Rsp", "Write Req", "Error Rsp",
    ]
    assert p.addresses[0x40] == "aa:bb:cc:dd:ee:ff (random)"
    assert all(e.peer == "aa:bb:cc:dd:ee:ff (random)" for e in p.events)

    mtu_req, mtu_rsp, _, rbt_rsp, wcmd, notif, _, rrsp, _, err = p.events
    assert mtu_req.direction == "TX" and "mtu=247" in mtu_req.detail
    assert mtu_rsp.direction == "RX" and "mtu=185" in mtu_rsp.detail
    assert "value=0x0012" in rbt_rsp.detail and "Nordic UART TX" in rbt_rsp.detail
    assert wcmd.handle == 0x0015 and wcmd.value == b"\x01\x02"
    # 再構成された notification: handle -> UUID の対応が引けている
    assert notif.handle == 0x0012 and notif.value == bytes(range(40))
    assert notif.char_uuid == "6e400003-b5a3-f393-e0a9-e50e24dcca9e"
    # Read Rsp は直前の Read Req の handle に紐づく
    assert rrsp.handle == 0x0003 and rrsp.value == b"Toy"
    assert err.handle == 0x0099 and "Insufficient Authentication" in err.detail and "Write Req" in err.detail


def test_summary_counts():
    p = SnoopParser()
    for idx, flags, ts, pkt in iter_records(BytesIO(sample())):
        p.feed(idx, flags, ts, pkt)
    s = summarize(p.events)
    assert "0x0012  Notification" in s and "Nordic UART TX" in s
    assert "0x0015  Write Cmd" in s


def test_rejects_non_btsnoop():
    import pytest

    with pytest.raises(ValueError):
        list(iter_records(BytesIO(b"not a snoop file at all......")))
