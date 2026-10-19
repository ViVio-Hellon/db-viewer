"""表を持ってくる(python-web-tools の table_bring を移植)

読むのはいつも .sqlite3。持ってくる元は「受領 DB を取り込んだもの」を写して、
Access 側で表を足した・列を足した・列を消した/名前を変えた、を再現する。
"""
from __future__ import annotations

import io
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from dbviewer import auth, config, import_diag, settings_tables, table_bring, user_settings
from tests._fixture_db import fresh_db_path
from tests._web import TOKEN, make_client


def _source_from(dest: Path, folder: Path) -> Path:
    """本体と同じ中身の .sqlite3 を作り、Access 側の変更を入れる。"""
    src = folder / "access側" / "作業標準管理システム_変換.sqlite3"
    src.parent.mkdir()
    shutil.copyfile(dest, src)
    with sqlite3.connect(src) as c:
        # 新しい表(Access で作った)
        c.execute("CREATE TABLE [工程マスタ] ([管理番号] INTEGER, [工程マスタ] TEXT, [備考] TEXT)")
        c.executemany("INSERT INTO [工程マスタ] VALUES (?, ?, ?)",
                      [(1, "切断", ""), (2, "梱包", "手作業"), (3, "検査", None)])
        c.execute("CREATE UNIQUE INDEX [idx_工程マスタ_管理番号] ON [工程マスタ] ([管理番号])")
        # Access の内部の表(持ってこない)
        c.execute("CREATE TABLE [MSysObjects] ([Id] INTEGER)")
        # もうある表に列を足した → 入れ替える
        c.execute("ALTER TABLE [タイプ] ADD COLUMN [表示順] TEXT")
        c.execute("UPDATE [タイプ] SET [表示順] = [管理番号]")
        c.execute("INSERT INTO [タイプ] ([管理番号],[タイプ],[備考],[表示順]) VALUES (8, 'ｶﾃｺﾞﾘ', '', '8')")
        # もうある表の列を消した → 作り直す(指定幅 は フィルタ_設定 で使っている列)
        c.execute("ALTER TABLE [フィルタ] DROP COLUMN [指定幅]")
        # メインテーブル・ID も変えておく(入れ替えない表)
        c.execute("DELETE FROM [作業標準管理] WHERE [管理番号] > 10")
    return src


class TableBringTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dest = fresh_db_path(Path(self.tmp.name))
        user_settings.save(config.KEY_DB_PATH, str(self.dest))
        self.src = _source_from(self.dest, Path(self.tmp.name))

    def _tables(self, path=None):
        with sqlite3.connect(path or self.dest) as c:
            return [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")]

    def test_plan_reads_sqlite_and_sorts_out_tables(self):
        p = table_bring.plan(str(self.src))
        self.assertTrue(p.ok, p.message)
        self.assertEqual(p.dest, str(self.dest))
        by = {c.name: c for c in p.candidates}
        self.assertNotIn("MSysObjects", by)
        self.assertIn("Access の内部の表 1 個は出していません", p.message)
        new = by["工程マスタ"]
        self.assertEqual((new.exists, new.rows, new.can_bring), (False, 3, True))
        t = by["タイプ"]
        self.assertEqual((t.action, t.added_columns, t.can_refresh, t.current_rows, t.rows),
                         ("refresh", ["表示順"], True, 7, 8))
        f = by["フィルタ"]
        self.assertEqual((f.action, f.removed_columns, f.import_loses), ("rebuild", ["指定幅"], ["指定幅"]))
        self.assertIn("メインテーブル", by["作業標準管理"].refresh_why)
        self.assertFalse(by["作業標準管理"].can_refresh)
        self.assertIn("誰も入れなくなる", by["ID"].refresh_why)
        self.assertIn("本体に無い表が 1 個", p.message)
        d = table_bring.plan_dict(p)
        self.assertFalse(d["access"])

    def test_bring_new_table_only(self):
        r = table_bring.bring(str(self.src), ["工程マスタ"])
        self.assertTrue(r.ok, r.message)
        self.assertEqual(r.brought, [("工程マスタ", 3)])
        self.assertTrue(Path(r.backup).is_file())
        with sqlite3.connect(self.dest) as c:
            self.assertEqual(c.execute("SELECT count(*) FROM [工程マスタ]").fetchone()[0], 3)
            self.assertEqual(c.execute("SELECT [備考] FROM [工程マスタ] WHERE [管理番号]=2").fetchone()[0], "手作業")
            self.assertTrue(c.execute("SELECT 1 FROM sqlite_master WHERE name='idx_工程マスタ_管理番号'").fetchone())
            reg = c.execute(f"SELECT 表, 元のファイル FROM [{config.BROUGHT_REGISTRY}]").fetchall()
            self.assertEqual(reg, [("工程マスタ", str(self.src))])
            # 控えの表は VBA の表一覧(整合性検査・マスタ管理)には出さない
            self.assertNotIn(config.BROUGHT_REGISTRY, settings_tables.table_names(c))
            self.assertIn("工程マスタ", settings_tables.table_names(c))
            self.assertEqual(table_bring.brought_tables(c), {"工程マスタ"})
            # ほかの表には触らない
            self.assertEqual(c.execute("SELECT count(*) FROM [作業標準管理]").fetchone()[0], 327)
        self.assertIn("持ってきた: 工程マスタ (3行・索引1)", import_diag.latest_path().read_text(encoding="utf-8"))
        # 2 回目はもうあるので断る
        r = table_bring.bring(str(self.src), ["工程マスタ"])
        self.assertEqual((r.ok, r.reason), (False, table_bring.REFUSE_EXISTS))
        r = table_bring.bring(str(self.src), ["MSysObjects"])
        self.assertEqual(r.reason, table_bring.REFUSE_NOTHING)

    def test_refresh_adds_columns_and_rebuild_keeps_old(self):
        r = table_bring.refresh(str(self.src), ["タイプ", "フィルタ"])
        self.assertTrue(r.ok, r.message)
        self.assertEqual(r.refreshed, [("タイプ", 7, 8), ("フィルタ", 11, 11)])
        self.assertEqual(len(r.kept_old), 1)
        old = r.kept_old[0][1]
        self.assertTrue(old.startswith("フィルタ_作り直す前_"))
        with sqlite3.connect(self.dest) as c:
            cols = [x[1] for x in c.execute("PRAGMA table_info([タイプ])")]
            self.assertEqual(cols[-1], "表示順")
            self.assertEqual(c.execute("SELECT [タイプ] FROM [タイプ] WHERE [管理番号]=8").fetchone()[0], "ｶﾃｺﾞﾘ")
            self.assertNotIn("指定幅", [x[1] for x in c.execute("PRAGMA table_info([フィルタ])")])
            self.assertIn("指定幅", [x[1] for x in c.execute(f"PRAGMA table_info([{old}])")])
        self.assertIn("作り直す前の表は", r.message)

    def test_refresh_refuses_owned_tables(self):
        r = table_bring.refresh(str(self.src), ["作業標準管理", "タイプ"])
        self.assertEqual((r.ok, r.reason), (False, table_bring.REFUSE_NOTHING))
        self.assertIn("メインテーブル", r.message)
        self.assertIn("何も変えていません", r.message)
        with sqlite3.connect(self.dest) as c:
            self.assertEqual(c.execute("SELECT count(*) FROM [作業標準管理]").fetchone()[0], 327)
            self.assertNotIn("表示順", [x[1] for x in c.execute("PRAGMA table_info([タイプ])")])
        r = table_bring.refresh(str(self.src), ["工程マスタ"])
        self.assertIn("「持ってくる」を使ってください", r.message)

    def test_same_file_and_missing(self):
        self.assertIn("本体そのもの", table_bring.plan(str(self.dest)).message)
        self.assertIn("見つかりません", table_bring.plan(str(Path(self.tmp.name) / "無い.sqlite3")).message)
        self.assertEqual(table_bring.bring(str(self.dest), ["x"]).reason, table_bring.REFUSE_SAME_FILE)

    def test_access_is_converted_through_bridge(self):
        # Access は bridge(cscript → ADODB → ACE)で .sqlite3 にしてから読む。
        # Linux には cscript が無いので、その理由がそのまま画面に出る
        accdb = Path(self.tmp.name) / "作業標準管理システム.accdb"
        accdb.write_bytes(b"\0" * 64)
        p = table_bring.plan(str(accdb))
        self.assertFalse(p.ok)
        if shutil.which("cscript") is None:
            self.assertIn("cscript.exe が見つかりません", p.message)
        r = table_bring.bring(str(accdb), ["工程マスタ"])
        self.assertEqual(r.reason, table_bring.REFUSE_CONVERT)
        self.assertTrue(table_bring.plan_dict(p)["access"])

    def test_access_conversion_result_is_read_as_sqlite(self):
        # 変換の中身(importer.import_accdb)を差し替えて、変換後の .sqlite3 を読むことを確かめる
        from dbviewer.accdb_bridge import importer
        accdb = Path(self.tmp.name) / "access側.accdb"
        accdb.write_bytes(b"x")
        original = importer.import_accdb

        def fake(src, target, **_kw):
            shutil.copyfile(self.src, target)
        importer.import_accdb = fake
        self.addCleanup(setattr, importer, "import_accdb", original)
        table_bring._converted_cache.clear()
        p = table_bring.plan(str(accdb))
        self.assertTrue(p.ok, p.message)
        self.assertIn("cscript → ADODB → ACE", p.converted)
        r = table_bring.bring(str(accdb), ["工程マスタ"])
        self.assertTrue(r.ok, r.message)


class TableBringWebTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dest = fresh_db_path(Path(self.tmp.name))
        user_settings.save(config.KEY_DB_PATH, str(self.dest))
        self.src = _source_from(self.dest, Path(self.tmp.name))
        auth.end()
        self.addCleanup(auth.end)
        self.c = make_client()
        self.h = {"X-Tool-Token": TOKEN}

    def _login(self, level):
        auth.start(auth.LoginResult(True, user_id="tester", level=level))

    def test_gate_and_flow(self):
        body = {"path": str(self.src), "tables": ["工程マスタ"]}
        r = self.c.get(f"/api/settings/table-bring/plan?path={self.src}", headers=self.h)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.get_json()["ok"])
        self._login(auth.VIEW_LEVEL)                                          # 閲覧のみ
        r = self.c.post("/api/settings/table-bring", json=body, headers=self.h)
        self.assertEqual(r.status_code, 403)
        self.assertIn("編集可", r.get_json()["error"]["message"])
        self._login(5)
        r = self.c.post("/api/settings/table-bring", json=body, headers=self.h)
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        d = r.get_json()
        self.assertEqual(d["brought"], [{"name": "工程マスタ", "rows": 3}])
        self.assertTrue(any(t["name"] == "工程マスタ" and t["exists"] for t in d["plan"]["tables"]))
        self.assertEqual(self.c.post("/api/settings/table-bring", json=body, headers=self.h).status_code, 409)
        # 一覧の表の選び欄に「足」の印
        m = self.c.post("/api/list/data", json={}, headers=self.h).get_json()
        tables = {t["name"]: t for t in m["tables"]}
        self.assertTrue(tables["工程マスタ"]["brought"])
        self.assertNotIn(config.BROUGHT_REGISTRY, tables)
        # 入れ替える
        r = self.c.post("/api/settings/table-refresh", json={"path": str(self.src), "tables": ["タイプ"]},
                        headers=self.h)
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual(r.get_json()["refreshed"], [{"name": "タイプ", "before": 7, "after": 8}])
        r = self.c.post("/api/settings/table-refresh", json={"path": str(self.src), "tables": ["ID"]},
                        headers=self.h)
        self.assertEqual(r.status_code, 400)

    def test_upload(self):
        data = {"file": (io.BytesIO(self.src.read_bytes()), "持ってくる.sqlite3")}
        r = self.c.post("/api/settings/table-bring/upload", data=data, headers=self.h,
                        content_type="multipart/form-data")
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        path = Path(r.get_json()["path"])
        self.assertEqual((path.name, path.parent.name), ("持ってくる.sqlite3", "表を持ってくる"))
        self.assertTrue(table_bring.plan(str(path)).ok)
        data = {"file": (io.BytesIO(b"x"), "../../悪い.txt")}
        r = self.c.post("/api/settings/table-bring/upload", data=data, headers=self.h,
                        content_type="multipart/form-data")
        self.assertEqual(r.status_code, 400)

    def test_settings_page_has_dialog(self):
        page = self.c.get("/settings", headers=self.h).get_data(as_text=True)
        for marker in ('id="bringOpen"', 'id="bringDialog"', 'id="bringDrop"', 'data-browse="bringPath"'):
            self.assertIn(marker, page)


if __name__ == "__main__":
    unittest.main()
