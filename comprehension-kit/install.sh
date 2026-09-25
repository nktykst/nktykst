#!/usr/bin/env bash
# comprehension-kit を対象プロジェクトに導入する。
#
#   使い方:  bash comprehension-kit/install.sh /path/to/project
#            (引数省略時はカレントディレクトリ)
#
#   やること:
#     - .claude/{hooks,skills,rules,comprehension} をコピー (既存ファイルは上書きしない。--force で上書き)
#     - .claude/settings.json に hooks をマージ (既存の hooks は保持)
#     - docs/comprehension/ と .github/workflows/comprehension-review.yml を配置
#   依存: python3 のみ

set -euo pipefail

FORCE=0
TARGET=""
for arg in "$@"; do
  case "$arg" in
    --force) FORCE=1 ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) TARGET="$arg" ;;
  esac
done
TARGET="${TARGET:-$PWD}"
TARGET="$(cd "$TARGET" && pwd)"
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/template" && pwd)"

if [ ! -d "$TARGET/.git" ]; then
  echo "warn: $TARGET は git リポジトリではありません (audit が動きません)" >&2
fi

copied=0; skipped=0
copy_file() { # src rel
  local rel="$1" dst="$TARGET/$1"
  mkdir -p "$(dirname "$dst")"
  if [ -e "$dst" ] && [ "$FORCE" != 1 ]; then
    if ! cmp -s "$SRC/$rel" "$dst"; then
      echo "  skip (exists, differs): $rel"; skipped=$((skipped+1))
    fi
    return
  fi
  cp "$SRC/$rel" "$dst"; copied=$((copied+1))
  echo "  add: $rel"
}

echo "== comprehension-kit → $TARGET"
while IFS= read -r -d '' rel; do
  rel="${rel#./}"
  [ "$rel" = ".claude/settings.json" ] && continue
  copy_file "$rel"
done < <(cd "$SRC" && find . -type f -print0)
chmod +x "$TARGET"/.claude/hooks/*.py "$TARGET"/.claude/comprehension/ledger.py

# settings.json のマージ
python3 - "$SRC/.claude/settings.json" "$TARGET/.claude/settings.json" <<'PY'
import json, sys
from pathlib import Path
src, dst = Path(sys.argv[1]), Path(sys.argv[2])
new = json.loads(src.read_text())
cur = json.loads(dst.read_text()) if dst.exists() else {}
hooks = cur.setdefault("hooks", {})
added = 0
for event, groups in new["hooks"].items():
    existing = hooks.setdefault(event, [])
    for g in groups:
        cmds = {h.get("command") for eg in existing for h in eg.get("hooks", [])}
        if any(h.get("command") in cmds for h in g["hooks"]):
            continue
        existing.append(g); added += 1
dst.parent.mkdir(parents=True, exist_ok=True)
dst.write_text(json.dumps(cur, ensure_ascii=False, indent=2) + "\n")
print(f"  settings.json: hooks {added} 件追加")
PY

echo "== 完了: 追加 $copied, スキップ $skipped"
echo
echo "次にやること:"
echo "  1. git add .claude docs/comprehension .github && git commit -m 'Add comprehension-kit'"
echo "  2. claude を起動 → セッション開始時に台帳の状態が表示されることを確認"
echo "  3. コードを変えたら /debrief、翌日 /review、週次 Issue が来たら /debt-audit"
