"""画面と API の通し試験(権限 → 一覧 → 編集 → 登録)"""
from __future__ import annotations

import os
from contextlib import closing
import tempfile
import unittest
from pathlib import Path

from dbviewer import auth, config, shared_db, user_settings
from tests._fixture_db import fresh_db_path
from tests._web import TOKEN, make_client


class WebFlowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = path = fresh_db_path(Path(self.tmp.name))
        user_settings.save(config.KEY_DB_PATH, str(path))
        auth.end()
        self.addCleanup(auth.end)
        self.env = {k: os.environ.get(k) for k in ("USERNAME", "COMPUTERNAME")}
        os.environ["USERNAME"] = "Xfa-Ngyinazawa"
        os.environ["COMPUTERNAME"] = "NLM-NGY-240638"
        self.addCleanup(self._restore_env)
        self.c = make_client()
        self.h = {"X-Tool-Token": TOKEN}

    def _restore_env(self):
        for k, v in self.env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def login(self, uid="Xfa-Ngyinazawa", pw=""):
        return self.c.post("/api/login", json={"id": uid, "password": pw}, headers=self.h)

    def test_gate(self):
        """0.16.0: パスワードは無い。画面を開けば この PC の ID・PC 名で自動で入る。"""
        self.assertEqual(self.c.get("/list").status_code, 200)
        s = self.c.get("/api/session", headers=self.h).get_json()
        self.assertEqual((s["user_id"], s["label"], s["edit"]), ("Xfa-Ngyinazawa", "編集可", True))   # マスタ未設定
        self.assertEqual(s["rights"]["source"], "unset")
        self.assertEqual(self.c.get("/login").status_code, 200)
        r = self.login("nobody", "なんでも")                                  # ID・パスワードは使わない
        self.assertEqual((r.status_code, r.get_json()["user_id"]), (200, "Xfa-Ngyinazawa"))
        self.assertEqual(self.c.post("/api/logout", json={}, headers=self.h).status_code, 200)
        self.assertEqual(self.c.post("/api/list/data", json={}, headers=self.h).status_code, 200)  # 読み直して入る
        # 0.19.0: 起動モードは無い。DB を決めていなくても画面は開き、一覧は「DB を決めて」と断る
        user_settings.save(config.KEY_DB_PATH, "")
        self.addCleanup(user_settings.save, config.KEY_DB_PATH, str(self.path))
        auth.end()
        self.assertEqual(self.c.get("/list").status_code, 200)
        r = self.c.post("/api/list/data", json={}, headers=self.h)
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.get_json()["error"]["message"], shared_db.MSG_NOT_SET)
        self.assertIn("(DB を決めていません)", self.c.get("/settings").get_data(as_text=True))

    def test_list_and_edit_cycle(self):
        self.login()
        d = self.c.post("/api/list/data", json={}, headers=self.h).get_json()
        self.assertEqual(d["count"], 327)
        rank = [c["name"] for c in d["columns"]].index("危険度分類ランク")
        self.assertEqual(sum(1 for r in d["rows"] if r[rank] == "B"), 100)   # 画面で絞ると 100 件(VBA と同じ)
        # ▲▼ は一覧に並んでいる順(画面で並べ替えたあと)を辿る
        f = self.c.post("/api/record/form", json={"mode": "修正", "key": "2", "list_keys": ["9", "2", "5"]},
                        headers=self.h).get_json()
        self.assertEqual(f["keys"], ["9", "2", "5"])
        originals = {x["name"]: x["original"] for x in f["fields"]}
        body = {"mode": "修正", "key": "2", "values": {"作業名": "改"}, "originals": originals}
        r = self.c.post("/api/record/execute", json=body, headers=self.h).get_json()
        self.assertEqual(r["reason"], "question")
        r = self.c.post("/api/record/execute", json={**body, "confirmed": True, "answer": True}, headers=self.h)
        self.assertEqual((r.status_code, r.get_json()["message"]), (200, "データを更新しました！"))
        # 別の端末が先に直していた → 409
        r = self.c.post("/api/record/execute", json={**body, "values": {"作業名": "改2"}, "confirmed": True, "answer": True}, headers=self.h)
        self.assertEqual(r.status_code, 409)
        # 新規 → 削除
        f = self.c.post("/api/record/form", json={"mode": "新規"}, headers=self.h).get_json()
        r = self.c.post("/api/record/execute", json={"mode": "新規", "key": f["key"], "values": {"作業名": "x"}, "confirmed": True, "answer": True}, headers=self.h)
        self.assertEqual(r.get_json()["executed"], "ADDNEW")
        r = self.c.post("/api/record/execute", json={"mode": "削除", "key": f["key"], "values": {}, "confirmed": True, "answer": True}, headers=self.h)
        self.assertEqual((r.get_json()["executed"], r.get_json()["reset_filters"]), ("DELETE", True))

    def test_level_refusal(self):
        """閲覧のみ(設定ファイルのアクセス権限に、この ID・PC で 編集可 の行が無い)→ 新規は断る・ダブルクリックは参照。
        行を足すと 編集可。"""
        from dbviewer import settings_file
        sf = Path(self.tmp.name) / "設定.sqlite3"
        user_settings.save(config.KEY_SETTINGS_FILE, str(sf))
        self.addCleanup(user_settings.save, config.KEY_SETTINGS_FILE, "")

        def add(login, pc, perm):
            with closing(settings_file.connect()) as c:
                c.execute("INSERT INTO アクセス権限 (ログインID, PC名, 権限, 有効) VALUES (?, ?, ?, 1)", (login, pc, perm))
                c.commit()
        add("Xfa-Ngyinazawa", "NLM-NGY-240638", "閲覧のみ")
        r = self.login()
        self.assertEqual((r.status_code, r.get_json()["level"], r.get_json()["label"]), (200, 1, "閲覧のみ"))
        r = self.c.post("/api/record/form", json={"mode": "新規"}, headers=self.h)
        self.assertEqual((r.status_code, r.get_json()["error"]["message"]), (403, auth.MSG_NO_LEVEL))
        r = self.c.post("/api/record/form", json={"mode": "修正", "key": "2", "click": "DblClick"}, headers=self.h)
        self.assertEqual(r.get_json()["mode"], "参照")
        self.assertIn("閲覧のみ", self.c.get("/list").get_data(as_text=True))    # 上の帯
        add("Xfa-Ngyinazawa", "", "編集可")
        r = self.login()
        self.assertEqual((r.get_json()["level"], r.get_json()["label"]), (5, "編集可"))
        r = self.c.post("/api/record/form", json={"mode": "新規"}, headers=self.h)
        self.assertEqual(r.status_code, 200)


if __name__ == "__main__":
    unittest.main()


class OperationTest(unittest.TestCase):
    """運用機能: 更新あり通知(db_mtime)、接続先は DB ファイル 1 つ(0.19.0)"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = fresh_db_path(Path(self.tmp.name))
        user_settings.save(config.KEY_DB_PATH, str(self.path))
        auth.end()
        self.addCleanup(auth.end)
        self.env = {k: os.environ.get(k) for k in ("USERNAME", "COMPUTERNAME")}
        os.environ["USERNAME"] = "Xfa-Ngyinazawa"
        os.environ["COMPUTERNAME"] = "NLM-NGY-240638"
        self.addCleanup(self._restore_env)
        self.c = make_client()
        self.h = {"X-Tool-Token": TOKEN}

    def _restore_env(self):
        for k, v in self.env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_health_and_list_carry_db_mtime(self):
        h = self.c.get("/api/health").get_json()
        self.assertAlmostEqual(h["db_mtime"], self.path.stat().st_mtime, places=3)
        self.c.post("/api/login", json={"id": "Xfa-Ngyinazawa", "password": ""}, headers=self.h)
        d = self.c.post("/api/list/data", json={}, headers=self.h).get_json()
        self.assertEqual(d["db_mtime"], h["db_mtime"])
        # 他の端末が書いた → 心拍の db_mtime が進む(画面はこれを見て「更新されました」を出す)
        os.utime(self.path, (self.path.stat().st_atime, self.path.stat().st_mtime + 10))
        self.assertGreater(self.c.get("/api/health").get_json()["db_mtime"], d["db_mtime"])

    def test_db_path_is_one_file(self):
        """0.19.0: 接続先は DB ファイル 1 つ(フルパス)。システム・オンライン/オフライン・フォルダ 2 つは無い。"""
        page = self.c.get("/login").get_data(as_text=True)
        self.assertNotIn("modeCard", page)
        self.assertNotIn("起動モード", page)
        settings_page = self.c.get("/settings?tab=source").get_data(as_text=True)
        for gone in ('id="progNo"', 'id="online"', 'id="serverDir"', 'id="localDir"', 'id="dbFile"'):
            self.assertNotIn(gone, settings_page)
        self.assertIn('id="dbPathText"', settings_page)
        self.assertEqual(self.c.post("/api/settings/reset-mode", json={}, headers=self.h).status_code, 404)

        def save(text):
            return self.c.post("/api/settings/save", json={"db_path": text}, headers=self.h)
        for bad, why in ((str(self.path.parent), "フォルダではなく"),
                         (str(self.path.with_suffix(".accdb")), "Access 取り込み"),
                         (str(self.path.with_name("無い.sqlite3")), "存在しない"),
                         (str(self.path.with_suffix(".txt")), "開けるのは")):
            r = save(bad)
            self.assertEqual(r.status_code, 400, bad)
            self.assertIn(why, r.get_json()["error"]["message"])
            self.assertEqual(r.get_json()["error"]["field"], "db_path")
        self.assertEqual(user_settings.get(config.KEY_DB_PATH), str(self.path))     # 断ったら変えない
        # 別のフォルダの DB でも、パス 1 つで開く
        other = Path(self.tmp.name) / "別の場所" / "ほかの.sqlite3"
        other.parent.mkdir()
        import shutil
        shutil.copy(self.path, other)
        r = save(f'"{other}"')                                                     # エクスプローラの「パスのコピー」の引用符も外す
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["db_path"], str(other))
        self.assertEqual(shared_db.db_path(), other)
        self.assertIn("ほかの.sqlite3", self.c.get("/list").get_data(as_text=True))  # 上の帯
        self.assertEqual(self.c.post("/api/list/data", json={}, headers=self.h).status_code, 200)
        save(str(self.path))

    def use_own_settings_file(self) -> Path:
        """この試験だけの設定ファイル(権限・接続先がほかの試験に漏れないように)。"""
        sf = Path(self.tmp.name) / "設定" / "DBビューア設定.sqlite3"
        user_settings.save(config.KEY_SETTINGS_FILE, str(sf))
        self.addCleanup(user_settings.save, config.KEY_SETTINGS_FILE, "")
        return sf

    def test_db_list_in_settings_file(self):
        """0.22.0: 接続先は設定ファイルの一覧(コンボボックス)。足す・外すは 編集可 の人だけ。"""
        import shutil
        from dbviewer import settings_file
        sf = self.use_own_settings_file()
        self.assertFalse(sf.exists())
        self.addCleanup(user_settings.save, config.KEY_DB_PATH, str(self.path))
        root = Path(self.tmp.name)
        a = root / "A" / "部品.sqlite3"
        b = root / "B" / "設備.sqlite3"
        for p in (a, b):
            p.parent.mkdir()
            shutil.copy(self.path, p)
        shutil.copy(self.path, a.with_name("仕入先.sqlite3"))                    # A に もう 1 つ
        a.with_name("メモ.txt").write_text("x", encoding="utf-8")               # .sqlite3 でないものは出ない

        def choices():
            return self.c.get("/api/settings/db-choices", headers=self.h).get_json()
        d = choices()                                  # 作ったばかりの設定ファイルに、いまの DB が入る
        self.assertTrue(sf.is_file())
        self.assertEqual([(r["file"], r["current"]) for r in d["dbs"]], [(self.path.name, True)])
        for p in (a, b, a):                            # 足した順(管理番号)。同じものは 1 つ
            r = self.c.post("/api/settings/open-db", json={"path": str(p)}, headers=self.h)
            self.assertEqual(r.status_code, 200, r.get_json())
        d = choices()
        self.assertEqual([r["file"] for r in d["dbs"]], [self.path.name, "部品.sqlite3", "設備.sqlite3"])
        self.assertEqual([r["no"] for r in d["dbs"]], [1, 2, 3])
        self.assertTrue(d["dbs"][1]["current"])
        self.assertEqual(d["settings_file"], str(sf))
        self.assertEqual([r["file"] for r in d["same_folder"]], ["仕入先.sqlite3"])  # 一覧に無いものだけ
        # 画面: 帯のボタン・接続先のコンボボックス
        self.assertIn('id="dbMenu"', self.c.get("/list").get_data(as_text=True))
        self.assertIn('id="dbSelect"', self.c.get("/settings?tab=source").get_data(as_text=True))
        # 外す(ファイルは消さない)。いま開いているものは外せない
        r = self.c.post("/api/settings/db-forget", json={"path": str(b)}, headers=self.h)
        self.assertEqual([x["file"] for x in r.get_json()["dbs"]], [self.path.name, "部品.sqlite3"])
        self.assertTrue(b.is_file())
        self.assertEqual(self.c.post("/api/settings/db-forget", json={"path": str(a)}, headers=self.h).status_code, 400)
        # 閲覧のみ: 一覧から選ぶだけ(足す・外すはできない。同じフォルダの候補も出さない)
        with closing(settings_file.connect()) as c:
            c.execute("INSERT INTO アクセス権限 (ログインID, PC名, 権限, 有効) VALUES ('だれか', '', '編集可', 1)")
            c.commit()
        auth.end()
        d = choices()
        self.assertEqual((d["can_add"], d["same_folder"]), (False, []))
        r = self.c.post("/api/settings/open-db", json={"path": str(self.path)}, headers=self.h)
        self.assertEqual(r.status_code, 200)                                     # 一覧にある → 選べる
        r = self.c.post("/api/settings/open-db", json={"path": str(b)}, headers=self.h)
        self.assertEqual(r.status_code, 400)
        self.assertIn("接続先に無い DB", r.get_json()["error"]["message"])
        self.assertEqual(self.c.post("/api/settings/db-forget", json={"path": str(a)}, headers=self.h).status_code, 403)

    def test_old_settings_move_to_one_path(self):
        """0.18.0 までの 5 つの設定しか無い PC は、指していたファイルを 1 回だけ DB ファイルに移す。"""
        user_settings.delete(config.KEY_DB_PATH)
        self.addCleanup(user_settings.save, config.KEY_DB_PATH, str(self.path))
        user_settings.save(config.KEY_PROG_NO, 4)
        user_settings.save(config.KEY_ONLINE, False)
        user_settings.save(config.KEY_LOCAL_DIR, str(self.path.parent))
        user_settings.save(config.KEY_SERVER_DIR, r"\\どこか\共有")
        user_settings.save(config.KEY_DB_FILE, self.path.name)
        self.assertEqual(shared_db.db_path(), self.path)
        self.assertEqual(user_settings.get(config.KEY_DB_PATH), str(self.path))
        for k in (config.KEY_PROG_NO, config.KEY_ONLINE, config.KEY_LOCAL_DIR, config.KEY_SERVER_DIR, config.KEY_DB_FILE):
            self.assertIsNone(user_settings.get(k), k)
        # オンライン(既定)でフォルダが空なら、前と同じく作業標準管理の共有フォルダ
        user_settings.delete(config.KEY_DB_PATH)
        user_settings.save(config.KEY_ONLINE, True)
        self.assertEqual(str(shared_db.db_path()),
                         str(Path(config.profile(4).server_dir) / "作業標準管理システム.sqlite3"))
        # 何も無い PC で、設定ファイルの接続先も空なら「決めていない」(勝手な場所を見に行かない)
        user_settings.save(config.KEY_SETTINGS_FILE, str(Path(self.tmp.name) / "空の設定.sqlite3"))
        self.addCleanup(user_settings.save, config.KEY_SETTINGS_FILE, "")
        user_settings.delete(config.KEY_DB_PATH)
        self.assertFalse(shared_db.is_set())
        # 接続先があれば、いちばん上を開く(0.22.0)
        from dbviewer import settings_file
        settings_file.add_db(str(self.path))
        user_settings.delete(config.KEY_DB_PATH)
        self.assertEqual(shared_db.db_path(), self.path)

    def test_access_log_written_like_vba(self):
        from dbviewer import access_log
        user_settings.save(config.KEY_ACCESS_LOG, True)
        self.addCleanup(user_settings.save, config.KEY_ACCESS_LOG, False)
        self.c.post("/api/login", json={}, headers=self.h)                # 権限を引くたびに 1 行(0.16.0)
        self.c.post("/api/login", json={}, headers=self.h)
        log_path = self.path.parent / config.ACCESS_LOG_NAME
        lines = log_path.read_bytes().decode("cp932").split("\r\n")
        self.assertEqual([l.split("\t")[4] for l in lines if l], ["認証成功", "認証成功"])
        self.assertEqual(len(lines[0].split("\t")), 6)
        self.assertTrue(lines[0].split("\t")[5].startswith("VER"))
        self.assertEqual(access_log.write.__doc__.split("。")[0], "1 行追記する")


class ListGridTest(unittest.TestCase):
    """一覧(Web の表): 全行・列の種類・列の見せ方の保存"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        path = fresh_db_path(Path(self.tmp.name))
        user_settings.save(config.KEY_DB_PATH, str(path))
        auth.end()
        self.addCleanup(auth.end)
        auth.start(auth.LoginResult(True, user_id="t", level=5))
        self.c = make_client()
        self.h = {"X-Tool-Token": TOKEN}

    def test_data(self):
        d = self.c.post("/api/list/data", json={}, headers=self.h).get_json()
        kinds = {c["name"]: c["kind"] for c in d["columns"]}
        self.assertEqual((kinds["管理番号"], kinds["Ⅰ"], kinds["作業NO"], kinds["更新日"], kinds["作業名"]),
                         ("num", "num", "text", "date", "text"))
        self.assertEqual(d["key_index"], len(d["columns"]))           # 0.9.0: 行は末尾の rowid で決める
        self.assertEqual(len(d["rows"]), 327)
        self.assertEqual(d["view"]["order"][:3], ["管理番号", "作業名", "作業NO"])
        self.assertEqual((d["view"]["hidden"], d["view"]["widths"]), ([], {}))
        self.assertFalse(d["setup"]["report_flag"] is None)

    def test_view_saved_and_reset(self):
        body = {"order": ["作業NO", "作業名", "無い列"], "hidden": ["A", "B", "C"], "widths": {"作業名": 999, "作業NO": "80"}}
        v = self.c.post("/api/list/view", json=body, headers=self.h).get_json()["view"]
        self.assertEqual(v["order"][:3], ["作業NO", "作業名", "管理番号"])     # 残りは元の順で後ろに
        self.assertNotIn("無い列", v["order"])
        self.assertEqual(v["widths"], {"作業名": 800, "作業NO": 80})
        self.assertEqual(self.c.post("/api/list/data", json={}, headers=self.h).get_json()["view"], v)
        v = self.c.post("/api/list/view", json={"reset": True}, headers=self.h).get_json()["view"]
        self.assertEqual((v["order"][0], v["hidden"]), ("管理番号", []))

    def test_column_kind_rules(self):
        from dbviewer import grid_data as g
        self.assertEqual(g.column_kind(["1", "2.5", "", "-3"]), "num")
        self.assertEqual(g.column_kind(["007", "1"]), "text")          # 先頭 0 は番号
        self.assertEqual(g.column_kind(["0-1-1"]), "text")
        self.assertEqual(g.column_kind(["2014/04/13", "2021/1/25 10:00:00", ""]), "date")
        self.assertEqual(g.column_kind(["", ""]), "text")
        cols, rows = g.pick(["k", "a", "b"], [["1", "x", "p"], ["2", "y", "q"], ["3", "z", "r"]],
                            keys=["3", "1", "9"], key_index=0, names=["b", "k"])
        self.assertEqual((cols, rows), (["b", "k"], [["r", "3"], ["p", "1"]]))

    def test_page_has_grid(self):
        page = self.c.get("/list", headers=self.h).get_data(as_text=True)
        for marker in ('id="grid"', 'id="actPrint"', 'id="outCopy"', 'id="detail"'):
            self.assertIn(marker, page)
