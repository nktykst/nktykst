"""PacketLogger (.pklg) を合成して読めることを確かめる。"""

import struct
from io import BytesIO

import pytest

from ble_re.snoop import SnoopParser, iter_records


def pklg_rec(ptype: int, body: bytes, sec: int, usec: int, endian: str) -> bytes:
    payload = struct.pack(endian + "II", sec, usec) + bytes([ptype]) + body
    return struct.pack(endian + "I", len(payload)) + payload


def acl_att(conn: int, pdu: bytes) -> bytes:
    l2 = struct.pack("<HH", len(pdu), 0x0004) + pdu
    return struct.pack("<HH", conn, len(l2)) + l2


@pytest.mark.parametrize("endian", [">", "<"])
def test_pklg_roundtrip(endian):
    conn = 0x0041
    data = b"".join(
        [
            pklg_rec(0x00, bytes([0x01, 0x20, 0x00]), 1_700_000_000, 0, endian),  # HCI cmd
            pklg_rec(0x02, acl_att(conn, bytes([0x52]) + struct.pack("<H", 0x0021) + b"\x02"), 1_700_000_001, 500_000, endian),
            pklg_rec(0x03, acl_att(conn, bytes([0x1B]) + struct.pack("<H", 0x0025) + b"\xd2\x03"), 1_700_000_002, 0, endian),
            pklg_rec(0xFC, b"some log string", 1_700_000_003, 0, endian),  # 文字列レコードは無視
        ]
    )
    recs = list(iter_records(BytesIO(data)))
    assert [r[3][0] for r in recs] == [0x01, 0x02, 0x02]  # H4 に正規化されている
    assert [r[1] for r in recs] == [0, 0, 1]  # TX, TX, RX
    assert recs[1][2].isoformat() == "2023-11-14T22:13:21.500000+00:00"

    p = SnoopParser()
    for idx, flags, ts, pkt in recs:
        p.feed(idx, flags, ts, pkt)
    assert [(e.opcode_name, e.direction, e.handle, e.value) for e in p.events] == [
        ("Write Cmd", "TX", 0x0021, b"\x02"),
        ("Notification", "RX", 0x0025, b"\xd2\x03"),
    ]
