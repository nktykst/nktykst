# Phase 0 — 土台づくりの具体手順 (Linux)

> 親ドキュメント: [`life-os-concept.md`](./life-os-concept.md)
> 前提: Linux (Ubuntu / Debian 系で記述)、RTX 5060 Ti 16GB、NVIDIA ドライバ導入済み、Docker が使える
> ゴール: **各部品が単体で動く**。統合はしない。所要 1〜2 週間 (部品の到着待ち含む)

順番は依存関係の順。上から潰していけば良い。各ステップに「確認コマンド」を付けた。

---

## 0. 買い物 (最初に発注しておく)

| 品目 | 数量 | 用途 | 目安 |
|---|---|---|---|
| **ESP32 開発ボード** (ESP32-DevKitC / ESP32-S3-DevKitC-1 など。ESPHome が動けば何でも良い) | 2〜3 | 在室センサー ×1、IR リモコン ×1、予備 ×1 | 1,000〜2,500 円 / 個 |
| **LD2450 mmWave レーダーモジュール** (HLK-LD2450) | 1 (机とベッドが別の部屋なら 2) | 人の座標を取り、机 / ベッドのゾーン判定 | 1,500〜2,500 円 |
| **FSR 感圧センサー** (FSR-406 などの長いもの、または薄型の圧力マット) + 10kΩ 抵抗 | 1〜2 | マットレスの下に敷いてベッド在床を検知 | 1,000〜3,000 円 |
| **赤外線 LED** (940nm) + **NPN トランジスタ** (2N2222 / S8050) + 抵抗 (100Ω, 1kΩ) | 各 1 | IR 送信 (ESP32 の GPIO 直結だと出力が弱い) | 数百円 |
| **赤外線受信モジュール** (VS1838B / TSOP38238) | 1 | 手持ちリモコンの信号を学習する | 数百円 |
| ブレッドボード、ジャンパ線、USB-C ケーブル | 一式 | | 1,000 円前後 |
| (後で) **USB スピーカーフォン** (Jabra Speak 410/510、Anker PowerConf S330 など) | 1 | 英会話の barge-in 用 (エコーキャンセル内蔵) | 1〜2 万円 |
| (任意) **USB マイク** | 1 | スピーカーフォンを買うまでの間に合わせ | 3,000 円〜 |

時間を買うなら: IR 工作の代わりに **SwitchBot Hub Mini** (約 5,000 円) を買えば HA から即リモコン操作できる。工作したいなら上のリストで良い。

RuView (WiFi CSI) 用の ESP32-S3 は**今は買わない**。後で試したくなったら ESP32-S3 / C6 を別途足す。

---

## 1. システム基盤

```bash
# GPU が見えるか
nvidia-smi

# Docker + NVIDIA Container Toolkit (コンテナから GPU を使う場合。Ollama をネイティブで動かすなら任意)
sudo apt-get install -y docker.io docker-compose-plugin
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
  | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
  | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
sudo apt-get update && sudo apt-get install -y nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker && sudo systemctl restart docker
sudo usermod -aG docker "$USER"   # 再ログイン

# 音声まわり (PipeWire 前提)
sudo apt-get install -y pipewire pipewire-pulse wireplumber pulseaudio-utils ffmpeg espeak-ng portaudio19-dev
wpctl status          # Sinks (出力) に Dell モニター、Sources (入力) にマイクが出ること

# uv (Python 管理)
curl -LsSf https://astral.sh/uv/install.sh | sh
```

**確認:** `docker run --rm --gpus all nvidia/cuda:12.4.0-base-ubuntu22.04 nvidia-smi` で GPU が見える。`wpctl status` に入出力デバイスが出る。

---

## 2. ローカル LLM (Ollama)

```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama pull qwen3:14b
ollama pull qwen3:8b

# 速度比較 (tokens/s を見る)
ollama run qwen3:14b --verbose "電気を消して、という指示を JSON {\"intent\":..., \"target\":...} にして"
ollama run qwen3:8b  --verbose "同上"

# tool calling が通るか (OpenAI 互換 API)
curl http://localhost:11434/v1/chat/completions -d '{
  "model": "qwen3:8b",
  "messages": [{"role":"user","content":"リビングの電気を消して"}],
  "tools": [{"type":"function","function":{"name":"light_turn_off","parameters":{"type":"object","properties":{"room":{"type":"string"}},"required":["room"]}}}]
}'
```

**確認:** 14B で 20 tok/s 以上、8B で 40 tok/s 以上が目安。`nvidia-smi` で VRAM 使用量をメモしておく (2.1 の配分表の実測値になる)。
**決めること:** 意図分類と即答用にどちらを常駐させるか。迷ったら 8B。

---

## 3. Home Assistant + VOICEVOX (Docker)

`~/life-os/docker-compose.yml`:

```yaml
services:
  homeassistant:
    image: ghcr.io/home-assistant/home-assistant:stable
    container_name: homeassistant
    network_mode: host          # mDNS / ESPHome 機器の発見のため
    privileged: true
    volumes:
      - ./data/ha:/config
      - /etc/localtime:/etc/localtime:ro
    restart: unless-stopped

  voicevox:
    image: voicevox/voicevox_engine:nvidia-latest
    container_name: voicevox
    ports: ["50021:50021"]
    deploy:
      resources:
        reservations:
          devices: [{ driver: nvidia, count: 1, capabilities: [gpu] }]
    restart: unless-stopped
```

```bash
mkdir -p ~/life-os/{config,data/ha} && cd ~/life-os
docker compose up -d
```

**確認:**
- `http://localhost:8123` で HA の初期設定 (アカウント作成)。設定 → 統合 → **ESPHome** を追加しておく (9 章と 10 章の機器が自動発見される)。
- `http://localhost:50021/docs` で VOICEVOX の API ドキュメントが出る。

Mosquitto (MQTT) と RuView は今は入れない。後で RuView を試すときに compose に足す。

---

## 4. 音声: STT と TTS の単体テスト

```bash
cd ~/life-os && uv init --name lifeos --python 3.12
uv add faster-whisper kokoro soundfile sounddevice numpy httpx
uv add nvidia-cublas-cu12 nvidia-cudnn-cu12   # faster-whisper (CTranslate2) の GPU 実行に必要
```

`scripts/test_stt.py` — マイクから 5 秒録音して文字起こし:

```python
import sounddevice as sd, soundfile as sf
from faster_whisper import WhisperModel

sd.default.samplerate = 16000
audio = sd.rec(int(5 * 16000), channels=1, dtype="float32"); sd.wait()
sf.write("sample.wav", audio, 16000)

model = WhisperModel("large-v3-turbo", device="cuda", compute_type="float16")
segments, info = model.transcribe("sample.wav", language=None)   # 日英自動判定
print(info.language, [s.text for s in segments])
```

`scripts/test_tts_en.py` — Kokoro で英語を再生:

```python
import sounddevice as sd
from kokoro import KPipeline

pipe = KPipeline(lang_code="a")   # American English
for _, _, audio in pipe("Hello. Let's practice English for ten minutes today.", voice="af_heart"):
    sd.play(audio, 24000); sd.wait()
```

`scripts/test_tts_ja.py` — VOICEVOX で日本語を再生:

```python
import httpx, sounddevice as sd, soundfile as sf, io
text, speaker = "おはようございます。今日の予定を読み上げます。", 3
q = httpx.post("http://localhost:50021/audio_query", params={"text": text, "speaker": speaker}).json()
wav = httpx.post("http://localhost:50021/synthesis", params={"speaker": speaker}, json=q).content
data, sr = sf.read(io.BytesIO(wav)); sd.play(data, sr); sd.wait()
```

**確認:** 3 つとも動く。STT は日本語と英語の両方で試す。`nvidia-smi` で Whisper 常駐時の VRAM をメモ。

**エコーキャンセルの下準備 (スピーカーフォンを買うまでの間に合わせ):**

```bash
# PipeWire の WebRTC エコーキャンセルを有効化 → 仮想の入出力デバイスが増える
mkdir -p ~/.config/pipewire/pipewire.conf.d
cat > ~/.config/pipewire/pipewire.conf.d/99-echo-cancel.conf <<'CONF'
context.modules = [
  { name = libpipewire-module-echo-cancel
    args = { library.name = aec/libspa-aec-webrtc
             source.props = { node.name = "echo_cancel_source" }
             sink.props   = { node.name = "echo_cancel_sink" } } }
]
CONF
systemctl --user restart pipewire pipewire-pulse wireplumber
wpctl status | grep -i echo
```

---

## 5. Claude Code (Max サブスクで headless 実行)

```bash
curl -fsSL https://claude.ai/install.sh | bash
claude            # 初回: ブラウザで claude.ai アカウントにログイン (Max)
claude setup-token   # 1 年有効のトークンが表示される → 安全な場所に保存

# 自動化用の環境変数 (~/.config/lifeos/env などに置き、systemd から読む。リポジトリに入れない)
export CLAUDE_CODE_OAUTH_TOKEN="<setup-token の出力>"

# headless の疎通確認
claude -p "1 行で自己紹介して" --output-format json | jq .
```

**確認:** JSON に `result`, `session_id`, `usage` が入って返る。`claude.ai` の設定画面で使用量が動いていれば、サブスク枠で動いている。

---

## 6. Discord Bot (スマホからの入口)

1. https://discord.com/developers/applications で New Application → Bot → Token を発行。**Message Content Intent** を ON。
2. OAuth2 → URL Generator で `bot` スコープ、権限は Send Messages / Read Message History / Attach Files → 生成 URL で自分のサーバーに招待。
3. サーバーにチャンネルを作る: `#planner` `#kitchen` `#tasks` `#tutor` `#home` `#log`。

```bash
uv add discord.py
```

`scripts/test_discord.py` — メッセージをオウム返し:

```python
import os, discord
intents = discord.Intents.default(); intents.message_content = True
client = discord.Client(intents=intents)

@client.event
async def on_message(m):
    if m.author == client.user: return
    await m.channel.send(f"[{m.channel.name}] 受け取った: {m.content}")

client.run(os.environ["DISCORD_BOT_TOKEN"])
```

**確認:** スマホの Discord から送って返事が来る。写真を添付したとき `m.attachments[0].url` が取れることも確認 (冷蔵庫写真の経路)。

---

## 7. Google Calendar

1. Google Cloud Console でプロジェクト作成 → Google Calendar API を有効化 → OAuth クライアント ID (デスクトップアプリ) を作成し `credentials.json` を取得。
2. HA 側: 設定 → 統合 → Google Calendar を追加 (同じクライアント ID を使える)。HA にカレンダーエンティティが出る。
3. iPhone: 設定 → カレンダー → アカウント追加 → Google。以後は Google Calendar が正。

```bash
uv add google-api-python-client google-auth-oauthlib
```

**確認:** Python から今日の予定が取れる。Meet のリンクが `conferenceData` / `hangoutLink` に入っていることを確認 (会議録音のトリガーに使う)。

---

## 8. 会議録音の下準備 (Google Meet)

```bash
# 出力 (相手の声) のモニターソース名を確認
pactl get-default-sink            # 例: alsa_output.pci-0000_01_00.1.hdmi-stereo
pactl get-default-source          # マイク

# 2 トラック録音 (track0 = 相手, track1 = 自分)
ffmpeg -f pulse -i "$(pactl get-default-sink).monitor" \
       -f pulse -i "$(pactl get-default-source)" \
       -map 0 -map 1 -c:a flac meeting_$(date +%Y%m%d_%H%M).mka
```

**確認:** 自分で Meet を開いて 1 分録音し、2 トラックがそれぞれ入っている。Phase 3 ではこれを Whisper に流す。

---

## 9. 照明の赤外線リモコン (ESPHome)

```bash
uv tool install esphome
```

**Step 1: 手持ちリモコンの信号を学習する** (`config/esphome/ir-learn.yaml`):

```yaml
esphome:
  name: ir-blaster
esp32:
  board: esp32-s3-devkitc-1
  framework: { type: esp-idf }
wifi: { ssid: !secret wifi_ssid, password: !secret wifi_password }
api:
logger:
remote_receiver:
  pin: { number: GPIO4, inverted: true }
  dump: all          # ログに NEC / Panasonic などのプロトコルとコードが出る
```

```bash
esphome run config/esphome/ir-learn.yaml   # 書き込み後、ログを見ながらリモコンのボタンを押す
```

**Step 2: 送信側を書く** (受信ログのコードを埋める):

```yaml
remote_transmitter:
  pin: GPIO5           # トランジスタ経由で IR LED へ
  carrier_duty_percent: 50%
switch:
  - platform: template
    name: "Ceiling Light Power"
    turn_on_action:
      - remote_transmitter.transmit_nec: { address: 0x1234, command: 0x56 }   # 学習した値
    turn_off_action:
      - remote_transmitter.transmit_nec: { address: 0x1234, command: 0x57 }
```

**確認:** HA に ESPHome 統合で自動発見され、`switch.ceiling_light_power` を HA からトグルすると照明が反応する。プロトコルが NEC 以外 (Panasonic など) なら `transmit_*` を対応するものに変える。

---

## 10. 在室センサー (LD2450 mmWave + ベッド圧力センサー)

**配線:**
- LD2450 → ESP32: `5V`/`GND`、`TX`→`GPIO16`、`RX`→`GPIO17` (UART、256000 bps)
- FSR → ESP32: FSR の片側を 3.3V、もう片側を `GPIO34` (ADC) と 10kΩ 抵抗を介して GND (分圧)

`config/esphome/presence.yaml`:

```yaml
esphome:
  name: presence
esp32:
  board: esp32dev
  framework: { type: esp-idf }
wifi: { ssid: !secret wifi_ssid, password: !secret wifi_password }
api:
logger: { baud_rate: 0 }   # UART をセンサーに使うのでシリアルログは切る

uart:
  id: ld2450_uart
  tx_pin: GPIO17
  rx_pin: GPIO16
  baud_rate: 256000
  parity: NONE
  stop_bits: 1

ld2450:
  id: ld2450_radar
  uart_id: ld2450_uart

binary_sensor:
  - platform: ld2450
    ld2450_id: ld2450_radar
    has_target: { name: "Room Occupied" }
  # ゾーン。座標 (mm) はセンサー位置を原点、正面が +y。設置後に HA の座標表示を見て決める
  - platform: template
    name: "Desk Occupied"
    lambda: return id(zone1_target_count).state > 0;
  - platform: template
    name: "Bed Occupied"
    lambda: return id(zone2_target_count).state > 0 && id(bed_pressure).state > 0.4;

sensor:
  - platform: ld2450
    ld2450_id: ld2450_radar
    target_count: { name: "Person Count" }
    zone_1: { target_count: { id: zone1_target_count, name: "Zone Desk Count" } }
    zone_2: { target_count: { id: zone2_target_count, name: "Zone Bed Count" } }
    target_1: { x: { name: "T1 X" }, y: { name: "T1 Y" } }   # 設置調整用。決まったら消して良い
  - platform: adc
    id: bed_pressure
    pin: GPIO34
    attenuation: 12db
    name: "Bed Pressure"
    update_interval: 2s
    filters: [ { sliding_window_moving_average: { window_size: 5, send_every: 1 } } ]

number:   # ゾーンの矩形は HA 側から数値で調整できるようにする
  - platform: ld2450
    ld2450_id: ld2450_radar
    zone_1: { x1: { name: "Desk X1" }, y1: { name: "Desk Y1" }, x2: { name: "Desk X2" }, y2: { name: "Desk Y2" } }
    zone_2: { x1: { name: "Bed X1" },  y1: { name: "Bed Y1" },  x2: { name: "Bed X2" },  y2: { name: "Bed Y2" } }
```

```bash
esphome run config/esphome/presence.yaml
```

**設置と調整:**
1. 机とベッドの両方が見える壁に、高さ 1〜1.5 m で取り付ける (両面テープで仮止め)。
2. HA の「T1 X / T1 Y」を見ながら、自分が机に座った時とベッドに寝た時の座標を読む。
3. その座標を囲む矩形を「Desk X1..Y2」「Bed X1..Y2」に入れる。
4. 1 週間、`Desk Occupied` / `Bed Occupied` の履歴グラフを見て誤検知を潰す。FSR のしきい値 (`0.4`) もベッドに乗った時の値を見て直す。

**確認:** 机に座ると `Desk Occupied` が ON、ベッドに寝ると `Bed Occupied` が ON、部屋を出ると `Room Occupied` が OFF になる。ESPHome の `ld2450` コンポーネントのキー名はバージョンで変わることがあるので、コンパイルエラーが出たら公式ドキュメント (esphome.io/components/sensor/ld2450) を正とする。

---

## 11. リポジトリの初期化

```bash
cd ~/life-os
git init && git add -A && git commit -m "Phase 0: scaffold"
```

`.gitignore` に `data/`, `.env`, `credentials.json`, `token.json`, `secrets.yaml` を必ず入れる。

SQLite スキーマ v0 (`src/lifeos/memory/schema.sql`) は概念ドキュメント 4 章の表をそのまま `CREATE TABLE` にしたもので良い。Phase 2 で列を足す前提で、最初は `goals`, `tasks`, `daily_log`, `presence_log`, `preferences`, `claude_usage` の 6 つだけ作る。

---

## Phase 0 完了チェックリスト

- [ ] `nvidia-smi` が Docker 内外で通る、`wpctl status` に入出力が出る
- [ ] Ollama で 14B / 8B の速度を測り、常駐モデルを決めた
- [ ] HA が `:8123`、VOICEVOX が `:50021` で動く
- [ ] STT (日 / 英)、TTS (英 / 日) の 3 スクリプトが動く。VRAM 実測値をメモした
- [ ] `claude -p ... --output-format json` がサブスク認証で通る
- [ ] Discord から送った文章と写真が PC に届く
- [ ] Google Calendar から今日の予定が取れ、iPhone にも同じ予定が見える
- [ ] Meet の 2 トラック録音ができる
- [ ] ESP32 が届いた: IR でシーリングライトが消える / `Desk Occupied` `Bed Occupied` `Room Occupied` が HA に出て正しく動く
- [ ] `life-os/` リポジトリを初期化し、秘密情報が `.gitignore` されている

ここまで揃えば Phase 1 (声で照明 + 在室で消灯 + 机 / ベッド判定) に進める。
