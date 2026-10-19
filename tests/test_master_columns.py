"""列を追加(一覧の「＋ 列を追加」。0.18.0 でマスタ管理の画面はなくした)"""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from dbviewer import auth, config, master_admin, shared_db, user_settings
from dbviewer.settings_tables import column_names, load_setup, master_file_check
from tests._fixture_db import fresh_db_path
from tests._web import TOKEN, make_client

MAIN, CONV = "作業標準管理", "作業標準管理_設定"


class _DbCase(unittest.TestCase):
    level = 5

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = fresh_db_path(Path(self.tmp.name))
        user_settings.save(config.KEY_ONLINE, False)
        user_settings.save(config.KEY_LOCAL_DIR, str(self.path.parent))
        user_settings.save(config.KEY_DB_FILE, self.path.name)
        auth.end()
        self.addCleanup(auth.end)
        auth.start(auth.LoginResult(True, user_id="Xfa-Ngyinazawa", level=self.level))
        self.c = make_client()
        self.h = {"X-Tool-Token": TOKEN}

    def q(self, sql, *args):
        with sqlite3.connect(self.path) as c:
            return c.execute(sql, args).fetchall()

    def cols(self, table):
        with sqlite3.connect(self.path) as c:
            return column_names(c, table)

    def problem(self):
        with sqlite3.connect(self.path) as c:
            return master_file_check(c, MAIN)

    def add(self, table=MAIN, **body):
        self.c.post("/api/list/data", json={"table": table}, headers=self.h)   # 一覧で表を選ぶ
        return self.c.post("/api/list/add-column", json=body, headers=self.h)


class AddColumnTest(_DbCase):
    def test_main_add_column_without_setting_table(self):
        n_conv = len(self.q(f"SELECT * FROM [{CONV}]"))
        r = self.add(name="部署", kind="text", fill="梱包")
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        j = r.get_json()
        self.assertEqual((j["column"], j["setting_table"]), ("部署", ""))
        # 本体の末尾に列、いまある行には入れた値。_設定 には触らない
        self.assertEqual(self.cols(MAIN)[-1], "部署")
        self.assertEqual({v for (v,) in self.q(f"SELECT 部署 FROM [{MAIN}]")}, {"梱包"})
        self.assertEqual(len(self.q(f"SELECT * FROM [{CONV}]")), n_conv)
        # 一覧にも出て、その場で直せる(行は rowid)
        d = self.c.post("/api/list/data", json={}, headers=self.h).get_json()
        names = [c["name"] for c in d["columns"]]
        self.assertIn("部署", names)
        row = next(r for r in d["rows"] if r[names.index("管理番号")] == "2")
        r = self.c.post("/api/list/row-save", headers=self.h, json={
            "table": MAIN, "rowid": row[d["key_index"]], "values": {"部署": "出荷"}, "seen": {"部署": "梱包"}})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual(self.q(f"SELECT 部署 FROM [{MAIN}] WHERE 管理番号=2"), [("出荷",)])
        # 書く前の控え
        self.assertTrue(Path(j["backup"]).is_file())

    def test_kinds(self):
        r = self.add(name="内線", kind="integer", fill="12")
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual({v for (v,) in self.q(f"SELECT 内線 FROM [{MAIN}]")}, {12})
        self.assertEqual(self.q(f"SELECT typeof(内線) FROM [{MAIN}] LIMIT 1"), [("integer",)])
        r = self.add(name="係数", kind="number", fill="abc")
        self.assertEqual((r.status_code, r.get_json()["reason"]), (422, "bad_fill"))
        self.assertNotIn("係数", self.cols(MAIN))

    def test_names_refused(self):
        for name, reason in (("作業名", "duplicate"), ("ra", "duplicate"), ("", "bad_name"),
                             (" 部署", "bad_name"), ("a[b", "bad_name"), ("改\n行", "bad_name"), ("x" * 65, "bad_name")):
            r = self.add(name=name)
            self.assertEqual((r.status_code, r.get_json()["reason"]), (422, reason), repr(name))
        self.assertEqual(len(self.cols(MAIN)), 25)

    def test_fixed_tables_refused(self):
        """列が決まっている表(全般設定・_設定)には足さない(一覧では選べない表だが、決まりとして)。"""
        conn = shared_db.connect(self.path)
        self.addCleanup(conn.close)
        for table in ("全般設定", CONV):
            r = master_admin.add_column(conn, load_setup(conn), table, "追加", level=5)
            self.assertEqual(r.reason, "fixed_columns", table)

    def test_works_with_any_setting_table_state(self):
        """_設定 が本体と合っていなくても・無くても、列は足せる。"""
        conn = shared_db.connect(self.path)
        self.addCleanup(conn.close)
        conn.execute(f"INSERT INTO [{CONV}] (管理番号, 項目名) VALUES ('99', '迷子')")
        conn.commit()
        r = master_admin.add_column(conn, load_setup(conn), MAIN, "部署", level=5)
        self.assertTrue(r.ok, r.message)
        conn.execute(f"DROP TABLE [{CONV}]")
        conn.commit()
        r = master_admin.add_column(conn, load_setup(conn), MAIN, "係", level=5)
        self.assertTrue(r.ok, r.message)
        self.assertEqual(column_names(conn, MAIN)[-2:], ["部署", "係"])


class LowerLevelTest(_DbCase):
    level = 1                                                          # 閲覧のみ

    def test_columns_need_edit(self):
        r = self.add(name="内線")
        self.assertEqual((r.status_code, r.get_json()["reason"]), (403, "level"))
        self.assertNotIn("内線", self.cols(MAIN))


if __name__ == "__main__":
    unittest.main()
