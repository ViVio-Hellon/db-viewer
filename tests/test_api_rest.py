"""試験から呼ばれていなかった API の、いまの答えを固定する(0.46.0)

仕様は変えない。共通化(設定ファイルの列・権限の数字・控え など)で壊れていないかの土台にする。
Windows でファイル・フォルダを本当に開いてしまう呼び方は、Windows では流さない。
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from dbviewer import auth, config, user_settings
from tests._fixture_db import fresh_db_path
from tests._web import TOKEN, make_client

ON_WINDOWS = os.name == "nt"


class RestApiTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.path = fresh_db_path(self.tmp)
        user_settings.save(config.KEY_DB_PATH, str(self.path))
        auth.end()
        self.addCleanup(auth.end)
        auth.start(auth.LoginResult(True, user_id="Xfa-Ngyinazawa", level=5))
        self.c = make_client()
        self.h = {"X-Tool-Token": TOKEN}

    def post(self, url, **body):
        return self.c.post(url, json=body, headers=self.h)

    # -- 一覧 ---------------------------------------------------------
    def test_list_value(self):
        """金額の列を直し始めるときの本体の値。無い行は空(いまの答え)。"""
        r = self.post("/api/list/value", key="1", column="作業名")
        self.assertEqual((r.status_code, r.get_json()), (200, {"value": "ﾊﾝﾏｰの取り扱い"}))
        r = self.post("/api/list/value", key="99999", column="作業名")
        self.assertEqual((r.status_code, r.get_json()), (200, {"value": ""}))

    # -- 配布設定 -----------------------------------------------------
    def test_dist_reapply_and_remove_need_password(self):
        for url in ("/api/dist/reapply", "/api/dist/remove"):
            r = self.post(url)
            self.assertEqual(r.status_code, 403, url)
            self.assertEqual(r.get_json()["error"]["code"], "need_password", url)

    # -- 参照… の窓 ---------------------------------------------------
    def test_fs_list(self):
        (self.tmp / "中" / "下").mkdir(parents=True)
        r = self.c.get("/api/fs/list", query_string={"path": str(self.tmp / "中")}, headers=self.h)
        self.assertEqual(r.status_code, 200)
        d = r.get_json()
        self.assertTrue(d["exists"])
        self.assertEqual([x["name"] for x in d["dirs"]], ["下"])
        self.assertEqual(Path(d["parent"]), self.tmp)
        r = self.c.get("/api/fs/list", query_string={"path": str(self.tmp / "無い")}, headers=self.h)
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.get_json()["exists"])

    # -- レコード画面(VBA 版の道) -------------------------------------
    def test_record_choices(self):
        r = self.post("/api/record/choices", kind="?")
        self.assertEqual(r.status_code, 422)
        r = self.post("/api/record/choices", kind="field")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["items"][:3], ["管理番号", "作業名", "作業NO"])

    def test_record_new_code_bad_date(self):
        r = self.post("/api/record/new-code", date="だめ")
        self.assertEqual((r.status_code, r.get_json()["error"]["code"]), (422, "date"))

    def test_record_open_file_missing(self):
        r = self.post("/api/record/open-file", dir=str(self.tmp), name="無い.pdf")
        self.assertEqual((r.status_code, r.get_json()["error"]["code"]), (422, "open"))

    @unittest.skipIf(ON_WINDOWS, "Windows では本当に開いてしまう")
    def test_record_open_file_other_os(self):
        (self.tmp / "a.txt").write_text("x", encoding="utf-8")
        r = self.post("/api/record/open-file", dir=str(self.tmp), name="a.txt")
        self.assertEqual(r.status_code, 422)
        self.assertIn("この OS では開けません", r.get_json()["error"]["message"])

    def test_record_icon_only_images_in_db_folder(self):
        png = self.path.parent / "i.png"
        png.write_bytes(b"\x89PNG\r\n\x1a\n")
        txt = self.path.parent / "i.txt"
        txt.write_text("x", encoding="utf-8")
        r = self.c.get("/api/record/icon", query_string={"path": str(png)}, headers=self.h)
        self.assertEqual(r.status_code, 200)
        r.close()
        self.assertEqual(self.c.get("/api/record/icon", query_string={"path": str(txt)}, headers=self.h).status_code, 403)
        with tempfile.TemporaryDirectory() as other:
            far = Path(other) / "x.png"
            far.write_bytes(b"\x89PNG\r\n\x1a\n")
            self.assertEqual(self.c.get("/api/record/icon", query_string={"path": str(far)},
                                        headers=self.h).status_code, 403)

    # -- フォルダを開く -----------------------------------------------
    @unittest.skipIf(ON_WINDOWS, "Windows では本当にエクスプローラーが開く")
    def test_open_folder_other_os(self):
        for url in ("/api/export/open", "/api/log/open"):
            r = self.post(url)
            self.assertEqual((r.status_code, r.get_json()["error"]["code"]), (501, "not_windows"), url)

    # -- 心拍・Access 取り込み ------------------------------------------
    def test_alive_without_token(self):
        r = self.c.post("/api/alive", json={"screen_id": "s"})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.get_json()["ok"])

    def test_accdb_import_missing_source(self):
        r = self.post("/api/settings/accdb-import", accdb_source=str(self.tmp / "無い.accdb"))
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.get_json()["error"]["field"], "accdb_source")


if __name__ == "__main__":
    unittest.main()
