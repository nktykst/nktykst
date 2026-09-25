# iPhone 制御 BLE 機器のリバースエンジニアリング手順

iPhone 純正アプリから操作する BLE (Bluetooth Low Energy) 機器のプロトコルを解析し、
自作アプリから制御するまでの手順をまとめる。対象は **自分が所有する機器** に限る
(利用規約・不正アクセス禁止法・技適などは各自で確認すること)。

```
[0] 準備  →  [1] 機器の特定  →  [2] 通信の取得  →  [3] プロトコル解析
        →  [4] PC からリプレイ  →  [5] 自作 iOS アプリ
```

---

## 0. 準備するもの

| 用途 | ツール | 備考 |
| --- | --- | --- |
| iPhone 上での GATT 探索 | nRF Connect for Mobile / LightBlue | App Store。UUID・Notify の確認 |
| iPhone の BLE ログ取得 | **Bluetooth Logging プロファイル** + **PacketLogger** (Xcode Additional Tools) | Mac 必須。最も確実な方法 |
| ログ解析 | Wireshark (`.pklg` を直接開ける) / `tshark` | Homebrew: `brew install --cask wireshark` |
| PC からのリプレイ | Python 3 + `bleak` | `pip install bleak` (macOS/Linux/Windows 対応) |
| 無線スニッファ (任意) | nRF52840 Dongle + nRF Sniffer for BLE | iPhone のログが取れない場合の代替 |
| アプリの静的解析 (任意) | Ghidra / Hopper / `strings` | 暗号化・チェックサムの解明用 |

---

## 1. 機器の特定 (GATT の把握)

1. 純正アプリを **完全終了** し、iPhone の Bluetooth 設定で機器を「このデバイスの登録を解除」しておく
   (ボンディングされていると別アプリから接続できないことがある)。
2. nRF Connect で Scan → 対象機器を探す。以下をメモする。
   - Advertised Name / Manufacturer Specific Data / Service UUIDs
   - iOS では **MAC アドレスは見えない** (端末ごとに異なる `identifier` UUID になる)。
     自作アプリでは名前・Service UUID・Manufacturer Data で機器を判別する。
3. Connect → 全 Service / Characteristic を展開し、以下を記録する。
   - Characteristic UUID と Properties (`Read` / `Write` / `WriteWithoutResponse` / `Notify` / `Indicate`)
   - `Notify` があるものは購読 (Subscribe) して、機器を物理操作したときに値が来るか見る
4. 128-bit のカスタム UUID (例 `0000fff1-0000-1000-8000-00805f9b34fb`, `6e400002-b5a3-...`) が
   本命。`6e40xxxx-b5a3-f393-e0a9-e50e24dcca9e` なら Nordic UART Service (NUS) で、
   TX/RX のシリアル通信として扱える。

`tools/gatt_explore.py` を使えば PC から同じことができる:

```bash
pip install bleak
python tools/gatt_explore.py scan                 # 周辺の機器一覧
python tools/gatt_explore.py dump  "<名前 or アドレス>"   # 全 Service/Characteristic
python tools/gatt_explore.py listen "<名前 or アドレス>"   # 全 Notify を購読して待ち受け
```

---

## 2. 通信の取得 (純正アプリが何を送っているか)

### 2-A. iPhone 自身のログを取る (推奨・最も確実)

暗号化されたリンクでも **iPhone 側で復号済みの ATT パケット** が取れるので、まずこれを試す。

1. Mac で Apple Developer サイトの *Profiles and Logs* から **Bluetooth Logging Profile (iOS)** を
   ダウンロードし、AirDrop などで iPhone にインストール (設定 → 一般 → VPN とデバイス管理)。
2. Xcode → *Open Developer Tool* → *More Developer Tools...* から **Additional Tools for Xcode** を
   ダウンロードし、`Hardware/PacketLogger.app` を起動。
3. iPhone を USB で Mac に接続し、PacketLogger で *File → New iOS Trace* を選ぶとリアルタイムで
   HCI/ATT が流れ始める。
4. 純正アプリで **1 操作ずつ** (ON → 数秒待つ → OFF → 明るさ 50% ...) 操作し、操作した時刻と
   内容をメモする。この対応表が解析の核になる。
5. `.pklg` で保存 → Wireshark で開く。フィルタ例:

```
btatt.opcode == 0x12 || btatt.opcode == 0x52   # Write Request / Write Command (アプリ → 機器)
btatt.opcode == 0x1b                            # Handle Value Notification (機器 → アプリ)
btatt                                           # ATT 全部
```

USB 接続なしで採る場合は、操作後に iPhone で **sysdiagnose** (音量上下+サイドボタン長押し) を取り、
中の `.pklg` を取り出す。

`tools/extract_att.sh` で `.pklg` から Write / Notify だけを CSV 化できる:

```bash
./tools/extract_att.sh capture.pklg > att.csv
```

### 2-B. 無線スニッファ (nRF Sniffer)

iPhone のログが使えない場合。nRF52840 Dongle に nRF Sniffer ファームウェアを書き込み、
Wireshark のプラグインとして使う。**接続確立前 (Advertising 中) から機器を follow する** 必要がある。
LE Secure Connections でペアリングする機器は暗号化を復号できないので、ペアリングしない機器向け。

### 2-C. Android 版アプリがある場合

Android の「開発者向けオプション → Bluetooth HCI スヌープログを有効化」で `btsnoop_hci.log` が取れる。
ペアリング不要な機器なら iOS と同じ手順で解析できる。

### 2-D. アプリ本体の解析 (暗号化・認証がある場合)

- **動的解析**: Frida + `objection` で `-[CBPeripheral writeValue:forCharacteristic:type:]` と
  `-[CBPeripheral ...didUpdateValueForCharacteristic:...]` をフックし、送受信バイト列をそのまま出す。
  脱獄機か、自分で再署名したアプリが必要。
- **静的解析**: 復号済み IPA を Ghidra/Hopper で開き、Characteristic UUID 文字列や
  `CCCrypt` / `AES` / `CRC` / `checksum` を検索する。鍵やチェックサム多項式はここで見つかることが多い。

---

## 3. プロトコル解析

取得した「操作 ↔ バイト列」の対応表を並べて差分を取る。

```
ON        : 7e 00 04 01 00 00 00 ef
OFF       : 7e 00 04 00 00 00 00 ef
明るさ 50%: 7e 00 01 32 00 00 00 ef      # 0x32 = 50
色 (R,G,B): 7e 00 05 03 ff 00 00 ef
```

よくある構造:

| 要素 | 見分け方 |
| --- | --- |
| ヘッダ / フッタ | 全パケット共通の先頭・末尾バイト (`7e`, `aa 55`, `ef` など) |
| 長さ | 残りバイト数と一致する 1 バイト |
| オペコード | 操作の種類ごとに変わるバイト |
| 引数 | 値をスライダーで変えると連続的に変わるバイト (LE/BE の 16-bit も疑う) |
| シーケンス番号 | 送るたびに +1 されるバイト。リプレイ時も増やす必要あり |
| チェックサム | 末尾 1〜2 バイト。XOR / 加算 / CRC-8 / CRC-16 を試す (`tools/checksum_guess.py`) |
| 暗号化 | ペイロードがランダムに見え 16 バイト境界 → AES-ECB/CBC。鍵はアプリ内 or ペアリング時に交換 |
| 認証ハンドシェイク | 接続直後にアプリが必ず送る固定/半固定パケットと、それへの Notify 応答 |

**接続直後のシーケンス**も必ず記録する (CCCD の書き込み 0x0001 → 認証パケット → 応答 → 通常コマンド の
順など)。自作アプリでも同じ順序を再現しないと機器がコマンドを無視することがある。

---

## 4. PC からリプレイして検証

```bash
# Notify を購読しながら書き込む (機器の応答も見える)
python tools/gatt_explore.py write "<名前>" 0000fff2-0000-1000-8000-00805f9b34fb 7e000401000000ef
python tools/gatt_explore.py write "<名前>" 0000fff2-0000-1000-8000-00805f9b34fb 7e000400000000ef --no-response
```

- 機器は 1 台の Central としか接続できないことが多い。**テスト中は iPhone の Bluetooth を切る**。
- `Write` と `WriteWithoutResponse` は ATT レベルで別物。ログに出ていた方に合わせる。
- 反応がなければ、Notify 購読 → 認証パケット の順を守っているか確認する。
- 上手くいけばここでプロトコルが「わかった」と言える。PC 側実装 (Python) をそのまま
  Home Assistant 連携などにも使える。

---

## 5. 自作 iOS アプリ (CoreBluetooth)

`ios/BLEController.swift` に最小構成を置いた。要点:

- `Info.plist` に `NSBluetoothAlwaysUsageDescription` を追加 (ないと起動時にクラッシュ)。
- スキャンは `scanForPeripherals(withServices: [ServiceUUID])` で絞る。`nil` を渡すと
  バックグラウンドでは検出できない。
- 機器の識別は `peripheral.identifier` (UUID) を `UserDefaults` に保存し、次回は
  `retrievePeripherals(withIdentifiers:)` で再取得する。
- `Write` 前に `discoverServices` → `discoverCharacteristics` → `setNotifyValue(true)` を
  終えてから、解析した順序で送る。
- ペアリングが必要な機器は、暗号化された Characteristic にアクセスした時点で
  iOS がペアリングダイアログを自動で出す。アプリ側から明示的に呼ぶ API はない。
- クロスプラットフォームにするなら Flutter (`flutter_blue_plus`) / React Native (`react-native-ble-plx`)。
  プロトコル層は Python 版と同じバイト列を組み立てるだけ。

---

## 詰まりやすいポイント

- **接続できない**: 純正アプリが裏で接続を保持している / iPhone とボンディング済み → アプリ終了、登録解除。
- **書き込んでも無反応**: 認証ハンドシェイク・Notify 購読・シーケンス番号・チェックサムのどれかが欠けている。
- **ペイロードがランダム**: 暗号化。2-D でアプリから鍵を探す。鍵が接続ごとに変わるなら鍵交換手順を追う。
- **iOS で見つからない**: Service UUID を Advertise していない機器は `withServices: nil` でしか見つからない
  (フォアグラウンド限定)。
- **Notify が来ない**: CCCD への `0x0001` (Notify) / `0x0002` (Indicate) の書き込みが必要。bleak/CoreBluetooth は
  `start_notify` / `setNotifyValue` が代行する。

## 参考にできる公開実装

Home Assistant の Bluetooth 系インテグレーションや `bleak` の examples は、
「解析済みプロトコルをどうコードに落とすか」の実例として読みやすい。
