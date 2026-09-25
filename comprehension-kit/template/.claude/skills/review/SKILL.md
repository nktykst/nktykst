---
name: review
description: 理解台帳の復習セッション。期限が来たエントリについて、答えを見ずにユーザーに説明させ、間隔反復で verify/fail を記録する。認知負債を計画的に返済する。週次または未検証が溜まったときに使う。
argument-hint: "[entry-id | 空で期限到来分すべて]"
disable-model-invocation: true
---

# /review — 間隔反復による理解の検証

対象: $ARGUMENTS (空なら期限到来分)

## 手順

1. `python3 .claude/comprehension/ledger.py due` を実行し、対象エントリを列挙する。引数で id が指定されていればそれだけ。
   0 件なら「期限到来なし」と stats を出して終了。

2. **エントリごとに、順番に**:
   1. エントリの **title と What だけ** を示す。How と Recall の答えは **見せない**。
   2. 「このコードは何のために、どう動いていますか? 代替案を選ばなかった理由は?」と問い、ユーザーの説明を待つ。
   3. Recall の問いを 1 問ずつ出す。答えは伏せる。
   4. ユーザーの回答をエントリの How/Why/Recall の答えと照合し、**具体的に何が言えて何が抜けたか** を伝える。
   5. 判定:
      - 本質 (Why と How の核) が言えていれば → `python3 .claude/comprehension/ledger.py verify <id> pass --note "<抜けた点>"`
      - 核が言えなければ → `python3 .claude/comprehension/ledger.py verify <id> fail --note "<何が言えなかったか>"`
        その場で How を再説明し、当該コードの入口を一緒に読む (5 分以内)。
   6. 対象コードがすでに存在しない/大きく変わっている場合は `retire` を提案するか、エントリを更新する。

3. **セッション終了時**
   `python3 .claude/comprehension/ledger.py stats` を示し、
   - shaky が増えていれば「次回は shaky から」と伝える
   - 未検証が上限以下に戻ったら、新機能に進んで良い旨を伝える

## 原則
- 答えを先に見せない。見せたら検証にならない。
- 甘く採点しない。「なんとなく」の説明は fail。ただし責めない。fail は情報であって失敗ではない。
- 1 回のセッションは最大 7 エントリ程度。それ以上は次回。
