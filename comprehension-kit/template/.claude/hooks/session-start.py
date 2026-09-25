#!/usr/bin/env python3
"""SessionStart フック: 現在の理解負債の状態を Claude のコンテキストに注入する。

stdout に出した内容はそのまま Claude に見える。ここで
  - 未検証/期限到来のエントリ数
  - 直近の未記録変更
を出しておくと、Claude が「まず /review をしませんか」と自発的に提案できる。
"""
import json
import os
import subprocess
import sys
from pathlib import Path

root = Path(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
ledger = root / ".claude" / "comprehension" / "ledger.py"

try:
    payload = json.load(sys.stdin)
except Exception:  # noqa: BLE001
    payload = {}

# セッション単位の「触ったファイル」記録をリセットする
session_id = str(payload.get("session_id") or "unknown")
state_dir = root / ".claude" / "comprehension" / ".session"
state_dir.mkdir(parents=True, exist_ok=True)
(state_dir / f"{session_id}.touched").write_text("", encoding="utf-8")

if not ledger.exists():
    sys.exit(0)

r = subprocess.run([sys.executable, str(ledger), "stats"], capture_output=True, text=True, cwd=root)
text = r.stdout.strip() or r.stderr.strip()
if text:
    print("[comprehension-kit] " + text.replace("\n", "\n[comprehension-kit] "))
    print("[comprehension-kit] 台帳: docs/comprehension/entries/  コマンド: /debrief /review /explain /debt-audit")
