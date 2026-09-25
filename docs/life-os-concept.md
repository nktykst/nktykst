# Life OS 構想 — Claude × ローカルLLM で生活を回す

> 対象ハード: デスクトップPC (RTX 5060 Ti 16GB / RAM 32GB)、マイク、スピーカー、部屋の照明
> 作成日: 2026-09-25 / 状態: 構想 v0.1 (実装前)

---

## 0. 一枚で言うと

**「家の中に常駐する秘書」を、耳と口 (音声パイプライン) と手 (家電操作) と頭 (LLM) に分けて作る。**

- **頭は二段構え。** 速さ・プライバシー・コストが要る処理はローカルLLM、深く考える処理は Claude。
- **記憶は一つ。** 目標・タスク・冷蔵庫・会議ログを全部同じDBに入れ、どの機能も同じ記憶を見る。
- **入口は複数。** 音声 (部屋で話しかける)、チャット (スマホから)、定時 (朝のブリーフィング)。
- **段階的に育てる。** まず「声で照明を消せる」から始め、機能を一つずつ足す。

やりたいこと 5 つは、全部「同じ土台 + 専用のエージェント 1 つ」で実現できる。

| やりたいこと | エージェント名 | 主に使う頭 | 主な入力 | 主な出力 |
|---|---|---|---|---|
| 生活の計画・律してくれる存在 | Planner | Claude | 目標、カレンダー、タスク、昨日のログ | 朝のブリーフィング、夜の振り返り、途中のリマインド |
| 自炊の管理 | Kitchen | Claude (+ 画像認識) | 冷蔵庫の写真 / 買い物の申告 | 今日の献立、買い物リスト |
| 英会話の練習 | Tutor | ローカル or Claude | 音声 | 音声 + セッション後の添削 |
| タスク管理 (会議録音含む) | Tasks | ローカル (文字起こし) → Claude (要約・抽出) | 会議録音、チャット、音声 | タスク一覧 (自分 / メンバー別) |
| 音声で会話・家電操作 | Home / Voice | ローカル | ウェイクワード + 音声 | スピーカー音声、照明操作 |

---

## 1. 全体アーキテクチャ

```
                 ┌──────────────────────────── デスクトップ PC (RTX 5060 Ti 16GB) ────────────────────────────┐
                 │                                                                                             │
  マイク ──▶ [Wake word] ─▶ [VAD] ─▶ [STT: Whisper] ─▶┐                                                        │
                 │                                    │                                                        │
  スマホ/PC チャット (Telegram / Discord / Web UI) ────┤                                                        │
                 │                                    ▼                                                        │
  スケジューラ (毎朝 7:00 など) ─────────────────▶ [Orchestrator / Router]                                      │
                 │                                    │  意図分類・どの頭に投げるか決める                       │
                 │                 ┌──────────────────┼────────────────────┐                                   │
                 │                 ▼                  ▼                    ▼                                   │
                 │        [ローカル LLM]        [Claude API]        [エージェント群]                            │
                 │        Ollama / llama.cpp    claude-opus-5       Planner / Kitchen / Tutor / Tasks / Home     │
                 │        (速い・無料・私的)    (賢い・長文・画像)   それぞれが tool を持つ                       │
                 │                 └──────────────────┬────────────────────┘                                   │
                 │                                    ▼                                                        │
                 │                          [Memory: SQLite + ベクトル索引]                                      │
                 │                goals / tasks / people / inventory / meetings / daily_log / preferences        │
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

| 観点 | ローカル LLM (Ollama) | Claude API |
|---|---|---|
| 得意 | 即答、短い指示の解釈、家電操作、分類、埋め込み | 長文の要約・計画、複数日をまたぐ推論、画像理解、英語の自然さ |
| 遅延 | 短い (0.3〜1 秒で最初の音が出せる) | やや長い (思考あり。streaming で体感を短縮) |
| コスト | 電気代のみ | トークン従量 (後述の試算) |
| プライバシー | 音声・会議録音を外に出さない | テキスト化した後の内容のみ送る |
| 弱点 | 16GB VRAM で動く 8〜14B クラスは長文の要約や複雑な計画で精度が落ちる | ネット依存、従量課金 |

**ルーティングの原則:**

1. 音声 → テキスト (STT)、テキスト → 音声 (TTS) は**常にローカル**。生の音声は外に出さない。
2. 「電気消して」「今日の予定は」のような**短い定型意図はローカル LLM** で即答。
3. **計画・要約・献立・添削は Claude**。ここが体験の質を決めるので妥協しない。
4. 英会話は**両方**用意する。まずローカルで遅延を最小化し、会話ログの添削だけ Claude に回す。物足りなければ会話本体も Claude に切り替える。

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
| Claude | **Anthropic Python SDK** (`anthropic`)、モデル `claude-opus-5` | 計画・要約・画像理解の本命。Tool Runner でエージェントループを書かずに済む |
| 家電ハブ | **Home Assistant** (Docker) | 1.1 参照 |
| ウェイクワード | **openWakeWord** (HA の Wyoming 経由) | 軽量、カスタムワード学習可 |
| VAD | **Silero VAD** | 発話区間の切り出し。英会話の応答速度に直結 |
| STT | **faster-whisper** `large-v3-turbo` | 日英両対応で速い。HA の Wyoming Whisper でも同じもの |
| TTS (英語) | **Kokoro-82M** | 軽くて自然。英会話向き |
| TTS (日本語) | **VOICEVOX** または **Style-Bert-VITS2** | 日本語品質は Piper より上。VOICEVOX は HA 連携の実績あり |
| 記憶 DB | **SQLite** + `sqlite-vec` (ベクトル検索) | 一人用なら十分。バックアップがファイルコピーで済む |
| オーケストレータ | **Python (uv)** + FastAPI | 既存スキルと揃える。HA / Ollama / Claude を全部ここから叩く |
| スケジューラ | APScheduler (アプリ内) or systemd timer | 朝のブリーフィング、週次レビュー |
| チャット入口 | Telegram Bot (推奨) or Discord | スマホから外出先でも使える。写真も送れる (冷蔵庫の中身) |
| 会議録音 | OBS / PulseAudio loopback / Windows なら WASAPI loopback | Zoom / Meet の音声とマイクを 2 トラックで取る |

### 2.3 OS についての注意

- **Windows + WSL2** の場合: GPU は WSL2 から使えるが、**マイク・スピーカーは WSL2 から素直に触れない**。
  音声 I/O (録音・再生・ウェイクワード) は Windows ネイティブ Python か HA 側の「音声サテライト」で行い、LLM / STT サーバは WSL2 や Docker に置く分離が現実的。
- **Linux ネイティブ**なら全部一つの環境で済むので最も楽。デュアルブートやこの PC を Linux 専用にできるなら推奨。
- 音声サテライトを別筐体 (Raspberry Pi + ReSpeaker、または HA Voice Preview Edition) にすると、PC の電源状態や OS 都合から独立できる。部屋の音を拾う位置にも置きやすい。**ここは要判断。**

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

**必要な tool:** `read_calendar`, `list_tasks`, `list_goals`, `write_daily_log`, `speak(text)`, `schedule_reminder`.

### 3.2 Kitchen — 自炊の管理

**役割:** 冷蔵庫の中身を把握し、栄養バランスを見て献立と買い物を提案する。

**在庫の把握方法 (現実的な順):**
1. **写真** — Telegram で冷蔵庫の写真を送る → Claude (画像対応) が品目と概量を JSON で抽出 → `inventory` に反映。週 1〜2 回で十分。
2. **買い物申告** — レシートの写真、または「豚肉と玉ねぎ買った」と声で言う → ローカル LLM で品目抽出。
3. **消費申告** — 献立を「作った」とマークすると、使った食材を自動で減らす。

完璧な在庫管理は目指さない。**「だいたい何があるか」+「賞味期限が近そうなもの」** が分かれば献立提案には足りる。

**献立提案:**
- 入力: 在庫、直近 7 日の献立履歴、好み / NG 食材、今日の予定 (帰宅時間)、栄養の偏り。
- 出力: 今日の献立 (主菜 / 副菜 / 汁物)、調理時間、使う在庫、足りないものがあれば買い足しリスト。
- 栄養は「日本食品標準成分表」ベースの簡易計算 + Claude の推定で**傾向を見る**程度に留める (厳密な計算は続かない)。

**買い物リスト (週次):**
- 在庫 + 来週の予定 + 直近 2 週間の栄養の偏り (野菜不足、タンパク質不足など) から Claude が生成。
- Telegram にチェックリストとして送り、店で消し込めるようにする。

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
   ─▶ 本人が Telegram / Web UI で確認・修正してから `tasks` に登録 (自動登録はしない)
```

- **生の音声は PC の外に出さない。** Claude に送るのはテキスト化した後の内容だけ。
- 会議相手の録音は**事前に同意を取る**。文字起こしの保存期間も決めておく (例: 90 日で削除)。
- 話者分離の精度は期待しすぎない。「誰のタスクか」は Claude が文脈 (「田中さんお願いします」) からも推定し、本人が最終確認する。

**会議以外のタスク入力:**
- 声: 「田中さんに来週の資料を依頼した、金曜まで」 → ローカル LLM が構造化 → 登録。
- チャット: Telegram に一行送る。
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

---

## 4. 記憶 (Memory) の設計

一つの SQLite ファイルに全部入れる。主要テーブル:

| テーブル | 主な列 | 誰が書くか |
|---|---|---|
| `goals` | title, why, definition_of_done, due, priority, status | 本人 (週次) |
| `tasks` | title, owner, due, status, source (meeting/voice/chat), source_ref, project | Tasks, Planner |
| `people` | name, role, voice_embedding, notes | 本人、Tasks |
| `meetings` | date, participants, transcript_path, summary, decisions | Tasks |
| `daily_log` | date, wake, sleep, mood, notes, done | Planner |
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

## 5. Claude の使い方と費用試算

### 5.1 実装方式

- **Anthropic Python SDK + Tool Runner** を基本にする。エージェントごとに tool 関数を `@beta_tool` で書き、ループは SDK に任せる。
- モデルは `claude-opus-5`、`thinking: {type: "adaptive"}`、長い出力は streaming。
- 定時ジョブ (朝のブリーフィング、週次レビュー) はローカルのスケジューラから同じ SDK を叩く。将来的に Managed Agents の scheduled deployments へ寄せる選択肢もあるが、家電操作が PC 側にあるので**まずはローカル完結**。
- Claude Code (この環境) は「Life OS 自体の開発」に使う。日々の運用には API を使う。
- system prompt と tool 定義を固定して prompt caching を効かせる (日々同じ前置きを送るので効果が大きい)。

### 5.2 費用の目安 (Opus 5: 入力 $5 / 出力 $25 per 1M tokens)

| 用途 | 頻度 | 1 回あたり (入力 / 出力) | 月額目安 |
|---|---|---|---|
| 朝ブリーフィング + 夜の振り返り | 2 回/日 | 6k / 1k | $2〜3 |
| 週次レビュー | 1 回/週 | 30k / 3k | $1 |
| 会議の要約・タスク抽出 | 5 回/週 | 15k / 2k | $2〜3 |
| 献立 + 買い物リスト | 1 回/日 + 1 回/週 | 4k / 1k | $1〜2 |
| 冷蔵庫の写真解析 | 2 回/週 | 3k / 0.5k | $0.5 |
| 英会話の添削 | 1 回/日 | 5k / 1.5k | $1〜2 |
| チャットでの相談 | 10 回/日 | 4k / 0.5k | $5〜8 |
| **合計 (英会話本体はローカル)** | | | **$15〜20 / 月** |
| 英会話本体も Claude にした場合の追加 | 30 分/日 | 60 ターン、文脈が伸びる | +$30〜60 (キャッシュ有効時はもっと下がる) |

- 会話系 (チャット、英会話) は `output_config.effort: "low"` で十分。計画・要約は `high` のまま。
- 英会話本体を Claude にするなら Sonnet 5 (入力 $2 / 出力 $10) に落とす選択肢もある。品質を試してから決める。

---

## 6. ロードマップ

「一つ動くものを作って、そこに機能を足す」順で並べる。各 Phase は 1〜2 週間の想定。

### Phase 0 — 土台 (まずここだけやる)
- [ ] OS の決定 (Linux ネイティブ / Windows + WSL2 / 音声サテライト別筐体)
- [ ] Ollama を入れ、Qwen3 14B と 8B を試して速度と賢さを比較
- [ ] Home Assistant を Docker で立て、照明を登録し、ブラウザから点灯 / 消灯
- [ ] faster-whisper と TTS (Kokoro + VOICEVOX) を単体で動かし、マイク → 文字、文字 → スピーカーを確認
- [ ] `anthropic` SDK で Claude を一回呼ぶ。API キーは環境変数で管理
- [ ] リポジトリ作成 (`life-os/`)、uv でプロジェクト初期化、SQLite スキーマ v0

**完了条件:** 各部品が単体で動く。

### Phase 1 — 声で照明が操作できる
- [ ] HA Assist (openWakeWord + Whisper + TTS) で「電気消して」が通る
- [ ] Orchestrator (FastAPI) を作り、HA の会話エージェントとして登録。意図分類をローカル LLM で行う
- [ ] Telegram Bot を繋ぎ、同じ Orchestrator にテキストでも話せる

**完了条件:** 部屋で話しかけると照明が変わり、スマホからも同じことができる。

### Phase 2 — Planner + Tasks (テキストから)
- [ ] `goals` / `tasks` / `daily_log` のテーブルと tool
- [ ] Claude による朝のブリーフィング (まずは Telegram にテキストで、次にスピーカー読み上げ)
- [ ] 夜の振り返りを音声で記録
- [ ] 声・チャットからのタスク登録、メンバー別一覧
- [ ] 週次レビュー

**完了条件:** 一週間、毎朝の提案と毎晩の記録が回る。

### Phase 3 — 会議録音パイプライン
- [ ] 2 トラック録音の仕組み
- [ ] Whisper + pyannote → 文字起こし + 話者ラベル
- [ ] Claude で要約 + タスク抽出 (structured outputs) → 確認 UI → `tasks` 登録
- [ ] 保存期間ポリシー、同意の運用

**完了条件:** 会議のあと 10 分以内に、要約とタスク候補が Telegram に届く。

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
- [ ] 人感センサー等の追加、外出 / 帰宅の検知
- [ ] 費用とレイテンシの計測ダッシュボード
- [ ] 既存タスクツールとの同期 (必要なら)

---

## 7. リポジトリ構成案 (`life-os/`)

```
life-os/
├── pyproject.toml           # uv で管理
├── docker-compose.yml       # home-assistant, ollama, (voicevox)
├── config/
│   ├── prompts/             # エージェントごとの system prompt (固定してキャッシュを効かせる)
│   └── settings.toml        # モデル名、HA の URL、ウェイクワード、nudge_level など
├── src/lifeos/
│   ├── orchestrator/        # FastAPI、意図分類、ルーティング
│   ├── agents/
│   │   ├── planner.py
│   │   ├── kitchen.py
│   │   ├── tutor.py
│   │   ├── tasks.py
│   │   └── home.py
│   ├── llm/
│   │   ├── local.py         # Ollama クライアント
│   │   └── claude.py        # Anthropic SDK ラッパ (tool runner, caching, fallbacks)
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
- **API キーの管理。** 環境変数または OS のキーチェーン。リポジトリには絶対に入れない。
- **「律してくれる」が「うるさい」になる。** nudge_level を設定可能にし、Phase 2 で一週間使って調整する。
- **最初から全部作らない。** Phase 1 で「声で照明」が動いた時点の満足感が、その後の継続を支える。

---

## 9. 着手前に決めること (回答待ち)

1. **OS** — この PC は Windows か Linux か。Linux 専用にできるか。
2. **照明の種類** — Hue / SwitchBot / Nature Remo / その他。HA 対応可否がここで決まる。
3. **スピーカーの種類** — PC 直結できるものか、Nest / Echo のようなスマートスピーカーか。
4. **マイクの置き場所** — PC のあるデスクだけで良いか、リビングやキッチンでも話しかけたいか (後者なら音声サテライト別筐体)。
5. **会議のプラットフォーム** — Zoom / Meet / Teams / 対面。録音方法が変わる。
6. **既存のカレンダー・タスクツール** — Google Calendar、Notion、GitHub Issues など。連携先を決める。
7. **チャット入口** — Telegram で良いか、Discord / Slack / LINE が良いか。
8. **英会話のレベル感と目的** — 日常会話 / 研究発表 / 面接など。Roleplay の初期セットに反映。

これらが決まれば、Phase 0 の具体的な手順 (インストールコマンド、docker-compose、スキーマ) をこの構想の続きとして書く。
