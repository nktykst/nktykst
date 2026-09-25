#!/usr/bin/env python3
"""Stop フック: コードを変更したのに台帳エントリを書いていなければ、終了をブロックして /debrief を促す。

「理解負債は、理解せずにコードを受け入れた瞬間に発生する」ので、
発生した瞬間 (= セッション終了時) に必ず記録させるのがこのキットの要。

ループ防止: stop_hook_active が true (すでにこのフックで継続中) なら二度目はブロックしない。
無効化: 環境変数 COMPREHENSION_KIT_OFF=1
"""
import json
import os
import sys
from pathlib import Path

if os.environ.get("COMPREHENSION_KIT_OFF"):
    sys.exit(0)

root = Path(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()).resolve()
sys.path.insert(0, str(root / ".claude" / "comprehension"))

try:
    payload = json.load(sys.stdin)
except Exception:  # noqa: BLE001
    sys.exit(0)

if payload.get("stop_hook_active"):
    sys.exit(0)

session_id = str(payload.get("session_id") or "unknown")
touched_file = root / ".claude" / "comprehension" / ".session" / f"{session_id}.touched"
if not touched_file.exists():
    sys.exit(0)

touched = [l.strip() for l in touched_file.read_text(encoding="utf-8").splitlines() if l.strip()]
if not touched:
    sys.exit(0)

try:
    from ledger import is_code_file  # type: ignore
except Exception:  # noqa: BLE001
    def is_code_file(rel: str) -> bool:  # 最低限のフォールバック
        return not rel.startswith(("docs/comprehension/", ".claude/"))

code_changes = sorted({t for t in touched if is_code_file(t)})
ledger_changes = [t for t in touched if t.startswith("docs/comprehension/entries/")]

min_files = int(os.environ.get("COMPREHENSION_MIN_FILES", "1"))
if len(code_changes) < min_files or ledger_changes:
    sys.exit(0)

shown = ", ".join(code_changes[:8]) + (" …" if len(code_changes) > 8 else "")
reason = (
    f"[comprehension-kit] このセッションで {len(code_changes)} 個のコードファイルを変更しましたが、"
    f"理解台帳 (docs/comprehension/entries/) にエントリがありません: {shown}\n"
    "終了する前に /debrief を実行してください: 変更ごとに What/Why/How/Unknowns/確認問題を、"
    "ユーザーがコードを見ずに再実装できる粒度で書き、`python3 .claude/comprehension/ledger.py new` で作成したファイルに記入します。"
    "本当に些細な変更 (typo, フォーマットのみ) なら、その旨を一行で述べてから終了して構いません。"
)
# exit code 2 + stderr = 「停止をブロックし、stderr を Claude に見せる」(hooks 仕様)
print(reason, file=sys.stderr)
sys.exit(2)
