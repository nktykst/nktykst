#!/usr/bin/env bash
# PacketLogger (.pklg) / btsnoop (.log) から ATT の Write / Notify / Indicate を CSV で抜き出す。
#   ./extract_att.sh capture.pklg > att.csv
# 列: time, direction, opcode, handle, value
# opcode: 0x12=Write Request, 0x52=Write Command, 0x1b=Notification, 0x1d=Indication,
#         0x0b=Read Response, 0x08=Read By Type Request (サービス探索)
set -euo pipefail
[ $# -ge 1 ] || { echo "usage: $0 <capture.pklg|btsnoop_hci.log>" >&2; exit 1; }
command -v tshark >/dev/null || { echo "tshark が必要です (brew install --cask wireshark)" >&2; exit 1; }

echo "time,direction,opcode,handle,value"
tshark -r "$1" \
  -Y 'btatt.opcode == 0x12 || btatt.opcode == 0x52 || btatt.opcode == 0x1b || btatt.opcode == 0x1d || btatt.opcode == 0x0b' \
  -T fields -E separator=, -E quote=n \
  -e frame.time_relative -e hci_h4.direction -e btatt.opcode -e btatt.handle -e btatt.value 2>/dev/null \
| awk -F, 'BEGIN{OFS=","}{
    dir = ($2=="0x00"||$2=="0") ? "app->dev" : (($2=="0x01"||$2=="1") ? "dev->app" : $2);
    print $1, dir, $3, $4, $5
  }'
