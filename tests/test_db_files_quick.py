"""フォルダの中の DB を軽く調べる(0.46.3)── 結果は今までと同じ・丸ごと写さない・2 回目は調べない"""
from __future__ import annotations

import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from dbviewer import db_files, source_db
from tests._fixture_db import fresh_db_path


class QuickInspectTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.vba = fresh_db_path(self.dir)                              # 全般設定のある DB
        with sqlite3.connect(self.dir / "simple.sqlite3") as c:
            c.execute("CREATE TABLE 条件表 (a)")
        with sqlite3.connect(self.dir / "empty.sqlite3") as c:
            c.execute("CREATE TABLE sqlite_x_dummy (a)") if False else None
        (self.dir / "Thumbs.db").write_bytes(b"\xd0\xcf\x11\xe0 not sqlite")
        (self.dir / "broken.db").write_bytes(b"x" * 100)
        db_files._cache.clear()

    def test_same_result_as_before_without_copying(self):
        for p in sorted(self.dir.glob("*.sqlite3")):
            before = db_files.inspect_sqlite(p)
            db_files._cache.clear()
            with mock.patch.object(source_db, "_connect", side_effect=AssertionError("写してはいけない")):
                got = db_files.inspect_cached(p)
            self.assertEqual(got, before, p.name)

    def test_non_sqlite_is_quick_other(self):
        with mock.patch.object(source_db, "_connect", side_effect=AssertionError("開いてはいけない")):
            for name in ("Thumbs.db", "broken.db"):
                self.assertEqual(db_files.inspect_cached(self.dir / name)["kind"], db_files.KIND_OTHER)

    def test_second_scan_uses_cache(self):
        db_files.scan(self.dir)
        with mock.patch.object(db_files, "_quick", side_effect=AssertionError("2 回目は調べない")):
            names = [f["name"] for f in db_files.scan(self.dir)["files"]]
        self.assertIn("simple.sqlite3", names)


if __name__ == "__main__":
    unittest.main()
