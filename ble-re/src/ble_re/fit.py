"""キャプチャした notify のバイト列と、公式アプリが書き出した CSV の値を突き合わせて
パケットのフィールド配置 (オフセット・型・スケール) を推定する。

やっていること:
  1. パケット列 (JSONL の "hex") と CSV の行を 1:1 に並べる (先頭ズレは自動探索)
  2. CSV の各数値列について、全オフセット × 全整数/浮動小数型の候補を切り出し、
     y = a * x + b の線形回帰で決定係数 R² を出す
  3. R² が高い候補を「この列はここに入っている」として報告する

WHS-3 のように「1 notify = 1 拍 = CSV 1 行」の機器で特に効く。
"""

from __future__ import annotations

import csv
import io
import json
import math
import struct
from dataclasses import dataclass
from pathlib import Path

# 型名 -> (struct フォーマット, バイト幅)。u24 は手で組む
FIELD_TYPES: dict[str, tuple[str | None, int]] = {
    "u8": ("<B", 1),
    "i8": ("<b", 1),
    "u16le": ("<H", 2),
    "u16be": (">H", 2),
    "i16le": ("<h", 2),
    "i16be": (">h", 2),
    "u24le": (None, 3),
    "u24be": (None, 3),
    "u32le": ("<I", 4),
    "u32be": (">I", 4),
    "i32le": ("<i", 4),
    "i32be": (">i", 4),
    "f32le": ("<f", 4),
    "f32be": (">f", 4),
}


def extract_field(data: bytes, offset: int, ftype: str) -> float | None:
    fmt, width = FIELD_TYPES[ftype]
    if offset < 0 or offset + width > len(data):
        return None
    chunk = data[offset : offset + width]
    if fmt is None:
        return float(int.from_bytes(chunk, "little" if ftype.endswith("le") else "big"))
    v = struct.unpack(fmt, chunk)[0]
    if isinstance(v, float) and not math.isfinite(v):
        return None
    return float(v)


@dataclass
class Fit:
    column: str
    offset: int
    ftype: str
    slope: float
    intercept: float
    r2: float
    n: int

    def describe(self) -> str:
        expr = f"{self.ftype}[{self.offset}]"
        if abs(self.slope - 1) < 1e-9 and abs(self.intercept) < 1e-9:
            rhs = expr
        elif abs(self.intercept) < 1e-6 * max(1.0, abs(self.slope)):
            rhs = f"{expr} * {_nice(self.slope)}"
        else:
            rhs = f"{expr} * {_nice(self.slope)} + {_nice(self.intercept)}"
        return f"R²={self.r2:.5f}  n={self.n:<5} {self.column} = {rhs}"


def _nice(x: float, rel_tol: float = 5e-4) -> str:
    """1/4096 や 0.01 のような「きれいな」スケールに近ければそう表示する (実値も添える)。"""
    if x != 0 and abs(x) < 1:
        inv = 1 / x
        if abs(inv - round(inv)) <= rel_tol * abs(inv):
            snapped = 1 / round(inv)
            tail = "" if abs(snapped - x) < 1e-12 else f" (≈{x:.6g})"
            return f"1/{int(round(inv))}{tail}"
    return f"{x:.6g}"


def linear_fit(xs: list[float], ys: list[float]) -> tuple[float, float, float] | None:
    """最小二乗で (slope, intercept, R²) を返す。x か y が定数なら None。"""
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx == 0 or syy == 0:
        return None
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    slope = sxy / sxx
    intercept = my - slope * mx
    r2 = (sxy * sxy) / (sxx * syy)
    return slope, intercept, r2


def fit_column(packets: list[bytes], values: list[float | None], column: str, min_r2: float = 0.9) -> list[Fit]:
    """1 列ぶん、全オフセット × 全型を試して R² 順に返す。"""
    maxlen = max((len(p) for p in packets), default=0)
    fits: list[Fit] = []
    for ftype, (_, width) in FIELD_TYPES.items():
        for off in range(0, maxlen - width + 1):
            xs, ys = [], []
            for pkt, y in zip(packets, values):
                if y is None:
                    continue
                x = extract_field(pkt, off, ftype)
                if x is None:
                    continue
                xs.append(x)
                ys.append(y)
            res = linear_fit(xs, ys)
            if res and res[2] >= min_r2:
                fits.append(Fit(column, off, ftype, res[0], res[1], res[2], len(xs)))
    fits.sort(key=lambda f: (-f.r2, FIELD_TYPES[f.ftype][1], f.offset))
    return fits


# ---------------------------------------------------------------- 入力

def load_packets(path: Path | str, handle: int | None = None, min_len: int = 1) -> list[bytes]:
    """`ble-re snoop --jsonl` / `ble-re watch -o` の JSONL から hex を取り出す。"""
    out: list[bytes] = []
    with open(path, encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if handle is not None and rec.get("handle") != handle:
                continue
            kind = rec.get("kind") or rec.get("op", "")
            if kind and kind.lower() not in ("notify", "indicate", "notification", "indication", "read rsp", "read"):
                continue
            data = bytes.fromhex(rec["hex"])
            if len(data) >= min_len:
                out.append(data)
    return out


def load_csv_columns(path: Path | str, columns: list[str]) -> tuple[list[str], list[list[float | None]]]:
    """メタ行つき CSV (WHS-3 アプリの書式など) から数値列を読む。

    見出し行は「指定した列名を全部含む最初の行」として探す。
    Returns: (見つかった列名, 列ごとの値リスト)
    """
    with open(path, encoding="utf-8-sig") as fp:
        lines = fp.read().splitlines()
    header_idx = None
    for i, line in enumerate(lines[:200]):
        cells = [c.strip() for c in next(csv.reader([line]))]
        if all(c in cells for c in columns):
            header_idx = i
            break
    if header_idx is None:
        raise ValueError(f"列 {columns} を全部含む見出し行が見つかりません: {path}")
    reader = csv.DictReader(io.StringIO("\n".join(lines[header_idx:])))
    reader.fieldnames = [c.strip() for c in reader.fieldnames or []]
    cols: list[list[float | None]] = [[] for _ in columns]
    for row in reader:
        for j, name in enumerate(columns):
            raw = (row.get(name) or "").strip()
            try:
                cols[j].append(float(raw))
            except ValueError:
                cols[j].append(None)
    return columns, cols


# ---------------------------------------------------------------- 整列

def best_offset(packets: list[bytes], values: list[float | None], column: str, search: int) -> tuple[int, float]:
    """パケット列と CSV 行の先頭ズレを、最初の列の最大 R² で決める。"""
    best_k, best_r2 = 0, -1.0
    for k in range(-search, search + 1):
        pk, vl = _shift(packets, values, k)
        fits = fit_column(pk, vl, column, min_r2=0.0)
        r2 = fits[0].r2 if fits else 0.0
        if r2 > best_r2:
            best_k, best_r2 = k, r2
    return best_k, best_r2


def _shift(packets: list[bytes], values: list[float | None], k: int) -> tuple[list[bytes], list[float | None]]:
    """k > 0: パケット側を k 個捨てる (CSV より先に始まっている)。k < 0: CSV 側を捨てる。"""
    if k > 0:
        packets = packets[k:]
    elif k < 0:
        values = values[-k:]
    n = min(len(packets), len(values))
    return packets[:n], values[:n]


def run_fit(
    packets: list[bytes],
    columns: list[str],
    cols: list[list[float | None]],
    offset_search: int = 5,
    min_r2: float = 0.9,
    top: int = 3,
) -> str:
    lines: list[str] = []
    lengths = sorted({len(p) for p in packets})
    lines.append(f"packets: {len(packets)}  lengths: {lengths}   csv rows: {len(cols[0])}")
    if len(lengths) > 1:
        lines.append("  ※ 長さが揃っていない。1 notify に複数拍が入る、または種類の違うパケットが混ざっている可能性")

    k, r2 = best_offset(packets, cols[0], columns[0], offset_search)
    lines.append(f"alignment: shift={k:+d} (by '{columns[0]}', R²={r2:.4f})")
    lines.append("")

    used: dict[int, str] = {}
    for name, values in zip(columns, cols):
        pk, vl = _shift(packets, values, k)
        fits = fit_column(pk, vl, name, min_r2=min_r2)
        lines.append(f"[{name}]")
        if not fits:
            lines.append(f"  R² ≥ {min_r2} の候補なし (非線形・ビットフィールド・別パケットの可能性)")
        for f in fits[:top]:
            lines.append("  " + f.describe())
        if fits:
            b = fits[0]
            for o in range(b.offset, b.offset + FIELD_TYPES[b.ftype][1]):
                used.setdefault(o, name)
        lines.append("")

    if used and packets:
        n = max(len(p) for p in packets)
        lines.append("layout (best guess):")
        row = []
        for o in range(n):
            row.append(f"{o:>2}:{(used.get(o) or '?')[:6]:<6}")
        for i in range(0, len(row), 6):
            lines.append("  " + " ".join(row[i : i + 6]))
    return "\n".join(lines)
