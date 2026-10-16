"""ログ(0.11.0): なぜなぜシート・エラーだけ / PC で絞る・CSV・見出しの数・出力先に戻る・警告を拾う・直前の操作

参照: ViVio-Hellon/vba-daily-report-python-migration の「エラーを後から追う」(v4.1.0)。
"""
from __future__ import annotations

import logging
import time
from datetime import date
from pathlib import Path

from dbviewer import config, logging_utils, trace, user_settings, why_sheet
from dbviewer.presenters import log_view
from tests._web import TOKEN, make_client
from tests.test_trace import _LogDirCase


class WhySheetTest(_LogDirCase):
    def setUp(self):
        super().setUp()
        why_sheet.forget_cache()
        self.c = make_client()
        self.h = {"X-Tool-Token": TOKEN, "X-Screen-Id": "S-log"}

    def errors(self):
        self.write([
            {"t": f"{date.today()}T09:00:00.000", "lv": "ERROR", "ev": "error.server", "rid": "aaaa1111",
             "pc": "PC-A", "msg": "こわれた", "path": "/api/list/row-save",
             "exc": {"type": "KeyError", "msg": "'LOT'",
                     "tb": ['  File "/x/dbviewer/simple_table.py", line 42, in update\n']}},
            {"t": f"{date.today()}T09:05:00.000", "lv": "WARN", "ev": "request", "rid": "bbbb2222", "st": 409,
             "pc": "PC-A", "msg": "競合"},
            {"t": f"{date.today()}T09:10:00.000", "lv": "ERROR", "ev": "error.client", "rid": "cccc3333",
             "pc": "PC-B", "msg": "画面のエラー"},
        ], "PC-A")

    def test_sheet_saves_and_counts_go_down(self):
        self.errors()
        self.assertEqual(why_sheet.open_errors(), 2)                         # エラー 2 件(断りは数えない)
        r = self.c.post("/api/log/sheet", headers=self.h, json={
            "rid": "aaaa1111", "status": "doing", "whys": ["LOT 列が無い", "表を替えた"], "measure": "列を戻す",
            "by": "taro"})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual((r.get_json()["status_label"], r.get_json()["open_errors"]), ("対策中", 2))
        self.assertTrue(list(self.dir.glob("なぜなぜ_*.jsonl")))              # ログの出力先に残る
        r = self.c.post("/api/log/sheet", headers=self.h, json={"rid": "aaaa1111", "status": "done",
                                                               "whys": ["LOT 列が無い"], "measure": "列を戻した"})
        self.assertEqual(r.get_json()["open_errors"], 1)                     # 対策済で数が減る
        sh = self.c.get("/api/log/sheet?rid=aaaa1111", headers=self.h).get_json()["sheet"]
        self.assertEqual((sh["status"], sh["whys"][:2], sh["measure"]), ("done", ["LOT 列が無い", ""], "列を戻した"))
        self.assertEqual(len(sh["whys"]), why_sheet.WHY_COUNT)
        # 一覧に状況が出る・前後の窓にシートが付く
        v = self.c.get("/api/log/events?only=errors", headers=self.h).get_json()
        self.assertEqual([r["rid"] for r in v["rows"]], ["cccc3333", "aaaa1111"])  # エラーだけ・新しい順
        self.assertEqual(v["rows"][1]["sheet"], "対策済")
        self.assertEqual(v["open_errors"], 1)
        ch = log_view.chain("aaaa1111")
        self.assertEqual(ch["sheet"]["status"], "done")

    def test_pc_filter_and_scopes(self):
        self.errors()
        v = self.c.get("/api/log/events?only=problems&pc=PC-B", headers=self.h).get_json()
        self.assertEqual([r["rid"] for r in v["rows"]], ["cccc3333"])
        v = self.c.get("/api/log/events?only=problems", headers=self.h).get_json()
        self.assertEqual(len(v["rows"]), 3)
        self.assertIn("PC-A", v["pcs"])

    def test_csv_export(self):
        self.errors()
        why_sheet.save("aaaa1111", {"status": "doing", "whys": ["なぜ一"], "measure": "対策"}, by="taro")
        r = self.c.post("/api/log/export", headers=self.h, json={"days": 7, "only": "errors"})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        path = Path(r.get_json()["path"])
        self.assertEqual(path.parent, self.dir / why_sheet.EXPORT_DIR)       # ログの出力先の 書き出し
        text = path.read_bytes().decode("cp932")                             # Excel がそのまま開ける
        lines = text.split("\r\n")
        self.assertTrue(lines[0].startswith("時刻,記録番号,重さ"))
        self.assertIn("なぜ1", lines[0])
        row = next(x for x in lines if "aaaa1111" in x)
        self.assertIn("KeyError: 'LOT'", row)
        self.assertIn("simple_table.py:42(update)", row)
        self.assertIn("なぜ一", row)
        self.assertIn("対策中", row)
        self.assertEqual(r.get_json()["rows"], 2)

    def test_nav_badge_counts_open_errors(self):
        self.errors()
        why_sheet.forget_cache()
        page = self.c.get("/settings", headers=self.h).get_data(as_text=True)
        self.assertIn("エラー2件", page)
        self.assertEqual(self.c.get("/api/log/summary", headers=self.h).get_json()["open_errors"], 2)


class CaptureAndRetryTest(_LogDirCase):
    def test_warning_captured_and_thinned(self):
        trace.install_capture()
        log = logging.getLogger("dbviewer.試験")
        log.warning("マスタが読めません: %s", "タイプ")
        log.warning("マスタが読めません: %s", "タイプ")                     # 1 分以内の同じもの → 間引く
        try:
            raise ValueError("こわれた")
        except ValueError:
            log.exception("握りつぶしていたエラー")
        recs = [r for r in self.lines() if r["ev"].startswith("log.")]
        self.assertEqual([(r["ev"], r["lv"]) for r in recs], [("log.warning", "WARN"), ("log.error", "ERROR")])
        self.assertIn("タイプ", recs[0]["msg"])
        self.assertEqual(recs[1]["exc"]["type"], "ValueError")
        self.assertIn("test_log_feature.py", recs[0]["data"]["where"])

    def test_server_error_not_doubled(self):
        trace.install_capture()
        logging.getLogger("dbviewer.app").error("x", extra={"no_trace": True})
        self.assertFalse([r for r in self.lines() if r["ev"].startswith("log.")])

    def test_retry_configured_dir(self):
        """出力先に書けないあいだは既定の場所へ。5 分たつと設定の出力先を試し直して戻る。"""
        wanted = Path(self.tmp.name) / "共有" / "ログ"
        blocker = Path(self.tmp.name) / "共有"
        blocker.write_text("つながっていない", encoding="utf-8")            # まだ届かない
        user_settings.save(config.KEY_LOG_DIR, str(wanted))
        st = logging_utils.apply_settings()
        self.assertTrue(st["problem"])
        trace.event("試験", "届かないあいだ")
        self.assertEqual(config.LOG_DIR, self.dir)
        blocker.unlink()                                                     # つながった
        trace._retry["at"] = time.monotonic() - trace.RETRY_SECONDS - 1
        trace.event("試験", "つながったあと")
        self.assertEqual(config.LOG_DIR, wanted)
        self.assertTrue(list(wanted.glob("記録_*.jsonl")))
        # 読むときは両方(書けなかったあいだのぶんも並ぶ)
        msgs = [r.get("msg") for r in trace.read(1, limit=50)]
        self.assertIn("届かないあいだ", msgs)
        self.assertIn("つながったあと", msgs)


class ClientCrumbsTest(_LogDirCase):
    def test_client_error_keeps_crumbs(self):
        c = make_client()
        h = {"X-Tool-Token": TOKEN, "X-Screen-Id": "S-c"}
        crumbs = [f"10:00:{i:02d} 押した ボタン{i}" for i in range(15)]
        r = c.post("/api/log/client", headers=h, json={"kind": "error", "message": "x is undefined",
                                                       "page": "/list", "crumbs": crumbs})
        self.assertEqual(r.status_code, 200)
        rec = [x for x in self.lines() if x["ev"] == "error.client"][-1]
        self.assertEqual(rec["data"]["crumbs"][-1], "10:00:14 押した ボタン14")
        self.assertEqual(len(rec["data"]["crumbs"]), 15)
