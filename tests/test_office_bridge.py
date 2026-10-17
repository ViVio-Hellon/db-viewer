""".xls / .doc のプレビュー(0.14.0): その PC の Excel・Word に PDF にしてもらう(all-tools 点検表と同じ方式)

本物の Excel・Word は Windows でしか動かないので、`office_pdf.vbs` と同じ決まりで話す偽物
(`tests/fake_office_worker.py`)で、Python 側の決まりを確かめる。VBScript は書き方を確かめる。
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

from dbviewer import app_config, config, office_bridge, user_settings
from dbviewer.office_bridge import procs

ROOT = Path(__file__).resolve().parent.parent
FAKE = [sys.executable, str(Path(__file__).resolve().parent / "fake_office_worker.py")]
PDF = b"%PDF-1.4\n" + bytes(range(256)) * 40 + b"\n%%EOF\n"


class _Fake(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        (self.tmp / "out.pdf").write_bytes(PDF)
        self.env = mock.patch.dict(os.environ, {"FAKE_OFFICE_PDF": str(self.tmp / "out.pdf"),
                                                "FAKE_OFFICE_JOB": str(self.tmp / "job.json"),
                                                "FAKE_OFFICE_PIDFILE": str(self.tmp / "office.pid"),
                                                "DBVIEWER_LOCAL_DIR": str(self.tmp / "local")})
        self.env.start()
        self.addCleanup(self.env.stop)
        p = mock.patch.object(office_bridge, "_command", return_value=FAKE)
        p.start()
        self.addCleanup(p.stop)
        office_bridge.forget_cache()
        self.addCleanup(office_bridge.forget_cache)
        # 「起こした Office」の代わりは python。Windows は実行ファイル名まで確かめるので、試験では python も許す
        p = mock.patch.object(procs, "OFFICE_EXES", procs.OFFICE_EXES + ("python.exe",))
        p.start()
        self.addCleanup(p.stop)

    def mode(self, name):
        os.environ["FAKE_OFFICE"] = name

    def office_pid(self):
        return int((self.tmp / "office.pid").read_text())


class ConvertTest(_Fake):
    def test_pdf_comes_back_and_the_job_is_right(self):
        self.mode("pdf")
        src = self.tmp / "点検表 A-1.xls"
        src.write_bytes(b"\xd0\xcf\x11\xe0old")
        got = office_bridge.convert(src)
        self.assertEqual((got.kind, got.app, got.data), ("pdf", "Excel", PDF))
        job = json.loads((self.tmp / "job.json").read_text(encoding="utf-8"))
        self.assertEqual(job["path"], str(src))                                # 日本語のパスも 16 進で届く
        self.assertEqual(job["kind"], "excel")
        self.assertEqual(job["password"], office_bridge.DUMMY_PASSWORD)        # 待たずに失敗させる
        self.assertEqual(job["max_pages"], str(office_bridge.MAX_PAGES))
        self.assertEqual(office_bridge.kind_of(Path("a.DOC")), "word")
        # 同じファイルは覚えておく(Excel をもう一度起こさない)
        (self.tmp / "job.json").unlink()
        self.assertIs(office_bridge.convert(src), got)
        self.assertFalse((self.tmp / "job.json").exists())
        os.utime(src, (time.time() + 5, time.time() + 5))                      # 直されたら作り直す
        office_bridge.convert(src)
        self.assertTrue((self.tmp / "job.json").exists())

    def test_failure_says_why_and_where(self):
        self.mode("fail")
        src = self.tmp / "鍵付き.doc"
        src.write_bytes(b"x")
        with self.assertRaises(office_bridge.ConvertError) as cm:
            office_bridge.convert(src)
        msg = str(cm.exception)
        self.assertEqual(cm.exception.code, "OPEN_FAILED")
        for want in ("開けませんでした", "文書を開く所", "0x800A03EC", "パスワードが違います", "現物を開く"):
            self.assertIn(want, msg)

    def test_broken_data_is_an_error(self):
        self.mode("garbage")
        src = self.tmp / "a.xls"
        src.write_bytes(b"x")
        with self.assertRaises(office_bridge.ConvertError):
            office_bridge.convert(src)

    def test_hang_ends_only_the_office_it_started(self):
        """隠れた確認の窓で止まった: 時間で cscript と、起こした Office(その PID だけ)を止める。"""
        self.mode("hang")
        src = self.tmp / "止まる.doc"
        src.write_bytes(b"x")
        t0 = time.monotonic()
        with self.assertRaises(office_bridge.ConvertError) as cm:
            office_bridge.convert(src, timeout=3)
        self.assertLess(time.monotonic() - t0, 20)
        self.assertEqual(cm.exception.code, "TIMEOUT")
        self.assertIn("文書を開く所", str(cm.exception))
        pid = self.office_pid()
        deadline = time.monotonic() + 10
        while procs.identity(pid) is not None and time.monotonic() < deadline:
            time.sleep(0.1)
        self.assertIsNone(procs.identity(pid))                                 # 起こした Office は止めた
        self.assertEqual(procs._load(), [])                                    # 記録も片付いた

    def test_users_word_is_never_ended(self):
        self.mode("shared")
        src = self.tmp / "b.doc"
        src.write_bytes(b"x")
        with self.assertRaises(office_bridge.ConvertError):
            office_bridge.convert(src, timeout=3)
        pid = self.office_pid()
        self.addCleanup(lambda: procs._terminate(pid))
        self.assertIsNotNone(procs.identity(pid))                              # 利用者の Word は残す
        self.assertEqual(procs._load(), [])

    def test_leftovers_are_cleaned_on_start_and_stop(self):
        sleeper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        self.addCleanup(lambda: (sleeper.poll() is None and sleeper.kill(), sleeper.wait()))
        ident = procs.identity(sleeper.pid)
        procs.track(ident, "Excel")
        procs.track(procs.Identity(999999, 1, "excel.exe"), "Excel")           # もう無いもの
        self.assertEqual(len(procs._load()), 2)
        self.assertEqual(procs.cleanup_leftovers(), 1)
        sleeper.wait(timeout=10)
        self.assertEqual(procs._load(), [])
        # 作成時刻が違う(PID の使い回し)なら触らない
        other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        self.addCleanup(lambda: (other.poll() is None and other.kill(), other.wait()))
        procs.track(procs.Identity(other.pid, 12345, "excel.exe"), "Excel")
        self.assertEqual(procs.cleanup_leftovers(), 0)
        self.assertIsNone(other.poll())

    def test_other_programs_are_never_ended(self):
        """Excel・Word でない実行ファイルは、PID と作成時刻が合っても止めない(Windows の決まり)。"""
        sleeper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        self.addCleanup(lambda: (sleeper.poll() is None and sleeper.kill(), sleeper.wait()))
        ident = procs.identity(sleeper.pid)
        with mock.patch.object(procs, "OFFICE_EXES", ("excel.exe", "winword.exe")), \
                mock.patch.object(procs, "IS_WINDOWS", True), \
                mock.patch.object(procs, "identity", return_value=procs.Identity(ident.pid, ident.create_time,
                                                                                r"C:\Python\python.exe")):
            self.assertTrue(procs.terminate_verified(ident, wait_sec=0.2))
        self.assertIsNone(sleeper.poll())


class VbsTest(unittest.TestCase):
    """VBScript の書き方(点検表で現場の PC を通った決まり)。"""

    @classmethod
    def setUpClass(cls):
        raw = office_bridge.WORKER_VBS.read_bytes()
        cls.raw = raw
        cls.text = raw.decode("ascii")

    def body(self, head):
        start = self.text.index(head)
        return self.text[start:self.text.index("\nEnd ", start)]

    def test_ascii_crlf(self):
        self.assertTrue(all(b < 128 for b in self.raw))                        # cscript は ANSI で読む
        self.assertNotIn(b"\n", self.raw.replace(b"\r\n", b""))
        self.assertNotRegex(self.text, r"(?m)^\s*(Sub|Function) Log\(")         # Log は VBScript の関数名

    def test_read_only_hidden_no_macros(self):
        excel = self.body("Function StartExcel(")
        for want in ("gXl.Visible = False", "gXl.IgnoreRemoteRequests = True", "gXl.DisplayAlerts = False",
                     "gXl.AutomationSecurity = msoAutomationSecurityForceDisable", "gXl.EnableEvents = False"):
            self.assertIn(want, excel)
        self.assertLess(excel.index('Emit "EXCEL '), excel.index("gXl.Visible"))   # PID が先
        self.assertIn("gXl.Workbooks.Open(path, 0, True, , pwd,", self.body("Function OpenBook("))
        self.assertIn("gWd.Documents.Open(path, False, True, False, pwd,", self.body("Function OpenDoc("))
        quit_ = self.body("Sub QuitExcel(")
        self.assertLess(quit_.index("IgnoreRemoteRequests = False"), quit_.index("gXl.Quit"))

    def test_users_word_left_alone(self):
        word = self.body("Function StartWord(")
        self.assertLess(word.index('Emit "WORDPID '), word.index("gWd.Visible = False"))
        self.assertIn("If Not gWdShared Then gWd.Visible = False", word)
        finish = self.body("Sub FinishWord(")
        self.assertLess(finish.index("If gWdShared Then"), finish.index("gWd.Quit"))

    def test_export_arguments(self):
        """ExportAsFixedFormat の引数の位置(点検表と同じ)。Stage を先に知らせる。"""
        calls = re.findall(r"doc\.ExportAsFixedFormat (.*)\r?$", self.text, re.M)
        self.assertEqual(len(calls), 2)
        for c in calls:
            args = [a.strip() for a in c.split(",")]
            self.assertEqual(args[:4], ["outPath", "wdExportFormatPDF", "False", "wdExportOptimizeForOnScreen"])
            self.assertEqual(len(args), 14)
        xl = re.findall(r"wb\.ExportAsFixedFormat (.*)\r?$", self.text, re.M)
        self.assertTrue(xl)
        for c in xl:
            args = [a.strip() for a in c.split(",")]
            self.assertEqual(args[:2], ["xlTypePDF", "outPath"])
            self.assertEqual(args[-1], "False")                                # PDF の読み手を起こさない
        self.assertLess(self.text.index('Stage "word.pdf"'), self.text.index("doc.ExportAsFixedFormat"))
        self.assertLess(self.text.index('Stage "excel.pdf"'), self.text.index("wb.ExportAsFixedFormat"))

    def test_temp_is_its_own_and_deleted(self):
        self.assertIn('"dbv_preview"', self.text)
        for fn in ("Function RunExcel(", "Function RunWord("):
            body = self.body(fn)
            self.assertGreaterEqual(body.count("DeleteTempFile outPath"), 3, fn)


class PreviewRouteTest(_Fake):
    """一覧のプレビューから: .xls / .doc は待つ画面 → PDF。できなければ理由と「現物を開く」。"""

    def setUp(self):
        super().setUp()
        from tests.test_easy_setup import plain_db, _Case
        case = _Case("setUp")
        case.setUp()
        self.addCleanup(case.doCleanups)
        self.case = case
        case.use(plain_db(self.tmp / "部品台帳.sqlite3"))
        with closing(sqlite3.connect(case.path)) as c:
            c.execute("ALTER TABLE 部品 ADD COLUMN 資料 TEXT")
            c.executemany("UPDATE 部品 SET 資料 = ? WHERE 品番 = ?", [("手順/A-1.xls", "A-1"), ("手順/A-2.doc", "A-2")])
            c.commit()
        ref = self.tmp / "参照"
        (ref / "手順").mkdir(parents=True)
        (ref / "手順" / "A-1.xls").write_bytes(b"\xd0\xcf\x11\xe0xls")
        (ref / "手順" / "A-2.doc").write_bytes(b"\xd0\xcf\x11\xe0doc")
        user_settings.save(config.KEY_REF_DIR, str(ref))
        self.addCleanup(user_settings.save, config.KEY_REF_DIR, "")
        case.login()
        st = case.ok("/api/easy/state", table="部品")
        rows = [dict(c, **({"click": "preview"} if c["name"] == "資料" else {})) for c in st["columns"]]
        case.ok("/api/easy/columns", table="部品", rows=rows)
        p = mock.patch.object(office_bridge, "available", return_value=True)
        p.start()
        self.addCleanup(p.stop)

    def get(self, value, go=False):
        q = {"table": "部品", "column": "資料", "value": value}
        if go:
            q["go"] = "1"
        return self.case.c.get("/api/file/preview", query_string=q, headers=self.case.h)

    def test_wait_then_pdf(self):
        self.mode("pdf")
        r = self.get("手順/A-1.xls")
        self.assertEqual(r.status_code, 200)
        page = r.get_data(as_text=True)
        self.assertIn("Excel で PDF にしています", page)
        self.assertIn("searchParams.set('go','1')", page)
        r = self.get("手順/A-1.xls", go=True)
        self.assertEqual((r.status_code, r.content_type), (200, "application/pdf"))
        self.assertEqual(r.data, PDF)
        self.assertIn("inline", r.headers["Content-Disposition"])
        r = self.get("手順/A-2.doc")
        self.assertIn("Word で PDF にしています", r.get_data(as_text=True))

    def test_xlsx_fallback_is_shown_as_tables(self):
        sys.path.insert(0, str(ROOT / "vendor"))
        import xlsx_writer
        xlsx_writer.write_xlsx([{"name": "検査", "columns": ["品番", "判定"], "rows": [["A-1", "合格"]]}],
                               str(self.tmp / "conv.xlsx"))
        os.environ["FAKE_OFFICE_XLSX"] = str(self.tmp / "conv.xlsx")
        self.mode("xlsx")
        html = self.get("手順/A-1.xls", go=True).get_data(as_text=True)
        for want in ("PDF にできなかった", "検査", "合格", "A-1.xls"):
            self.assertIn(want, html)

    def test_failure_page(self):
        self.mode("fail")
        r = self.get("手順/A-2.doc", go=True)
        self.assertEqual(r.status_code, 422)
        text = r.get_data(as_text=True)
        self.assertIn("開けませんでした", text)
        self.assertIn("現物を開く", text)

    def test_without_office_says_so(self):
        with mock.patch.object(office_bridge, "available", return_value=False):
            r = self.get("手順/A-1.xls", go=True)
        self.assertEqual(r.status_code, 415)
        self.assertIn("現物を開く", r.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
