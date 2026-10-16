"""本体を組み立てられなかったとき、Python を残さない(0.11.1)

all-tools で起きた: 起動の途中で本体を組み立てられない(フォルダの一部が欠けた・設定ファイルが壊れた・
部品が無い)→ 待機画面が理由を出したまま Python が残る → 待機画面が止める合図に答えないので stop.bat でも
--force でも止まらない → stop.bat は確かめずに「終わってもらいました」と出す → フォルダを消せない。

ここでは本物の start_app.py を子として起こし、組み立てをわざと失敗させて確かめる。
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
# 自分自身(127.0.0.1)への問い合わせはプロキシを通さない(launch_guard と同じ)
_LOCAL = urllib.request.build_opener(urllib.request.ProxyHandler({}))

# 組み立てを失敗させて start_app を動かす子
CHILD = """
import sys
sys.path.insert(0, {root!r})
import server
def boom(self):
    raise RuntimeError("必要な部品がありません(試験)")
server.AppServer.build = boom
from dbviewer import app_config
app_config.port_candidates = lambda mode="main": [{port}]   # 前の試験の TIME_WAIT に当たらない
import start_app
start_app.FAILED_IDLE_SEC = {idle}
sys.exit(start_app.main(["--no-browser"]))
"""


class FailedStartTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        config = root / "user_config.json"
        config.write_text(json.dumps({"online": False, "local_dir": str(root), "db_file": "無い.sqlite3",
                                      "access_log": False}), encoding="utf-8")
        self.env = dict(os.environ, DBVIEWER_CONFIG_PATH=str(config), DBVIEWER_LOG_DIR=str(root / "logs"),
                        DBVIEWER_LOCAL_DIR=str(root / "local"))
        self.lock = root / "local" / "runtime" / "main.lock"

    def start(self, idle: float = 600) -> subprocess.Popen:
        with socket.socket() as sock:                                  # OS に空いている番号を選ばせる
            sock.bind(("127.0.0.1", 0))
            free = sock.getsockname()[1]
        proc = subprocess.Popen([sys.executable, "-c", CHILD.format(root=str(ROOT), idle=idle, port=free)],
                                env=self.env, cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        self.addCleanup(lambda: (proc.poll() is None and proc.kill(), proc.wait(timeout=10), proc.stdout.close()))
        # 最初の錠は仮置き(port=0・トークン無し)。待ち受けが始まってから本物に書き換わる
        deadline = time.monotonic() + 60
        info = {}
        while time.monotonic() < deadline and proc.poll() is None:
            try:
                info = json.loads(self.lock.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                info = {}
            if info.get("port") and info.get("token"):
                break
            time.sleep(0.05)
        self.assertTrue(info.get("port") and info.get("token"), "待ち受けが始まらない")
        self.port, self.token = int(info["port"]), info["token"]
        # 待機画面が理由を出している(本体は無い)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            health = self.health()
            if health and health.get("startup_error"):
                break
            time.sleep(0.1)
        self.assertIn("必要な部品がありません", (health or {}).get("startup_error", ""))
        return proc

    def health(self):
        try:
            with _LOCAL.open(f"http://127.0.0.1:{self.port}/api/health", timeout=2) as r:
                return json.loads(r.read().decode("utf-8"))
        except OSError:
            return None

    def post_shutdown(self, token: str):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/shutdown", data=b"{}", method="POST",
                                     headers={"Content-Type": "application/json", "X-Tool-Token": token})
        try:
            with _LOCAL.open(req, timeout=3) as r:
                return r.status, json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def test_stop_bat_stops_it_and_says_so_only_after_it_is_gone(self):
        proc = self.start()
        done = subprocess.run([sys.executable, str(ROOT / "process_manager.py"), "--all"],
                              env=self.env, cwd=str(ROOT), capture_output=True, timeout=60)
        out = done.stdout.decode("utf-8", "replace")
        self.assertEqual(done.returncode, 0, out + done.stderr.decode("utf-8", "replace")[-800:])
        self.assertIn("正常に終了しました", out)
        self.assertIsNotNone(proc.poll(), "stop.bat が「終了しました」と言ったのに Python が残っている")
        self.assertFalse(self.lock.exists())

    def test_waiting_screen_answers_shutdown_with_token(self):
        proc = self.start()
        status, body = self.post_shutdown("wrong-token")
        self.assertEqual(status, 401)                                  # トークンが無ければ止めない
        self.assertIsNone(proc.poll())
        status, body = self.post_shutdown(self.token)                  # 失敗の画面の「終了する」と同じ
        self.assertEqual((status, body.get("stopped")), (200, True))
        proc.wait(timeout=20)
        self.assertEqual(proc.returncode, 1)

    def test_ends_by_itself_when_the_screen_is_gone(self):
        proc = self.start(idle=2)                                      # 画面からの問い合わせが 2 秒来なければ
        proc.wait(timeout=30)
        self.assertEqual(proc.returncode, 1)
        out = proc.stdout.read().decode("utf-8", "replace")
        self.assertIn("来ないので終了します", out)
        self.assertNotIn("Traceback (most recent call last)", out.split("来ないので終了します")[-1])  # 止め方で落ちない


class StopReportsTruthTest(unittest.TestCase):
    """stop.bat は、プロセスが本当に消えたのを確かめてから「止めた」と言う。"""

    def test_force_kill_that_does_not_end_is_not_reported_as_stopped(self):
        import launch_guard
        import process_manager as pm
        info = mock.Mock(pid=4242, port=1, token="", app_root=str(ROOT))
        with mock.patch.object(launch_guard, "is_process_alive", return_value=True), \
                mock.patch.object(pm, "_looks_like_our_process", return_value=True), \
                mock.patch.object(pm.os, "kill"), \
                mock.patch.object(pm, "FORCE_WAIT_SEC", 0.3), \
                mock.patch.object(launch_guard, "remove_lock") as removed:
            r = pm._stop_by_pid("main", info, pm.StopResult("main"), force=True)
        self.assertFalse(r.stopped)
        self.assertIn("まだ残っています", r.message)
        self.assertIn("4242", r.message)
        removed.assert_not_called()                                    # 錠も消さない(残っているので)

    def test_graceful_needs_pid_gone_too(self):
        import launch_guard
        import process_manager as pm
        info = mock.Mock(pid=4242, port=1, token="t", app_root=str(ROOT))
        with mock.patch.object(launch_guard, "read_lock", return_value=info), \
                mock.patch.object(launch_guard, "probe_health", return_value={"app_id": "x"}), \
                mock.patch.object(launch_guard, "is_our_app", return_value=True), \
                mock.patch.object(pm, "_request_shutdown", return_value={"ok": True}), \
                mock.patch.object(pm, "_wait_gone", return_value=True), \
                mock.patch.object(pm, "_wait_pid_gone", return_value=False), \
                mock.patch.object(pm, "_stop_by_pid", return_value="pid-path") as by_pid:
            self.assertEqual(pm.stop("main"), "pid-path")              # 待ち受けが閉じても PID が残れば PID で
        by_pid.assert_called_once()

    def test_command_line_falls_back_to_powershell_without_wmic(self):
        import launch_guard
        calls = []

        def run(cmd, **kw):
            calls.append(cmd[0])
            if cmd[0] == "wmic":
                raise FileNotFoundError("wmic がありません")
            return mock.Mock(stdout="C:\\\\Python\\\\pythonw.exe start_app.py\\r\\n")
        with mock.patch.object(launch_guard.subprocess, "run", side_effect=run):
            got = launch_guard._command_line_windows(4242)
        self.assertEqual(calls, ["wmic", "powershell"])
        self.assertIn("start_app.py", got)


if __name__ == "__main__":
    unittest.main()
