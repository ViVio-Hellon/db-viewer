"""一覧のマスを直してすぐ保存 / 接続先フォルダのファイル / Access → .sqlite3 の保存先"""
from __future__ import annotations

import shutil
from contextlib import closing
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

from dbviewer import auth, cell_edit, config, db_files, jobs, shared_db, user_settings
from dbviewer.presenters import settings as settings_presenter
from dbviewer.settings_tables import load_setup
from tests._fixture_db import fresh_db_path
from tests._web import TOKEN, make_client


class CellEditTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = fresh_db_path(Path(self.tmp.name))
        user_settings.save(config.KEY_ONLINE, False)
        user_settings.save(config.KEY_LOCAL_DIR, str(self.path.parent))
        user_settings.save(config.KEY_DB_FILE, self.path.name)
        auth.end()
        self.addCleanup(auth.end)
        self.c = make_client()
        self.h = {"X-Tool-Token": TOKEN}

    def db(self, sql, *args):
        with sqlite3.connect(self.path) as c:
            return c.execute(sql, args).fetchone()

    def test_specs(self):
        conn = shared_db.connect(self.path)
        self.addCleanup(conn.close)
        sp = cell_edit.edit_specs(conn, load_setup(conn))
        self.assertEqual((sp["作業名"]["inline"], sp["Ⅰ"]["inline"], sp["登録日"]["inline"]), ("text", "pick", "pick"))
        self.assertEqual((sp["管理番号"]["inline"], sp["更新者"]["inline"]), ("", ""))
        self.assertIn("自動", sp["更新者"]["why"])

    def test_save_immediately_with_rules(self):
        auth.start(auth.LoginResult(True, user_id="Xfa-Ngyinazawa", level=5))
        d = self.c.post("/api/list/data", json={}, headers=self.h).get_json()
        self.assertTrue(d["edit"]["allowed"])
        before = self.db("SELECT 作業名 FROM 作業標準管理 WHERE 管理番号=2")[0]
        r = self.c.post("/api/list/cell", json={"key": "2", "column": "作業名", "value": "改名", "seen": before},
                        headers=self.h)
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        j = r.get_json()
        self.assertEqual((j["ok"], j["row"][1]), (True, "改名"))
        # 確認なしで、もう本体に入っている。自動項目(更新者 = #UPUSER#)も入る
        self.assertEqual(self.db("SELECT 作業名, 更新者 FROM 作業標準管理 WHERE 管理番号=2"), ("改名", "Xfa-Ngyinazawa"))
        # 画面が古い値を見ていた → 書かない(409)、いまの値を返す
        r = self.c.post("/api/list/cell", json={"key": "2", "column": "作業名", "value": "別名", "seen": before},
                        headers=self.h)
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.get_json()["row"][1], "改名")
        self.assertEqual(self.db("SELECT 作業名 FROM 作業標準管理 WHERE 管理番号=2")[0], "改名")
        # 数値の欄は登録画面と同じく範囲・変換を通る。自動項目・管理番号・結合列は断る
        r = self.c.post("/api/list/cell", json={"key": "2", "column": "Ⅱ", "value": "9", "seen": "7"}, headers=self.h)
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual(self.db("SELECT Ⅱ FROM 作業標準管理 WHERE 管理番号=2")[0], "9")
        for col in ("更新者", "管理番号", "無い列"):
            r = self.c.post("/api/list/cell", json={"key": "2", "column": col, "value": "x", "seen": ""}, headers=self.h)
            self.assertEqual((r.status_code, r.get_json()["reason"]), (422, "not_editable"))
        # 同じ値なら何もしない
        r = self.c.post("/api/list/cell", json={"key": "2", "column": "作業名", "value": "改名", "seen": "改名"}, headers=self.h)
        self.assertEqual(r.get_json()["reason"], "unchanged")

    def test_level_and_required(self):
        auth.start(auth.LoginResult(True, user_id="t", level=1))
        d = self.c.post("/api/list/data", json={}, headers=self.h).get_json()
        self.assertFalse(d["edit"]["allowed"])
        r = self.c.post("/api/list/cell", json={"key": "2", "column": "作業名", "value": "x", "seen": ""}, headers=self.h)
        self.assertEqual(r.status_code, 403)
        auth.start(auth.LoginResult(True, user_id="t", level=5))
        with sqlite3.connect(self.path) as c:
            c.execute("UPDATE 作業標準管理_設定 SET 登録条件='必須' WHERE 項目名='作業名'")
        seen = self.db("SELECT 作業名 FROM 作業標準管理 WHERE 管理番号=3")[0]
        r = self.c.post("/api/list/cell", json={"key": "3", "column": "作業名", "value": "", "seen": seen}, headers=self.h)
        self.assertEqual((r.status_code, r.get_json()["reason"]), (422, "required"))
        self.assertEqual(self.db("SELECT 作業名 FROM 作業標準管理 WHERE 管理番号=3")[0], seen)


class DbFilesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.path = fresh_db_path(self.dir)
        user_settings.save(config.KEY_ONLINE, False)
        user_settings.save(config.KEY_LOCAL_DIR, str(self.dir))
        user_settings.save(config.KEY_DB_FILE, self.path.name)

    def test_scan_classifies(self):
        shutil.copyfile(self.path, self.dir / "写し.sqlite3")
        with sqlite3.connect(self.dir / "ほか.db") as c:
            c.execute("CREATE TABLE x (a)")
        with closing(sqlite3.connect(self.dir / "空.db")) as c:
            c.execute("CREATE TABLE ID (a)")              # 業務の表が無い
        (self.dir / "作業標準管理システム.accdb").write_bytes(b"\0")
        (self.dir / "新しい.accdb").write_bytes(b"\0")
        (self.dir / "メモ.txt").write_text("x", encoding="utf-8")
        d = db_files.scan(self.dir, current=self.path)
        got = [(f["name"], f["kind"], f["current"]) for f in d["files"]]
        self.assertEqual(sorted(got), sorted([
            ("作業標準管理システム.sqlite3", "open", True), ("写し.sqlite3", "open", False),
            ("ほか.db", "open", False),                          # 設定の表が無くても開ける(0.7.0)
            ("作業標準管理システム.accdb", "convert", False), ("新しい.accdb", "convert", False),
            ("空.db", "other", False)]))
        self.assertEqual([k for _n, k, _c in got], ["open", "open", "open", "convert", "convert", "other"])
        notes = {f["name"]: f["note"] for f in d["files"]}
        self.assertIn("327 件", notes["写し.sqlite3"])
        self.assertIn("変換済み", notes["作業標準管理システム.accdb"])
        self.assertIn("Access 取り込み", notes["新しい.accdb"])
        self.assertIn("全般設定", notes["ほか.db"])
        self.assertIn("閲覧だけ", notes["ほか.db"])
        self.assertIn("表がありません", notes["空.db"])
        self.assertEqual(db_files.scan(self.dir / "無い")["exists"], False)

    def test_open_db_and_web(self):
        other = self.dir / "別" / "写し.sqlite3"
        other.parent.mkdir()
        shutil.copyfile(self.path, other)
        r = settings_presenter.open_db(str(other))
        self.assertTrue(r.ok, r.message)
        self.assertEqual(shared_db.db_path(), other)              # オフラインのまま、フォルダと名前が替わる
        self.assertFalse(settings_presenter.open_db(str(self.dir / "無い.sqlite3")).ok)
        c = make_client()
        h = {"X-Tool-Token": TOKEN}
        d = c.get(f"/api/settings/db-files?path={self.dir}", headers=h).get_json()
        self.assertEqual(d["files"][0]["kind"], "open")
        j = c.post("/api/settings/open-db", json={"path": str(self.path)}, headers=h).get_json()
        self.assertIn("開きます", j["message"])
        self.assertEqual(shared_db.db_path(), self.path)

    def test_convert_target(self):
        """Access → .sqlite3 は指定の場所へ。空ならいまの接続先フォルダに同じ名前で。"""
        from dbviewer.accdb_bridge import importer
        accdb = self.dir / "元" / "台帳.accdb"
        accdb.parent.mkdir()
        accdb.write_bytes(b"\0")
        seen = []
        original = importer.import_accdb
        importer.import_accdb = lambda src, dest, **kw: seen.append((src, dest))
        self.addCleanup(setattr, importer, "import_accdb", original)
        def wait_idle():
            for _ in range(100):
                if not jobs.get_registry().busy_labels():
                    return
                time.sleep(0.05)

        _job, r = settings_presenter.start_accdb_import(str(accdb), "")
        self.assertTrue(r.ok, r.message)
        wait_idle()
        out = self.dir / "出す"
        out.mkdir()
        _job, r = settings_presenter.start_accdb_import(str(accdb), str(out))          # フォルダだけ → 同じ名前
        wait_idle()
        self.assertEqual([d for _s, d in seen], [self.dir / "台帳.sqlite3", out / "台帳.sqlite3"])
        self.assertEqual(user_settings.get(config.KEY_ACCDB_TARGET), str(out / "台帳.sqlite3"))
        _job, r = settings_presenter.start_accdb_import(str(accdb), str(self.dir / "無いフォルダ" / "x.sqlite3"))
        self.assertFalse(r.ok)
        _job, r = settings_presenter.start_accdb_import(str(self.path), "")
        self.assertIn("Access のファイル", r.message)


if __name__ == "__main__":
    unittest.main()
