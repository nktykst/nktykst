#!/usr/bin/env python3
"""理解台帳 (Comprehension Ledger) CLI — 標準ライブラリのみで動く。

バイブコーディングで生まれる「理解負債」を、
  1. 変更のたびに記録し (new)
  2. 間隔反復で検証し (due / verify)
  3. 未記録の変更を検出し (audit)
  4. 現在の負債量を可視化する (stats / report)
ための小さなツール。

台帳は docs/comprehension/entries/*.md に 1 エントリ 1 ファイルで置く。
先頭の frontmatter (--- ... ---) をこのスクリプトが読み書きする。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# 設定
# ---------------------------------------------------------------------------

PROJECT_DIR = Path(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()).resolve()
LEDGER_DIR = Path(os.environ.get("COMPREHENSION_LEDGER_DIR") or PROJECT_DIR / "docs" / "comprehension")
ENTRIES_DIR = LEDGER_DIR / "entries"
TEMPLATE_PATH = LEDGER_DIR / "TEMPLATE.md"

# 間隔反復のスケジュール (日)。level が上がるほど次回レビューが遠くなる。
INTERVALS = [1, 3, 7, 14, 30, 90, 180]

# 認知負債の警告しきい値: 未検証エントリがこれ以上なら新規開発より復習を優先させる
WIP_LIMIT = int(os.environ.get("COMPREHENSION_WIP_LIMIT", "10"))

# audit で「理解を要する変更」とみなす拡張子。docs 等は除外する。
CODE_SUFFIXES = {
    ".py", ".ipynb", ".js", ".ts", ".tsx", ".jsx", ".mjs", ".cjs", ".vue", ".svelte",
    ".rs", ".go", ".java", ".kt", ".c", ".cc", ".cpp", ".h", ".hpp", ".cs", ".rb",
    ".sh", ".bash", ".zsh", ".sql", ".toml", ".yaml", ".yml", ".json", ".cfg", ".ini",
    ".env.example", ".dockerfile", ".tf", ".proto", ".cu", ".m", ".r", ".jl", ".lua",
}
IGNORE_PREFIXES = (
    "docs/comprehension/",
    ".claude/",
    ".github/workflows/comprehension-review.yml",
    "node_modules/",
    ".venv/",
    "venv/",
    "dist/",
    "build/",
)
IGNORE_NAMES = {"uv.lock", "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "Cargo.lock"}

STATUSES = ("unverified", "verified", "shaky", "retired")


# ---------------------------------------------------------------------------
# frontmatter の読み書き (PyYAML なし。key: value と "- item" のリストだけ扱う)
# ---------------------------------------------------------------------------

def parse_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        return {}, text
    m = re.match(r"^---\n(.*?)\n---\n?(.*)$", text, re.S)
    if not m:
        return {}, text
    head, body = m.group(1), m.group(2)
    data: dict = {}
    key = None
    for raw in head.splitlines():
        if not raw.strip():
            continue
        if raw.startswith("  - ") and key is not None:
            data.setdefault(key, [])
            if not isinstance(data[key], list):
                data[key] = []
            data[key].append(_unquote(raw[4:].strip()))
            continue
        km = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", raw)
        if km:
            key, val = km.group(1), km.group(2).strip()
            if val == "" or val == "[]":
                data[key] = [] if val == "[]" else ""
            else:
                data[key] = _coerce(_unquote(val))
    return data, body


def _unquote(s: str) -> str:
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        return s[1:-1]
    return s


def _coerce(v: str):
    if re.fullmatch(r"-?\d+", v):
        return int(v)
    return v


def dump_frontmatter(data: dict) -> str:
    lines = ["---"]
    for k, v in data.items():
        if isinstance(v, list):
            if not v:
                lines.append(f"{k}: []")
            else:
                lines.append(f"{k}:")
                lines.extend(f"  - {_q(x)}" for x in v)
        else:
            lines.append(f"{k}: {_q(v)}")
    lines.append("---")
    return "\n".join(lines) + "\n"


def _q(v) -> str:
    s = str(v)
    if s == "" or re.search(r"[:#\[\]{}]|^\s|\s$", s):
        return json.dumps(s, ensure_ascii=False)
    return s


# ---------------------------------------------------------------------------
# エントリ
# ---------------------------------------------------------------------------

class Entry:
    def __init__(self, path: Path):
        self.path = path
        self.meta, self.body = parse_frontmatter(path.read_text(encoding="utf-8"))

    @property
    def id(self) -> str:
        return str(self.meta.get("id") or self.path.stem)

    @property
    def status(self) -> str:
        return str(self.meta.get("status") or "unverified")

    @property
    def files(self) -> list[str]:
        f = self.meta.get("files") or []
        return [str(x) for x in f] if isinstance(f, list) else [str(f)]

    @property
    def next_review(self) -> dt.date | None:
        v = self.meta.get("next_review")
        return _parse_date(v) if v else None

    @property
    def level(self) -> int:
        try:
            return int(self.meta.get("level") or 0)
        except (TypeError, ValueError):
            return 0

    def is_due(self, today: dt.date) -> bool:
        if self.status == "retired":
            return False
        nr = self.next_review
        return nr is None or nr <= today

    def save(self) -> None:
        self.path.write_text(dump_frontmatter(self.meta) + self.body, encoding="utf-8")


def _parse_date(v) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def load_entries() -> list[Entry]:
    if not ENTRIES_DIR.exists():
        return []
    entries = []
    for p in sorted(ENTRIES_DIR.glob("*.md")):
        if p.name.startswith("."):
            continue
        try:
            entries.append(Entry(p))
        except Exception as e:  # noqa: BLE001 - 壊れたファイルで全体を止めない
            print(f"warn: {p}: {e}", file=sys.stderr)
    return entries


def find_entry(entries: list[Entry], ident: str) -> Entry:
    for e in entries:
        if e.id == ident or e.path.stem == ident or e.path.name == ident:
            return e
    # 前方一致 / slug 一致 (日付を省いた "model-init" など) / 部分一致も、一意なら許す
    for pred in (
        lambda e: e.id.startswith(ident),
        lambda e: e.id.endswith("-" + ident) or re.fullmatch(r"\d{4}-\d{2}-\d{2}-" + re.escape(ident) + r"(-\d+)?", e.id),
        lambda e: ident in e.id,
    ):
        cands = [e for e in entries if pred(e)]
        if len(cands) == 1:
            return cands[0]
        if len(cands) > 1:
            break
    raise SystemExit(f"error: entry not found (or ambiguous): {ident}")


def slugify(s: str) -> str:
    s = re.sub(r"[^\w\-]+", "-", s.strip().lower(), flags=re.UNICODE)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return s[:60] or "entry"


# ---------------------------------------------------------------------------
# git ヘルパ
# ---------------------------------------------------------------------------

def git(*args: str) -> str:
    try:
        return subprocess.run(
            ["git", *args], cwd=PROJECT_DIR, capture_output=True, text=True, check=False
        ).stdout
    except FileNotFoundError:
        return ""


def changed_files_since(days: int) -> list[str]:
    out = git("log", f"--since={days} days ago", "--name-only", "--pretty=format:", "--diff-filter=AM")
    files = {l.strip() for l in out.splitlines() if l.strip()}
    # 未コミットの変更も含める
    out = git("status", "--porcelain")
    for line in out.splitlines():
        if len(line) > 3:
            files.add(line[3:].strip().split(" -> ")[-1])
    return sorted(files)


def is_code_file(rel: str) -> bool:
    if rel.startswith(IGNORE_PREFIXES):
        return False
    name = Path(rel).name
    if name in IGNORE_NAMES:
        return False
    suffixes = "".join(Path(rel).suffixes[-2:]).lower()
    return any(suffixes.endswith(s) for s in CODE_SUFFIXES) or name in {"Dockerfile", "Makefile", "justfile"}


def covered_by(entries: list[Entry], rel: str) -> Entry | None:
    for e in entries:
        if e.status == "retired":
            continue
        for f in e.files:
            f = f.strip()
            if not f:
                continue
            if f == rel or rel.startswith(f.rstrip("/") + "/"):
                return e
            if any(ch in f for ch in "*?[") and Path(rel).match(f):
                return e
    return None


# ---------------------------------------------------------------------------
# コマンド
# ---------------------------------------------------------------------------

def cmd_new(a) -> None:
    ENTRIES_DIR.mkdir(parents=True, exist_ok=True)
    today = dt.date.today()
    slug = slugify(a.slug or a.title)
    eid = f"{today.isoformat()}-{slug}"
    path = ENTRIES_DIR / f"{eid}.md"
    n = 2
    while path.exists():
        path = ENTRIES_DIR / f"{eid}-{n}.md"
        n += 1
    meta = {
        "id": path.stem,
        "title": a.title,
        "created": today.isoformat(),
        "source": a.source,
        "files": [f.strip() for f in a.files] if a.files else [],
        "status": "unverified",
        "level": 0,
        "next_review": (today + dt.timedelta(days=INTERVALS[0])).isoformat(),
        "verified_at": [],
        "tags": [t.strip() for t in a.tags] if a.tags else [],
    }
    body = _template_body()
    path.write_text(dump_frontmatter(meta) + body, encoding="utf-8")
    print(str(path.relative_to(PROJECT_DIR)) if path.is_relative_to(PROJECT_DIR) else str(path))


def _template_body() -> str:
    if TEMPLATE_PATH.exists():
        _, body = parse_frontmatter(TEMPLATE_PATH.read_text(encoding="utf-8"))
        return body.lstrip("\n") if body else DEFAULT_BODY
    return DEFAULT_BODY


DEFAULT_BODY = """
## 何をしたか (What)

<!-- 1〜3 文。変更の範囲と入口となる関数/ファイル -->

## なぜそうしたか (Why)

<!-- 採用した理由。検討した代替案と、選ばなかった理由を最低 1 つ -->

## どう動くか (How) — 自分の言葉で

<!-- 読者が「コードを見ずに再実装できる」レベルの説明。専門用語は 1 行で定義する -->

## 分からないところ (Unknowns)

<!-- 正直に書く。ここが空なら疑う。"なし" と書く場合は理由も -->

## 壊れるとしたらどこか (Failure modes)

<!-- 入力の前提、境界条件、依存の更新で壊れる箇所 -->

## 確認問題 (Recall)

<!-- 答えを見ずに自分の言葉で答えられれば verified。3 問以上 -->

- Q1:
  A1:
- Q2:
  A2:
- Q3:
  A3:

## 検証ログ (Review log)

<!-- verify コマンドが追記する。手書きでも可 -->
"""


def cmd_list(a) -> None:
    today = dt.date.today()
    entries = load_entries()
    if a.status:
        entries = [e for e in entries if e.status == a.status]
    if a.due:
        entries = [e for e in entries if e.is_due(today)]
    if a.json:
        print(json.dumps([_entry_dict(e, today) for e in entries], ensure_ascii=False, indent=2))
        return
    if not entries:
        print("(no entries)")
        return
    for e in entries:
        print(_fmt_line(e, today))


def _entry_dict(e: Entry, today: dt.date) -> dict:
    return {
        "id": e.id,
        "title": e.meta.get("title", ""),
        "status": e.status,
        "level": e.level,
        "next_review": e.meta.get("next_review", ""),
        "due": e.is_due(today),
        "files": e.files,
        "path": str(e.path.relative_to(PROJECT_DIR)) if e.path.is_relative_to(PROJECT_DIR) else str(e.path),
    }


def _fmt_line(e: Entry, today: dt.date) -> str:
    nr = e.next_review
    if e.status == "retired":
        due = "retired"
    elif nr is None:
        due = "due"
    elif nr <= today:
        due = f"due ({(today - nr).days}d overdue)" if nr < today else "due today"
    else:
        due = f"in {(nr - today).days}d"
    mark = {"unverified": "○", "verified": "●", "shaky": "△", "retired": "×"}.get(e.status, "?")
    return f"{mark} {e.id}  [{e.status} L{e.level}, {due}]  {e.meta.get('title','')}"


def cmd_due(a) -> None:
    a.status = None
    a.due = True
    cmd_list(a)


def cmd_verify(a) -> None:
    entries = load_entries()
    e = find_entry(entries, a.id)
    today = dt.date.today()
    if a.result == "pass":
        lvl = min(e.level + 1, len(INTERVALS) - 1)
        e.meta["status"] = "verified"
    else:
        lvl = 0
        e.meta["status"] = "shaky"
    e.meta["level"] = lvl
    e.meta["next_review"] = (today + dt.timedelta(days=INTERVALS[lvl])).isoformat()
    va = e.meta.get("verified_at") or []
    if not isinstance(va, list):
        va = [str(va)]
    va.append(f"{today.isoformat()}:{a.result}")
    e.meta["verified_at"] = va
    note = f"- {today.isoformat()} {a.result.upper()} (L{lvl}, next {e.meta['next_review']})"
    if a.note:
        note += f" — {a.note}"
    if "## 検証ログ" in e.body:
        e.body = e.body.rstrip("\n") + "\n" + note + "\n"
    else:
        e.body = e.body.rstrip("\n") + "\n\n## 検証ログ (Review log)\n\n" + note + "\n"
    e.save()
    print(_fmt_line(e, today))


def cmd_retire(a) -> None:
    entries = load_entries()
    e = find_entry(entries, a.id)
    e.meta["status"] = "retired"
    e.meta["retired"] = dt.date.today().isoformat()
    if a.note:
        e.body = e.body.rstrip("\n") + f"\n- {dt.date.today().isoformat()} RETIRED — {a.note}\n"
    e.save()
    print(f"× {e.id} retired")


def compute_stats(days: int = 30) -> dict:
    today = dt.date.today()
    entries = load_entries()
    active = [e for e in entries if e.status != "retired"]
    due = [e for e in active if e.is_due(today)]
    by_status = {s: sum(1 for e in entries if e.status == s) for s in STATUSES}
    changed = [f for f in changed_files_since(days) if is_code_file(f)]
    orphans = [f for f in changed if covered_by(entries, f) is None]
    covered = len(changed) - len(orphans)
    unverified = by_status["unverified"] + by_status["shaky"]
    return {
        "date": today.isoformat(),
        "total": len(entries),
        "by_status": by_status,
        "due": [e.id for e in due],
        "unverified_count": unverified,
        "wip_limit": WIP_LIMIT,
        "over_wip_limit": unverified >= WIP_LIMIT,
        "window_days": days,
        "changed_code_files": len(changed),
        "orphan_files": orphans,
        "coverage": (covered / len(changed)) if changed else 1.0,
    }


def cmd_stats(a) -> None:
    s = compute_stats(a.days)
    if a.json:
        print(json.dumps(s, ensure_ascii=False, indent=2))
        return
    print(_stats_text(s, verbose=not a.brief))


def _stats_text(s: dict, verbose: bool = True) -> str:
    bs = s["by_status"]
    lines = [
        f"理解台帳: 全{s['total']}件  ●verified {bs['verified']}  ○unverified {bs['unverified']}  △shaky {bs['shaky']}  ×retired {bs['retired']}",
        f"レビュー期限到来: {len(s['due'])}件   直近{s['window_days']}日のコード変更カバー率: {s['coverage']*100:.0f}% ({s['changed_code_files']}ファイル中 未記録{len(s['orphan_files'])})",
    ]
    if s["over_wip_limit"]:
        lines.append(
            f"⚠ 未検証 {s['unverified_count']}件 ≥ 上限 {s['wip_limit']}: 認知負債が上限超過。新機能より先に /review を推奨。"
        )
    if verbose:
        if s["due"]:
            lines.append("  due: " + ", ".join(s["due"][:10]) + (" …" if len(s["due"]) > 10 else ""))
        if s["orphan_files"]:
            lines.append("  未記録の変更: " + ", ".join(s["orphan_files"][:10]) + (" …" if len(s["orphan_files"]) > 10 else ""))
    return "\n".join(lines)


def cmd_audit(a) -> None:
    entries = load_entries()
    changed = [f for f in changed_files_since(a.days) if is_code_file(f)]
    rows = []
    for f in changed:
        e = covered_by(entries, f)
        rows.append((f, e.id if e else None, e.status if e else None))
    if a.json:
        print(json.dumps([{"file": f, "entry": e, "status": s} for f, e, s in rows], ensure_ascii=False, indent=2))
        return
    orphans = [r for r in rows if r[1] is None]
    print(f"直近{a.days}日に変更されたコードファイル: {len(rows)}  未記録: {len(orphans)}")
    for f, e, s in rows:
        if e is None:
            print(f"  ✗ {f}")
        elif a.all:
            print(f"  ✓ {f}  ← {e} [{s}]")
    if orphans:
        print("\n未記録ファイルは「なぜ変えたか」を説明できない負債。/debrief で台帳に載せること。")


def cmd_report(a) -> None:
    s = compute_stats(a.days)
    today = dt.date.today()
    entries = {e.id: e for e in load_entries()}
    out = [f"## 🧠 理解負債レビュー ({s['date']})", ""]
    out.append(_stats_text(s, verbose=False).replace("\n", "  \n"))
    out.append("")
    if s["due"]:
        out.append("### 期限到来 (答えを見ずに自分の言葉で説明できるか?)")
        for eid in s["due"]:
            e = entries[eid]
            rel = e.path.relative_to(PROJECT_DIR) if e.path.is_relative_to(PROJECT_DIR) else e.path
            out.append(f"- [ ] `{eid}` — {e.meta.get('title','')} ([{e.status} L{e.level}]({rel}))")
        out.append("")
    if s["orphan_files"]:
        out.append(f"### 未記録の変更 (直近{s['window_days']}日)")
        out.extend(f"- [ ] `{f}`" for f in s["orphan_files"])
        out.append("")
    out.append("### やること")
    out.append("1. プロジェクトで `claude` を起動し `/review` を実行 (期限到来分を口頭で説明 → verify)")
    out.append("2. 未記録の変更は `/debrief` で台帳に追加")
    out.append("")
    out.append("<sub>generated by comprehension-kit `ledger.py report`</sub>")
    print("\n".join(out))


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="ledger.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = p.add_subparsers(dest="cmd", required=True)

    q = sp.add_parser("new", help="台帳エントリを作る")
    q.add_argument("--title", required=True)
    q.add_argument("--slug", help="ファイル名に使う短い英字 (省略時は title から生成)")
    q.add_argument("--files", nargs="*", default=[], help="対象ファイル/ディレクトリ/glob")
    q.add_argument("--source", default="claude-code", help="コードを書いた主体 (claude-code / human / pair)")
    q.add_argument("--tags", nargs="*", default=[])
    q.set_defaults(fn=cmd_new)

    q = sp.add_parser("list", help="エントリ一覧")
    q.add_argument("--status", choices=STATUSES)
    q.add_argument("--due", action="store_true")
    q.add_argument("--json", action="store_true")
    q.set_defaults(fn=cmd_list)

    q = sp.add_parser("due", help="レビュー期限が来ているエントリ")
    q.add_argument("--json", action="store_true")
    q.set_defaults(fn=cmd_due)

    q = sp.add_parser("verify", help="復習結果を記録 (pass で間隔が伸び、fail で戻る)")
    q.add_argument("id")
    q.add_argument("result", choices=["pass", "fail"])
    q.add_argument("--note")
    q.set_defaults(fn=cmd_verify)

    q = sp.add_parser("retire", help="対象コードが消えた等でエントリを退役させる")
    q.add_argument("id")
    q.add_argument("--note")
    q.set_defaults(fn=cmd_retire)

    q = sp.add_parser("stats", help="負債量のサマリ")
    q.add_argument("--days", type=int, default=30)
    q.add_argument("--json", action="store_true")
    q.add_argument("--brief", action="store_true")
    q.set_defaults(fn=cmd_stats)

    q = sp.add_parser("audit", help="直近の変更のうち台帳に載っていないファイルを出す")
    q.add_argument("--days", type=int, default=30)
    q.add_argument("--all", action="store_true", help="記録済みも表示")
    q.add_argument("--json", action="store_true")
    q.set_defaults(fn=cmd_audit)

    q = sp.add_parser("report", help="週次レビュー用 Markdown (GitHub Issue 向け)")
    q.add_argument("--days", type=int, default=30)
    q.set_defaults(fn=cmd_report)

    a = p.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
