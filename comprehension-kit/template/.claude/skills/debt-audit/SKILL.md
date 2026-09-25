---
name: debt-audit
description: 理解負債の棚卸し。直近の git 変更のうち台帳に載っていないコードを検出し、未検証エントリ数から認知負債の量を判定して、返済計画 (どれを /debrief し、どれを /review するか) を提案する。
argument-hint: "[--days N]"
---

# /debt-audit — 理解負債の棚卸し

引数: $ARGUMENTS (既定 --days 30)

## 手順

1. 実行:
   ```bash
   python3 .claude/comprehension/ledger.py stats --days <N>
   python3 .claude/comprehension/ledger.py audit --days <N>
   ```

2. **未記録の変更 (orphans)** について、`git log --oneline -- <file>` でコミットメッセージを見て、
   - まとめて 1 エントリにできるグループ
   - 個別にエントリが必要なもの
   - 記録不要 (生成物、設定の微修正) なもの
   に仕分けして提案する。ユーザーの承認後、記録が必要なものは `/debrief` の手順でエントリを作る。

3. **認知負債の判定**
   - 未検証 (unverified + shaky) が上限以上 → 「新機能は止めて返済週間」を提案。優先順位は shaky > 古い unverified > 期限到来。
   - 上限未満 → 期限到来分だけ /review。

4. **台帳の腐敗チェック**
   `verified` エントリの `files` が直近で大きく変更されていたら (git log で確認)、内容が古い可能性を指摘し、status を unverified に戻すか更新するかを提案する。

5. 最後に、次にやる 3 つのアクションを箇条書きで出す。
