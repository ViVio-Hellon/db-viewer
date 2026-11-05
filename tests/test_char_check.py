"""見えない文字の点検(0.42.0)"""
from __future__ import annotations

import sqlite3
import unittest

from dbviewer import char_check as cc


class CharCheckTest(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute("CREATE TABLE 作業 (管理番号 INTEGER, 作業NO TEXT, 作業名 TEXT)")
        self.conn.executemany("INSERT INTO 作業 VALUES (?, ?, ?)", [
            (1, "0-1-1", "ハンマーの取り扱い"),             # きれい
            (2, "﻿0-1-2 ", "ディスク"),            # BOM・NBSP
            (3, "0-1-3 ", "カッター"),                       # 後ろに半角空白
            (4, "'10-12-5", "敷板"),                         # Excel の名残
            (5, "0-2-1", "改行\nあり"),                      # 途中に改行
            (6, "0-2-​2", "ゼロ幅"),                    # 途中にゼロ幅スペース
            (7, 123, None),
        ])
        self.addCleanup(self.conn.close)

    def test_finds_and_marks(self) -> None:
        r = cc.check_table(self.conn, "作業")
        got = {(f["head"], f["column"]): f for f in r["findings"]}
        self.assertNotIn(("1", "作業NO"), got)
        self.assertEqual(got[("2", "作業NO")]["shown"], "⟨BOM⟩0-1-2⟨NBSP⟩")
        self.assertTrue(any("見えない文字" in k for k in got[("2", "作業NO")]["kinds"]))
        self.assertEqual(got[("3", "作業NO")]["shown"], "0-1-3⟨半角空白⟩")
        self.assertTrue(any("先頭に '" in k for k in got[("4", "作業NO")]["kinds"]))
        self.assertIn("途中に改行", got[("5", "作業名")]["kinds"])
        self.assertEqual(got[("6", "作業NO")]["shown"], "0-2-⟨ゼロ幅スペース⟩2")
        self.assertEqual((r["total"], r["rows"], r["head"]), (5, 7, "管理番号"))
        self.assertEqual(r["by_column"], {"作業NO": 4, "作業名": 1})

    def test_unknown_table(self) -> None:
        with self.assertRaises(ValueError):
            cc.check_table(self.conn, "無い表")


if __name__ == "__main__":
    unittest.main()
