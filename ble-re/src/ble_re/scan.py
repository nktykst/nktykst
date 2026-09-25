"""アドバタイズのスキャンと AD 構造のデコード。"""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import dataclass, field
from typing import Any

from bleak import BleakScanner
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData

from .hexutil import hexstr
from .uuids import company_name, short_uuid, uuid_name


@dataclass
class Seen:
    device: BLEDevice
    adv: AdvertisementData
    count: int = 1
    rssi_history: list[int] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        adv = self.adv
        return {
            "address": self.device.address,
            "name": adv.local_name or self.device.name,
            "rssi": adv.rssi,
            "tx_power": adv.tx_power,
            "count": self.count,
            "service_uuids": [
                {"uuid": u, "short": short_uuid(u), "name": uuid_name(u)} for u in adv.service_uuids
            ],
            "manufacturer_data": {
                f"0x{cid:04x}": {"company": company_name(cid), "hex": bytes(d).hex()}
                for cid, d in adv.manufacturer_data.items()
            },
            "service_data": {u: bytes(d).hex() for u, d in adv.service_data.items()},
        }


def format_seen(s: Seen) -> str:
    adv = s.adv
    name = adv.local_name or s.device.name or "(no name)"
    lines = [f"{s.device.address}  rssi={adv.rssi:>4}  n={s.count:<3} {name}"]
    if adv.tx_power is not None:
        lines.append(f"    tx_power: {adv.tx_power} dBm")
    for u in adv.service_uuids:
        lines.append(f"    service : {short_uuid(u):<40} {uuid_name(u)}")
    for cid, data in adv.manufacturer_data.items():
        lines.append(f"    mfr     : 0x{cid:04x} {company_name(cid)}  {hexstr(data)}")
    for u, data in adv.service_data.items():
        lines.append(f"    svcdata : {short_uuid(u)} {uuid_name(u)}  {hexstr(data)}")
    return "\n".join(lines)


async def scan(
    timeout: float = 8.0,
    name_filter: str | None = None,
    address_filter: str | None = None,
    passive: bool = False,
    live: bool = False,
) -> dict[str, Seen]:
    seen: dict[str, Seen] = {}

    def matches(dev: BLEDevice, adv: AdvertisementData) -> bool:
        if address_filter and dev.address.lower() != address_filter.lower():
            return False
        if name_filter:
            n = (adv.local_name or dev.name or "").lower()
            if name_filter.lower() not in n:
                return False
        return True

    def on_adv(dev: BLEDevice, adv: AdvertisementData) -> None:
        if not matches(dev, adv):
            return
        s = seen.get(dev.address)
        if s is None:
            seen[dev.address] = s = Seen(dev, adv)
            if live:
                print(format_seen(s), file=sys.stderr)
        else:
            s.count += 1
            # 複数のアドバタイズ (ADV + SCAN_RSP) の情報を取りこぼさないよう、新しい方で上書き
            s.adv = adv
        s.rssi_history.append(adv.rssi)

    mode = "passive" if passive else "active"
    async with BleakScanner(detection_callback=on_adv, scanning_mode=mode):
        await asyncio.sleep(timeout)
    return seen


def print_scan_result(seen: dict[str, Seen], as_json: bool = False) -> None:
    ordered = sorted(seen.values(), key=lambda s: s.adv.rssi, reverse=True)
    if as_json:
        print(json.dumps([s.to_dict() for s in ordered], ensure_ascii=False, indent=2))
        return
    if not ordered:
        print("デバイスが見つかりませんでした。")
        return
    for s in ordered:
        print(format_seen(s))
        print()
