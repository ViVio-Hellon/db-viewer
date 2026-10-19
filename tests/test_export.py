"""出力(一覧の「出力」: ファイルに保存)。0.18.0 で閲覧・出力の画面をなくし、出力は一覧から"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from dbviewer import auth, config, table_browse, user_settings
from tests._fixture_db import fresh_db_path
from tests._web import TOKEN, make_client


class ExportFileTest(unittest.TestCase):
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


class WebExportTest(unittest.TestCase):
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
        self.c.post("/api/login", json={}, headers=self.h)

    def _restore_env(self):
        for k, v in self.env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

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


if __name__ == "__main__":
    unittest.main()
