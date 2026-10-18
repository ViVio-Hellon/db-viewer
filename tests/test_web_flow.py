"""画面と API の通し試験(権限 → 一覧 → 編集 → 登録)"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from dbviewer import auth, config, user_settings
from tests._fixture_db import fresh_db_path
from tests._web import TOKEN, make_client


class WebFlowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        path = fresh_db_path(Path(self.tmp.name))
        user_settings.save(config.KEY_ONLINE, False)
        user_settings.save(config.KEY_LOCAL_DIR, str(path.parent))
        user_settings.save(config.KEY_DB_FILE, path.name)
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
        # 起動モードが未設定なら、先にそれ
        user_settings.save(config.KEY_ONLINE, None)
        auth.end()
        self.assertEqual(self.c.get("/list").status_code, 302)
        self.assertEqual(self.c.post("/api/list/data", json={}, headers=self.h).status_code, 401)

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
        """閲覧のみ(アクセス権限に dbviewer:edit の行が無い)→ 新規は断る・ダブルクリックは参照。行を足すと 編集可。"""
        from tests.test_access_rights import access_db
        master = access_db(Path(self.tmp.name) / "梱包資材マスタ.sqlite3",
                           [("Xfa-Ngyinazawa", "NLM-NGY-240638", "mode:field", 1)])
        user_settings.save(config.KEY_ACCESS_DB, str(master))
        self.addCleanup(user_settings.save, config.KEY_ACCESS_DB, "")
        r = self.login()
        self.assertEqual((r.status_code, r.get_json()["level"], r.get_json()["label"]), (200, 1, "閲覧のみ"))
        r = self.c.post("/api/record/form", json={"mode": "新規"}, headers=self.h)
        self.assertEqual((r.status_code, r.get_json()["error"]["message"]), (403, auth.MSG_NO_LEVEL))
        r = self.c.post("/api/record/form", json={"mode": "修正", "key": "2", "click": "DblClick"}, headers=self.h)
        self.assertEqual(r.get_json()["mode"], "参照")
        self.assertIn("閲覧のみ", self.c.get("/list").get_data(as_text=True))    # 上の帯
        access_db(master, [("Xfa-Ngyinazawa", "", "dbviewer:edit", 1)])
        r = self.login()
        self.assertEqual((r.get_json()["level"], r.get_json()["label"]), (5, "編集可"))
        r = self.c.post("/api/record/form", json={"mode": "新規"}, headers=self.h)
        self.assertEqual(r.status_code, 200)


if __name__ == "__main__":
    unittest.main()


class OperationTest(unittest.TestCase):
    """運用機能: 更新あり通知(db_mtime)、起動モードの初期化と選び直し(VBA regUF / LOCKBT)"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = fresh_db_path(Path(self.tmp.name))
        user_settings.save(config.KEY_ONLINE, False)
        user_settings.save(config.KEY_LOCAL_DIR, str(self.path.parent))
        user_settings.save(config.KEY_DB_FILE, self.path.name)
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

    def test_boot_mode_reset_and_choose(self):
        # 設定済み → ログイン欄が出て、モード選択は隠れている
        page = self.c.get("/login").get_data(as_text=True)
        self.assertIn('id="modeCard" hidden', page)
        self.assertIn('id="loginCard" >', page)
        # VBA LOCKBT ダブルクリック: 起動モード設定情報を初期化
        r = self.c.post("/api/settings/reset-mode", json={}, headers=self.h).get_json()
        self.assertEqual((r["message"], r["mode_set"]), ("起動モード設定情報を初期化しました!", False))
        self.assertIsNone(user_settings.get(config.KEY_ONLINE))
        page = self.c.get("/login").get_data(as_text=True)
        self.assertIn('id="modeCard" >', page)
        self.assertIn('id="loginCard" hidden', page)
        # regUF: オフラインを選んで「設定」
        r = self.c.post("/api/settings/save", json={"online": False}, headers=self.h)
        self.assertEqual(r.status_code, 200)
        self.assertIs(user_settings.get(config.KEY_ONLINE), False)
        self.assertIn('id="modeCard" hidden', self.c.get("/login").get_data(as_text=True))

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
        user_settings.save(config.KEY_ONLINE, False)
        user_settings.save(config.KEY_LOCAL_DIR, str(path.parent))
        user_settings.save(config.KEY_DB_FILE, path.name)
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
