"""画面と API の通し試験(ログインの関門 → 一覧 → 編集 → 登録)"""
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
        self.assertEqual(self.c.get("/list").status_code, 302)
        self.assertEqual(self.c.post("/api/list", json={}, headers=self.h).status_code, 401)
        self.assertEqual(self.c.get("/settings").status_code, 200)
        self.assertEqual(self.c.get("/login").status_code, 200)
        r = self.login("nobody")
        self.assertEqual(r.status_code, 403)
        self.assertIn("IDが登録されていません", r.get_json()["error"]["message"])
        r = self.login()
        self.assertEqual((r.status_code, r.get_json()["level"]), (200, 5))
        self.assertEqual(self.c.get("/list").status_code, 200)
        self.assertEqual(self.c.post("/api/logout", json={}, headers=self.h).status_code, 200)
        self.assertEqual(self.c.get("/list").status_code, 302)

    def test_list_and_edit_cycle(self):
        self.login()
        d = self.c.post("/api/list", json={"filters": {"3": "B"}, "event_no": 3}, headers=self.h).get_json()
        self.assertEqual(d["page"]["rows"], 100)
        self.assertEqual(d["where"], "[危険度分類ランク]='B'")
        f = self.c.post("/api/record/form", json={"mode": "修正", "key": "2"}, headers=self.h).get_json()
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
        os.environ["USERNAME"] = "Xfa-Ngykonpo"
        os.environ["COMPUTERNAME"] = "NLM-NGY-232010"
        # PASS は伏せ字 *** のまま基準データに入っている
        r = self.login("Xfa-Ngykonpo", "***")
        self.assertEqual((r.status_code, r.get_json()["level"]), (200, 3))
        r = self.c.post("/api/record/form", json={"mode": "新規"}, headers=self.h)
        self.assertEqual(r.status_code, 200)
        auth.current().level = 1
        r = self.c.post("/api/record/form", json={"mode": "新規"}, headers=self.h)
        self.assertEqual((r.status_code, r.get_json()["error"]["message"]), (403, auth.MSG_NO_LEVEL.format(lv=1)))
        r = self.c.post("/api/record/form", json={"mode": "修正", "key": "2", "click": "DblClick"}, headers=self.h)
        self.assertEqual(r.get_json()["mode"], "参照")


if __name__ == "__main__":
    unittest.main()
