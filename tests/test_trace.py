"""記録(なぜなぜ分析のため): 書く・読む・前後をたどる・置き場所を変える・画面のエラー"""
from __future__ import annotations

import json
import re
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

from dbviewer import auth, config, logging_utils, trace, user_settings
from dbviewer.presenters import log_view
from dbviewer.presenters import settings as settings_presenter
from tests._web import TOKEN, make_client


class _LogDirCase(unittest.TestCase):
    """この試験のあいだだけ、記録を空の一時フォルダに書く。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name) / "記録"
        before, default = config.LOG_DIR, config.DEFAULT_LOG_DIR
        self.addCleanup(setattr, config, "DEFAULT_LOG_DIR", default)
        self.addCleanup(logging_utils.set_log_dir, before)
        self.addCleanup(user_settings.delete, config.KEY_LOG_DIR)
        self.addCleanup(user_settings.delete, config.KEY_LOG_KEEP_DAYS)
        config.DEFAULT_LOG_DIR = self.dir
        self.assertEqual(logging_utils.set_log_dir(self.dir), "")

    def lines(self) -> list[dict]:
        out = []
        for p in sorted(self.dir.glob("記録_*.jsonl")):
            out += [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
        return out

    def write(self, recs: list[dict], pc: str, day: date | None = None) -> None:
        p = trace.file_for(day or date.today(), pc)
        with open(p, "a", encoding="utf-8") as f:
            for r in recs:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")


class TraceTest(_LogDirCase):
    def test_event_line(self):
        rec = trace.event("cell.save", "保存しました", data={"before": "A", "after": "B"}, st=200)
        self.assertEqual(rec["ev"], "cell.save")
        got = self.lines()
        self.assertEqual(len(got), 1)
        self.assertEqual((got[0]["ev"], got[0]["lv"], got[0]["msg"], got[0]["data"]),
                         ("cell.save", "INFO", "保存しました", {"before": "A", "after": "B"}))
        self.assertEqual(got[0]["pc"], trace.pc_name())
        self.assertIn(trace.pc_name(), trace.file_for().name)

    def test_token_not_in_path(self):
        self.assertEqual(trace.clean_path("/list?t=abc"), "/list")
        self.assertEqual(trace.clean_path("/report/preview?x=1&t=abc&y=記録"), "/report/preview?x=1&y=記録")
        self.assertEqual(trace.clean_path("/list"), "/list")
        self.assertEqual(trace.summarize("at http://127.0.0.1:8743/list?t=abc123:1:36"),
                         "at http://127.0.0.1:8743/list?t=***:1:36")

    def test_secrets_and_size(self):
        s = trace.summarize({"PASS": "abc", "token": "t", "id": "u", "long": "x" * 1000,
                             "many": list(range(50))})
        self.assertEqual((s["PASS"], s["token"], s["id"]), ("***", "***", "u"))
        self.assertLess(len(s["long"]), 500)
        self.assertEqual(s["many"]["件数"], 50)

    def test_never_raises(self):
        blocker = Path(self.tmp.name) / "ふさがり"
        blocker.write_text("x", encoding="utf-8")
        config.LOG_DIR = blocker / "下"                       # フォルダを作れない
        rec = trace.event("request", "書けなくても止めない")
        self.assertEqual(rec["ev"], "request")

    def test_exception_kept(self):
        try:
            raise ValueError("こわれた")
        except ValueError as exc:
            trace.event("error.server", "x", level="ERROR", exc=exc)
        exc = self.lines()[0]["exc"]
        self.assertEqual((exc["type"], exc["msg"]), ("ValueError", "こわれた"))
        self.assertTrue(any("test_trace.py" in t for t in exc["tb"]))

    def test_read_filters_newest_first(self):
        t = datetime.now()
        iso = lambda m: (t + timedelta(minutes=m)).isoformat(timespec="milliseconds")
        self.write([{"t": iso(0), "lv": "INFO", "ev": "screen", "rid": "a1"},
                    {"t": iso(1), "lv": "WARN", "ev": "request", "rid": "a2", "msg": "Level が足りません"},
                    {"t": iso(2), "lv": "ERROR", "ev": "error.server", "rid": "a3", "user": "taro"}], "PC1")
        self.write([{"t": iso(3), "lv": "ERROR", "ev": "error.client", "rid": "b1"}], "PC2")
        self.assertEqual([r["rid"] for r in trace.read(1)], ["b1", "a3", "a2", "a1"])
        self.assertEqual([r["rid"] for r in trace.read(1, only_problems=True)], ["b1", "a3", "a2"])
        self.assertEqual([r["rid"] for r in trace.read(1, q="taro")], ["a3"])
        self.assertEqual([r["rid"] for r in trace.read(1, q="level")], ["a2"])

    def test_chain_same_screen_and_pc(self):
        t = datetime.now()
        iso = lambda m: (t + timedelta(minutes=m)).isoformat(timespec="milliseconds")
        self.write([{"t": iso(-30), "ev": "screen", "rid": "old", "sid": "S1", "pc": "PC1"},     # 遠すぎる
                    {"t": iso(-3), "ev": "screen", "rid": "r0", "sid": "S1", "pc": "PC1"},
                    {"t": iso(-2), "ev": "screen", "rid": "x0", "sid": "S2", "pc": "PC1"},       # 別の画面
                    {"t": iso(-1), "ev": "job.done", "rid": "", "pc": "PC1"},                     # 画面の無い出来事
                    {"t": iso(0), "lv": "ERROR", "ev": "error.server", "rid": "r1", "sid": "S1", "pc": "PC1"},
                    {"t": iso(1), "ev": "request", "rid": "r2", "sid": "S1", "pc": "PC1"}], "PC1")
        self.write([{"t": iso(0), "ev": "request", "rid": "y0", "sid": "S1", "pc": "PC2"}], "PC2")  # 別の PC
        got = trace.chain("r1")
        self.assertTrue(got["found"])
        self.assertEqual([r["rid"] for r in got["events"]], ["r0", "", "r1", "r2"])
        self.assertFalse(trace.chain("nope")["found"])

    def test_cleanup_only_own_old(self):
        me = trace.pc_name()
        old = date.today() - timedelta(days=40)
        names = [f"記録_{me}_{old:%Y%m%d}.jsonl", f"記録_{me}_{date.today():%Y%m%d}.jsonl",
                 f"記録_ほかのPC_{old:%Y%m%d}.jsonl", f"dbviewer_{me}_{old:%Y%m%d}.log",
                 f"dbviewer_{old:%Y%m%d}.log", f"取り込み診断_{me}_{old:%Y%m%d}.log", "メモ.txt"]
        for n in names:
            (self.dir / n).write_text("x", encoding="utf-8")
        self.assertEqual(trace.cleanup(30), 4)
        left = sorted(p.name for p in self.dir.iterdir())
        self.assertEqual(left, sorted([names[1], names[2], "メモ.txt"]))
        self.assertEqual(trace.cleanup(0), 0)                 # 0 = 消さない


class LogViewTest(_LogDirCase):
    def test_screen_names(self):
        self.assertEqual(log_view.screen_of("/api/list/cell"), "一覧(その場で直す)")
        self.assertEqual(log_view.screen_of("/api/list/data?x=1"), "一覧")
        self.assertEqual(log_view.screen_of("/master"), "マスタ管理")      # 0.18.0 より前の記録も読める
        self.assertEqual(log_view.screen_of("/settings"), "設定")

    def test_row_labels(self):
        r = log_view.row({"t": "2026-10-01T09:00:00.000", "lv": "WARN", "ev": "request", "st": 409,
                          "path": "/api/list/cell", "data": {"reason": "conflict"}})
        self.assertEqual((r["label"], r["screen"], r["msg"], r["time"]),
                         ("断られた", "一覧(その場で直す)", "理由: conflict", "10/01 09:00:00"))

    def test_draft_has_facts_and_blank_whys(self):
        t = datetime.now()
        iso = lambda m: (t + timedelta(minutes=m)).isoformat(timespec="milliseconds")
        self.write([{"t": iso(-1), "ev": "screen", "rid": "r0", "sid": "S", "pc": "P", "path": "/list"},
                    {"t": iso(0), "lv": "ERROR", "ev": "error.server", "rid": "r1", "sid": "S", "pc": "P",
                     "path": "/api/list/cell", "method": "POST", "user": "taro", "lvl": 3, "st": 500,
                     "msg": "こわれた", "exc": {"type": "KeyError", "msg": "'x'",
                                              "tb": ['  File "dbviewer/cell_edit.py", line 9, in save\n']}},
                    {"t": iso(0), "lv": "WARN", "ev": "request", "rid": "r1", "sid": "S", "pc": "P", "st": 500,
                     "ms": 12, "path": "/api/list/cell"}], "P")
        view = log_view.chain("r1")
        self.assertTrue(view["found"])
        self.assertEqual([r["center"] for r in view["rows"]], [False, True, True])
        d = view["draft"]
        self.assertIn("状態   : 500(かかった時間 12 ms)", d)
        self.assertIn("【その後】", d)
        for word in ("記録番号 r1", "taro", "一覧(その場で直す)", "KeyError", "cell_edit.py", "画面を開いた",
                     "なぜ1:", "なぜ5:", "真因", "対策"):
            self.assertIn(word, d)


class WebTraceTest(_LogDirCase):
    def setUp(self):
        super().setUp()
        auth.end()
        self.addCleanup(auth.end)
        self.c = make_client()
        self.h = {"X-Tool-Token": TOKEN, "X-Screen-Id": "S-test"}

    def test_request_id_header(self):
        r = self.c.get("/api/settings/state", headers=self.h)
        self.assertRegex(r.headers.get("X-Request-Id", ""), r"^[0-9a-f]{8}$")

    def test_start_recorded(self):
        self.assertIn("app.start", [x["ev"] for x in self.lines()])

    def test_unexpected_error_gives_record_no(self):
        app = self.c.application

        def boom():
            raise RuntimeError("思わぬ")
        app.add_url_rule("/api/log/boom", "boom", boom, methods=["POST"])   # ログイン無しで通る経路
        r = self.c.post("/api/log/boom", json={"PASS": "secret", "a": 1}, headers=self.h)
        self.assertEqual(r.status_code, 500)
        body = r.get_json()
        rid = body["rid"]
        self.assertEqual(rid, r.headers["X-Request-Id"])
        self.assertIn(rid, body["error"]["message"])
        errs = [x for x in self.lines() if x["ev"] == "error.server"]
        self.assertEqual(len(errs), 1)
        e = errs[0]
        self.assertEqual((e["rid"], e["sid"], e["lv"], e["exc"]["type"]), (rid, "S-test", "ERROR", "RuntimeError"))
        self.assertEqual(e["data"]["sent"], {"PASS": "***", "a": 1})
        # 二重に残さない(request の行は出ない)
        self.assertEqual([x["ev"] for x in self.lines() if x.get("rid") == rid], ["error.server"])

    def test_refusal_recorded_with_reason(self):
        r = self.c.post("/api/settings/save", json={"prog_no": "x"}, headers=self.h)
        self.assertEqual(r.status_code, 400)
        rid = r.headers["X-Request-Id"]
        mine = [x for x in self.lines() if x.get("rid") == rid]
        self.assertEqual([x["ev"] for x in mine], ["settings.save", "request"])
        req = mine[-1]
        self.assertEqual((req["lv"], req["st"], req["data"]["reason"]), ("WARN", 400, "bad_input"))
        self.assertIn("プログラム番号", req["msg"])

    def test_client_error_endpoint(self):
        r = self.c.post("/api/log/client", headers=self.h,
                        json={"kind": "error", "message": "x is undefined", "page": "/list?t=himitsu&tab=a",
                              "stack": "at list.js:10", "line": 10, "evil": "捨てる"})
        self.assertEqual(r.status_code, 200)
        rid = r.get_json()["rid"]
        mine = [x for x in self.lines() if x.get("rid") == rid]
        self.assertEqual([x["ev"] for x in mine], ["error.client"])
        self.assertEqual((mine[0]["path"], mine[0]["method"], mine[0]["lv"], mine[0]["msg"]),
                         ("/list?tab=a", "", "ERROR", "x is undefined"))
        self.assertNotIn("himitsu", json.dumps(mine[0], ensure_ascii=False))     # 起動トークンは残さない
        self.assertNotIn("evil", mine[0]["data"])

    def test_log_api_needs_token_not_login(self):
        self.assertIn(self.c.get("/api/log/events").status_code, (401, 403))
        trace.event("error.client", "画面のエラー", level="ERROR", rid="zz000001")
        trace.event("screen", rid="zz000002")
        r = self.c.get("/api/log/events?days=1", headers=self.h)
        self.assertEqual(r.status_code, 200)
        rids = [x["rid"] for x in r.get_json()["rows"]]
        self.assertIn("zz000001", rids)
        self.assertNotIn("zz000002", rids)                   # 既定は問題だけ
        rids = [x["rid"] for x in self.c.get("/api/log/events?days=1&only=all", headers=self.h).get_json()["rows"]]
        self.assertIn("zz000002", rids)
        ch = self.c.get("/api/log/chain?rid=zz000001", headers=self.h).get_json()
        self.assertTrue(ch["found"])
        self.assertIn("なぜ1", ch["draft"])
        self.assertFalse(self.c.get("/api/log/chain?rid=nothing", headers=self.h).get_json()["found"])
        self.assertEqual(self.c.get("/api/log/chain", headers=self.h).status_code, 400)

    def test_settings_tab_and_badge(self):
        trace.event("error.server", "こわれた", level="ERROR")
        state = self.c.get("/api/settings/state", headers=self.h).get_json()
        self.assertEqual(state["log"]["dir_real"], str(self.dir))
        self.assertGreaterEqual(state["log"]["errors_today"], 1)
        self.assertEqual(state["tab_badges"]["log"]["level"], "warn")
        page = self.c.get("/settings", headers=self.h).get_data(as_text=True)
        self.assertIn('id="panel-log"', page)
        self.assertIn('id="logChain"', page)


class SecretsTest(_LogDirCase):
    """マスタ管理で ID 表を直しても、PASS は記録に残らない(値は列名の無い並びで来る)。"""

    def setUp(self):
        super().setUp()
        import os
        from tests._fixture_db import fresh_db_path
        path = fresh_db_path(Path(self.tmp.name))
        user_settings.save(config.KEY_ONLINE, False)
        user_settings.save(config.KEY_LOCAL_DIR, str(path.parent))
        user_settings.save(config.KEY_DB_FILE, path.name)
        auth.end()
        self.addCleanup(auth.end)
        for k, v in (("USERNAME", "Xfa-Ngyinazawa"), ("COMPUTERNAME", "NLM-NGY-240638")):
            self.addCleanup(lambda k=k, old=os.environ.get(k): os.environ.pop(k, None) if old is None
                            else os.environ.__setitem__(k, old))
            os.environ[k] = v
        self.c = make_client()
        self.h = {"X-Tool-Token": TOKEN}
        self.c.post("/api/login", json={"id": "Xfa-Ngyinazawa", "password": ""}, headers=self.h)

    def test_pass_columns_not_recorded(self):
        """PASS のような列(名前に pass)の値は、一覧で足しても記録に残さない(伏せ字)。"""
        import sqlite3
        from contextlib import closing
        from dbviewer import shared_db
        with closing(sqlite3.connect(shared_db.db_path())) as c:
            c.execute("CREATE TABLE 社員 (名前 TEXT, PASS TEXT)")
            c.commit()
        self.c.post("/api/list/data", json={"table": "社員"}, headers=self.h)    # 一覧で表を選ぶ
        r = self.c.post("/api/list/row-add", headers=self.h,
                        json={"table": "社員", "values": {"名前": "試験", "PASS": "himitsu-PW"}})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        text = "".join(p.read_text(encoding="utf-8") for p in self.dir.glob("記録_*.jsonl"))
        self.assertNotIn("himitsu-PW", text)
        self.assertIn("試験", text)                                           # ほかの列は残す



class LogDirSettingTest(_LogDirCase):
    def test_move_and_refuse(self):
        new = Path(self.tmp.name) / "共有" / "記録"
        r = settings_presenter.save({"log_dir": str(new)})
        self.assertTrue(r.ok, r.message)
        self.assertEqual(config.LOG_DIR, new)
        # 移る前の場所にも「どこへ移したか」が残る
        old_lines = [json.loads(x) for p in self.dir.glob("記録_*.jsonl")
                     for x in p.read_text(encoding="utf-8").splitlines()]
        self.assertTrue(any(x["ev"] == "log.moved" and x["data"]["to"] == str(new) for x in old_lines))
        trace.event("screen")
        self.assertTrue(trace.file_for().exists())
        self.assertEqual(trace.file_for().parent, new)
        # 開発者向けのログも新しい場所へ
        logging_utils.get_logger("試験").info("ここに書く")
        self.assertTrue(list(new.glob("dbviewer_*.log")))

        blocker = Path(self.tmp.name) / "ふさがり"
        blocker.write_text("x", encoding="utf-8")
        r = settings_presenter.save({"log_dir": str(blocker / "下")})
        self.assertFalse(r.ok)
        self.assertEqual(r.field, "log_dir")
        self.assertEqual(config.LOG_DIR, new)                  # 変えない
        self.assertEqual(user_settings.get(config.KEY_LOG_DIR), str(new))

        # 空 = 既定へ戻す
        self.assertTrue(settings_presenter.save({"log_dir": ""}).ok)
        self.assertEqual(config.LOG_DIR, self.dir)

    def test_keep_days(self):
        self.assertFalse(settings_presenter.save({"log_keep_days": -1}).ok)
        self.assertFalse(settings_presenter.save({"log_keep_days": "abc"}).ok)
        self.assertTrue(settings_presenter.save({"log_keep_days": 30}).ok)
        self.assertEqual(settings_presenter.log_state()["keep_days"], 30)

    def test_unwritable_setting_falls_back(self):
        blocker = Path(self.tmp.name) / "ふさがり"
        blocker.write_text("x", encoding="utf-8")
        user_settings.save(config.KEY_LOG_DIR, str(blocker / "下"))
        st = logging_utils.apply_settings()
        self.assertTrue(st["problem"])
        self.assertEqual(Path(st["dir"]), self.dir)
        ls = settings_presenter.log_state()
        self.assertEqual(ls["dir_state"]["level"], "warn")
        self.assertIn("既定の場所", ls["problem"])

    def test_file_names_carry_pc(self):
        self.assertRegex(logging_utils.log_path_for(date(2026, 10, 1)).name,
                         rf"^dbviewer_{re.escape(trace.pc_name())}_20261001\.log$")


if __name__ == "__main__":
    unittest.main()
