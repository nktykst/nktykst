"""接続して GATT のサービス / キャラクタリスティック / ディスクリプタを列挙する。"""

from __future__ import annotations

import json
from typing import Any

from bleak import BleakClient
from bleak.backends.characteristic import BleakGATTCharacteristic

from .hexutil import describe_value
from .uuids import short_uuid, uuid_name


async def connect(address: str, timeout: float = 20.0, pair: bool = False) -> BleakClient:
    client = BleakClient(address, timeout=timeout, pair=pair)
    await client.connect()
    return client


async def dump_gatt(
    address: str,
    read_values: bool = False,
    read_descriptors: bool = False,
    timeout: float = 20.0,
    pair: bool = False,
) -> dict[str, Any]:
    client = await connect(address, timeout=timeout, pair=pair)
    try:
        result: dict[str, Any] = {
            "address": client.address,
            "mtu": client.mtu_size,
            "services": [],
        }
        for svc in client.services:
            svc_entry: dict[str, Any] = {
                "uuid": svc.uuid,
                "short": short_uuid(svc.uuid),
                "name": uuid_name(svc.uuid),
                "handle": svc.handle,
                "characteristics": [],
            }
            for ch in svc.characteristics:
                ch_entry: dict[str, Any] = {
                    "uuid": ch.uuid,
                    "short": short_uuid(ch.uuid),
                    "name": uuid_name(ch.uuid),
                    "handle": ch.handle,
                    "properties": list(ch.properties),
                    "max_write_without_response_size": ch.max_write_without_response_size,
                    "descriptors": [],
                }
                if read_values and "read" in ch.properties:
                    try:
                        val = await client.read_gatt_char(ch)
                        ch_entry["value_hex"] = bytes(val).hex()
                        ch_entry["value_desc"] = describe_value(val)
                    except Exception as e:  # 認証必須などで読めないことは普通にある
                        ch_entry["value_error"] = f"{type(e).__name__}: {e}"
                for d in ch.descriptors:
                    d_entry: dict[str, Any] = {
                        "uuid": d.uuid,
                        "short": short_uuid(d.uuid),
                        "name": uuid_name(d.uuid),
                        "handle": d.handle,
                    }
                    if read_descriptors:
                        try:
                            dv = await client.read_gatt_descriptor(d.handle)
                            d_entry["value_hex"] = bytes(dv).hex()
                        except Exception as e:
                            d_entry["value_error"] = f"{type(e).__name__}: {e}"
                    ch_entry["descriptors"].append(d_entry)
                svc_entry["characteristics"].append(ch_entry)
            result["services"].append(svc_entry)
        return result
    finally:
        await client.disconnect()


def format_gatt(info: dict[str, Any]) -> str:
    lines = [f"device : {info['address']}   MTU={info['mtu']}"]
    for svc in info["services"]:
        lines.append(f"\n[svc  h=0x{svc['handle']:04x}] {svc['short']}  {svc['name']}")
        for ch in svc["characteristics"]:
            props = ",".join(ch["properties"])
            lines.append(f"  [chr h=0x{ch['handle']:04x}] {ch['short']}  {ch['name']}  <{props}>")
            if "value_hex" in ch:
                lines.append(f"        value: {ch['value_desc']}")
            if "value_error" in ch:
                lines.append(f"        value: (read failed) {ch['value_error']}")
            for d in ch["descriptors"]:
                v = f"  = {d['value_hex']}" if "value_hex" in d else ""
                lines.append(f"      [dsc h=0x{d['handle']:04x}] {d['short']}  {d['name']}{v}")
    return "\n".join(lines)


def find_char(client: BleakClient, spec: str) -> BleakGATTCharacteristic:
    """UUID (完全 / 16bit 短縮) または handle (0x0012 / 18) からキャラクタリスティックを引く。"""
    s = spec.strip().lower()
    if s.startswith("0x") or s.isdigit():
        handle = int(s, 0)
        ch = client.services.get_characteristic(handle)
        if ch is None:
            raise KeyError(f"handle {spec} のキャラクタリスティックが見つかりません")
        return ch
    ch = client.services.get_characteristic(s)
    if ch is None:
        raise KeyError(f"UUID {spec} のキャラクタリスティックが見つかりません")
    return ch


async def read_char(address: str, spec: str, timeout: float = 20.0) -> bytes:
    client = await connect(address, timeout=timeout)
    try:
        ch = find_char(client, spec)
        return bytes(await client.read_gatt_char(ch))
    finally:
        await client.disconnect()


def gatt_to_json(info: dict[str, Any]) -> str:
    return json.dumps(info, ensure_ascii=False, indent=2)


