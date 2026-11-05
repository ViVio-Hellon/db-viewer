"""使い回された PID でロックが残り続けない(0.44.0)

ロックには PID を書く。異常終了・再起動のあと、Windows は**同じ番号を別のプロセスに使い回す**。
PID だけで「居る」と見ると、無関係なプロセスを「起動中」と取り違え、stop.bat も「確かめられないので
止めません」で終わってロックが消えなかった。ここでは本物の別プロセスを起こして確かめる。
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import launch_guard  # noqa: E402
import process_manager  # noqa: E402


def _closed_port() -> int:
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class StaleLockTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        for name, fn in (("lock_path", lambda mode: self.dir / f"{mode}.lock"),
                         ("_app_runtime", lambda name: self.dir / name)):
            p = mock.patch.object(launch_guard, name, fn)
            p.start()
            self.addCleanup(p.stop)

    def other(self) -> subprocess.Popen:
        """無関係なプロセス(このアプリではない)。"""
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        self.addCleanup(lambda: (proc.kill(), proc.wait()))
        time.sleep(0.3)
        return proc

    def lock(self, pid: int, *, written: float, proc_started: float = 0.0) -> None:
        info = launch_guard.LockInfo(app_id="dbviewer", mode="main", pid=pid, port=_closed_port(),
                                     url="http://127.0.0.1:1/", started_at=written,
                                     app_root=str(self.dir / "どこか別"), proc_started=proc_started)
        launch_guard.write_lock(info)

    # -- 判定 ---------------------------------------------------------
    def test_own_start_time_is_read(self):
        started = launch_guard.own_start_epoch()
        self.assertGreater(started, 0)
        self.assertLessEqual(started, time.time() + 1)
        self.assertTrue(launch_guard.owner_verified(launch_guard.os.getpid(), started))

    def test_reused_pid_with_start_time_is_not_owner(self):
        proc = self.other()
        real = launch_guard.process_start_epoch(proc.pid)
        self.assertTrue(launch_guard.lock_owner_alive(proc.pid, real))
        self.assertFalse(launch_guard.lock_owner_alive(proc.pid, real - 3600))   # 起動時刻が違う = 別人

    def test_legacy_lock_process_started_later_is_not_owner(self):
        """古い版のロック(起動時刻なし): 書いた時刻より後に起動したプロセスは書いた本人ではない。"""
        proc = self.other()
        self.assertFalse(launch_guard.lock_owner_alive(proc.pid, 0.0, time.time() - 3600))
        self.assertTrue(launch_guard.lock_owner_alive(proc.pid, 0.0, time.time() + 60))

    # -- 起動 ---------------------------------------------------------
    def test_start_cleans_reused_pid_lock(self):
        proc = self.other()
        self.lock(proc.pid, written=time.time() - 3600)          # 古い版のロック・番号を使い回された
        guard = launch_guard.check_existing("main")
        self.assertTrue(guard.should_start)
        self.assertIsNone(launch_guard.read_lock("main"))
        self.assertIsNone(proc.poll(), "無関係なプロセスを終わらせてはいけない")

    def test_start_ends_unresponsive_owner(self):
        """書いた本人が残っているのに応答しない → 待ってから終わらせて起動し直す。"""
        proc = self.other()
        self.lock(proc.pid, written=time.time(), proc_started=launch_guard.process_start_epoch(proc.pid))
        with mock.patch.object(launch_guard, "UNRESPONSIVE_WAIT_SEC", 0.5):
            guard = launch_guard.check_existing("main")
        self.assertTrue(guard.should_start)
        self.assertIsNone(launch_guard.read_lock("main"))
        proc.wait(timeout=5)

    # -- stop.bat -----------------------------------------------------
    def test_stop_cleans_reused_pid_lock(self):
        proc = self.other()
        self.lock(proc.pid, written=time.time() - 3600, proc_started=time.time() - 3600)
        with self.assertLogs(level="INFO") as logs:
            result = process_manager.stop("main")
        self.assertTrue(result.stopped, result.message)
        self.assertIn("片付けました。Start.vbs で起動できます", result.message)
        self.assertTrue(any("片付けました。Start.vbs で起動できます" in m for m in logs.output))
        self.assertIsNone(launch_guard.read_lock("main"))
        self.assertIsNone(proc.poll(), "無関係なプロセスを終わらせてはいけない")

    def test_desktop_marker_with_reused_pid_is_not_running(self):
        proc = self.other()
        (self.dir / launch_guard.DESKTOP_MARKER).write_text(
            '{"pid": %d, "started_at": %f, "proc_started": %f}' % (proc.pid, time.time() - 3600, time.time() - 3600),
            encoding="utf-8")
        self.assertIsNone(launch_guard.desktop_running())


if __name__ == "__main__":
    unittest.main()
