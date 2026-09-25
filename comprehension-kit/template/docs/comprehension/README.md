# 理解台帳 (Comprehension Ledger)

このディレクトリは、このプロジェクトのコードを **人間が自分の言葉で説明できるか** を記録・検証する台帳です。
AI に書かせたコードは動いても「なぜそう動くか」が頭に残らない (理解負債)。
半分しか理解していないものが積み上がると、判断のたびに全部を読み直す羽目になる (認知負債)。
この台帳は、その 2 つを **発生時に記録し、間隔反復で返済し、量を可視化する** ための仕組みです。

## 構成

```
docs/comprehension/
  README.md        ← これ
  TEMPLATE.md      ← 新規エントリの本文テンプレート (ledger.py new が使う)
  entries/         ← 1 変更 = 1 ファイル。ledger.py が frontmatter を管理する
```

## エントリの状態

| status | 意味 | 次に起きること |
|---|---|---|
| `unverified` | 記録したが、答えを見ずに説明できるか未確認 | 翌日に `/review` の対象になる |
| `verified` | 説明できた。level が上がるほど次回レビューが遠くなる (1→3→7→14→30→90→180 日) | 期限が来たら再検証 |
| `shaky` | 説明できなかった | level 0 に戻り、翌日に再検証 |
| `retired` | コードが消えた/置き換わった | レビュー対象外 |

## 日々のループ

1. **書いた/書かせた** → セッション終了前に `/debrief` (Stop フックが未記入なら止める)
2. **翌朝** → セッション開始時に期限到来数が表示される → `/review` で答えを見ずに説明 → pass/fail
3. **週に一度** → GitHub Actions が期限到来と未記録の変更を Issue にまとめる → `/debt-audit` で棚卸し
4. **未検証が上限 (10) を超えたら** → 新機能を止めて返済週間

## 手動操作

```bash
python3 .claude/comprehension/ledger.py new --title "題" --slug slug --files src/x.py
python3 .claude/comprehension/ledger.py due
python3 .claude/comprehension/ledger.py verify <id> pass|fail --note "..."
python3 .claude/comprehension/ledger.py audit --days 30
python3 .claude/comprehension/ledger.py stats
python3 .claude/comprehension/ledger.py report   # Issue 用 Markdown
```

環境変数: `COMPREHENSION_WIP_LIMIT` (既定 10), `COMPREHENSION_MIN_FILES` (Stop フックが反応する最小変更ファイル数, 既定 1), `COMPREHENSION_KIT_OFF=1` (Stop フック無効化)。
