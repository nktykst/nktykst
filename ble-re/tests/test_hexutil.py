import pytest

from ble_re.hexutil import describe_value, parse_bytes


@pytest.mark.parametrize(
    "text,expected",
    [
        ("0102ff", b"\x01\x02\xff"),
        ("01 02 FF", b"\x01\x02\xff"),
        ("0x0102ff", b"\x01\x02\xff"),
        ("01:02:ff", b"\x01\x02\xff"),
        ("str:hi", b"hi"),
        ("u8:200", b"\xc8"),
        ("u16le:513", b"\x01\x02"),
        ("u16be:513", b"\x02\x01"),
        ("u32le:0x01020304", b"\x04\x03\x02\x01"),
        ("", b""),
    ],
)
def test_parse_bytes(text, expected):
    assert parse_bytes(text) == expected


def test_parse_bytes_rejects_garbage():
    with pytest.raises(ValueError):
        parse_bytes("zz")
    with pytest.raises(ValueError):
        parse_bytes("abc")  # 奇数桁


def test_describe_value():
    assert "ascii='OK'" in describe_value(b"OK")
    assert "u16le=513" in describe_value(b"\x01\x02")
