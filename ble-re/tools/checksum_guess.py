#!/usr/bin/env python3
"""末尾 1〜2 バイトがどのチェックサムか総当たりで推定する。

    python checksum_guess.py 7e000401007bef 7e000400007aef 7e000132004def

複数パケットを渡し、すべてで一致した方式だけを表示する。
"""
from __future__ import annotations

import sys


def crc8(data: bytes, poly: int, init: int = 0, xorout: int = 0) -> int:
    crc = init
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = ((crc << 1) ^ poly) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc ^ xorout


def crc16(data: bytes, poly: int, init: int, refin: bool, refout: bool, xorout: int) -> int:
    def reflect(v: int, bits: int) -> int:
        return int(f"{v:0{bits}b}"[::-1], 2)
    crc = init
    for b in data:
        if refin:
            b = reflect(b, 8)
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ poly) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    if refout:
        crc = reflect(crc, 16)
    return crc ^ xorout


CANDIDATES_8 = {
    "sum8": lambda d: sum(d) & 0xFF,
    "sum8_neg": lambda d: (-sum(d)) & 0xFF,
    "xor8": lambda d: __import__("functools").reduce(lambda a, b: a ^ b, d, 0),
    "crc8_0x07": lambda d: crc8(d, 0x07),
    "crc8_0x31_maxim_init0": lambda d: crc8(d, 0x31),
    "crc8_0x9b": lambda d: crc8(d, 0x9B),
    "crc8_0x1d_init_ff": lambda d: crc8(d, 0x1D, 0xFF),
}
CANDIDATES_16 = {
    "crc16_ccitt_false": lambda d: crc16(d, 0x1021, 0xFFFF, False, False, 0),
    "crc16_xmodem": lambda d: crc16(d, 0x1021, 0x0000, False, False, 0),
    "crc16_modbus": lambda d: crc16(d, 0x8005, 0xFFFF, True, True, 0),
    "crc16_arc": lambda d: crc16(d, 0x8005, 0x0000, True, True, 0),
    "crc16_kermit": lambda d: crc16(d, 0x1021, 0x0000, True, True, 0),
    "sum16": lambda d: sum(d) & 0xFFFF,
}


def main(argv: list[str]) -> None:
    if len(argv) < 2:
        sys.exit(__doc__)
    pkts = [bytes.fromhex(a.replace(" ", "")) for a in argv[1:]]
    hits: list[str] = []
    # 1 バイト検査: 先頭 skip バイトを除いた範囲、末尾 1 バイト (+ 末尾に固定フッタが 0〜1 バイト)
    for skip in range(0, 3):
        for tail in range(0, 2):  # 末尾の固定フッタ (例 0xef) を除く
            for name, fn in CANDIDATES_8.items():
                if all(len(p) > skip + 1 + tail and fn(p[skip:len(p) - 1 - tail]) == p[len(p) - 1 - tail] for p in pkts):
                    hits.append(f"8bit  {name:24} range=[{skip}:-{1 + tail}] byte=-{1 + tail}")
            for name, fn in CANDIDATES_16.items():
                for endian in ("little", "big"):
                    if all(
                        len(p) > skip + 2 + tail
                        and fn(p[skip:len(p) - 2 - tail]) == int.from_bytes(p[len(p) - 2 - tail:len(p) - tail], endian)
                        for p in pkts
                    ):
                        hits.append(f"16bit {name:24} range=[{skip}:-{2 + tail}] {endian}-endian")
    print("\n".join(hits) if hits else "一致なし (暗号化・独自方式・フッタ長の違いを疑う)")


if __name__ == "__main__":
    main(sys.argv)
