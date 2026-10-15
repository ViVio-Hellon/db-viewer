"""どの表でも見て直す・かんたん設定(0.8.0): 設定は 見る DB と 列ごとの 表示・入力・印刷 だけ"""
from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

from dbviewer import auth, config, shared_db, simple_table, user_settings
from dbviewer.settings_tables import column_names, load_setup
from tests._fixture_db import fresh_db_path
from tests._web import TOKEN, make_client

MAIN, CONV = "作業標準管理", "作業標準管理_設定"


def plain_db(path: Path) -> Path:
    """設定の表が 1 つも無い、ふつうの DB(表 2 つ。管理番号 も無い)。"""
    with closing(sqlite3.connect(path)) as c:
        c.execute("CREATE TABLE 部品 (品番 TEXT, 名前 TEXT, 単価 REAL, 入荷日 TEXT)")
        c.executemany("INSERT INTO 部品 VALUES (?, ?, ?, ?)",
                      [("A-1", "箱", 120.5, "2026/10/01"), ("A-2", "袋", 30, None), ("B-1", "テープ", 2502.5, "")])
        c.execute("CREATE TABLE 仕入先 (名前 TEXT, 電話 TEXT)")
        c.execute("INSERT INTO 仕入先 VALUES ('東海紙工', '052-000-0000')")
        c.commit()
    return path


class _Case(unittest.TestCase):
    def setUp(self):
        # この PC のログイン ID・PC 名(試験の機械には無いことがある)
        env = mock.patch.dict(os.environ, {"USERNAME": "tester", "COMPUTERNAME": "PC-TEST"})
        env.start()
        self.addCleanup(env.stop)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)

    def use(self, path: Path) -> None:
        self.path = path
        user_settings.save(config.KEY_ONLINE, False)
        user_settings.save(config.KEY_LOCAL_DIR, str(path.parent))
        user_settings.save(config.KEY_DB_FILE, path.name)
        user_settings.save(config.KEY_MAIN_PICK, {})
        auth.end()
        self.addCleanup(auth.end)
        self.c = make_client()
        self.h = {"X-Tool-Token": TOKEN}

    def post(self, url, **body):
        return self.c.post(url, json=body, headers=self.h)

    def ok(self, url, **body):
        r = self.post(url, **body)
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        return r.get_json()

    def login(self, password=""):
        return self.ok("/api/login", id=auth.network_id(), password=password)

    def q(self, sql, *args):
        with closing(sqlite3.connect(self.path)) as c:
            return c.execute(sql, args).fetchall()

    def tables(self):
        return {n for (n,) in self.q("SELECT name FROM sqlite_master WHERE type='table'")}


class PlainDbTest(_Case):
    """設定の表が無い DB: どの表も一覧・マスタ管理・閲覧で見て直せる。"""

    def setUp(self):
        super().setUp()
        self.use(plain_db(self.tmp / "部品台帳.sqlite3"))

    def save_cols(self, table, **changes):
        st = self.ok("/api/easy/state", table=table)
        rows = []
        for c in st["columns"]:
            c = dict(c)
            c.update(changes.get(c["name"], {}))
            rows.append(c)
        return self.post("/api/easy/columns", table=table, rows=rows)

    def test_open_and_edit_any_table_without_settings(self):
        j = self.login()
        self.assertEqual(j["level"], auth.NO_ID_LEVEL)                    # ID が無い = 誰でも直せる
        self.assertIn("誰でも", j["note"])
        d = self.ok("/api/list/data")
        self.assertEqual((d["mode"], d["table"]), ("simple", "部品"))
        self.assertEqual([t["name"] for t in d["tables"]], ["部品", "仕入先"])
        self.assertEqual([c["name"] for c in d["columns"]], ["品番", "名前", "単価", "入荷日"])
        self.assertEqual(d["rows"][2][:3], ["B-1", "テープ", "2502.5"])           # 丸めない
        self.assertEqual(d["key_index"], 4)                                       # 末尾が rowid
        self.assertTrue(d["edit"]["allowed"])
        # マスを直す(管理番号 が無くても行は rowid で決まる)
        rowid = d["rows"][1][4]
        r = self.ok("/api/list/row-save", table="部品", rowid=rowid, values={"名前": "ポリ袋"}, seen={"名前": "袋"})
        self.assertEqual(r["row"][:2], ["A-2", "ポリ袋"])
        self.assertEqual(self.q("SELECT 名前 FROM 部品 WHERE rowid=?", int(rowid)), [("ポリ袋",)])
        # 見ていた値が古ければ書かない
        r = self.post("/api/list/row-save", table="部品", rowid=rowid, values={"名前": "x"}, seen={"名前": "袋"})
        self.assertEqual((r.status_code, r.get_json()["reason"]), (409, "conflict"))
        # 表を替える(この PC で覚える)。DB には何も書かない
        d = self.ok("/api/list/data", table="仕入先")
        self.assertEqual((d["table"], d["count"]), ("仕入先", 1))
        self.assertEqual(self.ok("/api/list/data")["table"], "仕入先")
        self.assertEqual(self.post("/api/list/row-save", table="部品", rowid=rowid, values={}, seen={}).status_code, 409)
        self.assertFalse(self.tables() & {"全般設定", "ID", "部品_設定", config.TOOL_SETTINGS_TABLE})

    def test_add_view_delete_rows(self):
        self.login()
        f = self.ok("/api/list/form", table="部品", rowid=0)
        self.assertEqual([x["name"] for x in f["fields"]], ["品番", "名前", "単価", "入荷日"])
        r = self.ok("/api/list/row-add", table="部品", values={"品番": "C-1", "名前": "緩衝材", "単価": "1,500"})
        rowid = r["rowid"]
        self.assertEqual(self.q("SELECT 品番, 単価, typeof(単価) FROM 部品 WHERE rowid=?", rowid),
                         [("C-1", 1500.0, "real")])                               # 数の列には数で
        f = self.ok("/api/list/form", table="部品", rowid=rowid)
        self.assertEqual(f["fields"][0]["value"], "C-1")
        seen = {x["name"]: x["value"] for x in f["fields"]}
        r = self.post("/api/list/row-delete", table="部品", rowid=rowid, seen={**seen, "名前": "違う"})
        self.assertEqual(r.status_code, 409)
        self.ok("/api/list/row-delete", table="部品", rowid=rowid, seen=seen)
        self.assertEqual(self.q("SELECT COUNT(*) FROM 部品"), [(3,)])

    def test_column_settings_show_input_print(self):
        self.login()
        r = self.save_cols("部品", 品番={"print": False}, 名前={"input": "combo"},
                           単価={"input": "spin", "step": "0.5"}, 入荷日={"input": "calendar", "show": True})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertIn(config.TOOL_SETTINGS_TABLE, self.tables())               # DB の中の隠した表に覚える
        d = self.ok("/api/list/data", table="部品")
        self.assertNotIn(config.TOOL_SETTINGS_TABLE, [t["name"] for t in d["tables"]])
        spec = d["edit"]["columns"]
        self.assertEqual((spec["名前"]["input"], spec["名前"]["choices"]), ("combo", ["テープ", "箱", "袋"]))
        self.assertEqual((spec["単価"]["input"], spec["単価"]["step"]), ("spin", "0.5"))
        rid = d["rows"][0][d["key_index"]]
        # スピン: 数でなければ断る / カレンダー: どの形で来ても列の形(2026/10/15)で書く
        r = self.post("/api/list/row-save", table="部品", rowid=rid, values={"単価": "abc"}, seen={"単価": "120.5"})
        self.assertEqual((r.status_code, r.get_json()["reason"]), (422, "bad_value"))
        self.ok("/api/list/row-save", table="部品", rowid=rid, values={"単価": "121.5", "入荷日": "2026-10-15"},
                seen={"単価": "120.5", "入荷日": "2026/10/01"})
        self.assertEqual(self.q("SELECT 単価, 入荷日 FROM 部品 WHERE rowid=?", int(rid)), [(121.5, "2026/10/15")])
        r = self.post("/api/list/row-save", table="部品", rowid=rid, values={"入荷日": "2026-02-30"},
                      seen={"入荷日": "2026/10/15"})
        self.assertEqual(r.status_code, 422)
        # 表示しない列は一覧・閲覧に出ない。印刷しない列は閲覧の印刷・保存に入らない
        self.save_cols("部品", 入荷日={"show": False}, 品番={"print": False})
        d = self.ok("/api/list/data")
        self.assertEqual([c["name"] for c in d["columns"]], ["品番", "名前", "単価"])
        b = self.ok("/api/browse/data", table="部品")
        self.assertEqual([c["name"] for c in b["columns"]], ["品番", "名前", "単価"])
        self.assertEqual(b["print_columns"], ["名前", "単価"])
        self.assertEqual(self.ok("/api/browse/tables")["tables"], ["部品", "仕入先"])
        # マスタ管理: 全部の列(表示しない列も)と、入力の種類どおりの欄
        f = self.ok("/api/master/form", table="部品", mode="UPDATE", row=["A-1", "箱", "121.5", "2026/10/15"])
        simple = {x["name"]: (x["simple"] or {}).get("input") for x in f["fields"]}
        self.assertEqual(simple, {"品番": None, "名前": "combo", "単価": "spin", "入荷日": "calendar"})
        r = self.post("/api/master/execute", table="部品", mode="UPDATE", values=["A-1", "箱", "x", "2026/10/15"],
                      key="A-1", seen=["A-1", "箱", "121.5", "2026/10/15"], confirmed=True, answer=True)
        self.assertEqual((r.status_code, r.get_json()["reason"]), (422, "bad_value"))
        self.ok("/api/master/execute", table="部品", mode="UPDATE", values=["A-1", "箱", "122", "2026-11-01"],
                key="A-1", seen=["A-1", "箱", "121.5", "2026/10/15"], confirmed=True, answer=True)
        self.assertEqual(self.q("SELECT 入荷日 FROM 部品 WHERE 品番='A-1'"), [("2026/11/01",)])

    def test_settings_follow_column_names(self):
        """列名で覚える: 列が増えても・並びが違う DB でも、ずれない。"""
        self.login()
        self.save_cols("部品", 単価={"input": "spin"}, 名前={"show": False})
        self.ok("/api/list/add-column", name="保管場所", kind="text", fill="倉庫A")
        d = self.ok("/api/list/data", table="部品")
        self.assertEqual([c["name"] for c in d["columns"]], ["品番", "単価", "入荷日", "保管場所"])   # 足した列は出る
        self.assertEqual(d["edit"]["columns"]["単価"]["input"], "spin")
        # 同じ表で列の並びが違う DB: 設定が無ければ既定(どの列も表示・文字・印刷)
        other = plain_db(self.tmp / "別.sqlite3")
        with closing(shared_db.connect(other)) as conn:
            self.assertEqual({c.input for c in simple_table.load(conn, "部品")}, {"text"})
            simple_table.save(conn, "部品", [simple_table.ColumnSetting("単価", input="spin")])
            conn.execute("ALTER TABLE 部品 RENAME TO 部品_古")
            conn.execute("CREATE TABLE 部品 (単価 REAL, 品番 TEXT)")              # 並びが違う
            got = {c.name: c.input for c in simple_table.load(conn, "部品")}
        self.assertEqual(got, {"単価": "spin", "品番": "text"})

    def test_bad_settings_refused(self):
        self.login()
        self.assertEqual(self.save_cols("部品", 単価={"input": "spin", "step": "0"}).status_code, 422)
        self.assertEqual(self.save_cols("部品", **{n: {"show": False} for n in ("品番", "名前", "単価", "入荷日")})
                         .status_code, 422)
        self.assertEqual(self.save_cols("部品", 単価={"input": "なにか"}).status_code, 422)
        self.assertEqual(self.post("/api/easy/columns", table=config.ID_TABLE, rows=[]).status_code, 422)

    def test_start_login(self):
        self.login()
        st = self.ok("/api/easy/state")
        self.assertFalse(st["has_id"])
        self.assertEqual(self.post("/api/easy/start", password="").status_code, 422)
        r = self.ok("/api/easy/start", password="Pass1", name="試験")
        self.assertEqual(auth.level(), 5)
        self.assertTrue(Path(r["backup"]).is_file())
        self.assertIn("ID", self.tables())
        self.assertNotIn("全般設定", self.tables())                               # 全般設定 は作らない
        self.assertEqual(self.post("/api/easy/start", password="x").get_json()["reason"], "has_id")
        auth.end()
        self.assertEqual(self.post("/api/login", id=auth.network_id(), password="bad").status_code, 403)
        self.assertEqual(self.login("pass1")["level"], 5)

    def test_master_rows_in_any_table(self):
        """マスタ管理にどの表も並び、1 列目が番号でない表も行を足す・直せる。"""
        self.login()
        tables = self.ok("/api/master/tables")["tables"]
        self.assertTrue({"部品", "仕入先"} <= set(tables))
        f = self.ok("/api/master/form", table="仕入先", mode="ADDNEW")
        self.assertEqual([(x["value"], x["is_key"]) for x in f["fields"]], [("", False), ("", False)])
        exe = lambda mode, values, seen=None: self.post(
            "/api/master/execute", table="仕入先", mode=mode, values=values, key=values[0], seen=seen,
            confirmed=True, answer=True)
        self.assertEqual(exe("ADDNEW", ["東海紙工", "052-222-2222"]).status_code, 200)     # 同じ名前でも足す
        self.assertEqual(self.q("SELECT * FROM 仕入先 ORDER BY rowid"),
                         [("東海紙工", "052-000-0000"), ("東海紙工", "052-222-2222")])
        self.assertEqual(exe("UPDATE", ["東海紙工 名古屋", "052-222-2222"], ["東海紙工", "052-222-2222"]).status_code, 200)
        self.assertEqual(self.q("SELECT * FROM 仕入先 ORDER BY rowid")[1], ("東海紙工 名古屋", "052-222-2222"))
        self.assertEqual(exe("UPDATE", ["x", "y"], ["無い", "行"]).get_json()["reason"], "conflict")
        # 本体(一覧の表)も VBA 版でなければ、マスタ管理で行を直せる
        self.assertTrue(self.ok("/api/master/rows", table="部品")["caps"]["rows"])


class LegacyDbTest(_Case):
    """VBA 版の DB(全般設定・<表>_設定・管理番号)は今までどおり。表示の設定は効く。"""

    def setUp(self):
        super().setUp()
        self.use(fresh_db_path(self.tmp))
        auth.start(auth.LoginResult(True, user_id="Xfa-Ngyinazawa", level=5))

    def test_legacy_path_and_hidden_columns(self):
        d = self.ok("/api/list/data")
        self.assertEqual((d["mode"], d["table"], d["count"]), ("legacy", MAIN, 327))
        st = self.ok("/api/easy/state")
        self.assertTrue(st["legacy"])
        rows = [dict(c, show=c["name"] != "RA") for c in st["columns"]]
        self.ok("/api/easy/columns", table=MAIN, rows=rows)
        d = self.ok("/api/list/data")
        self.assertIn("RA", d["view"]["hidden"])
        self.assertIn("RA", [c["name"] for c in d["columns"]])                  # VBA 版の表は隠すだけ
        # ほかの表(タイプ)も一覧で選べない(システムの表)が、業務の表なら選べる
        self.assertEqual([t["name"] for t in d["tables"]], [MAIN])

    def test_reordered_and_missing_rows(self):
        with closing(sqlite3.connect(self.path)) as c:
            c.execute(f"UPDATE [{CONV}] SET 管理番号 = 100 WHERE 項目名 = '作業名'")     # 並びを変える
            c.execute(f"DELETE FROM [{CONV}] WHERE 項目名 = 'RA'")                     # 1 行足りない
            c.execute(f"INSERT INTO [{CONV}] (管理番号, 項目名) VALUES ('101', '昔の列')")   # 本体に無い
            c.execute(f"UPDATE [{CONV}] SET 表示 = '金額' WHERE 項目名 = 'Ⅱ'")
            c.commit()
        with closing(shared_db.connect(self.path)) as conn:
            s = load_setup(conn)
        self.assertEqual([r.item for r in s.conv.rows], s.main_columns)
        self.assertEqual(s.conv.by_item("Ⅱ").ind_type, "金額")                       # 名前で合わせる
        notes = " ".join(s.notices)
        self.assertIn("RA", notes)
        self.assertIn("昔の列", notes)
        self.assertEqual(self.ok("/api/list/data")["count"], 327)
        names = [f["name"] for f in self.ok("/api/record/form", mode="修正", key="2")["fields"]]
        self.assertEqual(names, s.main_columns)

    def test_column_names_unchanged(self):
        with closing(sqlite3.connect(self.path)) as c:
            self.assertEqual(len(column_names(c, MAIN)), 25)


if __name__ == "__main__":
    unittest.main()
