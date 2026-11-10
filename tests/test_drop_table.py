"""一覧の「表を削除」(0.45.0)── 取り返しがつかないので、名前を打って確かめ、控えを取ってから消す"""
from __future__ import annotations

import sqlite3

from dbviewer import master_admin
from dbviewer.settings_tables import load_setup, table_names
from tests.test_master_columns import MAIN, _DbCase


class DropTableTest(_DbCase):
    def setUp(self):
        super().setUp()
        with sqlite3.connect(self.path) as c:
            c.execute("CREATE TABLE 古い表 (名前 TEXT)")
            c.executemany("INSERT INTO 古い表 VALUES (?)", [("a",), ("b",)])

    def pick(self, table):
        return self.c.post("/api/list/data", json={"table": table}, headers=self.h).get_json()

    def drop(self, confirm):
        return self.c.post("/api/list/drop-table", json={"confirm": confirm}, headers=self.h)

    def tables(self):
        with sqlite3.connect(self.path) as c:
            return table_names(c)

    def test_drop_with_name(self):
        d = self.pick("古い表")
        self.assertEqual(d["table"], "古い表")
        self.assertTrue(d["setup"]["can_drop_table"])
        r = self.drop("古い")                                  # 名前が違えば消さない
        self.assertEqual(r.status_code, 422)
        self.assertIn("古い表", self.tables())
        r = self.drop("古い表")
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertIn("2 行", r.get_json()["message"])
        self.assertNotIn("古い表", self.tables())
        backup = r.get_json()["backup"]
        with sqlite3.connect(backup) as c:                     # 控えには残っている
            self.assertEqual(c.execute("SELECT COUNT(*) FROM 古い表").fetchone()[0], 2)
        d = self.pick("")                                      # 選んでいた表が無くなっても一覧は開ける
        self.assertEqual(d["table"], MAIN)

    def test_main_table_of_general_settings_is_kept(self):
        d = self.pick(MAIN)
        self.assertFalse(d["setup"]["can_drop_table"])
        self.assertEqual(self.drop(MAIN).status_code, 422)
        self.assertIn(MAIN, self.tables())

    def test_system_tables_refused(self):
        from dbviewer import shared_db
        conn = shared_db.connect(self.path)
        self.addCleanup(conn.close)
        r = master_admin.drop_table(conn, load_setup(conn), "全般設定", confirm="全般設定", level=5)
        self.assertEqual(r.reason, master_admin.REFUSE_FIXED)


class DropTableLowLevelTest(DropTableTest):
    level = 1

    def test_drop_with_name(self):
        d = self.pick("古い表")
        self.assertFalse(d["setup"]["can_drop_table"])
        self.assertEqual(self.drop("古い表").status_code, 403)
        self.assertIn("古い表", self.tables())

    def test_main_table_of_general_settings_is_kept(self):
        pass

    def test_system_tables_refused(self):
        pass
