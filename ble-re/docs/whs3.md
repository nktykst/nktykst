# myBeat WHS-3 を自前アプリ (sleep_stage_app) に繋ぐための解析計画

対象: ユニオンツール myBeat WHS-3 (BLE 心拍センサ)。
ゴール: `sleep_stage_app` の `WHS3Client` (FR-1〜FR-4c) を、メーカーの通信仕様書が届く前に実装できる状態にする。
最終的には通信仕様書と突き合わせて答え合わせをする前提で、それまでの「つなぎ」として自力で読み解く。

## 1. いま分かっていること

`sleep_stage_app/docs/requirements/requirements.md` §4.2 (メーカー正式回答) と、
`sleep-automl` で読んだ実測 CSV (iOS アプリ 2.6.6) から確定している事実:

| 項目 | 内容 | 出典 |
|---|---|---|
| 接続 | BLE でスマホと直結。専用受信機なし | §4.2-1 |
| モード | 接続後に **心拍周期モードを明示設定** する必要がある。電源投入時は不定 | §4.2-2 → **何かを write する必要がある** |
| 送出 | 心拍周期モードでは **拍ごとに RRI・3軸加速度・体表温が同時に** 出る | §4.2-3 → 1 notify = 1 拍の可能性が高い |
| 欠測印 | 拍を検出できないと RRI = 3200 | §2 |
| メモリ | 本体メモリなし。切断中のデータは消える | §4.2-6 → 再送・履歴取得コマンドは無い |
| CSV | `timestamp, rri, temperature, acceleration x+, y+, z+, x-, y-, z-, lf, hf, longitude, latitude` | sleep-automl `preprocess_whs3.py` |
| 加速度 | 軸ごとに `+` と `-` の 2 値 (拍間隔内の最大/最小と推定)。単位 g | 同上 |
| 体表温 | 28.2364 のように小数 4 桁で出る → **アプリ側で生値から変換している**可能性 (サーミスタの式など) | 同上 |
| lf / hf | アプリ側の計算値。開始 120 秒は 0 → パケットには入っていない | 同上 |
| 他モード | 心拍波形モード・加速度モード | 公式サイト |
| 公式アプリ | iOS: App Store「WHS-3」 / Android: `uniontool.co.jp.whs3.whs3_android` | Google Play |
| 仕様書 | 購入者限定開示。研究室が手続き中 | §4.2-7 |

分かっていないもの = 解析で埋めるもの:

1. サービス / キャラクタリスティックの UUID (どれがコマンド用、どれが拍データ用か)
2. 心拍周期モードに切り替えるコマンドのバイト列 (と、その応答)
3. 拍ごとの notify のバイト配置 (RRI・温度・加速度 6 値・その他)
4. 接続直後に公式アプリが送っている初期化シーケンス (時刻設定・認証があるか)

## 2. 手順 (所要: 実機があれば半日)

### Step 1. Mac から GATT を見る (10 分)

公式アプリを閉じ (iPhone の Bluetooth を切っておく)、Mac のターミナルで:

```bash
cd ble-re && uv sync
uv run ble-re scan -t 10                     # WHS-3 のアドレス (CoreBluetooth UUID) を控える
uv run ble-re dump <ADDR> -r -d --json > whs3_gatt.json
uv run ble-re dump <ADDR> -r -d              # 目視用
uv run ble-re watch <ADDR> -D 60 -o whs3_idle.jsonl   # モード設定なしで何か流れてくるか
```

`dump` で見るポイント:

- `0x180a` Device Information があれば Firmware / Model が読める
- **ベンダー UUID で `write` + `notify` のペア**を探す。それがコマンド窓口と拍データの候補
- `watch` で何も来なければ「モード設定コマンドが必要」で確定 (§4.2-2 と整合)
- 何か来るなら、そのパケットが拍データ。長さと 1 秒あたりの頻度を見る

### Step 2. 公式アプリの通信を記録する (30 分)

**iPhone (手元の環境)**:

1. Mac で <https://developer.apple.com/bug-reporting/profiles-and-logs/> から **Bluetooth** の
   ロギングプロファイルをダウンロードし、iPhone にインストール (AirDrop → 設定 → プロファイル)
2. iPhone の公式 WHS-3 アプリで接続 → 心拍周期モードで **1〜2 分計測** → 切断
   (できれば途中でモードを切り替える操作も 1 回入れる。コマンドの違いが取れる)
3. **同じ計測を CSV に書き出す** (後で `fit` に使う。CSV と通信ログは同じセッションであること)
4. sysdiagnose を取る (音量上下 + サイドボタンを 1.5 秒長押し → 設定 → プライバシー → 解析 → sysdiagnose_...)。
   Mac に AirDrop し、中の `.pklg` (Bluetooth 関係のフォルダ) を取り出す
5. 解析:
   ```bash
   uv run ble-re snoop whs3.pklg --summary            # 接続先アドレス、handle × opcode の件数
   uv run ble-re snoop whs3.pklg --data-only --relative | head -50   # 接続直後の write が初期化シーケンス
   uv run ble-re snoop whs3.pklg --data-only --jsonl > whs3_att.jsonl
   ```

**Android 端末があるなら** (こちらの方が楽で確実):

1. 開発者向けオプション → Bluetooth HCI スヌープログ ON → Bluetooth OFF/ON
2. 公式アプリで同じ操作 → CSV 書き出し
3. `adb bugreport br.zip && unzip -j br.zip 'FS/data/misc/bluetooth/logs/btsnoop_hci.log'`
4. APK も取る: `adb shell pm path uniontool.co.jp.whs3.whs3_android` → `adb pull <path> whs3.apk`
   ```bash
   uv run ble-re apk-uuids whs3.apk       # UUID 一覧。Step 1 の dump と突き合わせる
   jadx-gui whs3.apk                       # UUID 文字列で検索 → writeCharacteristic を遡る
   ```
   モード設定コマンドのバイト列とチェックサムは、ここで読むのが一番早い。

### Step 3. 拍パケットの配置を推定する (10 分)

Step 2 の通信ログと CSV を突き合わせる。1 notify = 1 拍なら行が 1:1 で対応するので、
オフセット × 型を総当たりして線形回帰で当てる:

```bash
uv run ble-re fit whs3_att.jsonl --csv whs3_session.csv -H 0x<拍データの handle> \
    -c rri -c temperature \
    -c "acceleration x+" -c "acceleration y+" -c "acceleration z+" \
    -c "acceleration x-" -c "acceleration y-" -c "acceleration z-"
```

出力の見方:

- `rri = u16le[2]` のように R² ≈ 1 で出れば確定
- 加速度は `i16le[k] * 1/4096` や `1/256` のような「きれいな」スケールで出るはず
- **体表温だけ R² が低い**なら、アプリが非線形変換 (サーミスタの Steinhart-Hart など) をしている。
  その場合は生値 (R² が一番高かった候補) を記録しておき、変換式は jadx で `temperature` を検索して拾う
- `lengths` が揃っていない場合は「1 notify に複数拍」か「拍以外のパケット混在」。`--min-len` で絞るか、
  `snoop --relative` で長さ別に眺めて分ける
- どうしても合わない列は、`snoop --jsonl` の生データを pandas で開いて、CSV の値の差分と
  各バイトの差分の相関を取る (`fit` は単調線形しか見ない)

### Step 4. 自分で再現する (10 分)

Step 2 で見えた初期化シーケンスを Mac から送り、同じ notify が来ることを確認する:

```bash
uv run ble-re watch <ADDR> -o whs3_replay.jsonl \
    -w <コマンド用 UUID>=<モード設定のバイト列> \
    --write-delay 1.0 -D 60
uv run ble-re fit whs3_replay.jsonl --csv <このとき公式アプリで…>   # ※ 公式アプリと同時接続はできないので、
                                                                   #   ここは decode したものを目視で妥当性確認
```

再現できれば、プロトコルは「UUID 2〜3 個 + コマンド 1〜2 個 + 固定長パケット 1 種」に収まるはず。
`docs/protocol-template.md` を埋めて `docs/whs3-protocol.md` として残す。

## 3. sleep_stage_app への組み込み方

解析結果は **コードではなく設定に置く** (通信仕様書が届いたら差し替えるだけにするため。§9 の思想と同じ):

```jsonc
// Resources/Config/whs3_protocol.json  (案)
{
  "serviceUUID":        "xxxxxxxx-....",
  "commandCharUUID":    "xxxxxxxx-....",
  "beatCharUUID":       "xxxxxxxx-....",
  "setRRIModeCommand":  "a1 01 ...",          // FR-1: 接続後に必ず書く
  "beatPacket": {
    "length": 20,
    "rri":         { "offset": 2, "type": "u16le" },
    "temperature": { "offset": 4, "type": "u16le", "scale": 0.01 },
    "accel":       { "offset": 6, "type": "i16le", "scale": 0.000244140625, "order": ["x+","y+","z+","x-","y-","z-"] }
  }
}
```

`WHS3Client` の実装 (CoreBluetooth) は次の形になる:

1. `CBCentralManager(restoreIdentifier:)` で生成 (§4.3)。保存済み `CBPeripheral.identifier` があれば
   `retrievePeripherals(withIdentifiers:)` で直接取り、無ければ `serviceUUID` でスキャン (FR-4c)
2. 接続 → `discoverServices([serviceUUID])` → 2 つのキャラクタリスティックを取得
3. `setNotifyValue(true, for: beatChar)` → **その後** `writeValue(setRRIModeCommand, for: commandChar)` (FR-1)。
   順序は Step 2 の公式アプリのシーケンスに合わせる
4. `didUpdateValueFor` で `beatPacket` の配置に従って `BeatPacket` を組み立て、`timestamps` は受信時点の
   `ClockSource.now()`。RRI 3200 はそのまま渡す (品質判定は `SessionController` 側の責務)
5. `didDisconnect` → バックオフ再接続 (FR-4b)。再接続後は 3 を必ずやり直す (モードは接続ごとに不定)

`MockWHS3Source` と同じ `AsyncStream<BeatPacket>` を返す形にしておけば `SessionController.beatTask` の
差し替えは 1 行で済む。

## 4. 検収の基準

- 一晩 (8 時間) を自前実装で受信し、同じ夜を公式アプリで取った CSV と **RRI が拍単位で一致**すること
  (同時接続はできないので、別の夜で分布・欠測率・3200 の出方が同等であることで代替)
- 温度・加速度は CSV と同じ値域・同じ拍で動くこと
- 切断 → 再接続後もモード設定をやり直して拍が再開すること (FR-1 / FR-4)

## 5. 通信仕様書が届いたら

`whs3_protocol.json` の値を仕様書の値と照合し、違いがあれば仕様書に合わせる。
特に「モード設定コマンドの応答を待つ必要があるか」「時刻同期コマンドの有無」「電池残量の読み方」は
解析だけでは取りこぼしやすいので、届いたら真っ先に確認する。
