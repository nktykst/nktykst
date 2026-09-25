# comprehension-kit — バイブコーディングの理解負債を恒久的に返済する仕組み

AI にコードを書かせると速いが、**「動いたけど、なぜ動くか説明できない」** コードが溜まる。

| 負債 | 何が起きるか |
|---|---|
| **理解負債** | 自分のリポジトリなのに読めない。バグの原因が追えない。変更が怖い |
| **認知負債** | 半分だけ理解したものが 20 個あると、判断のたびに全部を思い出す必要がある。頭が常に重い |

どちらも「気をつける」では解決しない。気をつける余裕がないときにこそ発生するからだ。
このキットは **意志力に頼らず、機械的に** 発生を検知し、返済させ、量を見せる。

## 仕組み (3 つのループ)

```
 書く/書かせる ──► [Stop フック] 台帳未記入なら終了をブロック ──► /debrief で記録
                                                                    │
                                                                    ▼
 翌日以降 ◄── [SessionStart フック] 期限到来数を毎回表示 ◄── 間隔反復 (1→3→7→14→30→90→180 日)
    │
    ▼
 /review: 答えを見ずに自分の言葉で説明 → pass なら間隔が伸びる / fail なら翌日に戻る
    │
    ▼
 週次 [GitHub Actions] 期限到来 + 未記録の変更を Issue に集約 → /debt-audit で棚卸し
 未検証が上限 (10) を超えたら、新機能を止めて返済に切り替える
```

要点は 3 つ。

1. **発生した瞬間に捕まえる** — 理解負債はコードを受け入れた瞬間に生まれる。だからセッション終了時に Stop フックが「台帳に書いたか」を検査し、書いていなければ Claude に `/debrief` をさせる。
2. **理解の定義を「答えを見ずに説明できる」に固定する** — 読んで「分かった気になる」のは理解ではない。台帳の確認問題を、答えを伏せて答えさせる (アクティブリコール)。合格すれば次の確認は遠くなる (間隔反復)。
3. **量に上限を置く** — 未検証エントリが上限を超えたら、Claude が新機能より先に復習を提案する。認知負債を WIP 制限で抑える。

## 中身

```
comprehension-kit/
  install.sh                       # 対象プロジェクトに導入 (python3 のみ依存)
  template/
    .claude/
      settings.json                # フック 3 つ
      hooks/session-start.py       # 起動時に負債状況をコンテキストへ注入
      hooks/track-edits.py         # 触ったファイルをセッション単位で記録
      hooks/stop-guard.py          # 台帳未記入なら終了をブロック (exit 2)
      rules/comprehension.md       # Claude が常に守るルール (自動ロード)
      skills/debrief/              # 変更を台帳へ (What/Why/How/Unknowns/確認問題)
      skills/explain/              # 3 層の解説 + 消したら何が壊れるか
      skills/review/               # 答えを伏せた検証セッション (間隔反復)
      skills/debt-audit/           # 未記録の変更と負債量の棚卸し
      comprehension/ledger.py      # 台帳 CLI (標準ライブラリのみ)
    docs/comprehension/            # 台帳本体。entries/ に 1 変更 1 ファイル
    .github/workflows/comprehension-review.yml   # 週次 Issue
```

## 導入

```bash
git clone https://github.com/nktykst/nktykst  # or copy the comprehension-kit/ dir
bash nktykst/comprehension-kit/install.sh /path/to/your/project
cd /path/to/your/project
git add .claude docs/comprehension .github && git commit -m "Add comprehension-kit"
claude   # 起動時に "[comprehension-kit] 理解台帳: ..." が出れば動いている
```

既存の `.claude/settings.json` があればフックだけマージする。既存ファイルは上書きしない (`--force` で上書き)。

## 日常の使い方

| いつ | 何を |
|---|---|
| コードを変えた (変えさせた) | `/debrief` — 忘れても Stop フックが止める |
| 「これ何?」と思った | `/explain src/foo.py` |
| 起動時に「期限到来 N 件」と出た | `/review` — 5〜15 分。答えは見ない |
| 月曜に Issue が来た | `/debt-audit` — 何を記録し、何を復習するか決める |
| 未検証が 10 件を超えた | 新機能を止める。`/review` を優先 |

## 台帳エントリの形

```markdown
---
id: 2026-09-25-attention-mask
title: causal attention mask の導入
files: [src/model/attention.py]
status: unverified      # unverified → verified / shaky → retired
level: 0                # 間隔反復の段階
next_review: 2026-09-26
---
## 何をしたか (What)        入口となる関数名を書く
## なぜそうしたか (Why)      代替案と選ばなかった理由を必ず 1 つ
## どう動くか (How)          コードを見ずに再実装できる粒度。shape 遷移
## 分からないところ          正直に。空にしない
## 壊れるとしたらどこか
## 確認問題 (Recall)         「なぜ X でなく Y か」型を 3 問以上
## 検証ログ                  verify が追記
```

## 設計上の判断

- **Markdown + frontmatter、依存ゼロ**: 台帳は人間が読むもの。DB や外部サービスに置くと読まれなくなる。PyYAML すら不要にして、どの環境でも `python3` だけで動く。
- **ブロックは Stop フックだけ**: 編集のたびに止めると邪魔で、結局オフにされる。セッション終了という自然な区切りで一度だけ止める。`stop_hook_active` を見て二重ブロックはしない。`COMPREHENSION_KIT_OFF=1` で緊急停止できる。
- **検証は「説明させる」一択**: チェックボックスを付ける方式は自己申告になって形骸化する。答えを伏せて説明させ、Claude が採点する。
- **間隔反復**: 1 回説明できても 1 か月後には忘れる。忘れた頃に再検証し、覚えていれば間隔を伸ばす。
- **上限**: 「あとで理解する」を無限に許すと認知負債になる。10 件で新規開発を止める提案が入る。

## カスタマイズ

| 変数 | 既定 | 意味 |
|---|---|---|
| `COMPREHENSION_WIP_LIMIT` | 10 | 未検証エントリの上限 |
| `COMPREHENSION_MIN_FILES` | 1 | Stop フックが反応する最小コード変更ファイル数 |
| `COMPREHENSION_KIT_OFF` | (unset) | `1` で Stop フックを無効化 |
| `COMPREHENSION_LEDGER_DIR` | `docs/comprehension` | 台帳の場所 |

対象拡張子や除外パスは `ledger.py` 冒頭の `CODE_SUFFIXES` / `IGNORE_PREFIXES` を編集する。
