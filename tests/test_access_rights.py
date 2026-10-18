"""権限(0.16.0): 梱包資材マスタの「アクセス権限」で 編集可 / 閲覧のみ

読み方は all-tools・python-web-tools と同じ(空欄は問わない・両方空は効かない・大文字小文字を区別しない・
有効 0 は効かない・当てはまる行の和・当てはまらなければ 閲覧のみ・届かなければ前回の結果)。
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

from dbviewer import access_rights, auth, config, user_settings

DDL = ('CREATE TABLE "アクセス権限" ("管理番号" INTEGER PRIMARY KEY, "ログインID" TEXT DEFAULT \'\','
       ' "PC名" TEXT DEFAULT \'\', "権限" TEXT NOT NULL, "有効" INTEGER DEFAULT 1, "備考" TEXT DEFAULT \'\')')


def access_db(path: Path, rows) -> Path:
    """梱包資材マスタ(実物と同じ「アクセス権限」の定義)。rows = (ログインID, PC名, 権限, 有効)。作り直す。"""
    if path.exists():
        path.unlink()
    with closing(sqlite3.connect(path)) as c:
        c.execute(DDL)
        c.execute("CREATE TABLE パレット (管理番号 INTEGER)")                 # ほかの表もある
        c.executemany('INSERT INTO "アクセス権限" (ログインID, PC名, 権限, 有効) VALUES (?, ?, ?, ?)', rows)
        c.commit()
    return path


class DecideTest(unittest.TestCase):
    def rows(self, *rows):
        return [{"ログインID": a, "PC名": b, "権限": c, "有効": d} for a, b, c, d in rows]

    def test_rules(self):
        d = lambda rows, who="taro", pc="PC-1": access_rights.decide(self.rows(*rows), who, pc)[0]
        self.assertTrue(d([("taro", "PC-1", "dbviewer:edit", 1)]))
        self.assertTrue(d([("TARO", "pc-1", "DBVIEWER:EDIT", 1)]))                 # 大文字小文字
        self.assertTrue(d([("ｔａｒｏ", "", "dbviewer:edit", 1)]))                  # 全角も・PC は問わない
        self.assertTrue(d([("", "PC-1", "dbviewer:edit", None)]))                 # ID は問わない・有効の空欄は有効
        self.assertFalse(d([("", "", "dbviewer:edit", 1)]))                       # 両方空は誰にも効かない
        self.assertFalse(d([("taro", "PC-2", "dbviewer:edit", 1)]))               # PC が違う
        self.assertFalse(d([("jiro", "PC-1", "dbviewer:edit", 1)]))
        for off in (0, "0", "false", "×", "無効"):
            self.assertFalse(d([("taro", "PC-1", "dbviewer:edit", off)]), off)
        self.assertFalse(d([("taro", "PC-1", "mode:fullaccess", 1), ("taro", "PC-1", "作業長", 1)]))  # ほかのツールの権限
        self.assertTrue(d([("taro", "PC-1", "mode:field", 1), ("taro", "", "dbviewer:edit", 1)]))     # 和
        self.assertFalse(d([]))                                                   # 行が無ければ 閲覧のみ


class EvaluateTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        env = mock.patch.dict(os.environ, {"DBVIEWER_LOCAL_DIR": str(self.tmp / "local")})
        env.start()
        self.addCleanup(env.stop)
        self.addCleanup(user_settings.save, config.KEY_ACCESS_DB, "")
        self.master = access_db(self.tmp / "梱包資材マスタ.sqlite3", [
            ("taro", "PC-1", "dbviewer:edit", 1), ("hanako", "", "mode:field", 1), ("", "PC-9", "dbviewer:edit", 0)])

    def test_unset_master_lets_everyone_edit(self):
        user_settings.save(config.KEY_ACCESS_DB, "")
        with mock.patch.object(access_rights, "master_path", return_value=None):
            d = access_rights.evaluate("anyone", "PC-X")
        self.assertEqual((d.edit, d.source, d.label), (True, "unset", "編集可"))

    def test_master_decides(self):
        user_settings.save(config.KEY_ACCESS_DB, str(self.master))
        d = access_rights.evaluate("taro", "PC-1")
        self.assertEqual((d.edit, d.source, d.label), (True, "master", "編集可"))
        self.assertEqual([r["権限"] for r in d.matched], ["dbviewer:edit"])
        d = access_rights.evaluate("hanako", "PC-1")
        self.assertEqual((d.edit, d.label), (False, "閲覧のみ"))
        self.assertIn("dbviewer:edit の行がありません", d.reason)
        o = access_rights.overview()
        self.assertEqual((o["found"], o["rows"], len(o["edit_rows"])), (True, 3, 2))   # 誰が編集可か(無効の行も見せる)

    def test_unreachable_uses_last_result_then_view_only(self):
        user_settings.save(config.KEY_ACCESS_DB, str(self.master))
        self.assertTrue(access_rights.evaluate("taro", "PC-1").edit)          # 読めた結果を覚える
        self.master.rename(self.tmp / "退避.sqlite3")                          # 共有フォルダに届かない
        d = access_rights.evaluate("taro", "PC-1")
        self.assertEqual((d.edit, d.source), (True, "cache"))
        self.assertIn("前回", d.reason)
        d = access_rights.evaluate("jiro", "PC-2")                            # 前回の結果が無い人は 閲覧のみ
        self.assertEqual((d.edit, d.source), (False, "unreachable"))

    def test_same_folder_as_db_is_found(self):
        user_settings.save(config.KEY_ACCESS_DB, "")
        from dbviewer import shared_db
        with mock.patch.object(shared_db, "db_path", return_value=self.tmp / "作業標準管理システム.sqlite3"):
            self.assertEqual(access_rights.master_path(), self.master)
            self.assertTrue(access_rights.overview()["auto"])

    def test_check_path(self):
        self.assertEqual(access_rights.check_path(str(self.master)), "")
        self.assertIn(".sqlite3", access_rights.check_path(str(self.tmp / "a.xlsx")))
        other = self.tmp / "違う.sqlite3"
        with closing(sqlite3.connect(other)) as c:
            c.execute("CREATE TABLE x (a)")
        self.assertIn("アクセス権限", access_rights.check_path(str(other)))


class RightsApiTest(unittest.TestCase):
    """かんたん設定 → 権限: アクセス権限マスタの場所を決める。変えられるのは 編集可 か、まだ決めていない・読めないとき。"""

    def setUp(self):
        from tests.test_easy_setup import _Case, plain_db
        case = _Case("setUp")
        case.setUp()
        self.addCleanup(case.doCleanups)
        self.case = case
        case.use(plain_db(case.tmp / "部品台帳.sqlite3"))
        self.addCleanup(user_settings.save, config.KEY_ACCESS_DB, "")
        self.master = access_db(case.tmp / "kms.sqlite3",
                                [("tester", "PC-TEST", "mode:field", 1)])

    def test_set_master_then_view_only_cannot_change_it(self):
        c = self.case
        j = c.ok("/api/easy/rights")
        self.assertEqual((j["me"]["label"], j["me"]["source"], j["can_change"]), ("編集可", "unset", True))
        self.assertEqual(c.post("/api/easy/rights", path=str(c.tmp / "無い.sqlite3"), save=True).status_code, 422)
        c.ok("/api/easy/rights", path=str(self.master), save=True)
        s = c.ok("/api/login")
        self.assertEqual(s["label"], "閲覧のみ")                                     # dbviewer:edit の行が無い
        j = c.ok("/api/easy/rights")
        self.assertEqual((j["me"]["edit"], j["can_change"], j["master"]["found"]), (False, False, True))
        r = c.post("/api/easy/rights", path="", save=True)
        self.assertEqual(r.status_code, 403)                                        # 閲覧のみは場所を変えられない
        # 閲覧のみ: かんたん設定の保存・列の追加・行の追加は断る
        st = c.ok("/api/easy/state", table="部品")
        self.assertFalse(st["can_write"])
        self.assertEqual(c.post("/api/easy/columns", table="部品", rows=st["columns"]).status_code, 403)
        self.assertEqual(c.post("/api/list/row-add", table="部品", values={"品番": "Z"}).status_code, 403)
        # マスタ管理は見るだけ(表は並ぶ・行は直せない)
        t = c.ok("/api/master/tables")
        self.assertIn("部品", t["tables"])
        self.assertFalse(t["caps"]["部品"]["rows"])
        self.assertIn("閲覧のみ", t["caps"]["部品"]["note"])
        self.assertTrue(c.ok("/api/master/rows", table="部品")["read_only"])
        # 行を足してもらう → 読み直すと 編集可
        access_db(self.master, [("tester", "PC-TEST", "dbviewer:edit", 1)])
        self.assertEqual(c.ok("/api/login")["label"], "編集可")
        self.assertTrue(c.ok("/api/easy/state", table="部品")["can_write"])


if __name__ == "__main__":
    unittest.main()
