"""SESSION_STATE.md を圧縮の前に守り、セッションの始めに読ませる(Claude Code のフック)

  python .claude/hooks/session_state.py precompact   … PreCompact(手動 /compact と自動の両方)
  python .claude/hooks/session_state.py start        … SessionStart(起動・再開・圧縮のあと)

決まり:
- **圧縮を止めない。** 何が起きても終了コード 0(エラーは .claude/state/hook.log に残すだけ)。
- 標準ライブラリだけ。Windows でも動く(パスは pathlib、文字は UTF-8)。
- このプロジェクトの中だけを触る(.claude/ の下)。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(os.environ.get("CLAUDE_PROJECT_DIR") or Path(__file__).resolve().parents[2])
STATE = ROOT / ".claude" / "SESSION_STATE.md"
STORE = ROOT / ".claude" / "state"            # 控え(git では追わない)
KEEP = 5                                      # 控えは新しい 5 つだけ(ためこまない)
MAX_LINES = 200                               # これを超えたら「整理してください」と知らせる


def _log(msg: str) -> None:
    try:
        STORE.mkdir(parents=True, exist_ok=True)
        with open(STORE / "hook.log", "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
    except OSError:
        pass


def _read_input() -> dict:
    try:
        raw = sys.stdin.read() if not sys.stdin.isatty() else ""
        return json.loads(raw) if raw.strip() else {}
    except (ValueError, OSError):
        return {}


def _git(*args: str) -> str:
    try:
        out = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=10)
        return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def git_snapshot() -> str:
    branch = _git("rev-parse", "--abbrev-ref", "HEAD")
    if not branch:
        return "(git の様子を取れませんでした)"
    status = _git("status", "--short") or "(変更なし)"
    log = _git("log", "--oneline", "-5")
    return (f"ブランチ: {branch}\n\n変更中のファイル(git status --short):\n{status}\n\n"
            f"最近のコミット:\n{log}")


def precompact(data: dict) -> None:
    trigger = data.get("trigger") or "?"
    STORE.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    if STATE.is_file():
        shutil.copy2(STATE, STORE / f"SESSION_STATE_{stamp}.md")
        shutil.copy2(STATE, STORE / "SESSION_STATE.last.md")      # いちばん新しい控え(戻すときに使う)
    (STORE / "git_snapshot.md").write_text(
        f"# 圧縮の直前の git の様子({time.strftime('%Y-%m-%d %H:%M:%S')}・{trigger})\n\n" + git_snapshot() + "\n",
        encoding="utf-8")
    for old in sorted(STORE.glob("SESSION_STATE_2*.md"))[:-KEEP]:
        old.unlink()
    _log(f"precompact trigger={trigger} state={'あり' if STATE.is_file() else 'なし'}")


def start(data: dict) -> None:
    source = data.get("source") or "?"
    restored = ""
    if not STATE.is_file() and (STORE / "SESSION_STATE.last.md").is_file():
        shutil.copy2(STORE / "SESSION_STATE.last.md", STATE)          # 消えていたら控えから戻す
        restored = "(SESSION_STATE.md が無かったので、圧縮前の控えから戻しました)\n"
    if not STATE.is_file():
        _log(f"start source={source} state=なし")
        return
    text = STATE.read_text(encoding="utf-8")
    lines = text.count("\n") + 1
    note = (f"\n※ SESSION_STATE.md が {lines} 行あります。完了済みの項目を短くまとめ直してください。\n"
            if lines > MAX_LINES else "")
    snap = STORE / "git_snapshot.md"
    git_part = snap.read_text(encoding="utf-8") if source == "compact" and snap.is_file() else ""
    context = ("このプロジェクトの作業状態(.claude/SESSION_STATE.md)です。作業を続ける前に読み、"
               "進めたら同じファイルを更新してください。\n" + restored + note + "\n" + text
               + ("\n\n" + git_part if git_part else ""))
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart",
                                             "additionalContext": context}}, ensure_ascii=False))
    _log(f"start source={source} lines={lines}")


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")       # Windows の cp932 で文字化けしない
    except (AttributeError, ValueError):
        pass
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        data = _read_input()
        if mode == "precompact":
            precompact(data)
        elif mode == "start":
            start(data)
    except Exception as exc:                           # noqa: BLE001 - 圧縮も起動も止めない
        _log(f"{mode} エラー: {exc!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
