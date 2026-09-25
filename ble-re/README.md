# ble-re — BLE リバースエンジニアリング用ツールキット

自分の手元にある BLE ガジェット (スマートバンド、LED ライト、センサー、玩具など) の
GATT プロトコルを解析して、公式アプリなしで操作できるようにするための道具一式です。

```
ble-re scan                      # 周囲のアドバタイズを一覧
ble-re dump  <ADDR> -r           # 接続して GATT を全列挙 + 読める値を読む
ble-re watch <ADDR> -o log.jsonl # notify/indicate を全部購読してログ
ble-re write <ADDR> fff2 0x0101 --watch 3   # 書いて、直後の反応を 3 秒見る
ble-re snoop btsnoop_hci.log --data-only    # Android / iOS (.pklg) の HCI ログを ATT レベルで時系列化
ble-re fit att.jsonl --csv app.csv -c rri   # 通知バイト列と公式アプリの CSV からフィールド配置を推定
ble-re apk-uuids app.apk                    # 公式アプリの APK から UUID を拾う
```

> 具体例: myBeat WHS-3 (心拍センサ) を自前アプリに繋ぐための手順は [docs/whs3.md](docs/whs3.md)。

> **前提**: 解析対象は自分が所有し、解析する権利のあるデバイスに限ってください。

---

## セットアップ

```bash
cd ble-re
uv sync
uv run ble-re --help
```

| OS | 注意点 |
|---|---|
| Linux (BlueZ) | `bluetoothctl` が動く環境なら OK。権限エラーが出たら `sudo` か、`sudo setcap cap_net_raw,cap_net_admin+eip $(readlink -f .venv/bin/python)` |
| macOS (CoreBluetooth) | アドレスは MAC ではなく CoreBluetooth の UUID。`scan` の出力に出るものをそのまま使う。初回はターミナルに Bluetooth 権限を許可 |
| Windows (WinRT) | 標準の Bluetooth スタックで動作。デバイスを OS 側で「ペアリング済み」にしていると挙動が変わることがあるので、必要なら一度削除 |

---

## 解析の進め方

BLE の RE はほぼ次の流れになります。順番にコマンドを当てていけば、
「どの characteristic に何を書けば何が起きるか」が表として残ります。

### 0. 事前調査 (5 分)

- 製品名 + `BLE` / `protocol` / `reverse` / `python` で検索。すでに誰かが解析していることは多い
- 公式アプリの APK を用意 (後で jadx にかける)
- 米国向け製品なら FCC ID で内部写真を見ると SoC (Nordic nRF52 / TI CC26xx / Telink / Realtek など) が分かる。SoC が分かると使われがちな UUID や DFU 方式の当たりがつく

### 1. スキャン — 対象を見つける

```bash
uv run ble-re scan -t 10          # 10 秒スキャン
uv run ble-re scan -n "mi band"   # 名前でフィルタ
uv run ble-re scan --json > adv.json
```

見るところ:

- **アドレス**: 以後のコマンドで使う。iOS 由来のデバイスや一部製品は random address が定期的に変わる
- **service UUID**: 広告されているサービス。`0xfe95` (Xiaomi) や `6e400001-...` (Nordic UART) のように、それだけで系統が分かるものがある
- **manufacturer data**: Company ID とペイロード。センサー系はここに測定値を載せて接続なしで配信していることがある (→ 接続せずに `scan --json` を回すだけで済む場合も)

### 2. GATT ダンプ — 何ができるかを知る

```bash
uv run ble-re dump AA:BB:CC:DD:EE:FF -r -d      # 値とディスクリプタも読む
uv run ble-re dump AA:BB:CC:DD:EE:FF --json > gatt.json
```

出力例:

```
[svc  h=0x0010] 0000fff0-...  Vendor service FFF0
  [chr h=0x0011] 0xfff1  Vendor char FFF1  <notify>
      [dsc h=0x0013] 0x2902  Client Characteristic Configuration
  [chr h=0x0014] 0xfff2  Vendor char FFF2  <write-without-response,write>
```

見るところ:

- **標準サービス** (`0x180a` Device Information など) は読むだけで機種・FW バージョンが分かる
- **ベンダー UUID で `write` と `notify` がペアになっているもの**が「コマンド用 / 応答用」の窓口であることが多い。上の例なら FFF2 に書いて FFF1 で受ける
- 読めない (`Insufficient Authentication` / `Insufficient Encryption`) 場合はペアリングが必要 → `--pair`

### 3. 公式アプリの通信を横取りする (いちばん効く)

自分で書き込みを試す前に、公式アプリが実際に送っているバイト列を取るのが近道です。

#### Android: HCI snoop log

1. 開発者向けオプション → **Bluetooth HCI スヌープログを有効化** → ON
2. Bluetooth を一度 OFF/ON
3. 公式アプリで操作する (「ライト ON」「色を赤に」など、**1 操作ずつ間を空けて**やると後で対応付けやすい)
4. ログを取り出す
   ```bash
   adb bugreport bugreport.zip
   unzip -j bugreport.zip 'FS/data/misc/bluetooth/logs/btsnoop_hci.log'
   # 端末によっては bugreport 内の "btsnooz" テキスト形式になっている。
   # その場合は AOSP の tools/scripts/btsnooz.py で btsnoop に戻す
   ```
   root 済み端末なら `/data/misc/bluetooth/logs/btsnoop_hci.log` を直接 pull
5. 解析
   ```bash
   uv run ble-re snoop btsnoop_hci.log --summary        # どの handle が何回使われたか
   uv run ble-re snoop btsnoop_hci.log --data-only --relative
   uv run ble-re snoop btsnoop_hci.log -H 0x0014 --jsonl > writes.jsonl
   ```
   `snoop` は接続時のサービス探索 (Read By Type 0x2803) を追跡して **handle → characteristic UUID** を自動で解決するので、Wireshark を開かなくても「FFF2 に `7e 00 04 01 00 00 00 ff ef` を書いたら FFF1 で `...` が返った」という形で読めます。

#### iOS

Apple の Bluetooth プロファイル (Developer サイトの "Profiles and Logs") を入れ、sysdiagnose から PacketLogger の `.pklg` を取得。
`ble-re snoop` は `.pklg` もそのまま読めます (big/little endian 自動判定)。Wireshark 派なら `tshark` でも:

```bash
tshark -r capture.pklg -Y "btatt.opcode in {0x12 0x52 0x1b 0x1d 0x0b}" \
  -T fields -e frame.time_relative -e btatt.opcode -e btatt.handle -e btatt.value
```

#### 電波を直接キャプチャ (スマホを使わない場合)

nRF52840 ドングル + **nRF Sniffer for BLE** (Wireshark プラグイン) が定番です。
Wireshark で `.pcapng` に保存したら上の `tshark` コマンドで同様に抽出できます。
LE Secure Connections でペアリングされた通信は鍵が無いと復号できません (Legacy pairing なら `crackle` で解ける場合あり)。

### 4. 再現する — 書いて、反応を見る

取れたバイト列を自分の手で送って、同じ反応が返るか確かめます。

```bash
# 全 notify を購読したまま、FFF2 に 2 つのコマンドを 1 秒間隔で送る
uv run ble-re watch AA:BB:CC:DD:EE:FF -o session.jsonl \
    -w fff2=7e000401000000ffef \
    -w fff2=7e000400000000ffef

# 1 発だけ送って 3 秒眺める
uv run ble-re write AA:BB:CC:DD:EE:FF fff2 7e000401000000ffef --watch 3
```

ペイロードは `0102ff` / `01 02 ff` / `str:hello` / `u16le:513` の形式で書けます。
handle 指定 (`0x0014`) も UUID 指定 (`fff2` / 完全形) も使えます。

**フィールドの切り分け方**: 1 バイトずつ変えて送り、何が変わるかを見ます。

- 「ON/OFF」で 1 バイトだけ違う → そこが状態フラグ
- 「色を赤 / 緑 / 青」で 3 バイトが動く → RGB
- 最後の 1〜2 バイトが毎回変わる → チェックサム (下記)
- 先頭が固定 → ヘッダ / マジック
- 2 バイト目がペイロード長と一致 → 長さフィールド

**チェックサムの当たり方** (末尾 1〜2 バイトが常に変わるとき):

| 種類 | 試し方 |
|---|---|
| 総和 (sum & 0xff) | `sum(data[:-1]) & 0xff == data[-1]` |
| XOR | `functools.reduce(operator.xor, data[:-1]) == data[-1]` |
| CRC-8 / CRC-16 各種 | `crccheck` や `crcmod` で総当たり。`uv add crccheck` |
| 2 の補数 | `(-sum(data[:-1])) & 0xff` |

### 4b. CSV と突き合わせて配置を当てる — `fit`

公式アプリが CSV などで「正解の値」を書き出せる機器なら、通信ログの notify と CSV の行を 1:1 に並べて、
全オフセット × 全型 (u8/i16le/u16be/u24/u32/f32 …) を線形回帰で総当たりできます。

```bash
uv run ble-re snoop btsnoop_hci.log --data-only --jsonl > att.jsonl
uv run ble-re fit att.jsonl --csv session.csv -H 0x0012 -c rri -c temperature -c "acceleration x+"
```

```
alignment: shift=+3 (by 'rri', R²=1.0000)
[rri]
  R²=1.00000  n=147   rri = u16le[1]
[temperature]
  R²=1.00000  n=147   temperature = u16be[3] * 1/100
layout (best guess):
   0:?       1:rri     2:rri     3:temper  4:temper  5:accele ...
```

先頭ズレは自動で探索します。R² が低い列は非線形変換 (サーミスタの式など) かビットフィールドなので、
そこだけアプリのコードを読みます。

### 5. アプリを読む — 分からない部分を確定させる

```bash
# Android: まず UUID を機械的に拾う
uv run ble-re apk-uuids app.apk
# 次に jadx でその UUID を検索し、writeCharacteristic を遡る
jadx-gui app.apk
```

- `writeCharacteristic` / `setCharacteristicNotification` / GATT の UUID 文字列を検索して、送信パケットを組み立てている関数へ遡る
- `Flutter` 製アプリは `libapp.so` に Dart が AOT されていて読みづらい。`React Native` なら `index.android.bundle` を整形して読む
- 暗号化 / 認証 (ハンドシェイク) があるときは、鍵の導出をここで確定させる。snoop ログの最初のやりとりと突き合わせると速い

### 6. 結果をまとめる

`docs/protocol-template.md` を埋めていくと、後で自分のライブラリにするとき楽です。

---

## コマンドリファレンス

| コマンド | 説明 |
|---|---|
| `scan [-t SEC] [-n NAME] [-a ADDR] [--passive] [--live] [--json]` | アドバタイズをスキャン。RSSI 順に表示 |
| `dump ADDR [-r] [-d] [--pair] [--json]` | GATT の全列挙。`-r` で read 可能な値、`-d` でディスクリプタも読む |
| `read ADDR CHAR` | 1 つ読んで hexdump + 解釈候補を出す |
| `watch ADDR [-c CHAR ...] [-w CHAR=DATA ...] [-D SEC] [-o FILE.jsonl]` | notify/indicate を購読 (省略時は全部)。`-w` で購読後に順番に書き込み |
| `write ADDR CHAR DATA [--response auto\|yes\|no] [--watch SEC]` | 1 回書く。`--watch` で直後の通知を監視 |
| `snoop FILE [--summary] [--data-only] [-H 0x..] [--conn N] [--relative] [--jsonl]` | btsnoop / PacketLogger (.pklg) を ATT レベルで解析 |
| `fit PACKETS.jsonl --csv FILE -c COL ... [-H 0x..] [--offset-search N] [--min-r2 R]` | notify のバイト列と CSV の値からフィールド配置を推定 |
| `apk-uuids APK_OR_DIR` | APK / jadx 出力から UUID 文字列を拾う |

JSONL ログの各行は `{"t": 経過秒, "kind": "notify|write|...", "handle": 18, "uuid": "...", "hex": "..."}`
なので、pandas でそのまま `pd.read_json(path, lines=True)` できます。

---

## よくある詰まりどころ

- **接続できない / すぐ切れる**: 公式アプリがスマホから接続しっぱなしになっている (BLE ペリフェラルは通常 1 セントラルのみ)。スマホの Bluetooth を切る
- **notify が来ない**: `watch` は CCCD の書き込みまで自動でやるが、デバイス側が「先に特定コマンドを書かないと通知を開始しない」設計のことがある。snoop ログで公式アプリが接続直後に何を書いているか確認
- **Error 0x05 / 0x0F**: ペアリング (暗号化) が必要。`--pair` を付ける。Linux では先に `bluetoothctl pair` しておく方が安定
- **MTU**: `dump` の先頭に表示される。20 バイトを超える書き込みが失敗するなら、デバイスが小さい MTU しか受け付けていない
- **アドレスが毎回変わる**: Resolvable Private Address。ペアリングして IRK を持てば OS が解決してくれる
- **macOS でアドレスが違う**: CoreBluetooth の UUID なので、Android の snoop ログ内の MAC とは一致しない。UUID で照合する

## 開発

```bash
uv run pytest
```

btsnoop / pklg パーサと `fit` は合成データでテストされています (`tests/`)。
実機なしで動作を確かめたいときはそのファイルの `sample()` を参考にしてください。
