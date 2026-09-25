"""APK (または jadx で展開したディレクトリ) から BLE の UUID らしき文字列を拾う。

公式アプリの UUID を先に知っておくと、GATT ダンプで「どれがコマンド用か」の当たりが付く。
"""

from __future__ import annotations

import re
import zipfile
from collections import Counter
from pathlib import Path

from .uuids import is_vendor, short_uuid, uuid_name

_UUID_RE = re.compile(rb"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
# UTF-16LE で埋め込まれた文字列 (resources.arsc やネイティブ)
_UUID16_RE = re.compile(rb"(?:[0-9a-fA-F]\x00){8}-\x00(?:[0-9a-fA-F]\x00){4}-\x00(?:[0-9a-fA-F]\x00){4}-\x00(?:[0-9a-fA-F]\x00){4}-\x00(?:[0-9a-fA-F]\x00){12}")
# BLE の API 呼び出し名。含まれていれば「そのファイル (dex) に BLE コードがある」
_API_MARKERS = [b"writeCharacteristic", b"setCharacteristicNotification", b"CBPeripheral", b"BluetoothGatt", b"FlutterBluePlus", b"react-native-ble", b"cordova-plugin-ble"]


def _iter_blobs(path: Path):
    if path.is_dir():
        for f in path.rglob("*"):
            if f.is_file():
                yield str(f.relative_to(path)), f.read_bytes()
    else:
        with zipfile.ZipFile(path) as z:
            for info in z.infolist():
                if not info.is_dir():
                    yield info.filename, z.read(info)


def scan(path: Path | str) -> tuple[Counter[str], dict[str, set[str]], set[str]]:
    """Returns: (uuid -> 出現回数, uuid -> 見つかったファイル, BLE API を含むファイル)"""
    counts: Counter[str] = Counter()
    where: dict[str, set[str]] = {}
    api_files: set[str] = set()
    for name, blob in _iter_blobs(Path(path)):
        found = [m.group(0).decode("ascii") for m in _UUID_RE.finditer(blob)]
        found += [m.group(0).decode("utf-16-le") for m in _UUID16_RE.finditer(blob)]
        for u in found:
            u = u.lower()
            counts[u] += 1
            where.setdefault(u, set()).add(name)
        if any(marker in blob for marker in _API_MARKERS):
            api_files.add(name)
    return counts, where, api_files


def format_report(counts: Counter[str], where: dict[str, set[str]], api_files: set[str]) -> str:
    lines: list[str] = []
    if api_files:
        lines.append("BLE API を含むファイル:")
        lines += [f"  {f}" for f in sorted(api_files)]
        lines.append("")
    lines.append(f"UUID らしき文字列: {len(counts)} 種")
    vendor, sig = [], []
    for u, n in counts.most_common():
        nm = uuid_name(u)
        (vendor if is_vendor(u) else sig).append((u, n, nm))
    if vendor:
        lines.append("\n-- ベンダー UUID (ここが本命) --")
        for u, n, nm in vendor:
            lines.append(f"  {u}  x{n:<3} {', '.join(sorted(where[u]))[:80]}")
        lines.append("  (よく知られたベンダー UUID は ble_re/uuids.py の EXTRA_UUIDS で名前が付く)")
    if sig:
        lines.append("\n-- 標準 UUID --")
        for u, n, nm in sig:
            lines.append(f"  {short_uuid(u):<40} x{n:<3} {nm}")
    return "\n".join(lines)
