"""Access 取り込み(TSV → sqlite3)の試験。基準データは tests/fixtures/realdata。"""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from dbviewer.accdb_bridge import importer

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "realdata"


class UnescapeTest(unittest.TestCase):
    def test_escapes(self):
        self.assertEqual(importer.unescape("a\\tb\\nc\\rd\\\\e"), "a\tb\nc\rd\\e")
        self.assertIsNone(importer.unescape("\\N"))
        self.assertEqual(importer.unescape("plain"), "plain")

    def test_type_map(self):
        self.assertEqual(importer.sqlite_type(3), "INTEGER")
        self.assertEqual(importer.sqlite_type(11), "INTEGER")
        self.assertEqual(importer.sqlite_type(6), "REAL")
        self.assertEqual(importer.sqlite_type(7), "TEXT")
        self.assertEqual(importer.sqlite_type(202), "TEXT")


class ImportFixturesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.target = Path(self.tmp.name) / "作業標準管理システム.sqlite3"

    def test_builds_all_tables(self):
        steps = []
        result = importer.import_tsv_dir(FIXTURES, self.target,
                                         progress=lambda p, m: steps.append((p, m)))
        names = dict(result.tables)
        self.assertEqual(names["作業標準管理"], 327)
        self.assertEqual(names["作業標準管理_設定"], 25)
        self.assertEqual(names["ID"], 5)
        self.assertEqual(names["フィルタ"], 11)
        self.assertEqual(names["タイプ"], 7)
        self.assertEqual(names["全般設定"], 4)
        self.assertEqual(names["フィルタ_設定"], 5)
        self.assertTrue(steps and steps[-1][0] >= 90)
        conn = sqlite3.connect(str(self.target))
        try:
            # 型: 管理番号 は INTEGER、ほかは TEXT
            cols = {r[1]: r[2] for r in conn.execute("PRAGMA table_info(作業標準管理)")}
            self.assertEqual(cols["管理番号"], "INTEGER")
            self.assertEqual(cols["作業名"], "TEXT")
            # 一意な 管理番号 には UNIQUE 索引
            idx = conn.execute("SELECT sql FROM sqlite_master WHERE name='ix_作業標準管理_管理番号'").fetchone()[0]
            self.assertIn("UNIQUE", idx)
            # 値がそのまま(圧縮ユニコードの行も含む)
            row = conn.execute("SELECT 作業名, 課長承認 FROM 作業標準管理 WHERE 管理番号=18").fetchone()
            self.assertEqual(row[0], "ﾌｫｰｸによる分銅運搬作業 (0-2-4-1改訂)削除")
            self.assertEqual(row[1], "未")
            self.assertEqual(conn.execute("PRAGMA journal_mode").fetchone()[0], "delete")
        finally:
            conn.close()

    def test_swaps_in_with_backup(self):
        importer.import_tsv_dir(FIXTURES, self.target)
        first = self.target.read_bytes()[:100]
        result = importer.import_tsv_dir(FIXTURES, self.target)
        self.assertIsNotNone(result.backup)
        self.assertTrue(result.backup.exists())
        self.assertEqual(result.backup.read_bytes()[:100], first)
        self.assertFalse(self.target.with_name(self.target.name + ".tmp").exists())

    def test_duplicate_key_gets_plain_index_and_warning(self):
        tables = [importer.Table("T", [importer.Column("管理番号", 3), importer.Column("x")],
                                 [["1", "a"], ["1", "b"], ["2", "c"]])]
        result = importer.write_sqlite(tables, self.target)
        self.assertTrue(any("重複" in w for w in result.warnings))
        conn = sqlite3.connect(str(self.target))
        idx = conn.execute("SELECT sql FROM sqlite_master WHERE name='ix_T_管理番号'").fetchone()[0]
        conn.close()
        self.assertNotIn("UNIQUE", idx)

    def test_cscript_missing_is_a_clear_error(self):
        if importer.shutil.which("cscript"):
            self.skipTest("Windows")
        with self.assertRaises(importer.BridgeError):
            importer.list_tables(FIXTURES / "nothing.accdb")


if __name__ == "__main__":
    unittest.main()
