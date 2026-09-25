"""ble-re コマンドラインエントリ。"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from . import __version__
from .hexutil import describe_value, hexdump, parse_bytes


def _cmd_scan(a: argparse.Namespace) -> int:
    from .scan import print_scan_result, scan

    seen = asyncio.run(scan(timeout=a.timeout, name_filter=a.name, address_filter=a.address, passive=a.passive, live=a.live))
    print_scan_result(seen, as_json=a.json)
    return 0


def _cmd_dump(a: argparse.Namespace) -> int:
    from .gatt import dump_gatt, format_gatt, gatt_to_json

    info = asyncio.run(dump_gatt(a.address, read_values=a.read, read_descriptors=a.descriptors, timeout=a.timeout, pair=a.pair))
    print(gatt_to_json(info) if a.json else format_gatt(info))
    return 0


def _cmd_read(a: argparse.Namespace) -> int:
    from .gatt import read_char

    val = asyncio.run(read_char(a.address, a.char, timeout=a.timeout))
    print(hexdump(val))
    print(describe_value(val))
    return 0


def _cmd_watch(a: argparse.Namespace) -> int:
    from .watch import watch

    writes = [(spec, parse_bytes(data)) for spec, data in (w.split("=", 1) for w in a.write)]
    try:
        asyncio.run(
            watch(
                a.address,
                chars=a.char or None,
                duration=a.duration,
                out_path=Path(a.out) if a.out else None,
                writes=writes,
                write_delay=a.write_delay,
                timeout=a.timeout,
                pair=a.pair,
            )
        )
    except KeyboardInterrupt:
        pass
    return 0


def _cmd_write(a: argparse.Namespace) -> int:
    from .watch import write_once

    response = None if a.response == "auto" else (a.response == "yes")
    asyncio.run(write_once(a.address, a.char, parse_bytes(a.data), response=response, watch_after=a.watch, out_path=Path(a.out) if a.out else None, timeout=a.timeout))
    return 0


def _cmd_snoop(a: argparse.Namespace) -> int:
    from .snoop import DATA_OPCODES, events_to_jsonl, format_event, parse_file, summarize

    parser = parse_file(a.file)
    events = parser.events
    if a.handle:
        wanted = {int(h, 0) for h in a.handle}
        events = [e for e in events if e.handle in wanted]
    if a.data_only:
        events = [e for e in events if e.opcode in DATA_OPCODES]
    if a.conn is not None:
        events = [e for e in events if e.conn_handle == a.conn]
    if a.summary:
        if parser.addresses:
            for h, addr in parser.addresses.items():
                print(f"conn {h}: {addr}")
            print()
        print(summarize(events))
        return 0
    if a.jsonl:
        print(events_to_jsonl(events))
        return 0
    t0 = events[0].ts if (events and a.relative) else None
    for ev in events:
        print(format_event(ev, t0))
    print(f"\n{len(events)} ATT events ({len(parser.events)} total before filtering)", file=sys.stderr)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ble-re", description="BLE リバースエンジニアリング用ツールキット")
    p.add_argument("--version", action="version", version=f"ble-re {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scan", help="アドバタイズをスキャンして一覧表示")
    s.add_argument("-t", "--timeout", type=float, default=8.0, help="スキャン秒数 (default 8)")
    s.add_argument("-n", "--name", help="名前に含まれる文字列でフィルタ")
    s.add_argument("-a", "--address", help="アドレスでフィルタ")
    s.add_argument("--passive", action="store_true", help="パッシブスキャン (SCAN_REQ を送らない)")
    s.add_argument("--live", action="store_true", help="見つけ次第 stderr に表示")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=_cmd_scan)

    d = sub.add_parser("dump", help="接続して GATT を全列挙")
    d.add_argument("address", help="MAC (Linux/Windows) または CoreBluetooth UUID (macOS)")
    d.add_argument("-r", "--read", action="store_true", help="read 可能な値も読む")
    d.add_argument("-d", "--descriptors", action="store_true", help="ディスクリプタの値も読む")
    d.add_argument("--pair", action="store_true", help="接続時にペアリングを要求")
    d.add_argument("--timeout", type=float, default=20.0)
    d.add_argument("--json", action="store_true")
    d.set_defaults(func=_cmd_dump)

    r = sub.add_parser("read", help="1 つのキャラクタリスティックを読む")
    r.add_argument("address")
    r.add_argument("char", help="UUID (2a19 / 完全形) または handle (0x0012)")
    r.add_argument("--timeout", type=float, default=20.0)
    r.set_defaults(func=_cmd_read)

    w = sub.add_parser("watch", help="notify/indicate を購読してログ (省略時は全部)")
    w.add_argument("address")
    w.add_argument("-c", "--char", action="append", help="購読するキャラクタリスティック (複数可)")
    w.add_argument("-w", "--write", action="append", default=[], metavar="CHAR=DATA", help="購読後に書き込む。例: fff2=0x0102, 0x0012=str:hello (複数可, 順に送る)")
    w.add_argument("--write-delay", type=float, default=1.0, help="書き込み間隔秒")
    w.add_argument("-D", "--duration", type=float, help="秒数 (省略時は切断か Ctrl-C まで)")
    w.add_argument("-o", "--out", help="JSONL の出力先")
    w.add_argument("--pair", action="store_true")
    w.add_argument("--timeout", type=float, default=20.0)
    w.set_defaults(func=_cmd_watch)

    wr = sub.add_parser("write", help="1 回書き込む")
    wr.add_argument("address")
    wr.add_argument("char")
    wr.add_argument("data", help="0102ff / str:hello / u16le:513 など")
    wr.add_argument("--response", choices=["auto", "yes", "no"], default="auto", help="Write Request か Write Command か")
    wr.add_argument("--watch", type=float, default=0.0, metavar="SEC", help="書き込み後 SEC 秒だけ全 notify を監視")
    wr.add_argument("-o", "--out")
    wr.add_argument("--timeout", type=float, default=20.0)
    wr.set_defaults(func=_cmd_write)

    sn = sub.add_parser("snoop", help="btsnoop_hci.log を ATT レベルで解析")
    sn.add_argument("file")
    sn.add_argument("-H", "--handle", action="append", help="この handle のみ (0x0012, 複数可)")
    sn.add_argument("--conn", type=int, help="この接続 handle のみ")
    sn.add_argument("--data-only", action="store_true", help="値が動く PDU (write/notify/read rsp) のみ")
    sn.add_argument("--relative", action="store_true", help="最初のイベントからの相対秒で表示")
    sn.add_argument("--summary", action="store_true", help="handle × opcode の件数表")
    sn.add_argument("--jsonl", action="store_true")
    sn.set_defaults(func=_cmd_snoop)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        return 130
    except (ValueError, KeyError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
