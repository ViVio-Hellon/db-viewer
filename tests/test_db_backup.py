"""DB の控え(0.46.0 で SQLite の backup に揃えた)"""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from dbviewer import db_backup


class CopyDbTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.src = self.dir / "本体 #1 ?50%.sqlite3"           # URI で逃がす文字も入れる
        with closing(sqlite3.connect(self.src)) as c:
            c.execute("CREATE TABLE 作業 (作業NO TEXT)")
            c.executemany("INSERT INTO 作業 VALUES (?)", [("0-1-1",), ("10-12-2",)])
            c.commit()

    def rows(self, p):
        with closing(sqlite3.connect(p)) as c:
            return c.execute("SELECT 作業NO FROM 作業 ORDER BY 1").fetchall()

    def test_backup_has_same_rows(self):
        target = self.dir / "控え" / "a.sqlite3"
        self.assertEqual(db_backup.copy_db(self.src, target), "backup")
        self.assertEqual(self.rows(target), self.rows(self.src))
        with closing(sqlite3.connect(target)) as c:
            self.assertEqual(c.execute("PRAGMA integrity_check").fetchone()[0], "ok")

    def test_uncommitted_write_is_not_copied(self):
        """ほかの接続が書いている最中でも、書き終わった中身だけが控えに入る。"""
        writer = sqlite3.connect(self.src, isolation_level=None)
        self.addCleanup(writer.close)
        writer.execute("BEGIN IMMEDIATE")
        writer.execute("INSERT INTO 作業 VALUES ('書きかけ')")
        target = self.dir / "b.sqlite3"
        self.assertEqual(db_backup.copy_db(self.src, target), "backup")
        writer.execute("ROLLBACK")
        self.assertEqual(self.rows(target), [("0-1-1",), ("10-12-2",)])

    def test_not_a_database_is_copied(self):
        """DB でないファイルは、今までどおり丸写しにする。"""
        odd = self.dir / "壊れ.sqlite3"
        odd.write_bytes(b"not a database at all" * 100)
        target = self.dir / "c.sqlite3"
        with self.assertLogs("dbviewer.db_backup", "WARNING"):
            self.assertEqual(db_backup.copy_db(odd, target), "copy")
        self.assertEqual(target.read_bytes(), odd.read_bytes())

    def test_missing_source_raises_oserror(self):
        with self.assertRaises(OSError):
            db_backup.copy_db(self.dir / "無い.sqlite3", self.dir / "d.sqlite3")


if __name__ == "__main__":
    unittest.main()
