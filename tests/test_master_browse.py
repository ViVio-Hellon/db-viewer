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
            # いま使っている表だけ(フィルタ・フィルタ_設定・タイプは一覧が使わなくなった)
            self.assertEqual(master_admin.editable_tables(conn, s, 5), ["ID", "全般設定", "作業標準管理_設定"])
            self.assertEqual(master_admin.editable_tables(conn, s, 4), ["ID", "全般設定", "作業標準管理_設定"])
            self.assertEqual(master_admin.editable_tables(conn, s, 3), ["全般設定", "作業標準管理_設定"])
            self.assertEqual(master_admin.editable_tables(conn, s, 2), [])

    def test_used_tables_follow_setting(self):
        """_設定 が参照するマスタ(入力形式 s/m・マスタ結合)と履歴の H_ 表は出る。使っていない表は出ない。"""
        with tempfile.TemporaryDirectory() as tmp:
            conn = shared_db.connect(fresh_db_path(Path(tmp)))
            conn.execute("CREATE TABLE 担当者 (管理番号 INTEGER, 担当者 TEXT)")
            conn.execute("CREATE TABLE 区分マスタ (管理番号 INTEGER, 区分マスタ TEXT, 区分 TEXT)")
            conn.execute("CREATE TABLE H_変更 (管理番号 INTEGER)")
            conn.execute("CREATE TABLE 使っていない (管理番号 INTEGER)")
            conn.execute("UPDATE 作業標準管理_設定 SET 入力形式='s担当者' WHERE 項目名='担当'")
            conn.execute("UPDATE 作業標準管理_設定 SET マスタ結合='区分マスタ/区分/区分マスタ' WHERE 項目名='区分'")
            conn.execute("UPDATE 作業標準管理_設定 SET 履歴='H_変更' WHERE 項目名='作業名'")
            conn.commit()
            s = load_setup(conn)
            used = master_admin.used_tables(conn, s)
            self.assertEqual([n for n, _ in used], ["ID", "全般設定", "作業標準管理_設定", "担当者", "区分マスタ", "H_変更"])
            self.assertIn("担当", dict(used)["担当者"])
            conn.close()

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

    def test_csv_tsv_xlsx(self):
        """ふつうの CSV / TSV(VBA の「行末にも区切り」はやめた。docs/06)。上書きはしない。"""
        with tempfile.TemporaryDirectory() as tmp:
            cols, rows = ["a", "b"], [["1", "x,y"], ["0-1-1", '言う"'], ["ﾊﾝﾏｰ", "2014/04/13"]]
            p = table_browse.export(cols, rows, Path(tmp), "t", "tsv")
            self.assertEqual(p.name, "t.txt")
            self.assertEqual(p.read_bytes(), 'a\tb\r\n1\tx,y\r\n0-1-1\t"言う"""\r\nﾊﾝﾏｰ\t2014/04/13\r\n'.encode("cp932"))
            p = table_browse.export(cols, rows, Path(tmp), "t", "csv")
            self.assertEqual(p.read_bytes(), 'a,b\r\n1,"x,y"\r\n0-1-1,"言う"""\r\nﾊﾝﾏｰ,2014/04/13\r\n'.encode("cp932"))
            p2 = table_browse.export(cols, rows, Path(tmp), "t", "csv", "utf-8-sig")
            self.assertEqual(p2.name, "t_2.csv")
            self.assertTrue(p2.read_bytes().startswith(b"\xef\xbb\xbfa,b"))
            p = table_browse.export(cols, rows, Path(tmp), "a/b:c", "xlsx")
            self.assertEqual(p.name, "a_b_c.xlsx")
            self.assertGreater(p.stat().st_size, 1000)
            with self.assertRaises(ValueError):
                table_browse.export(cols, rows, Path(tmp), "t", "pdf")

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
        # 本体は見られる(列の追加のため)が、行はここでは直せない(一覧で)
        main = self.c.post("/api/master/rows", json={"table": "作業標準管理"}, headers=self.h).get_json()
        self.assertTrue(main["read_only"])
        for path, body in (("/api/master/form", {"table": "作業標準管理", "mode": "ADDNEW"}),
                           ("/api/master/execute", {"table": "作業標準管理", "mode": "ADDNEW", "values": ["999"]}),
                           ("/api/master/delete", {"table": "作業標準管理", "row": main["rows"][0], "confirmed": True})):
            r = self.c.post(path, json=body, headers=self.h)
            self.assertEqual((r.status_code, r.get_json()["error"]["code"]), (403, "main_rows"), path)
        self.assertEqual(self.c.post("/api/master/rows", json={"table": "タイプ"}, headers=self.h).status_code, 403)
        t = self.c.post("/api/master/tables", json={}, headers=self.h).get_json()
        # Level 5 では本体も並ぶ(列の追加だけ)
        self.assertEqual(t["tables"], ["ID", "全般設定", "作業標準管理", "作業標準管理_設定"])
        self.assertEqual((t["caps"]["作業標準管理"]["rows"], t["caps"]["作業標準管理"]["columns"]), (False, True))
        self.assertEqual(t["why"]["ID"], "ログイン")
        f = self.c.post("/api/master/form", json={"table": "全般設定", "mode": "ADDNEW"}, headers=self.h).get_json()
        key = f["fields"][0]["value"]
        row = [key, "テーブル閲覧", "ID", ""]
        r = self.c.post("/api/master/execute", json={"table": "全般設定", "mode": "ADDNEW", "values": row, "key": key,
                                                     "confirmed": True, "answer": True}, headers=self.h).get_json()
        self.assertEqual(r["executed"], "ADDNEW")
        r = self.c.post("/api/master/delete", json={"table": "全般設定", "row": row, "confirmed": True, "answer": True}, headers=self.h).get_json()
        self.assertTrue(r["ok"])

    def test_export_what_is_on_screen(self):
        body = {"columns": ["作業NO", "作業名"], "rows": [["0-1-1", "ﾊﾝﾏｰの取り扱い"], ["0-1-2", "x"]],
                "name": "一覧", "format": "csv"}
        r = self.c.post("/api/export", json=body, headers=self.h)
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        d = r.get_json()
        self.assertEqual(d["rows"], 2)
        path = Path(d["path"])
        self.assertEqual(path.parent, Path(self.tmp.name) / "out")
        self.assertEqual(path.read_bytes().decode("cp932"), "作業NO,作業名\r\n0-1-1,ﾊﾝﾏｰの取り扱い\r\n0-1-2,x\r\n")
        self.assertEqual(self.c.post("/api/export", json={**body, "rows": []}, headers=self.h).status_code, 422)
        info = self.c.get("/api/export/settings", headers=self.h).get_json()
        self.assertEqual((info["format"], info["encoding"], info["dir"]), ("csv", "cp932", str(Path(self.tmp.name) / "out")))

    def test_browse_data_and_view(self):
        from dbviewer import shared_db
        with shared_db.open_current() as conn:
            conn.execute(f"INSERT INTO [{config.SET_TABLE}] ([管理番号],[{config.ITEM_NAME}],[{config.PRESET}],[備考]) "
                         "VALUES (5,?,?,'')", (config.GS_TABLE_BROWSE, "タイプ/フィルタ"))
            conn.commit()
        self.assertEqual(self.c.post("/api/browse/tables", json={}, headers=self.h).get_json()["tables"], ["タイプ", "フィルタ"])
        d = self.c.post("/api/browse/data", json={"table": "タイプ"}, headers=self.h).get_json()
        self.assertEqual((d["count"], d["key_index"]), (7, -1))
        self.assertEqual([c["kind"] for c in d["columns"]], ["num", "text", "text"])
        v = self.c.post("/api/browse/view", json={"table": "タイプ", "order": ["タイプ", "管理番号"], "hidden": ["備考"]},
                        headers=self.h).get_json()["view"]
        self.assertEqual(v["order"], ["タイプ", "管理番号", "備考"])
        self.assertEqual(self.c.post("/api/browse/data", json={"table": "タイプ"}, headers=self.h).get_json()["view"], v)
        self.assertEqual(self.c.post("/api/browse/data", json={"table": "ID"}, headers=self.h).status_code, 403)


if __name__ == "__main__":
    unittest.main()
