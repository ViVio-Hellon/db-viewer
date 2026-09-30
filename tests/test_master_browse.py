"""マスタ管理(MF2)・閲覧/出力(MF3, ExportData)"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from dbviewer import auth, config, master_admin, shared_db, table_browse, user_settings
from dbviewer.settings_tables import load_setup
from tests._fixture_db import connect, fresh_db_path
from tests._web import TOKEN, make_client


class MasterAdminTest(unittest.TestCase):
    def test_editable_tables_by_level(self):
        with connect() as conn:
            s = load_setup(conn)
            self.assertEqual(master_admin.editable_tables(conn, s, 5),
                             ["ID", "全般設定", "作業標準管理_設定", "フィルタ", "フィルタ_設定", "タイプ"])
            self.assertEqual(master_admin.editable_tables(conn, s, 4),
                             ["ID", "全般設定", "作業標準管理_設定", "フィルタ", "フィルタ_設定"])
            self.assertEqual(master_admin.editable_tables(conn, s, 3),
                             ["全般設定", "作業標準管理_設定", "フィルタ", "フィルタ_設定"])
            self.assertEqual(master_admin.editable_tables(conn, s, 2), ["フィルタ"])

    def test_fields_use_setting_table(self):
        with connect() as conn:
            s = load_setup(conn)
            f = master_admin.build_fields(conn, s, "フィルタ", "ADDNEW")
            self.assertEqual([(x.name, x.spec.kind) for x in f],
                             [("管理番号", "free"), ("フィルタ", "field"), ("タイプ", "master"), ("指定幅", "free"), ("備考", "free")])
            self.assertEqual((f[0].value, f[0].is_key), ("12", True))
            f = master_admin.build_fields(conn, s, "タイプ", "UPDATE", ["1", "<=", "x"])
            self.assertEqual([x.value for x in f], ["1", "<=", "x"])

    def test_click_filter(self):
        self.assertEqual(master_admin.click_filter(["a", "b"], ["1", "it's"]), "[a]='1' AND [b]='it''s'")

    def test_execute_and_delete(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = shared_db.connect(fresh_db_path(Path(tmp)))
            r = master_admin.execute(conn, "タイプ", "ADDNEW", ["7", "新", ""], "7")
            self.assertEqual(r.reason, "question")
            r = master_admin.execute(conn, "タイプ", "ADDNEW", ["7", "新", ""], "7", confirmed=True, answer=True)
            self.assertEqual(r.executed, "ADDNEW")
            r = master_admin.execute(conn, "タイプ", "UPDATE", ["7", "新2", ""], "7", confirmed=True, answer=True)
            self.assertEqual(r.executed, "UPDATE")
            r = master_admin.delete(conn, "タイプ", ["7", "新2", ""])
            self.assertEqual(r.message, master_admin.MSG_DELETE_ASK)
            r = master_admin.delete(conn, "タイプ", ["7", "新2", ""], confirmed=True, answer=False)
            self.assertEqual(r.message, master_admin.MSG_DELETE_CANCEL)
            r = master_admin.delete(conn, "タイプ", ["7", "新2", ""], confirmed=True, answer=True)
            self.assertEqual((r.ok, r.message), (True, master_admin.MSG_DELETE_DONE))
            self.assertEqual(conn.execute("SELECT count(*) FROM タイプ").fetchone()[0], 7)
            conn.close()


class ExportTest(unittest.TestCase):
    def test_browse_tables(self):
        self.assertEqual(table_browse.browse_tables(""), [])
        self.assertEqual(table_browse.browse_tables("A"), ["A"])
        self.assertEqual(table_browse.browse_tables("A/B"), ["A", "B"])

    def test_txt_csv_like_vba(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = [["a", "b"], ["1", "x,y"], ["0-1-1", "2014/04/13"]]
            p = table_browse.export(data, Path(tmp), "t", "txt")
            self.assertEqual(p.read_bytes(), "a\tb\t\r\n1\tx,y\t\r\n0-1-1\t2014/04/13\t\r\n".encode("cp932"))
            p = table_browse.export(data, Path(tmp), "t", "csv")
            self.assertEqual(p.read_bytes(), "a,b,\r\n1,x,y,\r\n0-1-1,2014/04/13,\r\n".encode("cp932"))
            p = table_browse.export(data, Path(tmp), "", "xlsx")
            self.assertTrue(p.name.endswith(".xlsx") and p.stat().st_size > 1000)
            with self.assertRaises(ValueError):
                table_browse.export(data, Path(tmp), "t", "pdf")

    def test_number_rule(self):
        self.assertEqual(table_browse._number("123"), 123)
        self.assertEqual(table_browse._number("2.5"), 2.5)
        self.assertEqual(table_browse._number("007"), "007")
        self.assertEqual(table_browse._number("0-1-1"), "0-1-1")
        self.assertEqual(table_browse._number("2014/04/13"), "2014/04/13")


class WebMasterTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        path = fresh_db_path(Path(self.tmp.name))
        user_settings.save(config.KEY_ONLINE, False)
        user_settings.save(config.KEY_LOCAL_DIR, str(path.parent))
        user_settings.save(config.KEY_DB_FILE, path.name)
        user_settings.save(config.KEY_EXPORT_DIR, str(Path(self.tmp.name) / "out"))
        auth.end()
        self.addCleanup(auth.end)
        self.env = {k: os.environ.get(k) for k in ("USERNAME", "COMPUTERNAME")}
        os.environ["USERNAME"] = "Xfa-Ngyinazawa"
        os.environ["COMPUTERNAME"] = "NLM-NGY-240638"
        self.addCleanup(self._restore_env)
        self.c = make_client()
        self.h = {"X-Tool-Token": TOKEN}
        self.c.post("/api/login", json={"id": "Xfa-Ngyinazawa", "password": ""}, headers=self.h)

    def _restore_env(self):
        for k, v in self.env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_master_cycle_and_main_table_refused(self):
        self.assertEqual(self.c.post("/api/master/rows", json={"table": "作業標準管理"}, headers=self.h).status_code, 403)
        f = self.c.post("/api/master/form", json={"table": "タイプ", "mode": "ADDNEW"}, headers=self.h).get_json()
        key = f["fields"][0]["value"]
        r = self.c.post("/api/master/execute", json={"table": "タイプ", "mode": "ADDNEW", "values": [key, "新", ""], "key": key,
                                                     "confirmed": True, "answer": True}, headers=self.h).get_json()
        self.assertEqual(r["executed"], "ADDNEW")
        r = self.c.post("/api/master/delete", json={"table": "タイプ", "row": [key, "新", ""], "confirmed": True, "answer": True}, headers=self.h).get_json()
        self.assertTrue(r["ok"])

    def test_export_list(self):
        r = self.c.post("/api/export", json={"source": "list", "ext": "csv", "name": "x"}, headers=self.h).get_json()
        self.assertTrue(r["ok"])
        self.assertEqual(r["rows"], 327)
        self.assertTrue(Path(r["path"]).exists())


if __name__ == "__main__":
    unittest.main()
