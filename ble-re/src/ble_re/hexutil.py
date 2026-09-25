"""16進文字列 <-> bytes 変換とダンプ表示のユーティリティ。"""

from __future__ import annotations

import re
import struct

_HEX_RE = re.compile(r"^[0-9a-fA-F]*$")


def parse_bytes(text: str) -> bytes:
    """コマンドライン引数からペイロードを作る。

    受け付ける形式:
      "01 02 ff", "0102ff", "0x0102ff", "01:02:ff"   -> 16進
      "str:hello"                                     -> UTF-8 文字列
      "u8:200", "u16le:513", "u16be:513", "u32le:1"   -> 整数
    """
    if text.startswith("str:"):
        return text[4:].encode("utf-8")
    for prefix, fmt in (("u8:", "<B"), ("u16le:", "<H"), ("u16be:", ">H"), ("u32le:", "<I"), ("u32be:", ">I")):
        if text.startswith(prefix):
            return struct.pack(fmt, int(text[len(prefix) :], 0))
    cleaned = text.strip()
    if cleaned.lower().startswith("0x"):
        cleaned = cleaned[2:]
    cleaned = re.sub(r"[\s:_-]", "", cleaned)
    if not _HEX_RE.match(cleaned) or len(cleaned) % 2:
        raise ValueError(f"16進として解釈できません: {text!r}")
    return bytes.fromhex(cleaned)


def hexstr(data: bytes | bytearray | memoryview, sep: str = " ") -> str:
    return bytes(data).hex(sep) if data else ""


def printable(data: bytes | bytearray) -> str:
    return "".join(chr(b) if 32 <= b < 127 else "." for b in bytes(data))


def hexdump(data: bytes | bytearray, width: int = 16, indent: str = "") -> str:
    data = bytes(data)
    lines = []
    for off in range(0, len(data), width):
        chunk = data[off : off + width]
        hexpart = chunk.hex(" ").ljust(width * 3 - 1)
        lines.append(f"{indent}{off:04x}  {hexpart}  |{printable(chunk)}|")
    return "\n".join(lines)


def describe_value(data: bytes | bytearray) -> str:
    """値の「ありそうな解釈」を並べる。手動解析のとっかかり用。"""
    data = bytes(data)
    parts = [f"hex={data.hex()}" if data else "hex=(empty)"]
    if data and all(32 <= b < 127 for b in data):
        parts.append(f"ascii={data.decode('ascii')!r}")
    if len(data) == 1:
        parts.append(f"u8={data[0]}")
    if len(data) == 2:
        parts.append(f"u16le={struct.unpack('<H', data)[0]} u16be={struct.unpack('>H', data)[0]}")
    if len(data) == 4:
        parts.append(f"u32le={struct.unpack('<I', data)[0]} f32le={struct.unpack('<f', data)[0]:.4g}")
    return " ".join(parts)
