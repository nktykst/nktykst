"""合成パケット + CSV でフィールド配置の推定が当たることを確かめる。"""

import json
import random
import struct

from ble_re.fit import fit_column, load_csv_columns, load_packets, run_fit


def make_packet(rri: int, temp: float, ax: float) -> bytes:
    # 想定レイアウト: [0]=type, [1:3]=rri u16le, [3:5]=temp*100 u16be, [5:7]=ax*4096 i16le, [7]=seq
    return bytes([0xA1]) + struct.pack("<H", rri) + struct.pack(">H", int(round(temp * 100))) + struct.pack("<h", int(round(ax * 4096))) + bytes([rri % 256])


def synth(n: int = 200, seed: int = 0):
    rng = random.Random(seed)
    rows, pkts = [], []
    for _ in range(n):
        rri = rng.randint(600, 1200)
        temp = round(rng.uniform(28.0, 35.0), 2)
        ax = round(rng.uniform(-1.0, 1.0), 4)
        rows.append((rri, temp, ax))
        pkts.append(make_packet(rri, temp, ax))
    return rows, pkts


def test_fit_column_finds_layout():
    rows, pkts = synth()
    best = fit_column(pkts, [r[0] for r in rows], "rri")[0]
    assert (best.ftype, best.offset) == ("u16le", 1) and abs(best.slope - 1) < 1e-9

    best = fit_column(pkts, [r[1] for r in rows], "temperature")[0]
    assert (best.ftype, best.offset) == ("u16be", 3) and abs(best.slope - 0.01) < 1e-6

    best = fit_column(pkts, [r[2] for r in rows], "ax")[0]
    assert (best.ftype, best.offset) == ("i16le", 5) and abs(best.slope - 1 / 4096) < 1e-7


def test_end_to_end_with_offset_and_meta_csv(tmp_path):
    rows, pkts = synth(150, seed=1)
    # CSV は WHS-3 アプリ風にメタ行つき。パケット側は CSV より 3 拍早く始まっている
    csv_path = tmp_path / "s.csv"
    csv_path.write_text(
        "app,whs-3\nos,ios\nmode,rri\n"
        "timestamp,rri,temperature,acceleration x+\n"
        + "".join(f"2026/06/26 02:33:{i % 60:02d}.000,{r[0]},{r[1]},{r[2]}\n" for i, r in enumerate(rows[3:])),
        encoding="utf-8",
    )
    jsonl = tmp_path / "p.jsonl"
    with open(jsonl, "w") as fp:
        for i, p in enumerate(pkts):
            fp.write(json.dumps({"kind": "notify", "handle": 18, "hex": p.hex()}) + "\n")
        fp.write(json.dumps({"kind": "write", "handle": 21, "hex": "01"}) + "\n")  # 書き込みは無視される

    packets = load_packets(jsonl, handle=18)
    assert len(packets) == len(pkts)
    columns, cols = load_csv_columns(csv_path, ["rri", "temperature", "acceleration x+"])
    report = run_fit(packets, columns, cols)
    assert "shift=+3" in report
    assert "rri = u16le[1]" in report
    assert "temperature = u16be[3] * 1/100" in report
    assert "acceleration x+ = i16le[5] * 1/4096" in report
