#!/usr/bin/env python3
"""PostToolUse フック (Edit|Write|MultiEdit|NotebookEdit): このセッションで触ったファイルを記録する。

Stop フックがこの記録を見て「台帳に書かずに終わろうとしていないか」を判定する。
"""
import json
import os
import sys
from pathlib import Path

root = Path(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()).resolve()
try:
    payload = json.load(sys.stdin)
except Exception:  # noqa: BLE001
    sys.exit(0)

ti = payload.get("tool_input") or {}
path = ti.get("file_path") or ti.get("notebook_path")
if not path:
    sys.exit(0)

p = Path(path)
try:
    rel = str(p.resolve().relative_to(root))
except ValueError:
    rel = str(p)

session_id = str(payload.get("session_id") or "unknown")
state_dir = root / ".claude" / "comprehension" / ".session"
state_dir.mkdir(parents=True, exist_ok=True)
with (state_dir / f"{session_id}.touched").open("a", encoding="utf-8") as f:
    f.write(rel + "\n")
