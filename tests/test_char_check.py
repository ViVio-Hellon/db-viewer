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

    def test_clean(self) -> None:
        self.assertEqual(cc.clean("\ufeff0-1-2\u00a0"), "0-1-2")
        self.assertEqual(cc.clean("0-2-\u200b2"), "0-2-2")
        self.assertEqual(cc.clean("改行\nあり "), "改行\nあり")                 # 途中の改行は残す
        self.assertEqual(cc.clean("'10-12-5"), "'10-12-5")                         # ' は頼まれたときだけ
        self.assertEqual(cc.clean("'10-12-5", apostrophe=True), "10-12-5")

    def test_clean_table(self) -> None:
        backups = []
        r = cc.clean_table(self.conn, "作業", backup=lambda: backups.append(1) or "控え.sqlite3")
        self.assertTrue(r["ok"], r)
        self.assertEqual((r["changed"], backups), (3, [1]))                         # 書く前に控え 1 回
        vals = dict(self.conn.execute("SELECT 管理番号, 作業NO FROM 作業"))
        self.assertEqual((vals[2], vals[3], vals[4], vals[6]), ("0-1-2", "0-1-3", "'10-12-5", "0-2-2"))
        self.assertEqual(self.conn.execute("SELECT 作業名 FROM 作業 WHERE 管理番号 = 5").fetchone()[0], "改行\nあり")
        self.assertEqual(cc.check_table(self.conn, "作業")["total"], 2)              # 残るのは ' と途中の改行
        r = cc.clean_table(self.conn, "作業", apostrophe=True, backup=lambda: "控え")
        self.assertEqual(r["changed"], 1)
        self.assertEqual(cc.clean_table(self.conn, "作業", backup=lambda: "控え")["changed"], 0)

    def test_no_backup_no_write(self) -> None:
        r = cc.clean_table(self.conn, "作業", backup=lambda: "")
        self.assertFalse(r["ok"])
        self.assertEqual(self.conn.execute("SELECT 作業NO FROM 作業 WHERE 管理番号 = 2").fetchone()[0], "\ufeff0-1-2\u00a0")

    def test_failure_rolls_back(self) -> None:
        self.conn.execute("CREATE UNIQUE INDEX u ON 作業(作業NO)")
        self.conn.execute("INSERT INTO 作業 VALUES (8, '0-1-3', 'かぶる')")             # 0-1-3 と 0-1-3␣ がそろうとかぶる
        self.conn.commit()
        r = cc.clean_table(self.conn, "作業", backup=lambda: "控え")
        self.assertFalse(r["ok"])
        self.assertEqual(self.conn.execute("SELECT 作業NO FROM 作業 WHERE 管理番号 = 2").fetchone()[0], "\ufeff0-1-2\u00a0")

    def test_unknown_table(self) -> None:
        with self.assertRaises(ValueError):
            cc.check_table(self.conn, "無い表")



class SameListTest(unittest.TestCase):
    """点検で見つかる見えない文字と、開くときの照合で取る文字が同じ(0.46.0)。"""
    def test_check_and_match_agree(self):
        from dbviewer import simple_table
        for ch, name in cc.INVISIBLE.items():
            self.assertTrue(cc.inspect_value("10-12-2" + ch), name)
            self.assertEqual(simple_table.clean_value(ch + "10-12-2" + ch), "10-12-2", name)
        self.assertEqual(simple_table.clean_value("10\u00ad12"), "10\u00ad12")      # 中は変えない(今までどおり)

if __name__ == "__main__":
    unittest.main()
