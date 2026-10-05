"""マスタ管理: 列を追加・行を追加(本体と <メイン>_設定 を合わせたまま)"""
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
    def test_main_gets_setting_row_too(self):
        n_conv = len(self.q(f"SELECT * FROM [{CONV}]"))
        r = self.add(table=MAIN, name="部署", kind="text", fill="梱包")
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        j = r.get_json()
        self.assertEqual((j["column"], j["setting_table"]), ("部署", CONV))
        # 本体の末尾に列、いまある行には入れた値
        self.assertEqual(self.cols(MAIN)[-1], "部署")
        self.assertEqual({v for (v,) in self.q(f"SELECT 部署 FROM [{MAIN}]")}, {"梱包"})
        # _設定 の末尾に同じ名前の行(番号は最大 + 1、ほかは空 = 自由入力)
        rows = self.q(f"SELECT 管理番号, 項目名, 入力形式 FROM [{CONV}]")
        self.assertEqual(len(rows), n_conv + 1)
        self.assertEqual(rows[-1][1:], ("部署", None))
        self.assertEqual(int(rows[-1][0]), max(int(x[0]) for x in rows[:-1]) + 1)
        self.assertEqual(j["setting_key"], str(rows[-1][0]))
        # 起動の照合は通る。一覧にも出て、その場で直せる
        self.assertEqual(self.problem(), "")
        d = self.c.post("/api/list/data", json={}, headers=self.h).get_json()
        self.assertIn("部署", [c["name"] for c in d["columns"]])
        r = self.c.post("/api/list/cell", json={"key": "2", "column": "部署", "value": "出荷", "seen": "梱包"},
                        headers=self.h)
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual(self.q(f"SELECT 部署 FROM [{MAIN}] WHERE 管理番号=2"), [("出荷",)])
        # 書く前の控え
        self.assertTrue(Path(j["backup"]).is_file())

    def test_other_master_and_kinds(self):
        r = self.add(table="ID", name="内線", kind="integer", fill="12")
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual(r.get_json()["setting_table"], "")            # ID_設定 は無い
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
        for table in ("全般設定", CONV):
            r = self.add(table=table, name="追加")
            self.assertEqual((r.status_code, r.get_json()["reason"]), (422, "fixed_columns"), table)

    def test_rollback_when_main_would_break(self):
        """本体と _設定 が合わなくなるなら、足さずに戻す(同じ取引)。"""
        conn = shared_db.connect(self.path)
        self.addCleanup(conn.close)
        setup = load_setup(conn)
        conn.execute(f"INSERT INTO [{CONV}] (管理番号, 項目名) VALUES ('99', '迷子')")   # すでに合っていない
        conn.commit()
        r = master_admin.add_column(conn, setup, MAIN, "部署", level=5)
        self.assertFalse(r.ok)
        self.assertIn("やめました", r.message)
        self.assertNotIn("部署", column_names(conn, MAIN))
        self.assertEqual(conn.execute(f"SELECT COUNT(*) FROM [{CONV}] WHERE 項目名='部署'").fetchone()[0], 0)


class AddRowToSettingTest(_DbCase):
    def form_values(self, **items):
        f = self.c.post("/api/master/form", json={"table": CONV, "mode": "ADDNEW"}, headers=self.h).get_json()
        return [items.get(x["name"], x["value"]) for x in f["fields"]]

    def execute(self, values, **extra):
        return self.c.post("/api/master/execute", headers=self.h,
                           json={"table": CONV, "mode": "ADDNEW", "values": values, "key": values[0], **extra})

    def test_row_add_makes_main_column(self):
        values = self.form_values(項目名="確認者", 入力形式="数値変換", 登録条件="必須")
        r = self.execute(values).get_json()
        self.assertEqual(r["reason"], "question")
        self.assertIn("確認者", r["message"])
        self.assertNotIn("確認者", self.cols(MAIN))                     # 聞いただけでは足さない
        r = self.execute(values, confirmed=True, answer=True)
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual(self.cols(MAIN)[-1], "確認者")
        self.assertEqual(self.q(f"SELECT 項目名, 入力形式, 登録条件 FROM [{CONV}]")[-1], ("確認者", "数値変換", "必須"))
        self.assertEqual(self.problem(), "")

    def test_row_add_refused_names_and_cancel(self):
        r = self.execute(self.form_values(項目名="作業名"), confirmed=True, answer=True)
        self.assertEqual((r.status_code, r.get_json()["reason"]), (422, "bad_name"))
        r = self.execute(self.form_values(項目名="確認者"), confirmed=True, answer=False)
        self.assertEqual(r.get_json()["reason"], "cancelled")
        self.assertNotIn("確認者", self.cols(MAIN))

    def test_rename_and_delete_refused(self):
        cols = self.cols(CONV)
        row = [("" if v is None else str(v)) for v in self.q(f"SELECT * FROM [{CONV}]")[1]]
        renamed = list(row)
        renamed[cols.index("項目名")] = "作業名2"
        r = self.c.post("/api/master/execute", headers=self.h, json={
            "table": CONV, "mode": "UPDATE", "values": renamed, "key": row[0], "seen": row,
            "confirmed": True, "answer": True})
        self.assertEqual((r.status_code, r.get_json()["reason"]), (422, "rename_item"))
        # 項目名以外(入力形式など)は今までどおり直せる
        changed = list(row)
        changed[cols.index("登録条件")] = "必須"
        r = self.c.post("/api/master/execute", headers=self.h, json={
            "table": CONV, "mode": "UPDATE", "values": changed, "key": row[0], "seen": row,
            "confirmed": True, "answer": True})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        r = self.c.post("/api/master/delete", json={"table": CONV, "row": changed, "confirmed": True, "answer": True},
                        headers=self.h)
        self.assertEqual((r.status_code, r.get_json()["reason"]), (422, "delete_item"))
        self.assertEqual(self.problem(), "")


class LowerLevelTest(_DbCase):
    level = 4

    def test_columns_need_level5(self):
        t = self.c.post("/api/master/tables", json={}, headers=self.h).get_json()
        self.assertNotIn(MAIN, t["tables"])                            # 本体は Level 5 だけ
        self.assertFalse(t["caps"]["ID"]["columns"])
        self.assertTrue(t["caps"]["ID"]["rows"])
        r = self.add(table="ID", name="内線")
        self.assertEqual((r.status_code, r.get_json()["reason"]), (403, "level"))
        # _設定 に行を足す = 本体に列を足す なので、これも Level 5
        f = self.c.post("/api/master/form", json={"table": CONV, "mode": "ADDNEW"}, headers=self.h).get_json()
        values = [("確認者" if x["name"] == "項目名" else x["value"]) for x in f["fields"]]
        r = self.c.post("/api/master/execute", headers=self.h, json={
            "table": CONV, "mode": "ADDNEW", "values": values, "key": values[0], "confirmed": True, "answer": True})
        self.assertEqual((r.status_code, r.get_json()["reason"]), (403, "level"))
        # ふつうの行の追加(ID)は今までどおり
        f = self.c.post("/api/master/form", json={"table": "ID", "mode": "ADDNEW"}, headers=self.h).get_json()
        row = [f["fields"][0]["value"], "試験", "Xfa-Shiken", "PC", "", "1", ""]
        r = self.c.post("/api/master/execute", headers=self.h, json={
            "table": "ID", "mode": "ADDNEW", "values": row, "key": row[0], "confirmed": True, "answer": True})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
