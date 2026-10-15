"""マスタ管理: 列を追加(0.9.0: <メイン>_設定 は使わない。どの表も同じ)"""
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

    def add(self, **body):
        return self.c.post("/api/master/add-column", json=body, headers=self.h)


class AddColumnTest(_DbCase):
    def test_main_add_column_without_setting_table(self):
        n_conv = len(self.q(f"SELECT * FROM [{CONV}]"))
        r = self.add(table=MAIN, name="部署", kind="text", fill="梱包")
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

    def test_other_master_and_kinds(self):
        r = self.add(table="ID", name="内線", kind="integer", fill="12")
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual(r.get_json()["setting_table"], "")
        self.assertEqual({v for (v,) in self.q("SELECT 内線 FROM ID")}, {12})
        self.assertEqual(self.q("SELECT typeof(内線) FROM ID LIMIT 1"), [("integer",)])
        r = self.add(table="ID", name="係数", kind="number", fill="abc")
        self.assertEqual((r.status_code, r.get_json()["reason"]), (422, "bad_fill"))
        self.assertNotIn("係数", self.cols("ID"))

    def test_names_refused(self):
        for name, reason in (("作業名", "duplicate"), ("ra", "duplicate"), ("", "bad_name"),
                             (" 部署", "bad_name"), ("a[b", "bad_name"), ("改\n行", "bad_name"), ("x" * 65, "bad_name")):
            r = self.add(table=MAIN, name=name)
            self.assertEqual((r.status_code, r.get_json()["reason"]), (422, reason), repr(name))
        self.assertEqual(len(self.cols(MAIN)), 25)

    def test_fixed_tables_refused(self):
        r = self.add(table="全般設定", name="追加")
        self.assertEqual((r.status_code, r.get_json()["reason"]), (422, "fixed_columns"))
        # _設定 はもうマスタ管理に出さない(使わない表)
        self.assertEqual(self.add(table=CONV, name="追加").status_code, 403)

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


class DeleteNullTest(_DbCase):
    def test_row_with_null_cells_can_be_deleted(self):
        """Access から写した行(空の欄が NULL)も、マスタ管理で消せる。"""
        rows = self.c.post("/api/master/rows", json={"table": "ID"}, headers=self.h).get_json()["rows"]
        target = rows[-1]
        self.assertIn("", target)                                     # 画面では空(本体は NULL)
        before = len(self.q("SELECT * FROM ID"))
        r = self.c.post("/api/master/delete", json={"table": "ID", "row": target, "confirmed": True,
                                                    "answer": True}, headers=self.h)
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual(len(self.q("SELECT * FROM ID")), before - 1)


class LowerLevelTest(_DbCase):
    level = 4

    def test_columns_need_level5(self):
        t = self.c.post("/api/master/tables", json={}, headers=self.h).get_json()
        self.assertIn(MAIN, t["tables"])                               # 0.9.0: 本体も行を直せる(Level 3 から)
        self.assertTrue(t["caps"][MAIN]["rows"])
        self.assertFalse(t["caps"]["ID"]["columns"])
        self.assertTrue(t["caps"]["ID"]["rows"])
        r = self.add(table="ID", name="内線")
        self.assertEqual((r.status_code, r.get_json()["reason"]), (403, "level"))
        # ふつうの行の追加(ID)は今までどおり
        f = self.c.post("/api/master/form", json={"table": "ID", "mode": "ADDNEW"}, headers=self.h).get_json()
        row = [f["fields"][0]["value"], "試験", "Xfa-Shiken", "PC", "", "1", ""]
        r = self.c.post("/api/master/execute", headers=self.h, json={
            "table": "ID", "mode": "ADDNEW", "values": row, "key": row[0], "confirmed": True, "answer": True})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
