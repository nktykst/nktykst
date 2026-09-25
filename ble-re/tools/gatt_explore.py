#!/usr/bin/env python3
"""BLE GATT explorer / replay tool built on bleak.

    pip install bleak

    python gatt_explore.py scan [--seconds 5]
    python gatt_explore.py dump   <name-or-address>
    python gatt_explore.py listen <name-or-address> [--seconds 60]
    python gatt_explore.py read   <name-or-address> <char-uuid>
    python gatt_explore.py write  <name-or-address> <char-uuid> <hex> [--no-response] [--listen 3]

<name-or-address> は Advertised Name の部分一致、または macOS の identifier UUID /
Linux・Windows の MAC アドレス。<hex> は "7e000401000000ef" または "7e 00 04 ..." 形式。
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime

from bleak import BleakClient, BleakScanner
from bleak.backends.characteristic import BleakGATTCharacteristic


def hexdump(data: bytes) -> str:
    return " ".join(f"{b:02x}" for b in data)


def parse_hex(text: str) -> bytes:
    return bytes.fromhex(text.replace(" ", "").replace(":", ""))


def ts() -> str:
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


async def find_device(target: str, seconds: float = 8.0):
    """Advertised Name の部分一致 / アドレス完全一致で機器を探す。"""
    target_l = target.lower()
    found = await BleakScanner.discover(timeout=seconds, return_adv=True)
    for dev, adv in found.values():
        name = (adv.local_name or dev.name or "")
        if dev.address.lower() == target_l or (name and target_l in name.lower()):
            return dev
    sys.exit(f"device not found: {target!r} (scan で名前を確認してください)")


async def cmd_scan(args):
    print(f"scanning {args.seconds}s ...")
    found = await BleakScanner.discover(timeout=args.seconds, return_adv=True)
    rows = sorted(found.values(), key=lambda t: t[1].rssi or -999, reverse=True)
    for dev, adv in rows:
        name = adv.local_name or dev.name or "-"
        print(f"{adv.rssi:5} dBm  {dev.address}  {name}")
        for uuid in adv.service_uuids:
            print(f"           service : {uuid}")
        for cid, data in adv.manufacturer_data.items():
            print(f"           mfr 0x{cid:04x}: {hexdump(data)}")
        for uuid, data in adv.service_data.items():
            print(f"           svcdata {uuid}: {hexdump(data)}")


async def cmd_dump(args):
    dev = await find_device(args.target)
    async with BleakClient(dev) as client:
        print(f"connected: {dev.address}  mtu={client.mtu_size}")
        for svc in client.services:
            print(f"\n[service] {svc.uuid}  {svc.description}")
            for ch in svc.characteristics:
                props = ",".join(ch.properties)
                print(f"  [char] {ch.uuid}  handle=0x{ch.handle:04x}  ({props})  {ch.description}")
                if "read" in ch.properties:
                    try:
                        value = await client.read_gatt_char(ch)
                        print(f"         value = {hexdump(value)}  {value!r}")
                    except Exception as e:  # noqa: BLE001
                        print(f"         read failed: {e}")
                for d in ch.descriptors:
                    print(f"      [desc] {d.uuid}  handle=0x{d.handle:04x}  {d.description}")


def make_notify_handler(label: str):
    def handler(char: BleakGATTCharacteristic, data: bytearray):
        print(f"{ts()}  NOTIFY {label} {char.uuid}: {hexdump(data)}")
    return handler


async def subscribe_all(client: BleakClient) -> list[BleakGATTCharacteristic]:
    subscribed = []
    for svc in client.services:
        for ch in svc.characteristics:
            if "notify" in ch.properties or "indicate" in ch.properties:
                try:
                    await client.start_notify(ch, make_notify_handler(""))
                    subscribed.append(ch)
                    print(f"subscribed {ch.uuid} (handle 0x{ch.handle:04x})")
                except Exception as e:  # noqa: BLE001
                    print(f"subscribe failed {ch.uuid}: {e}")
    return subscribed


async def cmd_listen(args):
    dev = await find_device(args.target)
    async with BleakClient(dev) as client:
        print(f"connected: {dev.address}")
        subs = await subscribe_all(client)
        if not subs:
            print("no notify/indicate characteristics")
            return
        print(f"listening {args.seconds}s ... (機器を物理操作して値の変化を見る)")
        await asyncio.sleep(args.seconds)


async def cmd_read(args):
    dev = await find_device(args.target)
    async with BleakClient(dev) as client:
        value = await client.read_gatt_char(args.char)
        print(f"{hexdump(value)}  {value!r}")


async def cmd_write(args):
    dev = await find_device(args.target)
    payload = parse_hex(args.hex)
    async with BleakClient(dev) as client:
        print(f"connected: {dev.address}")
        if args.listen:
            await subscribe_all(client)
        print(f"{ts()}  WRITE {args.char}: {hexdump(payload)}  response={not args.no_response}")
        await client.write_gatt_char(args.char, payload, response=not args.no_response)
        if args.listen:
            await asyncio.sleep(args.listen)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scan"); s.add_argument("--seconds", type=float, default=5.0); s.set_defaults(fn=cmd_scan)
    s = sub.add_parser("dump"); s.add_argument("target"); s.set_defaults(fn=cmd_dump)
    s = sub.add_parser("listen"); s.add_argument("target"); s.add_argument("--seconds", type=float, default=60.0); s.set_defaults(fn=cmd_listen)
    s = sub.add_parser("read"); s.add_argument("target"); s.add_argument("char"); s.set_defaults(fn=cmd_read)
    s = sub.add_parser("write"); s.add_argument("target"); s.add_argument("char"); s.add_argument("hex")
    s.add_argument("--no-response", action="store_true", help="WriteWithoutResponse を使う")
    s.add_argument("--listen", type=float, default=3.0, help="書き込み後に Notify を待つ秒数 (0 で待たない)")
    s.set_defaults(fn=cmd_write)

    args = p.parse_args()
    try:
        asyncio.run(args.fn(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
