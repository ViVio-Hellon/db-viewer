"""この PC の設定(user_config.json)が、読み書きが重なっても消えない(0.46.3)

以前はファイルを直接書き直していて、書いている最中に読むと空に見え、「空 + 1 項目」で書き戻して
ほかの設定(見る DB・設定ファイル・ログの場所)がぜんぶ消えた(NLM-NGY-240638 で 11/19 に起きた)。
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from dbviewer import config, user_settings

ROOT = Path(__file__).resolve().parent.parent


class SafeSettingsTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "user_config.json"
        p = mock.patch.object(config, "USER_CONFIG_PATH", self.path)
        p.start()
        self.addCleanup(p.stop)

    def test_threads_do_not_lose_keys(self):
        user_settings.save("db_path", "x.sqlite3")

        def work(i):
            for j in range(20):
                user_settings.save(f"k{i}", j)
                user_settings.get("db_path")
        ts = [threading.Thread(target=work, args=(i,)) for i in range(6)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(data["db_path"], "x.sqlite3")
        self.assertEqual({k: data[k] for k in (f"k{i}" for i in range(6))}, {f"k{i}": 19 for i in range(6)})

    def test_other_process_never_sees_empty_file(self):
        """別のプロセス(デスクトップ版の外枠など)が書いている最中に読んでも、空や半端は見えない。"""
        user_settings.save("db_path", "x.sqlite3")
        code = ("import json,sys,time\nfrom pathlib import Path\np=Path(sys.argv[1]);bad=0\n"
                "end=time.time()+3\nwhile time.time()<end:\n"
                "  try:\n    d=json.loads(p.read_text(encoding='utf-8'))\n    bad+= 'db_path' not in d\n"
                "  except Exception: bad+=1\nprint(bad)")
        reader = subprocess.Popen([sys.executable, "-c", code, str(self.path)], stdout=subprocess.PIPE, text=True)
        for i in range(300):
            user_settings.save("n", i)
        self.assertEqual(reader.communicate(timeout=30)[0].strip(), "0")

    def test_broken_file_uses_backup_and_is_not_wiped(self):
        user_settings.save("db_path", "x.sqlite3")
        user_settings.save("log_dir", "L")
        self.path.write_text("", encoding="utf-8")               # 壊れた(空)
        self.assertEqual(user_settings.get("db_path"), "x.sqlite3")
        user_settings.save("last_user", "u")
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual((data["db_path"], data["log_dir"], data["last_user"]), ("x.sqlite3", "L", "u"))


if __name__ == "__main__":
    unittest.main()
