"""Claude Code のフック(.claude/hooks/session_state.py)── 圧縮の前に作業状態を守り、あとで読ませる

フックが受け取るのと同じ JSON を標準入力で渡し、本物と同じ呼び方(子プロセス)で確かめる。
Windows の CI でも流れる。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / ".claude" / "hooks" / "session_state.py"


class SessionStateHookTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.proj = Path(tmp.name)
        (self.proj / ".claude").mkdir()
        self.state = self.proj / ".claude" / "SESSION_STATE.md"
        self.state.write_text("# 作業状態\n## 作業目的\n表を削除を作る\n## 未完了の作業\n- 試験\n", encoding="utf-8")

    def run_hook(self, mode, payload):
        env = dict(os.environ, CLAUDE_PROJECT_DIR=str(self.proj))
        return subprocess.run([sys.executable, str(HOOK), mode], input=json.dumps(payload, ensure_ascii=False),
                              capture_output=True, text=True, encoding="utf-8", env=env, timeout=60)

    def test_precompact_then_start_hands_over(self):
        for trigger in ("manual", "auto"):
            r = self.run_hook("precompact", {"hook_event_name": "PreCompact", "trigger": trigger})
            self.assertEqual(r.returncode, 0, r.stderr)
        store = self.proj / ".claude" / "state"
        self.assertTrue((store / "SESSION_STATE.last.md").is_file())
        self.assertTrue((store / "git_snapshot.md").is_file())
        r = self.run_hook("start", {"hook_event_name": "SessionStart", "source": "compact"})
        self.assertEqual(r.returncode, 0, r.stderr)
        ctx = json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]
        for want in ("表を削除を作る", "未完了の作業", "- 試験"):
            self.assertIn(want, ctx)

    def test_restores_when_state_lost(self):
        self.run_hook("precompact", {"trigger": "auto"})
        self.state.unlink()
        r = self.run_hook("start", {"source": "compact"})
        self.assertIn("控えから戻しました", json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"])
        self.assertTrue(self.state.is_file())

    def test_never_blocks(self):
        """壊れた入力・状態ファイルなしでも終了コード 0(圧縮を止めない)。"""
        self.state.unlink()
        for mode in ("precompact", "start", "?"):
            r = subprocess.run([sys.executable, str(HOOK), mode], input="{壊れた", capture_output=True, text=True,
                               encoding="utf-8", env=dict(os.environ, CLAUDE_PROJECT_DIR=str(self.proj)), timeout=60)
            self.assertEqual(r.returncode, 0, (mode, r.stderr))

    def test_keeps_only_recent_backups(self):
        store = self.proj / ".claude" / "state"
        store.mkdir(parents=True)
        for i in range(8):
            (store / f"SESSION_STATE_2026010{i}_000000.md").write_text("x", encoding="utf-8")
        self.run_hook("precompact", {"trigger": "auto"})
        self.assertLessEqual(len(list(store.glob("SESSION_STATE_2*.md"))), 5)

    def test_settings_hooks_registered(self):
        s = json.loads((ROOT / ".claude" / "settings.json").read_text(encoding="utf-8"))
        self.assertEqual({m["matcher"] for m in s["hooks"]["PreCompact"]}, {"manual", "auto"})
        self.assertIn("compact", s["hooks"]["SessionStart"][0]["matcher"])


if __name__ == "__main__":
    unittest.main()
