"""Notify / Indicate を購読してログに落とす。必要なら書き込みも同時に行う。"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import IO

from bleak import BleakClient
from bleak.backends.characteristic import BleakGATTCharacteristic

from .gatt import connect, find_char
from .hexutil import describe_value, hexstr
from .uuids import short_uuid, uuid_name


class EventLogger:
    """人間向けの stdout と機械向けの JSONL の両方へ書く。"""

    def __init__(self, out_path: Path | None, quiet: bool = False) -> None:
        self.t0 = time.monotonic()
        self.quiet = quiet
        self.fp: IO[str] | None = out_path.open("a", encoding="utf-8") if out_path else None

    def log(self, kind: str, ch: BleakGATTCharacteristic | None, data: bytes, note: str = "") -> None:
        rel = time.monotonic() - self.t0
        rec = {
            "ts": datetime.now().isoformat(timespec="milliseconds"),
            "t": round(rel, 3),
            "kind": kind,  # notify / indicate / write / read
            "handle": ch.handle if ch else None,
            "uuid": ch.uuid if ch else None,
            "hex": bytes(data).hex(),
            "note": note,
        }
        if self.fp:
            self.fp.write(json.dumps(rec, ensure_ascii=False) + "\n")
            self.fp.flush()
        if not self.quiet:
            tag = f"{short_uuid(ch.uuid)} h=0x{ch.handle:04x}" if ch else "-"
            print(f"[{rel:9.3f}] {kind:<7} {tag:<20} {hexstr(data):<48} {describe_value(data)}")

    def close(self) -> None:
        if self.fp:
            self.fp.close()


async def watch(
    address: str,
    chars: list[str] | None = None,
    duration: float | None = None,
    out_path: Path | None = None,
    writes: list[tuple[str, bytes]] | None = None,
    write_delay: float = 1.0,
    timeout: float = 20.0,
    pair: bool = False,
) -> None:
    """chars を省略すると notify/indicate 可能な全キャラクタリスティックを購読する。"""
    logger = EventLogger(out_path)
    disconnected = asyncio.Event()
    loop = asyncio.get_running_loop()

    def on_disconnect(_: BleakClient) -> None:
        loop.call_soon_threadsafe(disconnected.set)

    client = BleakClient(address, disconnected_callback=on_disconnect, timeout=timeout, pair=pair)
    await client.connect()

    try:
        targets: list[BleakGATTCharacteristic] = []
        if chars:
            targets = [find_char(client, c) for c in chars]
        else:
            for svc in client.services:
                for ch in svc.characteristics:
                    if "notify" in ch.properties or "indicate" in ch.properties:
                        targets.append(ch)
        if not targets:
            print("notify/indicate 可能なキャラクタリスティックがありません。", file=sys.stderr)
            return

        def make_cb(ch: BleakGATTCharacteristic):
            kind = "notify" if "notify" in ch.properties else "indicate"

            def cb(_: BleakGATTCharacteristic, data: bytearray) -> None:
                logger.log(kind, ch, bytes(data))

            return cb

        for ch in targets:
            try:
                await client.start_notify(ch, make_cb(ch))
                print(f"subscribed: {short_uuid(ch.uuid)} h=0x{ch.handle:04x} {uuid_name(ch.uuid)}", file=sys.stderr)
            except Exception as e:
                print(f"subscribe failed: {short_uuid(ch.uuid)} h=0x{ch.handle:04x}: {e}", file=sys.stderr)

        if writes:
            await asyncio.sleep(write_delay)
            for spec, payload in writes:
                ch = find_char(client, spec)
                use_response = "write" in ch.properties
                await client.write_gatt_char(ch, payload, response=use_response)
                logger.log("write", ch, payload, note="with-response" if use_response else "no-response")
                await asyncio.sleep(write_delay)

        print("watching... (Ctrl-C で終了)", file=sys.stderr)
        try:
            if duration is None:
                await disconnected.wait()
            else:
                await asyncio.wait_for(disconnected.wait(), timeout=duration)
        except asyncio.TimeoutError:
            pass
        except asyncio.CancelledError:
            pass
        if disconnected.is_set():
            print("device disconnected", file=sys.stderr)
    finally:
        logger.close()
        if client.is_connected:
            await client.disconnect()


async def write_once(
    address: str,
    spec: str,
    payload: bytes,
    response: bool | None = None,
    watch_after: float = 0.0,
    out_path: Path | None = None,
    timeout: float = 20.0,
) -> None:
    """1 回書き込み、必要なら直後の notify を watch_after 秒だけ眺める。"""
    if watch_after > 0:
        await watch(address, chars=None, duration=watch_after, out_path=out_path, writes=[(spec, payload)], timeout=timeout)
        return
    client = await connect(address, timeout=timeout)
    try:
        ch = find_char(client, spec)
        if response is None:
            response = "write" in ch.properties
        await client.write_gatt_char(ch, payload, response=response)
        print(f"wrote {len(payload)} bytes to {short_uuid(ch.uuid)} h=0x{ch.handle:04x} (response={response})")
    finally:
        await client.disconnect()
