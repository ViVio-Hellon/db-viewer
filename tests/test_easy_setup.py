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


class AutoAndFileTest(_Case):
    """自動: 登録した人・直した人 と、ファイルの列のプレビュー(0.10.0)。"""

    def setUp(self):
        super().setUp()
        self.use(plain_db(self.tmp / "部品台帳.sqlite3"))
        with closing(sqlite3.connect(self.path)) as c:
            c.execute("ALTER TABLE 部品 ADD COLUMN 登録者 TEXT")
            c.execute("ALTER TABLE 部品 ADD COLUMN 更新者 TEXT")
            c.execute("ALTER TABLE 部品 ADD COLUMN 図面 TEXT")
            c.execute("UPDATE 部品 SET 図面 = '図面/A-1.pdf' WHERE 品番 = 'A-1'")
            c.execute("UPDATE 部品 SET 図面 = '図面/A-2.xlsx' WHERE 品番 = 'A-2'")
            c.commit()
        self.login()
        st = self.ok("/api/easy/state", table="部品")
        rows = [dict(c) for c in st["columns"]]
        for r in rows:
            r["input"] = {"登録者": "auto_new_user", "更新者": "auto_upd_user", "図面": "file"}.get(r["name"], r["input"])
        self.ok("/api/easy/columns", table="部品", rows=rows)
        self.refdir = self.tmp / "参照"
        (self.refdir / "図面").mkdir(parents=True)
        user_settings.save(config.KEY_REF_DIR, str(self.refdir))
        self.addCleanup(user_settings.save, config.KEY_REF_DIR, "")

    def who(self, rowid):
        return self.q("SELECT 登録者, 更新者 FROM 部品 WHERE rowid=?", int(rowid))[0]

    def test_auto_user_on_add_and_change(self):
        d = self.ok("/api/list/data", table="部品")
        self.assertEqual(d["edit"]["columns"]["登録者"]["inline"], "")            # 一覧では直せない
        self.assertIn("自動", d["edit"]["columns"]["更新者"]["why"])
        r = self.ok("/api/list/row-add", table="部品", values={"品番": "C-1", "登録者": "ほかの人"})
        self.assertEqual(self.who(r["rowid"]), ("tester", "tester"))           # 画面の値は使わない
        with closing(sqlite3.connect(self.path)) as c:
            c.execute("UPDATE 部品 SET 登録者 = '前の人', 更新者 = '前の人' WHERE rowid = ?", (r["rowid"],))
            c.commit()
        self.ok("/api/list/row-save", table="部品", rowid=r["rowid"], values={"名前": "x"}, seen={"名前": ""})
        self.assertEqual(self.who(r["rowid"]), ("前の人", "tester"))            # 直した人だけ替わる
        with closing(sqlite3.connect(self.path)) as c:
            c.execute("UPDATE 部品 SET 更新者 = '前の人' WHERE rowid = ?", (r["rowid"],))
            c.commit()
        self.ok("/api/list/row-save", table="部品", rowid=r["rowid"], values={"名前": "x"}, seen={"名前": "x"})
        self.assertEqual(self.who(r["rowid"]), ("前の人", "前の人"))            # 変わっていなければ入れない
        # マスタ管理でも同じ
        f = self.ok("/api/master/form", table="部品", mode="ADDNEW")
        kinds = {x["name"]: (x["simple"] or {}).get("input") for x in f["fields"]}
        self.assertEqual((kinds["登録者"], kinds["更新者"], kinds["図面"]), ("auto_new_user", "auto_upd_user", "file"))
        vals = ["D-1", "", "", "", "", "", "", ""]
        self.ok("/api/master/execute", table="部品", mode="ADDNEW", values=vals, key="D-1", confirmed=True, answer=True)
        self.assertEqual(self.q("SELECT 登録者, 更新者 FROM 部品 WHERE 品番='D-1'"), [("tester", "tester")])

    def preview(self, value, column="図面"):
        return self.c.get("/api/file/preview", query_string={"table": "部品", "column": column, "value": value},
                          headers=self.h)

    def test_file_preview(self):
        (self.refdir / "図面" / "A-1.pdf").write_bytes(b"%PDF-1.4 test")
        (self.refdir / "図面" / "A-2.xlsx").write_bytes(b"PK")
        d = self.ok("/api/list/data", table="部品")
        self.assertEqual(d["edit"]["columns"]["図面"]["inline"], "file")
        r = self.preview("図面/A-1.pdf")
        self.assertEqual((r.status_code, r.mimetype), (200, "application/pdf"))
        self.assertTrue(r.headers["Content-Disposition"].startswith("inline"))   # ダウンロードさせない
        self.assertEqual(r.data, b"%PDF-1.4 test")
        from dbviewer import office_bridge                                     # Office に頼めない PC では
        with mock.patch.object(office_bridge, "available", return_value=False):
            self.assertEqual(self.preview("図面/A-2.xlsx").status_code, 415)      # 読めない中身は断る
        self.assertEqual(self.preview("../../etc/passwd").status_code, 403)      # 表に無い値は見せない
        self.assertEqual(self.preview("箱", column="名前").status_code, 403)      # ファイルの列でない
        with closing(sqlite3.connect(self.path)) as c:
            c.execute("UPDATE 部品 SET 図面 = ? WHERE 品番 = 'B-1'", (str(self.refdir / "図面" / "メモ.txt"),))
            c.commit()
        self.assertEqual(self.preview(str(self.refdir / "図面" / "メモ.txt")).status_code, 404)   # まだ無い
        (self.refdir / "図面" / "メモ.txt").write_bytes("手順メモ".encode("cp932"))
        r = self.preview(str(self.refdir / "図面" / "メモ.txt"))                 # 絶対パス・Shift_JIS
        self.assertEqual((r.status_code, r.get_data(as_text=True)), (200, "手順メモ"))


def _docx(path: Path) -> Path:
    """Word の最小の文書(見出し・段落・表)。"""
    import zipfile
    w = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    doc = (f'<?xml version="1.0" encoding="UTF-8"?><w:document {w}><w:body>'
           '<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>作業手順書 A-1</w:t></w:r></w:p>'
           '<w:p><w:r><w:t>箱を</w:t></w:r><w:r><w:tab/><w:t>組み立てる &amp; 確かめる</w:t></w:r></w:p>'
           '<w:tbl><w:tr><w:tc><w:p><w:r><w:t>工程</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>時間</w:t></w:r></w:p></w:tc></w:tr>'
           '<w:tr><w:tc><w:p><w:r><w:t>折る</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>30秒</w:t></w:r></w:p></w:tc></w:tr></w:tbl>'
           '</w:body></w:document>')
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        z.writestr("word/document.xml", doc)
    return path


def _xlsx_shared(path: Path) -> Path:
    """Excel が書く形(共有文字列・日付の書式・空いた列)の最小のブック。"""
    import zipfile
    s = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    r = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("xl/workbook.xml", f'<workbook {s} {r}><sheets><sheet name="検査表" sheetId="1" r:id="rId1"/>'
                                      f'<sheet name="空" sheetId="2" r:id="rId2"/></sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels",
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Target="worksheets/sheet2.xml"/>'
                   '</Relationships>')
        z.writestr("xl/sharedStrings.xml", f'<sst {s}><si><t>品番</t></si><si><t>検査日</t></si><si><r><t>A-</t></r><r><t>1</t></r></si></sst>')
        z.writestr("xl/styles.xml", f'<styleSheet {s}><cellXfs><xf numFmtId="0"/><xf numFmtId="14"/></cellXfs></styleSheet>')
        z.writestr("xl/worksheets/sheet1.xml", f'<worksheet {s}><sheetData>'
                   '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="C1" t="s"><v>1</v></c></row>'
                   '<row r="2"><c r="A2" t="s"><v>2</v></c><c r="B2"><v>0.30000000000000004</v></c><c r="C2" s="1"><v>46310</v></c></row>'
                   '</sheetData></worksheet>')
        z.writestr("xl/worksheets/sheet2.xml", f'<worksheet {s}><sheetData/></worksheet>')
    return path


class ClickActionTest(_Case):
    """クリックで実行(0.12.0・0.13.0): 値をコピー / 現物を開く / プレビュー。開くもの = Excel・Word・PDF・URL。"""

    def setUp(self):
        super().setUp()
        self.use(plain_db(self.tmp / "部品台帳.sqlite3"))
        with closing(sqlite3.connect(self.path)) as c:
            c.execute("ALTER TABLE 部品 ADD COLUMN 資料 TEXT")
            c.executemany("UPDATE 部品 SET 資料 = ? WHERE 品番 = ?",
                          [("https://intra.example/doc/A-1", "A-1"), ("手順/A-2.docx", "A-2"), ("手順/B-1.xlsx", "B-1")])
            c.commit()
        self.ref = self.tmp / "参照"
        (self.ref / "手順").mkdir(parents=True)
        (self.ref / "図面").mkdir()
        user_settings.save(config.KEY_REF_DIR, str(self.ref))
        self.addCleanup(user_settings.save, config.KEY_REF_DIR, "")
        self.login()

    def save(self, **changes):
        st = self.ok("/api/easy/state", table="部品")
        self.assertEqual([k for k, _ in st["clicks"]], ["", "copy", "open", "preview"])
        rows = [dict(c, **changes.get(c["name"], {})) for c in st["columns"]]
        return self.post("/api/easy/columns", table="部品", rows=rows)

    def test_saved_and_handed_to_every_screen(self):
        r = self.save(品番={"click": "copy"}, 名前={"click": "open", "place": "https://intra.example/search?q={値}"},
                      資料={"click": "preview"}, 単価={"click": "copy", "place": "無視される"})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        a = lambda click, place="", col="": {"click": click, "place": place, "table": "部品", "column": col}
        want = {"品番": a("copy", col="品番"), "名前": a("open", "https://intra.example/search?q={値}", "名前"),
                "資料": a("preview", col="資料"), "単価": a("copy", col="単価")}     # コピーに場所は要らない
        self.assertEqual(self.ok("/api/list/data", table="部品")["actions"], want)
        self.assertEqual(self.ok("/api/browse/data", table="部品")["actions"], want)
        self.assertEqual(self.ok("/api/master/rows", table="部品")["actions"], want)
        self.assertEqual(self.ok("/api/list/data", table="仕入先")["actions"], {})   # 決めていない表は空

    def test_old_url_setting_reads_as_open(self):
        with closing(sqlite3.connect(self.path)) as c:
            simple_table._ensure(c)
            c.execute(f"INSERT INTO {config.TOOL_SETTINGS_TABLE} VALUES (?, ?, '', '')",
                      ("部品", '{"columns": {"名前": {"click": "url", "url": "https://x.example/{値}"}}}'))
            c.commit()
        a = self.ok("/api/list/data", table="部品")["actions"]["名前"]
        self.assertEqual((a["click"], a["place"]), ("open", "https://x.example/{値}"))

    def test_bad_place_refused(self):
        for bad in ("javascript:alert(1)", "file:///C:/x", "vbscript:x"):
            r = self.save(名前={"click": "open", "place": bad})
            self.assertEqual(r.status_code, 422, bad)
            self.assertIn("http", r.get_json()["message"])
        for good in ("図面\\{値}.pdf", "C:\\共有\\{値}.xlsx", "\\\\server\\共有\\", "https://intra.example/"):
            self.assertEqual(self.save(名前={"click": "preview", "place": good}).status_code, 200, good)
        self.assertEqual(self.save(名前={"click": "jump"}).status_code, 422)

    def test_click_target_rule(self):
        s = simple_table.ColumnSetting("名前", click="open", place="https://intra.example/search?q={値}&x=1")
        self.assertEqual(simple_table.click_target(s, "箱 A&B"),
                         ("url", "https://intra.example/search?q=%E7%AE%B1%20A%26B&x=1"))
        s.place = "https://intra.example/item/"                             # {値} が無ければ末尾に
        self.assertEqual(simple_table.click_target(s, "A-1"), ("url", "https://intra.example/item/A-1"))
        s.place = "図面\\{値}.pdf"                                          # ファイルの決まり(符号化しない)
        self.assertEqual(simple_table.click_target(s, "A 1"), ("file", "図面\\A 1.pdf"))
        s.place = ""                                                        # 空なら値そのもの
        self.assertEqual(simple_table.click_target(s, "https://intra.example/doc"), ("url", "https://intra.example/doc"))
        self.assertEqual(simple_table.click_target(s, "Z:\\手順\\x.docx"), ("file", "Z:\\手順\\x.docx"))
        self.assertEqual(simple_table.click_target(s, "javascript:alert(1)"), ("", ""))
        self.assertEqual(simple_table.click_target(s, ""), ("", ""))
        self.assertEqual((simple_table.open_kind("a.XLSX"), simple_table.open_kind("b.doc"),
                          simple_table.open_kind("c.pdf"), simple_table.open_kind("d.exe")), ("Excel", "Word", "PDF", ""))

    def test_open_real_file(self):
        from app.routes import files
        self.save(資料={"click": "open"}, 名前={"click": "open", "place": "図面/{値}.pdf"}, 単価={"click": "preview"})
        _docx(self.ref / "手順" / "A-2.docx")
        (self.ref / "図面" / "箱.pdf").write_bytes(b"%PDF-1.4 x")
        opened = []
        with mock.patch.object(files, "launch", side_effect=opened.append):
            j = self.ok("/api/file/open", table="部品", column="資料", value="手順/A-2.docx")
            self.assertEqual((j["kind"], j["what"]), ("file", "Word"))
            self.assertIn("Wordで開きました", j["message"])
            j = self.ok("/api/file/open", table="部品", column="名前", value="箱")       # 場所の決まりから
            self.assertEqual(j["what"], "PDF")
            j = self.ok("/api/file/open", table="部品", column="資料", value="https://intra.example/doc/A-1")
            self.assertEqual((j["kind"], j["url"]), ("url", "https://intra.example/doc/A-1"))  # URL は画面が開く
            # 断る: 表に無い値・ファイルが無い・開く列でない(プレビューだけの列)
            self.assertEqual(self.post("/api/file/open", table="部品", column="資料", value="手順/無い.docx").status_code, 403)
            self.assertEqual(self.post("/api/file/open", table="部品", column="資料", value="手順/B-1.xlsx").status_code, 404)
            self.assertEqual(self.post("/api/file/open", table="部品", column="単価", value="30").status_code, 403)
        self.assertEqual([p.name for p in opened], ["A-2.docx", "箱.pdf"])
        # 開かない形式(実行ファイルなど)
        with closing(sqlite3.connect(self.path)) as c:
            c.execute("UPDATE 部品 SET 資料 = '手順/x.bat' WHERE 品番 = 'A-1'")
            c.commit()
        (self.ref / "手順" / "x.bat").write_text("echo", encoding="ascii")
        with mock.patch.object(files, "launch", side_effect=opened.append):
            r = self.post("/api/file/open", table="部品", column="資料", value="手順/x.bat")
        self.assertEqual(r.status_code, 415)
        self.assertEqual(len(opened), 2)

    def test_preview_excel_word_url(self):
        # Office に頼めない PC のとき(Windows の試験機は cscript があるので、そちらの道は test_office_bridge で)
        from dbviewer import office_bridge
        p = mock.patch.object(office_bridge, "available", return_value=False)
        p.start()
        self.addCleanup(p.stop)
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "vendor"))
        import datetime as dt
        import xlsx_writer
        self.save(資料={"click": "preview"})
        _docx(self.ref / "手順" / "A-2.docx")
        xlsx_writer.write_xlsx([{"name": "検査", "columns": ["品番", "日付", "数"],
                                 "rows": [["B-1", dt.date(2026, 10, 15), 2502.5]]}], str(self.ref / "手順" / "B-1.xlsx"))
        get = lambda v: self.c.get("/api/file/preview", query_string={"table": "部品", "column": "資料", "value": v},
                                   headers=self.h)
        r = get("手順/B-1.xlsx")
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        html = r.get_data(as_text=True)
        for want in ("検査", "品番", "B-1", "2026/10/15", "2502.5"):
            self.assertIn(want, html)
        html = get("手順/A-2.docx").get_data(as_text=True)
        self.assertIn("<h2>作業手順書 A-1</h2>", html)
        self.assertIn("組み立てる &amp; 確かめる", html)
        self.assertIn("<td>30秒</td>", html)
        html = get("https://intra.example/doc/A-1").get_data(as_text=True)
        self.assertIn('<iframe src="https://intra.example/doc/A-1"', html)
        self.assertIn("既定のブラウザで開く", html)
        # Excel が書く形(共有文字列・日付の書式)と、古い形式
        from dbviewer import office_preview
        sheets = office_preview.read_xlsx(_xlsx_shared(self.tmp / "s.xlsx"))
        self.assertEqual(sheets[0]["rows"], [["品番", "", "検査日"], ["A-1", "0.3", "2026/10/15"]])
        self.assertEqual((sheets[1]["name"], sheets[1]["rows"]), ("空", []))
        with closing(sqlite3.connect(self.path)) as c:
            c.execute("UPDATE 部品 SET 資料 = '手順/古い.xls' WHERE 品番 = 'A-1'")
            c.commit()
        (self.ref / "手順" / "古い.xls").write_bytes(b"\xd0\xcf\x11\xe0")
        r = get("手順/古い.xls")
        self.assertEqual(r.status_code, 415)
        self.assertIn("現物を開く", r.get_data(as_text=True))
        # 壊れた .xlsx は理由を出す(落ちない)
        (self.ref / "手順" / "B-1.xlsx").write_bytes(b"not a zip")
        r = get("手順/B-1.xlsx")
        self.assertEqual(r.status_code, 415)
        self.assertIn("壊れている", r.get_data(as_text=True))


class VbaDbTest(_Case):
    """VBA 版の DB(全般設定・<表>_設定・管理番号)も同じ道(0.9.0)。_設定 は取り込んで消せる。"""

    def setUp(self):
        super().setUp()
        self.use(fresh_db_path(self.tmp))
        auth.start(auth.LoginResult(True, user_id="Xfa-Ngyinazawa", level=5))

    def test_same_path_and_inputs_from_vba(self):
        d = self.ok("/api/list/data")
        self.assertEqual((d["mode"], d["table"], d["count"]), ("simple", MAIN, 327))
        self.assertEqual(d["key_index"], len(d["columns"]))
        spec = d["edit"]["columns"]
        # VBA 版の入力形式から: 数値2/0/500/1 → スピン、calender → カレンダー、#UPUSER# → 文字
        self.assertEqual((spec["Ⅰ"]["input"], spec["Ⅰ"]["step"]), ("spin", "1"))
        self.assertEqual(spec["登録日"]["input"], "calendar")
        self.assertEqual(spec["登録者"]["input"], "auto_upd_user")               # #UPUSER# → 自動: 直した人
        st = self.ok("/api/easy/state")
        self.assertTrue(st["from_vba"])
        self.assertEqual(st["vba_tables"], ["フィルタ_設定", CONV])
        # _設定 の並び・数が本体と違っても、項目名で読む
        with closing(sqlite3.connect(self.path)) as c:
            c.execute(f"DELETE FROM [{CONV}] WHERE 項目名 = 'Ⅱ'")
            c.execute(f"UPDATE [{CONV}] SET 管理番号 = 100 WHERE 項目名 = 'Ⅰ'")
            c.commit()
        spec = self.ok("/api/list/data")["edit"]["columns"]
        self.assertEqual((spec["Ⅰ"]["input"], spec["Ⅱ"]["input"]), ("spin", "text"))

    def test_retire_vba_settings(self):
        n = len(self.q(f"SELECT * FROM [{MAIN}]"))
        self.assertEqual(self.post("/api/easy/retire").status_code, 200)
        self.assertFalse({CONV, "フィルタ_設定"} & self.tables())                  # _設定 は消えた
        self.assertEqual(len(self.q(f"SELECT * FROM [{MAIN}]")), n)              # 本体はそのまま
        st = self.ok("/api/easy/state")
        self.assertEqual((st["vba_tables"], st["from_vba"]), ([], False))
        inputs = {c["name"]: c["input"] for c in st["columns"]}                 # 入力の種類は取り込んだ
        self.assertEqual((inputs["Ⅳ"], inputs["完了日"], inputs["作業名"]), ("spin", "calendar", "text"))
        self.assertEqual(self.post("/api/easy/retire").status_code, 422)          # もう無い
        # 一覧・帳票・マスタ管理はそのまま動く
        d = self.ok("/api/list/data")
        self.assertEqual(d["count"], 327)
        self.assertEqual(d["edit"]["columns"]["Ⅰ"]["input"], "spin")
        rowids = [r[d["key_index"]] for r in d["rows"]][:319]
        rep = self.ok("/api/report/run", keys=rowids, by="rowid")
        self.assertEqual(rep["source_rows"], 319)
        self.assertIn(MAIN, self.ok("/api/master/tables")["tables"])

    def test_retire_needs_level5(self):
        auth.start(auth.LoginResult(True, user_id="Xfa-Ngyinazawa", level=4))
        self.assertEqual(self.post("/api/easy/retire").status_code, 403)
        self.assertIn(CONV, self.tables())

    def test_report_only_for_its_table(self):
        self.ok("/api/list/data", table=MAIN)
        self.assertTrue(self.ok("/api/list/data")["setup"]["report_flag"])
        with closing(sqlite3.connect(self.path)) as c:
            c.execute("CREATE TABLE 別表 (a TEXT)")
            c.commit()
        d = self.ok("/api/list/data", table="別表")
        self.assertFalse(d["setup"]["report_flag"])
        self.assertEqual(self.post("/api/report/run", keys=[]).get_json()["error"]["code"], "report_table")


if __name__ == "__main__":
    unittest.main()
