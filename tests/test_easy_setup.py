"""かんたん設定(0.7.0): 設定の表が無い・合わない DB でも開き、画面で決めた決まりで直せる"""
from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

from dbviewer import auth, config, easy_setup, shared_db, user_settings
from dbviewer.settings_tables import column_names, load_setup
from tests._fixture_db import fresh_db_path
from tests._web import TOKEN, make_client

MAIN, CONV = "作業標準管理", "作業標準管理_設定"


def plain_db(path: Path) -> Path:
    """設定の表が 1 つも無い、ふつうの DB(表 2 つ)。"""
    with closing(sqlite3.connect(path)) as c:
        c.execute("CREATE TABLE 部品 (品番 TEXT, 名前 TEXT, 単価 REAL)")
        c.executemany("INSERT INTO 部品 VALUES (?, ?, ?)",
                      [("A-1", "箱", 120.5), ("A-2", "袋", 30), ("B-1", "テープ", 2502.5)])
        c.execute("CREATE TABLE 仕入先 (名前 TEXT, 電話 TEXT)")
        c.execute("INSERT INTO 仕入先 VALUES ('東海紙工', '052-000-0000')")
        c.commit()
    return path


class _Case(unittest.TestCase):
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

    def setUp(self):
        # この PC のログイン ID・PC 名(試験の機械には無いことがある)
        env = mock.patch.dict(os.environ, {"USERNAME": "tester", "COMPUTERNAME": "PC-TEST"})
        env.start()
        self.addCleanup(env.stop)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)

    def post(self, url, **body):
        return self.c.post(url, json=body, headers=self.h)

    def login(self, password=""):
        r = self.post("/api/login", id=auth.network_id(), password=password)
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        return r.get_json()

    def setup_now(self):
        with closing(shared_db.connect(self.path)) as conn:
            return load_setup(conn)


class PlainDbTest(_Case):
    """設定の表が無い DB をそのまま開く → 閲覧だけ → 編集を始める → 決まりを選ぶ。"""

    def setUp(self):
        super().setUp()
        self.use(plain_db(self.tmp / "部品台帳.sqlite3"))

    def test_opens_without_settings_view_only(self):
        s = self.setup_now()
        self.assertEqual((s.general.main_table, s.auto_main, s.auto_conv, s.has_id), ("部品", True, True, False))
        self.assertEqual([r.item for r in s.conv.rows], ["品番", "名前", "単価"])     # どの列も自由入力
        j = self.login()
        self.assertEqual(j["level"], auth.VIEW_ONLY_LEVEL)
        self.assertIn("閲覧だけ", j["note"])
        d = self.post("/api/list/data").get_json()
        self.assertEqual([c["name"] for c in d["columns"]], ["品番", "名前", "単価"])
        self.assertEqual(d["count"], 3)
        self.assertEqual(d["rows"][2][2], "2502.5")                                # 丸めない
        self.assertFalse(d["edit"]["allowed"])
        self.assertEqual(self.post("/api/list/cell", key="1", column="名前", value="x", seen="箱").status_code, 403)
        # 書く設定は断る。表示する表は、この PC だけで切り替えられる
        self.assertEqual(self.post("/api/easy/columns", rows=[]).status_code, 403)
        r = self.post("/api/easy/main", table="仕入先")
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertIn("この PC", r.get_json()["message"])
        self.assertEqual(self.setup_now().general.main_table, "仕入先")
        with closing(sqlite3.connect(self.path)) as c:                               # DB には書いていない
            self.assertNotIn("全般設定", {n for (n,) in c.execute("SELECT name FROM sqlite_master")})

    def test_start_editing_then_simple_settings(self):
        self.login()
        st = self.post("/api/easy/state").get_json()
        self.assertTrue(st["can_start"])
        self.assertFalse(st["can_write"])
        self.assertEqual(self.post("/api/easy/start", password="").status_code, 422)
        r = self.post("/api/easy/start", password="Pass1", name="試験")
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual(auth.level(), 5)                                            # そのまま Level 5
        self.assertTrue(Path(r.get_json()["backup"]).is_file())
        s = self.setup_now()
        self.assertEqual((s.has_id, s.has_general, s.auto_main), (True, True, False))
        # 2 度目は断る(ID の表ができた)
        self.assertEqual(self.post("/api/easy/start", password="x").get_json()["reason"], "has_id")
        # 次のログインは登録したパスワードで
        auth.end()
        self.assertEqual(self.post("/api/login", id=auth.network_id(), password="bad").status_code, 403)
        self.assertEqual(self.login("pass1")["level"], 5)                            # VBA と同じく小文字でそろえる

        # 管理番号を足す → 一覧で直せる
        self.assertIn("管理番号", " ".join(s.notices))
        r = self.post("/api/easy/key")
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        d = self.post("/api/list/data").get_json()
        self.assertGreaterEqual(d["key_index"], 0)
        with closing(sqlite3.connect(self.path)) as c:
            self.assertEqual([k for (k,) in c.execute("SELECT 管理番号 FROM 部品 ORDER BY rowid")], [1, 2, 3])

        # 列の決まり: 単価は 数・必須・金額、名前は 仕入先 から選ぶ、品番で降順
        st = self.post("/api/easy/state").get_json()
        rows = {c["name"]: dict(c) for c in st["columns"]}
        rows["単価"].update(input="数値変換", required=True, money=True)
        rows["名前"].update(input="s", master="仕入先")
        rows["品番"].update(sorted=True, sort="DESC")
        r = self.post("/api/easy/columns", rows=list(rows.values()))
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        s = self.setup_now()
        self.assertFalse(s.auto_conv)
        self.assertEqual(s.conv.by_item("単価").input_type, "数値変換")
        self.assertEqual(s.conv.by_item("単価").reg_con, "必須")
        self.assertEqual(s.conv.by_item("名前").input_type, "s仕入先")
        d = self.post("/api/list/data").get_json()
        names = [c["name"] for c in d["columns"]]
        self.assertEqual([row[names.index("品番")] for row in d["rows"]], ["B-1", "A-2", "A-1"])
        self.assertEqual(d["rows"][0][names.index("単価")], "2,503")                # 金額で見せる
        self.assertTrue(d["edit"]["allowed"])
        # 必須の列を空にはできない(登録と同じ決まり)
        key = d["rows"][0][d["key_index"]]
        r = self.post("/api/list/cell", key=key, column="単価", value="", seen="2,503")
        self.assertEqual(r.status_code, 422, r.get_data(as_text=True))
        # 読み直した画面は選んだとおり
        again = {c["name"]: c for c in self.post("/api/easy/state").get_json()["columns"]}
        self.assertEqual((again["名前"]["input"], again["名前"]["master"]), ("s", "仕入先"))
        self.assertTrue(again["品番"]["sorted"])

    def test_bad_choices_refused(self):
        self.login()
        self.post("/api/easy/start", password="p")
        st = self.post("/api/easy/state").get_json()
        rows = [dict(c) for c in st["columns"]]
        rows[0].update(input="s", master="ID")                                        # ID は選べない
        self.assertEqual(self.post("/api/easy/columns", rows=rows).get_json()["reason"], "bad_input")
        rows = [dict(c, sorted=True) for c in st["columns"]]                          # 並べる列は 1 つ
        self.assertEqual(self.post("/api/easy/columns", rows=rows).status_code, 422)
        rows = [dict(c) for c in st["columns"]]
        rows[0].update(input="custom", raw="なにか")                                   # 読めない書き方
        self.assertEqual(self.post("/api/easy/columns", rows=rows).status_code, 422)
        self.assertEqual(self.post("/api/easy/main", table="ID").status_code, 422)

    def test_columns_can_be_added_to_any_table_from_browse(self):
        self.login()
        self.post("/api/easy/start", password="p")
        t = self.post("/api/browse/tables").get_json()
        self.assertEqual(t["tables"], ["部品", "仕入先"])                               # 決まりが無ければ全部(ID は出さない)
        self.assertTrue(t["can_add_column"])
        r = self.post("/api/browse/add-column", table="仕入先", name="担当", kind="text", fill="未定")
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        with closing(sqlite3.connect(self.path)) as c:
            self.assertEqual(column_names(c, "仕入先")[-1], "担当")
        self.assertEqual(self.post("/api/browse/add-column", table="ID", name="x").status_code, 403)

    def test_master_lists_every_table_when_unconfigured(self):
        """設定の無い DB は、マスタ管理にどの表も並ぶ(行を足せる)。"""
        self.login()
        self.post("/api/easy/start", password="p")
        tables = self.post("/api/master/tables").get_json()["tables"]
        self.assertIn("仕入先", tables)
        f = self.post("/api/master/form", table="仕入先", mode="ADDNEW").get_json()
        self.assertEqual([(x["value"], x["is_key"]) for x in f["fields"]], [("", False), ("", False)])  # 名前は打てる
        exe = lambda mode, values, seen=None: self.post(
            "/api/master/execute", table="仕入先", mode=mode, values=values, key=values[0], seen=seen,
            confirmed=True, answer=True)
        # 同じ名前でも上書きせずに足す(1 列目は鍵ではない)
        r = exe("ADDNEW", ["東海紙工", "052-222-2222"])
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        def rows():
            with closing(sqlite3.connect(self.path)) as c:
                return [list(x) for x in c.execute("SELECT * FROM 仕入先 ORDER BY rowid")]
        self.assertEqual(rows(), [["東海紙工", "052-000-0000"], ["東海紙工", "052-222-2222"]])
        # 修正は見ていた行を直す(1 列目も変えられる)
        r = exe("UPDATE", ["東海紙工 名古屋", "052-222-2222"], seen=["東海紙工", "052-222-2222"])
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual(rows()[1], ["東海紙工 名古屋", "052-222-2222"])
        self.assertEqual(rows()[0], ["東海紙工", "052-000-0000"])
        # 見ていた行がもう無ければ書かない
        r = exe("UPDATE", ["x", "y"], seen=["無い", "行"])
        self.assertEqual(r.get_json()["reason"], "conflict")
        # 番号の鍵がある表は今までどおり(ID の 番号)
        f = self.post("/api/master/form", table="ID", mode="ADDNEW").get_json()
        self.assertEqual((f["fields"][0]["value"], f["fields"][0]["is_key"]), ("2", True))


class MisalignedSettingTest(_Case):
    """VBA 版では起動しなかった「_設定 と本体の数・並びが違う」でも開く。"""

    def setUp(self):
        super().setUp()
        self.use(fresh_db_path(self.tmp))
        auth.start(auth.LoginResult(True, user_id="Xfa-Ngyinazawa", level=5))

    def test_reordered_and_missing_rows(self):
        with closing(sqlite3.connect(self.path)) as c:
            c.execute(f"UPDATE [{CONV}] SET 管理番号 = 100 WHERE 項目名 = '作業名'")     # 並びを変える
            c.execute(f"DELETE FROM [{CONV}] WHERE 項目名 = 'RA'")                     # 1 行足りない
            c.execute(f"INSERT INTO [{CONV}] (管理番号, 項目名) VALUES ('101', '昔の列')")   # 本体に無い
            c.execute(f"UPDATE [{CONV}] SET 表示 = '金額' WHERE 項目名 = 'Ⅱ'")
            c.commit()
        s = self.setup_now()
        self.assertEqual(s.master_problem, "")
        self.assertEqual([r.item for r in s.conv.rows], s.main_columns)
        self.assertEqual(s.conv.by_item("Ⅱ").ind_type, "金額")                       # 名前で合わせる
        notes = " ".join(s.notices)
        self.assertIn("RA", notes)
        self.assertIn("昔の列", notes)
        d = self.post("/api/list/data")
        self.assertEqual(d.status_code, 200, d.get_data(as_text=True))
        self.assertEqual(d.get_json()["count"], 327)
        r = self.post("/api/record/form", mode="修正", key="2")
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        names = [f["name"] for f in r.get_json()["fields"]]
        self.assertEqual(names, s.main_columns)

    def test_state_reads_existing_settings(self):
        with closing(shared_db.connect(self.path)) as conn:
            st = easy_setup.state(conn, self.setup_now(), 5)
        self.assertFalse(st["auto_main"])
        self.assertEqual(st["main"], MAIN)
        self.assertEqual(len(st["columns"]), 25)
        self.assertNotIn("ID", st["masters"])
        for c in st["columns"]:
            if c["raw"]:
                # 選択肢にある書き方はその選択肢、無ければ「そのまま」
                self.assertTrue(c["input"] == easy_setup.CUSTOM or c["input"] in {x[0] for x in easy_setup.INPUT_CHOICES})


if __name__ == "__main__":
    unittest.main()
