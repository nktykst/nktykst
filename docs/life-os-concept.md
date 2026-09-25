# Life OS 構想 — Claude × ローカルLLM で生活を回す

> 対象ハード: デスクトップPC (RTX 5060 Ti 16GB / RAM 32GB)、マイク、スピーカー、部屋の照明、ESP32 + mmWave 人感センサー + ベッド圧力センサー (追加購入)
> Claude の利用形態: **Max サブスク (100 ドルプラン) の Claude Code を headless で使う。API 従量課金は使わない**
> 作成日: 2026-09-25 / 更新: 2026-09-25 v0.3 (在室検知を mmWave + 圧力センサーに変更、RuView は後回し) / 状態: 構想 (実装前)

---

## 0. 一枚で言うと

**「家の中に常駐する秘書」を、耳と口 (音声パイプライン) と手 (家電操作) と頭 (LLM) に分けて作る。**

- **頭は二段構え。** 速さ・プライバシー・回数が要る処理はローカルLLM、深く考える処理は Claude (Max サブスクの Claude Code を headless で呼ぶ)。
- **目もある。** mmWave 人感センサー (LD2450) とベッドの圧力センサーで「机にいる / ベッドにいる / 不在」を掴み、カメラなしで文脈を持つ。**サボってベッドにいたら注意する**のが第一の用途。
- **記憶は一つ。** 目標・タスク・冷蔵庫・会議ログ・在室ログを全部同じDBに入れ、どの機能も同じ記憶を見る。
- **入口は複数。** 音声 (部屋で話しかける)、チャット (スマホから)、定時 (朝のブリーフィング)、センサー (帰宅・起床を検知して動く)。
- **段階的に育てる。** まず「声で照明を消せる」から始め、機能を一つずつ足す。

やりたいこと 5 つ + 監視は、全部「同じ土台 + 専用のエージェント 1 つ」で実現できる。

| やりたいこと | エージェント名 | 主に使う頭 | 主な入力 | 主な出力 |
|---|---|---|---|---|
| 生活の計画・律してくれる存在 | Planner | Claude | 目標、カレンダー、タスク、昨日のログ、在室 / 睡眠ログ | 朝のブリーフィング、夜の振り返り、途中のリマインド |
| 自炊の管理 | Kitchen | Claude (+ 画像認識) | 冷蔵庫の写真 / 買い物の申告 | 今日の献立、買い物リスト |
| 英会話の練習 | Tutor | ローカル or Claude | 音声 | 音声 + セッション後の添削 |
| タスク管理 (会議録音含む) | Tasks | ローカル (文字起こし) → Claude (要約・抽出) | 会議録音、チャット、音声 | タスク一覧 (自分 / メンバー別) |
| 音声で会話・家電操作 | Home / Voice | ローカル | ウェイクワード + 音声 | スピーカー音声、照明操作 |
| 在室・ベッド滞在の監視 | Sense (mmWave + 圧力センサー) | ローカル (LLM 不要) | 人の座標 (ゾーン: 机 / ベッド)、ベッドの荷重 | at_desk / in_bed / away / 就寝 / 起床 の状態イベント |

---

## 1. 全体アーキテクチャ

```
                 ┌──────────────────────────── デスクトップ PC (RTX 5060 Ti 16GB) ────────────────────────────┐
                 │                                                                                             │
  マイク ──▶ [Wake word] ─▶ [VAD] ─▶ [STT: Whisper] ─▶┐                                                        │
                 │                                    │                                                        │
  スマホ/PC チャット (Discord Bot / Web UI)      ────┤                                                        │
                 │                                    │                                                        │
  ESP32 + mmWave / ベッド圧力センサー ─(ESPHome)─▶ [Home Assistant] ─▶ 状態イベント (机 / ベッド / 不在 / 就寝 / 起床) ─┤ │
                 │                                    ▼                                                        │
  スケジューラ (毎朝 7:00 など) ─────────────────▶ [Orchestrator / Router]                                      │
                 │                                    │  意図分類・どの頭に投げるか決める                       │
                 │                 ┌──────────────────┼────────────────────┐                                   │
                 │                 ▼                  ▼                    ▼                                   │
                 │        [ローカル LLM]     [Claude Code headless]    [エージェント群]                          │
                 │        Ollama / llama.cpp   `claude -p` + MCP      Planner / Kitchen / Tutor / Tasks / Home   │
                 │        (速い・無料・私的)   (Max サブスク枠で動く)   それぞれが tool を持つ                    │
                 │                 └──────────────────┬────────────────────┘                                   │
                 │                                    ▼                                                        │
                 │                          [Memory: SQLite + ベクトル索引]                                      │
                 │        goals / tasks / people / inventory / meetings / daily_log / presence_log / preferences │
                 │                                    │                                                        │
                 │                                    ▼                                                        │
                 │            [TTS] ──▶ スピーカー      [Home Assistant] ──▶ 照明・その他家電                    │
                 └─────────────────────────────────────────────────────────────────────────────────────────────┘
```

### 1.1 なぜ Home Assistant を挟むか

照明・スピーカー・センサーなどの「家電の抽象化」は自作せず、Home Assistant (以下 HA) に任せる。

- Hue / SwitchBot / Nature Remo / Matter など主要な照明はほぼ全部対応済み。
- REST / WebSocket API があるので、エージェントからは `light.turn_on(entity_id, brightness)` のような tool 一つで呼べる。
- HA 自身が音声パイプライン (Assist: openWakeWord + Whisper + Piper) を持っているので、**Phase 1 は自作せず HA の機能だけで「声で照明を操作」まで到達できる。**
- 後で人感センサー・温湿度計・カーテンなどを足しても、エージェント側の変更は tool の追加だけで済む。

### 1.2 なぜ「頭を二段構え」にするか

| 観点 | ローカル LLM (Ollama) | Claude (Max サブスクの Claude Code) |
|---|---|---|
| 得意 | 即答、短い指示の解釈、家電操作、分類、埋め込み | 長文の要約・計画、複数日をまたぐ推論、画像理解、英語の自然さ |
| 遅延 | 短い (0.3〜1 秒で最初の音が出せる) | 長い (プロセス起動 + 思考で数秒〜数十秒)。対話向きではなく、ジョブ向き |
| コスト | 電気代のみ | 月額固定。ただし **5 時間ごとの利用枠と週次上限**があり、回数は有限 |
| プライバシー | 音声・会議録音を外に出さない | テキスト化した後の内容のみ送る |
| 弱点 | 16GB VRAM で動く 8〜14B クラスは長文の要約や複雑な計画で精度が落ちる | ネット依存、枠を使い切ると数時間止まる |

**ルーティングの原則:**

1. 音声 → テキスト (STT)、テキスト → 音声 (TTS) は**常にローカル**。生の音声は外に出さない。
2. 「電気消して」「今日の予定は」のような**短い定型意図はローカル LLM** で即答。
3. **計画・要約・献立・添削は Claude**。ここが体験の質を決めるので妥協しない。ただし 1 日の呼び出しは「数回〜十数回の重いジョブ」に絞り、サブスク枠を温存する。
4. 英会話は**ローカルが本体**。会話ログの添削だけ Claude に回す (1 日 1 回)。Claude はターン単位の対話に向かない (遅延と枠の両面)。
5. Claude の枠切れ (rate limit) を検知したら、そのジョブはローカル LLM で代替するか次の枠まで待つ。**止まらない設計**にする。

### 1.3 在室検知は mmWave + 圧力センサーで (RuView は後回し)

一番欲しいのは「**作業時間にベッドにいたら注意する**」こと。そのために必要なのは「机にいる / ベッドにいる / 不在」の 3 状態だけで、姿勢や心拍は要らない。

| 手段 | ベッドと机の区別 | 手間 | 判断 |
|---|---|---|---|
| **mmWave 人感センサー LD2450** (ESP32 + ESPHome) | できる。人の座標 (x, y) が取れるので 1 台でゾーン (机 / ベッド) を判定 | 小。ESPHome が標準対応、HA コミュニティで枯れている | **採用** |
| **ベッド圧力センサー** (FSR または荷重センサー + ESP32) | ベッドは確実。机は不明 | 小 | **採用** (mmWave の誤検知を潰す保険) |
| USB カメラ + ローカル物体検出 | できる (人の bbox × ベッド領域) | 中。VRAM 1GB 弱 | 保留。「寝転んでスマホ」まで区別したくなったら検討。映像は保存せず結果だけ残す前提 |
| [RuView](https://github.com/ruvnet/RuView) (WiFi CSI) | 同じ部屋で 1 台では難しい。ベータ | 大 | **後回し**。転倒検知・呼吸数が欲しくなった時に Phase 6 で試す |

**LD2450 の使い方:**
- 部屋の壁 (机とベッドの両方が視野に入る位置、高さ 1〜1.5 m) に 1 台。検知範囲は約 6 m、最大 3 人の座標を同時に返す。8.5 畳 (約 3.5 m × 4 m) なら 1 台で部屋全体が入る。
- ESPHome の設定でゾーンを矩形で切る: `zone_desk`、`zone_bed`。HA には「机に人がいる」「ベッドに人がいる」「部屋に人がいる」の 3 つのバイナリセンサーとして出る。
- 静止している人も検知できる (PIR との違い)。ただし壁や金属で反射するので、設置後に 1 週間ほどゾーンの境界を調整する。

**圧力センサーの使い方:**
- マットレスの下に FSR (感圧抵抗) を 1〜2 枚、または脚の下に荷重センサー (HX711 + ロードセル)。ESP32 のアナログ入力で読む。
- 「ベッドに荷重あり」の単純な ON / OFF。mmWave の `zone_bed` と AND を取ると誤検知がほぼ消える。

**部品はどれも ESP32 に載る**ので、IR リモコン (照明) と同じ工作の延長で作れる。

---

## 2. 技術スタック (推奨構成)

### 2.1 GPU / VRAM 配分 (16GB)

同時に常駐させるものの合計を 16GB 以内に収める。

| 常駐プロセス | モデル例 | VRAM 目安 |
|---|---|---|
| ローカル LLM | Qwen3 14B (Q4_K_M) または Gemma 3 12B (Q4) | 9〜10 GB (KV キャッシュ込み) |
| STT | faster-whisper `large-v3-turbo` (fp16) | 1.5〜2 GB |
| 話者分離 (会議時のみ) | pyannote.audio 3.x | 1 GB |
| TTS | Kokoro (英語) / VOICEVOX or Style-Bert-VITS2 (日本語) | 0.5〜2 GB |
| 埋め込み | bge-m3 or multilingual-e5 (CPU でも可) | 0〜1 GB |
| **合計** | | **13〜15 GB** |

補足:
- 会議の文字起こしはリアルタイムでなくて良いので、その間だけ LLM を降ろして Whisper + pyannote に VRAM を回す運用でも良い。
- 8B クラス (Qwen3 8B / Llama 3.x 8B) に落とせば余裕が増える。用途はルーティングと即答なので 8B でも十分な可能性が高い。**Phase 0 で両方試して決める。**
- モデル名は 2026 年 9 月時点の候補。着手時に Ollama のライブラリで最新の同クラスを確認する。

### 2.2 ソフトウェア一覧

| 役割 | 選定 | 理由 |
|---|---|---|
| ローカル LLM サーバ | **Ollama** (裏は llama.cpp) | 導入が最も楽。OpenAI 互換 API + tool calling 対応 |
| Claude | **Claude Code CLI を headless 実行** (`claude -p`)。認証は `claude setup-token` の長期トークン | Max サブスクの枠で動く。API キー不要。tool は MCP サーバー経由で渡す (5 章参照) |
| 家電ハブ | **Home Assistant** (Docker) | 1.1 参照 |
| 照明操作 | **ESPHome** (ESP32 + IR LED) で赤外線リモコンを再現 | 既存のシーリングライトをそのまま使う。HA にネイティブ統合 |
| 在室検知 | **LD2450 mmWave** + **ベッド圧力センサー** (ESP32 + ESPHome) | 1.3 参照。机 / ベッド / 不在をゾーンで判定。HA にネイティブ統合 |
| (後回し) 空間センシング | RuView + Mosquitto (MQTT) | 転倒検知・呼吸数が欲しくなったら Phase 6 で |
| カレンダー | **Google Calendar** (API + HA 統合) | Meet の予定 = 会議録音のトリガー |
| ウェイクワード | **openWakeWord** (HA の Wyoming 経由) | 軽量、カスタムワード学習可 |
| VAD | **Silero VAD** | 発話区間の切り出し。英会話の応答速度に直結 |
| STT | **faster-whisper** `large-v3-turbo` | 日英両対応で速い。HA の Wyoming Whisper でも同じもの |
| TTS (英語) | **Kokoro-82M** | 軽くて自然。英会話向き |
| TTS (日本語) | **VOICEVOX** または **Style-Bert-VITS2** | 日本語品質は Piper より上。VOICEVOX は HA 連携の実績あり |
| 記憶 DB | **SQLite** + `sqlite-vec` (ベクトル検索) | 一人用なら十分。バックアップがファイルコピーで済む |
| オーケストレータ | **Python (uv)** + FastAPI | 既存スキルと揃える。HA / Ollama / Claude を全部ここから叩く |
| スケジューラ | APScheduler (アプリ内) or systemd timer | 朝のブリーフィング、週次レビュー |
| チャット入口 | **Discord Bot** (`discord.py`) | スマホから外出先でも使える。写真も送れる (冷蔵庫の中身)。チャンネル分けとボイスチャンネルが利点 |
| 会議録音 | **PipeWire** (`pw-record` / ffmpeg) で Meet の音声 + マイクを 2 トラック | Linux ネイティブなので loopback が簡単 |

### 2.3 OS と周辺機器 (決定済み)

- **PC は Linux。** GPU・音声 I/O・Docker が全部一つの環境で済む。音声は PipeWire 経由で扱う。音声サテライト (別筐体) は不要。マイクはデスクのみ。
- **照明はスマート家電ではない → 赤外線リモコンを ESP32 + IR LED で代替する** (ESPHome)。mmWave / 圧力センサーと同じ ESP32 + ESPHome なので工作の知見が共通する。時間を買うなら SwitchBot Hub Mini / Nature Remo (5,000 円前後、HA 対応) という逃げ道もある。
- **スピーカーは Dell モニター内蔵で開始** (HDMI / DP 経由の音声出力)。ただし英会話の barge-in には「マイクがスピーカーの音を拾わない」ことが必要なので、**エコーキャンセル内蔵の USB スピーカーフォン** (Jabra Speak / Anker PowerConf 系、1〜2 万円) を Phase 5 までに用意するのが確実。それまでは PipeWire のエコーキャンセルモジュールで凌ぐ。
- **会議は Google Meet。** ブラウザの出力 (相手の声) とマイク (自分の声) を PipeWire で 2 トラック録音する。
- **カレンダーは Google Calendar に寄せる。** Meet の招待がそこに来るので会議録音のトリガーにも使える。iPhone には Google アカウントを追加して同じ予定を表示する (iCloud カレンダーへの拘りなし、と確認済み)。
- **チャット入口は Discord。** 自分用サーバーを 1 つ作り、エージェントごとにチャンネルを分ける (`#planner` `#kitchen` `#tasks` `#tutor` `#home` `#log`)。Bot は PC 上の Python プロセス 1 つで、外部ホスティング不要。ボイスチャンネルがあるので、将来スマホから英会話することもできる。

---

## 3. 各エージェントの設計

### 3.1 Planner — 生活の計画と「律してくれる存在」

**役割:** 目標を預かり、日々の予定とタスクに落とし、進捗を見て声をかける。

**記憶:**
- `goals` — 目標 (期限、なぜやるか、達成の定義、優先度)。**本人が定期的に入れる**。週次レビューで更新。
- `daily_log` — 起床 / 就寝、その日やったこと、気分、自由記述。夜の振り返りで音声入力。
- `habits` — 継続したい行動 (運動、読書、早寝など) と実績。

**動作 (定時ジョブ):**

| 時刻 | 何をするか | 頭 |
|---|---|---|
| 朝 (起床後) | カレンダー + タスク + 目標 + 昨日のログを読んで「今日の 3 つ」と時間割を提案。スピーカーで読み上げ | Claude |
| 日中 | 予定の 10 分前、集中ブロックの開始 / 終了を声で通知。「今何やるんだっけ」に答える | ローカル |
| 夜 | 「今日どうだった？」と聞き、答えを `daily_log` に記録。明日の下準備 | 音声はローカル、要約は Claude |
| 週末 | 一週間のログと目標を照らして振り返り。目標の見直しを促す | Claude |

**律し方の設計方針:**
- ただ通知を出すのではなく、**本人の回答を待って記録する**形にする (受け身の通知は無視されるが、質問には答える)。
- 叱らない。「やらなかった理由」を一言聞いて、翌日の計画に反映する。
- 強さは設定可能にする (`preferences.nudge_level`)。

**サボり検知のエスカレーション (Sense の `in_bed_during_work` を受けて):**

| 経過 | 行動 | 本人の応答があった場合 |
|---|---|---|
| 0 分 | 何もしない (ベッドに座って本を読むこともある) | |
| 15 分 | スピーカーで一言。「ベッドにいるね。休憩なら何分にする？」 | 「30 分寝る」→ `declare_break(30)`。30 分は黙り、終了時に一声かける |
| 25 分 | もう一度、少し強く。「予定では今『論文の続き』の時間だよ」。Discord `#planner` にも送る | 「今日は体調悪い」→ その日の nudge を止め、`daily_log` に記録 |
| 40 分 | 照明を focus シーン (昼白色 100%) にする。「起きてから 5 分だけやろう」 | 机に戻る (`at_desk`) → 「おかえり」だけ言って終了。戻った事実を `presence_log` に残す |
| 60 分〜 | 20 分ごとに繰り返す。週次レビューで「作業時間帯のベッド滞在」として合計を出す | |

- **除外条件:** 就寝時間帯、宣言済みの休憩、来客中 (`guests_present`)、体調不良の申告日、休日設定。
- **強さは `preferences.nudge_level` で 3 段階** (穏やか / 普通 / 厳しめ)。厳しめは 15 分の時点で照明も変える。
- 叱責はしない。「戻れたら勝ち」の扱いで、戻った回数を週次レビューで褒める側に計上する。
- 作業時間帯 (例: 平日 9:00〜18:00) は `preferences.work_hours` に持ち、カレンダーの予定 (授業、実習) で上書きされる。

**必要な tool:** `read_calendar`, `list_tasks`, `list_goals`, `write_daily_log`, `speak(text)`, `schedule_reminder`, `declare_break`, `set_light_scene`.

### 3.2 Kitchen — 自炊の管理

**役割:** 冷蔵庫の中身を把握し、栄養バランスを見て献立と買い物を提案する。

**在庫の把握方法 (現実的な順):**
1. **写真** — Discord の `#kitchen` に冷蔵庫の写真を送る → Claude (画像対応) が品目と概量を JSON で抽出 → `inventory` に反映。週 1〜2 回で十分。
2. **買い物申告** — レシートの写真、または「豚肉と玉ねぎ買った」と声で言う → ローカル LLM で品目抽出。
3. **消費申告** — 献立を「作った」とマークすると、使った食材を自動で減らす。

完璧な在庫管理は目指さない。**「だいたい何があるか」+「賞味期限が近そうなもの」** が分かれば献立提案には足りる。

**献立提案:**
- 入力: 在庫、直近 7 日の献立履歴、好み / NG 食材、今日の予定 (帰宅時間)、栄養の偏り。
- 出力: 今日の献立 (主菜 / 副菜 / 汁物)、調理時間、使う在庫、足りないものがあれば買い足しリスト。
- 栄養は「日本食品標準成分表」ベースの簡易計算 + Claude の推定で**傾向を見る**程度に留める (厳密な計算は続かない)。

**買い物リスト (週次):**
- 在庫 + 来週の予定 + 直近 2 週間の栄養の偏り (野菜不足、タンパク質不足など) から Claude が生成。
- Discord `#kitchen` にチェックリストとして送り、店で消し込めるようにする。

**必要な tool:** `get_inventory`, `update_inventory`, `log_meal`, `get_meal_history`, `send_checklist`.

### 3.3 Tutor — 英会話の練習

**役割:** 話し相手になり、後で直してくれる先生。

**遅延との戦い (ここが体験の全て):**

```
 発話終了 ─(Silero VAD 0.3s)─▶ Whisper turbo (0.3〜0.6s) ─▶ LLM 最初のトークン (ローカル 0.2s / Claude 0.8〜1.5s)
          ─▶ 文の区切りごとに TTS (Kokoro 0.1〜0.3s) ─▶ スピーカー
```

- LLM の出力を**文単位で切って逐次 TTS** に流す。全部生成してから話すと 3〜5 秒待たされる。
- 目標は「発話終了から 1.5 秒以内に相手が話し始める」。ローカル LLM なら達成しやすい。
- 相手が話している最中に割り込めるよう、マイクを聞き続けて VAD が反応したら再生を止める (barge-in)。

**モード:**
- **Free talk** — 今日あったことを英語で話す。相手は短く返し、質問で続ける。
- **Roleplay** — 「レストランで注文」「面接」「論文の口頭説明」など場面指定。
- **Shadowing / 発音** — TTS の音声を真似て録音し、Whisper の認識結果と原文を比較。

**セッション後の添削 (Claude):**
- 会話ログ全体を Claude に渡し、「言いたかったであろうこと」「より自然な言い方」「繰り返している癖」を日本語で返す。
- 添削結果を `vocab` / `mistakes` に保存し、次回の会話でさりげなく同じ表現を使う機会を作る。

**本人の現在地と目標 (決定済み):**

| 項目 | 現状 |
|---|---|
| 読む・聴く | TOEIC 820、TOEFL ITP 540、共通テスト R 100 / L 90。おおむね CEFR B2 |
| 話す | ほぼ話せない。おおむね A2 以下 |
| 目標 | 外資系企業への就職で使えるレベル (面接、日常業務、議論)。目安 CEFR B2〜C1 の発話 |
| 期限 | 医学部 2 年なので 3〜4 年の猶予。**毎日 10〜15 分を止めないこと**が最重要 |

「読み聴きは B2、発話は A2」という非対称な状態なので、**インプットは足りている。足りないのは口を動かした回数**。Tutor はそこに全振りする。

**カリキュラム (3 段階):**

| 段階 | 期間の目安 | 毎日やること (10〜15 分) | 添削の方針 | 卒業条件 |
|---|---|---|---|---|
| **A. 口を慣らす** | 最初の 2〜3 ヶ月 | Shadowing 3 分 + Free talk 7 分 (今日のこと、読んだ記事の内容)。会話中は**直さない**。相手は短く返し、質問で繋ぐ | 週 1 回だけ「繰り返す癖トップ 3」を返す。細かい文法は無視 | 3 分間止まらずに話せる。発話速度 (語/分) が安定して伸びる |
| **B. 型を作る** | 次の 6 ヶ月 | 「説明する」「意見を言う」「比較する」の型練習。Roleplay: 自己紹介、研究や授業内容の説明、雑談 (small talk)、断る・依頼する | 会話後に「より自然な言い方」を 5 個まで。翌日の会話で同じ場面を再現して使わせる | 初対面の相手に 5 分間、自分と関心事を説明できる |
| **C. 仕事で使う** | その後、継続 | 模擬面接 (behavioral: "Tell me about a time when…")、ケース議論、プレゼン + Q&A、会議での割り込み・要約。**医学の知識を英語で説明する**練習は差別化になるので混ぜる | 内容の論理と表現の両方。月 1 回、面接形式の「模擬試験」を Claude が採点 | 30 分の英語面接を最後まで受け答えできる |

**計測 (Tutor が自動で記録し、週次レビューに載せる):**
- 発話速度 (語/分)、1 発話あたりの語数、フィラー (um / like) の割合、自己訂正の回数
- 語彙の広さ (直近 30 日でユニークに使った語数)
- 月 1 回、Claude が会話ログから CEFR 相当の発話レベルを推定して記録 (絶対値ではなく推移を見る)

**設計上の工夫:**
- 読む力が強いので、**記事を読んでから話す** (read-then-talk) を基本形にする。話題探しに困らず、語彙が受動から能動に移る。
- ローカル LLM の英語の相手役は「短く返し、質問で繋ぐ」を system prompt で固定する。長文で返されると本人が話す時間が減る。
- 段階 A の間は「直さない」を徹底する。話す前に正しさを気にする癖を消すのが先。

**必要な tool:** `start_session(mode)`, `end_session`, `save_review`, `get_recent_mistakes`.

### 3.4 Tasks — タスク管理 (会議録音 → 要約 → タスク抽出)

**役割:** 自分とチームメンバーのタスクを一箇所で把握する。

**会議パイプライン:**

```
 録音 (2 トラック: 自分のマイク + 相手側音声)
   ─▶ faster-whisper で文字起こし (ローカル / 日英混在 OK)
   ─▶ pyannote で話者分離、`people` テーブルの声紋と照合して名前を付ける
   ─▶ Claude: 要約 + 決定事項 + タスク抽出 (structured outputs で JSON 固定)
        { "summary": ..., "decisions": [...],
          "tasks": [ { "title": ..., "owner": "田中", "due": "2026-10-03", "source_quote": "..." } ] }
   ─▶ 本人が Discord `#tasks` / Web UI で確認・修正してから `tasks` に登録 (自動登録はしない)
```

- **生の音声は PC の外に出さない。** Claude に送るのはテキスト化した後の内容だけ。
- 会議相手の録音は**事前に同意を取る**。文字起こしの保存期間も決めておく (例: 90 日で削除)。
- 話者分離の精度は期待しすぎない。「誰のタスクか」は Claude が文脈 (「田中さんお願いします」) からも推定し、本人が最終確認する。

**会議以外のタスク入力:**
- 声: 「田中さんに来週の資料を依頼した、金曜まで」 → ローカル LLM が構造化 → 登録。
- チャット: Discord `#tasks` に一行送る。
- 既存ツール (GitHub Issues / Notion / Todoist など) を使っているなら、そこを正とし、片方向または双方向で同期する。**まずは SQLite を正にして、必要になったら同期を足す。**

**メンバー別ビュー:**
- `tasks.owner` でグループ化。「田中さんに頼んでるものは？」で一覧。
- 期限が近いのに動きがないものを、朝のブリーフィングで「今日フォローすべき人」として出す。

**必要な tool:** `transcribe(audio_path)`, `extract_tasks(transcript)`, `create_task`, `update_task`, `list_tasks(owner, status)`, `list_people`.

### 3.5 Home / Voice — 音声会話と家電操作

**役割:** 全てのエージェントへの音声入口。かつ照明などの直接操作。

**パイプライン:**
1. ウェイクワード検出 (openWakeWord。カスタム名を学習させると誤反応が減る)
2. VAD で発話区間を切る
3. Whisper で文字起こし
4. **意図分類 (ローカル LLM、1 回の短い呼び出し):** `home_control` / `quick_qa` / `planner` / `kitchen` / `tutor` / `tasks` / `chitchat`
5. 該当エージェントに渡す。`home_control` なら HA の tool を直接呼んで即答
6. 返答を TTS → スピーカー

**照明操作の tool (HA 経由):**
- `light.turn_on / turn_off / set_brightness / set_color_temp`
- シーン: `scene.focus` (昼白色 100%)、`scene.relax` (電球色 30%)、`scene.sleep` (消灯)
- Planner と連動: 集中ブロック開始で focus、夜の振り返り開始で relax、就寝ログで sleep。

**スピーカー:**
- PC 直結 (USB / 3.5mm / Bluetooth) が最も簡単で、TTS 音声をそのまま流せる。
- Google Nest / Amazon Echo のようなスマートスピーカーは**外から任意音声を流すのが難しい** (HA のキャスト機能で一部可能)。手持ちがそれなら確認が必要。

### 3.6 Sense — mmWave + 圧力センサーによる在室・ベッド滞在の把握

**役割:** LLM を使わない「反射神経」。センサーの状態変化をイベントに変換し、他のエージェントに文脈を渡す。

**入力 (HA のバイナリセンサー):**
- `binary_sensor.desk_occupied` — LD2450 の `zone_desk`
- `binary_sensor.bed_occupied` — LD2450 の `zone_bed` AND 圧力センサー
- `binary_sensor.room_occupied` — LD2450 の全体
- `sensor.person_count` — LD2450 のターゲット数

**イベント化のルール (HA のオートメーション or Orchestrator 内):**

| 状態遷移 | 発行するイベント | 直後に起きること |
|---|---|---|
| desk_occupied: false → true | `at_desk` | 集中ブロック中なら照明を focus に。nudge 停止 |
| bed_occupied: false → true (作業時間帯、宣言した休憩なし) | `in_bed_during_work` | **サボり検知のエスカレーション開始** (3.1 参照) |
| bed_occupied: false → true (就寝時間帯) | `fell_asleep` (20 分継続で確定) | `daily_log.sleep` を記録、照明オフ |
| bed_occupied: true → false (朝、20 分以上戻らない) | `woke_up` | `daily_log.wake` を記録。10 分後に朝のブリーフィングを流す |
| room_occupied: false → true (夕方以降) | `came_home` | 照明を relax シーンに。Planner が「おかえり、今日残ってるのは 2 つ」と一言 |
| room_occupied: true → false (10 分継続) | `left_home` | 照明オフ、nudge 停止 |
| desk_occupied が 90 分連続 true | `sedentary` | 「立ち上がりませんか」の一言 (nudge_level に従う) |
| person_count ≥ 2 | `guests_present` | 音声の自発発話を全て止める |

**設計方針:**
- イベントは全て `presence_log` に残し、Planner の週次レビューで「机にいた時間」「作業時間帯のベッド滞在回数」「就寝 / 起床のばらつき」として振り返りに使う。
- 誤検知前提で**デバウンス**を入れる (状態が N 分続いてから発火)。特に `left_home` と `fell_asleep`。
- Claude には生のセンサー値を渡さない。渡すのは日次で集計した「机 6.5 時間、ベッド滞在 (作業時間帯) 2 回 計 40 分、就寝 0:40、起床 7:10」程度の要約だけ。

**必要な tool:** `get_presence_state()`, `get_sleep_summary(date)`, `get_presence_log(from, to)`, `declare_break(minutes)`.

---

## 4. 記憶 (Memory) の設計

一つの SQLite ファイルに全部入れる。主要テーブル:

| テーブル | 主な列 | 誰が書くか |
|---|---|---|
| `goals` | title, why, definition_of_done, due, priority, status | 本人 (週次) |
| `tasks` | title, owner, due, status, source (meeting/voice/chat), source_ref, project | Tasks, Planner |
| `people` | name, role, voice_embedding, notes | 本人、Tasks |
| `meetings` | date, participants, transcript_path, summary, decisions | Tasks |
| `daily_log` | date, wake, sleep, mood, notes, done | Planner (wake / sleep は Sense が自動記入) |
| `presence_log` | ts, event (at_desk / in_bed_during_work / fell_asleep / woke_up / came_home / left_home / sedentary ...), raw_state, escalation_step | Sense |
| `habits` / `habit_log` | name, target, date, done | Planner |
| `inventory` | item, qty, unit, added_at, expires_at | Kitchen |
| `meals` | date, dishes, ingredients_used, rating | Kitchen |
| `tutor_sessions` / `mistakes` / `vocab` | transcript, review, phrase, correction, seen_count | Tutor |
| `preferences` | key, value (nudge_level, ng_foods, voice, wake_word ...) | 本人 |
| `memory_notes` | text, embedding, created_at, source | 全エージェント (自由記述の長期記憶) |

**方針:**
- 各エージェントには「必要なテーブルだけ読める tool」を渡す。全部渡すと Claude のコンテキストが無駄に太る。
- 長期の自由記述は `memory_notes` に埋め込み付きで入れ、質問時にベクトル検索で数件だけ引く。
- バックアップは日次で別ドライブ / クラウドにファイルコピー。

---

## 5. Claude の使い方 (Max サブスク前提)

### 5.1 何が使えて何が使えないか (2026-09 時点の公式ドキュメントで確認)

| 手段 | Max サブスクで使えるか | 備考 |
|---|---|---|
| **Claude Code CLI の headless 実行** (`claude -p "..."`) | **使える** | `claude.ai` アカウントでログインした認証がそのまま使われ、消費はサブスク枠にカウントされる。自動化用に `claude setup-token` で 1 年有効のトークンを発行し `CLAUDE_CODE_OAUTH_TOKEN` に入れる |
| Claude Code の MCP 接続 | 使える | `.mcp.json` でローカルの MCP サーバー (stdio) を登録すれば、`-p` 実行時も tool として呼ばれる |
| Claude Code の Routines (クラウド定時実行) | 使える (Pro / Max 対象) | Anthropic 側で動くので PC が落ちていても動くが、**家の中の機器や DB には届かない**。今回は使わない |
| Claude Desktop の scheduled tasks | 使える | Desktop アプリが起動している間だけ。ローカル cron の代替候補 |
| **Anthropic Python SDK / Agent SDK** | **使えない (API キーが必要)** | サブスクの OAuth を SDK で使うのは不可。Max 向けの Agent SDK クレジット制度は 2026-06 時点で「一時停止」と案内されている。着手時に再確認 |
| Claude Tag (Slack 常駐) | 使えない (Team / Enterprise のみ) | スマホからの入口は自前の Discord Bot で作る |

参照: code.claude.com/docs/en/authentication, /scheduled-tasks, /routines, /desktop-scheduled-tasks, support.claude.com「What is the Max plan」「Use the Claude Agent SDK with your Claude plan」

**結論: Claude は「Claude Code を headless で起動するジョブ」として使う。** SDK でエージェントループを書く代わりに、Claude Code 自体をエージェントハーネスとして使い、Life OS の記憶と家電操作を **MCP サーバー**として Claude Code に渡す。

### 5.2 実装方式

```
 Orchestrator (Python)
   └─ subprocess: claude -p "<ジョブのプロンプト>" \
         --output-format json \
         --mcp-config config/mcp.json \          # lifeos-memory, home-assistant, ruview
         --allowedTools "mcp__lifeos__*,mcp__ha__light_*" \
         --append-system-prompt "$(cat config/prompts/planner.md)"
   └─ 返ってきた JSON (result, session_id, usage) を保存
```

- **Life OS 側は MCP サーバーを 1 つ書く** (`src/lifeos/mcp_server.py`)。`list_tasks`, `write_daily_log`, `get_inventory`, `get_sleep_summary`, `speak` などを tool として公開する。ローカル LLM からも同じ tool を Ollama の tool calling で呼べるように、実体は共通の Python 関数にする。
- **HA は既製の MCP** を繋ぐ (公式の MCP Server 統合)。センサーの状態も HA 経由で読める。
- **エージェント = プロンプトファイル + 許可 tool の組**。Planner / Kitchen / Tasks / Tutor(添削) はそれぞれ `config/prompts/*.md` と `--allowedTools` の違いだけ。
- **多ターンの相談**は `--resume <session_id>` で続ける。長期記憶は MCP 経由で SQLite に置くので、セッションが切れても困らない。
- **画像入力** (冷蔵庫の写真) はファイルパスをプロンプトに書いて Claude Code に読ませる。
- **モデル**は `--model` で指定。デフォルトで良いが、添削やチャット相談のような軽い用途は Sonnet を指定して枠を節約する選択肢がある (Max では Opus も使える)。
- Claude Code (この環境) は引き続き「Life OS 自体の開発」にも使う。開発と運用で同じ枠を消費することに注意。

### 5.3 サブスク枠の使い方 (費用ではなく「回数」の予算)

Max 5x は「5 時間ごとの利用枠」と「週次上限」があり、正確な回数は公開されていない。**重いジョブを 1 日 10 回前後**に抑える設計にする。

| 用途 | 頻度 | 重さ | 備考 |
|---|---|---|---|
| 朝ブリーフィング | 1 回/日 | 中 | 起床検知 (ベッドセンサー) をトリガーに |
| 夜の振り返りの要約 | 1 回/日 | 小 | 音声のやりとり自体はローカル |
| 週次レビュー | 1 回/週 | 大 | 週末の枠が空いている時間に |
| 会議の要約・タスク抽出 | 会議ごと (週 3〜5) | 中〜大 | 文字起こしはローカル。長い会議は分割 |
| 献立 + 買い物リスト | 1 回/日 + 1 回/週 | 小 | 在庫が変わらない日はスキップ |
| 冷蔵庫の写真解析 | 1〜2 回/週 | 小 | |
| 英会話の添削 | 1 回/日 | 小〜中 | 会話本体はローカル |
| チャットでの相談 | 都度 | 小 | ローカル LLM で答えられるものは Claude に回さない |
| **英会話の本体、家電操作、雑談、意図分類** | | | **Claude には投げない** (ローカル) |

- Claude Code が rate limit を返したら Orchestrator が検知し、`retry_after` まで待つか、そのジョブをローカル LLM で「簡易版」として実行する。朝のブリーフィングが 1 日抜けるより、簡易版でも出る方が良い。
- 開発でこの環境 (Claude Code on the web) を使う時間帯は運用ジョブと枠を食い合うので、運用ジョブは早朝 / 深夜に寄せる。
- 使用量は `--output-format json` の `usage` を `claude_usage` テーブルに残し、週次レビューで「今週 Claude を何回呼んだか」も見えるようにする。

---

## 6. ロードマップ

「一つ動くものを作って、そこに機能を足す」順で並べる。各 Phase は 1〜2 週間の想定。

### Phase 0 — 土台 (まずここだけやる)
- [x] OS の決定 → Linux (決定済み)
- [ ] Ollama を入れ、Qwen3 14B と 8B を試して速度と賢さを比較
- [ ] 買い物: ESP32 ×2〜3、LD2450 mmWave センサー、FSR (圧力センサー)、IR LED / IR 受信モジュール、(後で) USB スピーカーフォン
- [ ] Home Assistant を Docker で立て、照明を登録し、ブラウザから点灯 / 消灯
- [ ] faster-whisper と TTS (Kokoro + VOICEVOX) を単体で動かし、マイク → 文字、文字 → スピーカーを確認
- [ ] Claude Code をこの PC に入れ、`claude setup-token` で長期トークンを発行。`claude -p "hello" --output-format json` が通ることを確認
- [ ] リポジトリ作成 (`life-os/`)、uv でプロジェクト初期化、SQLite スキーマ v0

**完了条件:** 各部品が単体で動く。

### Phase 1 — 声で照明が操作できる + 在室が分かる
- [ ] HA Assist (openWakeWord + Whisper + TTS) で「電気消して」が通る
- [ ] Orchestrator (FastAPI) を作り、HA の会話エージェントとして登録。意図分類をローカル LLM で行う
- [ ] Discord Bot を繋ぎ、同じ Orchestrator にテキストでも話せる
- [ ] LD2450 を ESPHome で HA に繋ぎ、`zone_desk` / `zone_bed` のバイナリセンサーが出るまで。1 週間ゾーン境界を調整
- [ ] ベッドの圧力センサーを ESP32 に繋ぎ、`bed_occupied` が mmWave AND 圧力で出る
- [ ] 「不在 10 分で消灯」の HA オートメーション

**完了条件:** 部屋で話しかけると照明が変わり、スマホからも同じことができ、部屋を出ると勝手に消え、HA の画面で「机 / ベッド / 不在」が正しく出る。

### Phase 2 — Planner + Tasks (テキストから)
- [ ] Life OS の MCP サーバー v0 (`list_tasks`, `list_goals`, `write_daily_log`, `speak`) を書き、`claude -p --mcp-config` から呼べることを確認
- [ ] `goals` / `tasks` / `daily_log` / `presence_log` のテーブルと tool
- [ ] Claude Code による朝のブリーフィング (まずは Discord にテキストで、次にスピーカー読み上げ)。トリガーは cron → ベッドセンサーの `woke_up` へ
- [ ] 夜の振り返りを音声で記録。就寝・起床は `bed_occupied` から自動記入
- [ ] **サボり検知のエスカレーション** (`in_bed_during_work` → 15 / 25 / 40 分の段階)。`declare_break` で黙らせられること
- [ ] rate limit 時のフォールバック (ローカル LLM で簡易版) と `claude_usage` の記録
- [ ] 声・チャットからのタスク登録、メンバー別一覧
- [ ] 週次レビュー

**完了条件:** 一週間、毎朝の提案と毎晩の記録が回る。

### Phase 3 — 会議録音パイプライン
- [ ] 2 トラック録音の仕組み
- [ ] Whisper + pyannote → 文字起こし + 話者ラベル
- [ ] Claude で要約 + タスク抽出 (structured outputs) → 確認 UI → `tasks` 登録
- [ ] 保存期間ポリシー、同意の運用

**完了条件:** 会議のあと 10 分以内に、要約とタスク候補が Discord `#tasks` に届く。

### Phase 4 — Kitchen
- [ ] 冷蔵庫の写真 → `inventory`
- [ ] 献立提案 (在庫 + 履歴 + 好み)、作ったら在庫を減らす
- [ ] 週次の買い物リスト + 栄養の偏りコメント

**完了条件:** 二週間、献立と買い物をこれで回してみて不便を洗い出す。

### Phase 5 — Tutor
- [ ] VAD → Whisper → ローカル LLM → 文単位 TTS の低遅延ループ、barge-in
- [ ] Free talk / Roleplay モード
- [ ] セッション後の Claude 添削、`mistakes` の蓄積と再出題

**完了条件:** 発話終了から 1.5 秒以内に相手が返す。10 分続けてストレスがない。

### Phase 6 — 磨き込み
- [ ] Planner と照明シーンの連動 (集中 / リラックス / 就寝)
- [ ] Sense のイベントを増やす: `sedentary`、`guests_present`、`distress` → 安否確認フロー
- [ ] (任意) RuView を試す。転倒検知や夜間の呼吸数が欲しくなったら。Mosquitto + RuView server を compose に足す
- [ ] Claude 呼び出し回数とレイテンシの計測ダッシュボード
- [ ] 既存タスクツールとの同期 (必要なら)

---

## 7. リポジトリ構成案 (`life-os/`)

```
life-os/
├── pyproject.toml           # uv で管理
├── docker-compose.yml       # home-assistant, voicevox, (後で mosquitto, ruview)
├── config/
│   ├── esphome/             # ir-blaster.yaml, presence.yaml (LD2450 + 圧力センサー)
│   ├── prompts/             # エージェントごとの system prompt (planner.md, kitchen.md, ...)
│   ├── mcp.json             # Claude Code に渡す MCP サーバー一覧 (lifeos, home-assistant, ruview)
│   └── settings.toml        # モデル名、HA の URL、ウェイクワード、nudge_level など
├── src/lifeos/
│   ├── orchestrator/        # FastAPI、意図分類、ルーティング
│   ├── mcp_server.py        # Life OS の記憶・音声・家電を tool として公開 (Claude Code / ローカル LLM 共用)
│   ├── agents/
│   │   ├── planner.py
│   │   ├── kitchen.py
│   │   ├── tutor.py
│   │   ├── tasks.py
│   │   ├── home.py
│   │   └── sense.py         # センサーの状態遷移 → イベント (デバウンス、除外条件、エスカレーション)
│   ├── llm/
│   │   ├── local.py         # Ollama クライアント (tool calling 込み)
│   │   └── claude_code.py   # `claude -p` の subprocess ラッパ (JSON 解析、resume、rate limit 検知、usage 記録)
│   ├── voice/
│   │   ├── stt.py           # faster-whisper
│   │   ├── tts.py           # Kokoro / VOICEVOX
│   │   ├── vad.py           # Silero
│   │   └── pipeline.py      # 低遅延ループ、barge-in
│   ├── memory/
│   │   ├── schema.sql
│   │   └── store.py         # SQLite + sqlite-vec
│   ├── integrations/
│   │   ├── home_assistant.py
│   │   ├── telegram.py
│   │   └── calendar.py
│   └── jobs/                # 朝 / 夜 / 週次の定時ジョブ
├── scripts/                 # 録音、モデル取得、バックアップ
└── tests/
```

---

## 8. リスクと注意点

- **VRAM の取り合い。** LLM・Whisper・TTS・pyannote を同時に常駐させると 16GB ギリギリ。会議処理中は LLM を降ろす、8B に落とす、のどちらかで逃げる。
- **音声デバイスと WSL2。** 2.3 の通り。着手前に OS 構成を決める。
- **録音の同意とデータ保持。** 会議相手に説明し、生音声はローカルのみ、保存期間を決めて自動削除。
- **Claude Code の OAuth トークンの管理。** `claude setup-token` のトークンは環境変数か OS のキーチェーンに置く。リポジトリには絶対に入れない。1 年で失効するので更新日をカレンダーに入れる。
- **サブスク枠の枯渇。** 開発でも運用でも同じ枠を使う。運用ジョブが枠切れで落ちても、ローカル LLM の簡易版で動き続ける設計にする (5.3)。Agent SDK をサブスクで使える制度が再開されたら、SDK への移行を検討する。
- **センサーの誤検知。** mmWave は壁や金属の反射、扇風機などの動きで誤検知する。ベッド判定は圧力センサーと AND を取り、HA のオートメーションには必ずデバウンスを入れる。設置後 1 週間はログを見てゾーン境界を直す。
- **「律してくれる」が「うるさい」になる。** nudge_level を設定可能にし、Phase 2 で一週間使って調整する。センサーで不在・来客・就寝中を検知したら自発発話を止める。サボり検知は「戻れたら勝ち」の扱いにし、叱責しない。
- **最初から全部作らない。** Phase 1 で「声で照明」が動いた時点の満足感が、その後の継続を支える。

---

## 9. 決定事項と残りの確認

### 9.1 決定済み (2026-09-25)

| # | 項目 | 決定 | 設計への影響 |
|---|---|---|---|
| 0 | 在室検知 | 目的は「サボってベッドにいたら注意」。RuView は後回し | LD2450 mmWave + ベッド圧力センサー (1.3, 3.6)。エスカレーション設計 (3.1) |
| 1 | OS | Linux | 全部 1 環境。音声は PipeWire。サテライト不要 |
| 1b | 間取り | 1K、8.5 畳 | 机とベッドが同じ部屋なので LD2450 は 1 台。キッチンは別区画だが監視不要。マイクの音声はキッチンからでも届く距離 |
| 2 | 照明 | スマート家電ではない。赤外線リモコンを電子工作で代替 | ESPHome + ESP32 + IR LED (Phase 0 の買い物リスト参照) |
| 3 | スピーカー | Dell モニター内蔵で開始。必要なら購入 | 通知用はモニターで十分。英会話用にエコーキャンセル付き USB スピーカーフォンを Phase 5 までに |
| 4 | マイク | デスクのみ | ウェイクワード・VAD は PC 上で完結 |
| 5 | 会議 | Google Meet | PipeWire で 2 トラック録音。Google Calendar の予定をトリガーに |
| 6 | カレンダー | iPhone 標準を使っていたが拘りなし | Google Calendar に寄せる。iPhone には Google アカウントを追加 |
| 7 | チャット入口 | 楽なもの。Discord でも良い | Discord Bot。チャンネルをエージェント別に分ける |
| 8 | 英会話 | 医学部 2 年。外資系就職も選択肢にしたい。読み聴き B2、発話ほぼ不可 | 3.3 のカリキュラム (口を慣らす → 型を作る → 仕事で使う)。毎日 10〜15 分 |

### 9.2 まだ決めていないこと

1. **Claude Code をこの PC で使う頻度** — 開発と運用が同じサブスク枠を使うので、運用ジョブを早朝 / 深夜に寄せるか。
2. **Linux のディストリビューション** — Phase 0 の手順は Ubuntu / Debian 系で書いてある。違う場合はパッケージ名を読み替える。
3. **照明のリモコンの種類** — メーカーと型番。ESPHome で赤外線コードを学習させる際、既知のプロトコル (NEC など) なら楽。

Phase 0 の具体的な手順は [`phase0-setup.md`](./phase0-setup.md) に分けた。
