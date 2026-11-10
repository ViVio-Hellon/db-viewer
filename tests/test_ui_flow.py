"""画面を本当に動かす試験(0.46.0。Playwright があるときだけ)

文法の確かめ(test_js_syntax)では見つからない、**画面の動き**の不具合を見つける:
確かめの窓を続けて出すと 2 回目がすぐ閉じた(0.45.0)、かんたん設定で違う行が開いて見えた(0.38.0)など。

Playwright は試験のときだけ使う(ライン PC には入れない)。入っていない・ブラウザが無い環境では飛ばす。
本物の start_app.py を子として起こし、1 つのタブで順に操作する
(タブを閉じると、しばらくしてサーバが終わるため。idle_exit)。
"""
from __future__ import annotations

import glob
import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

try:
    from playwright.sync_api import sync_playwright
except ImportError:                                      # 入っていなければ飛ばす
    sync_playwright = None

CHILD = """
import sys; sys.path.insert(0, {root!r})
from dbviewer import app_config
app_config.port_candidates = lambda mode="main": [{port}]
import start_app
# 試験のあいだは「画面が居なくなったら終わる」見張りを止める(タブの移動が遅いと、閉じたと見て終わるため)
start_app._watch_for_idle = lambda srv: None
sys.exit(start_app.main(["--no-browser"]))
"""


def _launch(p):
    """ブラウザを起こす。入っている場所の違い(CI・この開発環境)を吸収する。"""
    try:
        return p.chromium.launch(args=["--no-proxy-server"])
    except Exception:                                    # noqa: BLE001 - 別の場所を試す
        for exe in sorted(glob.glob("/opt/pw-browsers/chromium-*/chrome-linux64/chrome")):
            try:
                return p.chromium.launch(executable_path=exe, args=["--no-proxy-server"])
            except Exception:                            # noqa: BLE001
                continue
    return None


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@unittest.skipIf(sync_playwright is None, "Playwright が無い")
class UiFlowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name)
        cls.db = root / "伝送用ファイル.sqlite3"
        with sqlite3.connect(cls.db) as c:
            c.execute("CREATE TABLE 作業 (管理番号 INTEGER, 作業名 TEXT, 作業NO TEXT, 廃止 INTEGER)")
            c.executemany("INSERT INTO 作業 VALUES (?,?,?,?)",
                          [(1, "ﾊﾝﾏｰの取り扱い", "0-1-1", 0), (2, "返り材切断", "0-2-6", -1), (3, "台車", "0-3-1", 0)])
            c.execute("CREATE TABLE 古い表 (名前 TEXT)")
            c.executemany("INSERT INTO 古い表 VALUES (?)", [("x",), ("y",)])
        cfg = root / "c.json"
        cfg.write_text(json.dumps({"db_path": str(cls.db), "access_log": False}), encoding="utf-8")
        # 置き場所はぜんぶこの試験だけのものにする。ほかの試験が作った配布設定(DBVIEWER_DISTRIBUTION_DIR)を
        # 引き継ぐと、起動したときにそれを読み込んで接続先が替わってしまう(全部の試験を流したときだけ落ちた)
        env = {k: v for k, v in os.environ.items() if not k.startswith(("DBVIEWER_", "FAKE_OFFICE"))}
        env.update(DBVIEWER_CONFIG_PATH=str(cfg), DBVIEWER_LOG_DIR=str(root / "logs"),
                   DBVIEWER_LOCAL_DIR=str(root / "local"), DBVIEWER_DISTRIBUTION_DIR=str(root / "配布設定"),
                   PYTHONIOENCODING="utf-8")
        cls.settings_file = root / "DBビューア設定.sqlite3"
        cls.proc = subprocess.Popen([sys.executable, "-c", CHILD.format(root=str(ROOT), port=_free_port())],
                                    env=env, cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, encoding="utf-8", errors="replace")
        cls.url = cls._wait_url()
        cls.base = cls.url.split("/?")[0].rstrip("/")
        cls._pw = sync_playwright().start()
        cls.browser = _launch(cls._pw)
        if cls.browser is None:
            cls._stop()
            raise unittest.SkipTest("ブラウザ(Chromium)が無い")
        cls.page = cls.browser.new_page(viewport={"width": 1400, "height": 900})
        cls.errors = []
        cls.page.on("pageerror", lambda e: cls.errors.append(str(e)))

    @classmethod
    def _wait_url(cls) -> str:
        found: list[str] = []
        ready = threading.Event()
        cls.output = []                                  # 落ちたときに見る(サーバの出力)

        def read():
            for line in cls.proc.stdout:                 # 残りも読む(出力で詰まらないように)
                cls.output.append(line.rstrip())
                del cls.output[:-200]
                if "起動しました:" in line and not found:
                    found.append(line.split("起動しました:")[1].strip())
                    ready.set()
            ready.set()                                  # 子が終わった
        threading.Thread(target=read, daemon=True).start()
        if ready.wait(timeout=90) and found:
            return found[0]
        cls._stop()
        raise RuntimeError("DBビューアが起動しませんでした")

    @classmethod
    def _stop(cls):
        cls.proc.kill()
        cls.proc.wait()

    @classmethod
    def tearDownClass(cls):
        try:
            cls.browser.close()
            cls._pw.stop()
        finally:
            cls._stop()
            cls._tmp.cleanup()

    def tearDown(self):
        if self.proc.poll() is not None:
            self.fail("DBビューアが途中で終わりました:\n" + "\n".join(self.output[-40:]))
        self.assertEqual(self.errors, [], "画面でエラーが出ました")

    # ------------------------------------------------------------------
    def open_list(self, table: str):
        pg = self.page
        pg.goto(self.url)
        pg.wait_for_selector("#listTable option", state="attached")
        pg.select_option("#listTable", table)
        pg.wait_for_function("t => document.querySelector('#listTable').value === t", arg=table)
        pg.wait_for_selector(".grid__table tbody tr")
        pg.wait_for_timeout(500)

    def tables(self):
        with sqlite3.connect(self.db) as c:
            return [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")]

    # ------------------------------------------------------------------
    def test_1_easy_settings_rows(self):
        """かんたん設定: 触った行が開いて見え、保存していない印が出る。チェックの入力を保存できる。"""
        pg = self.page
        pg.goto(self.base + "/settings?tab=easy")
        pg.wait_for_selector("#easyTable option", state="attached")
        pg.select_option("#easyTable", "作業")
        row = '#easyCols tr[data-name="%s"]'
        pg.wait_for_selector(row % "廃止")
        pg.select_option(row % "廃止" + ' [data-f="input"]', "check")
        pg.wait_for_timeout(200)
        unsaved = pg.eval_on_selector_all("#easyCols tr .unsaved:not([hidden])",
                                          "t => t.map(x => x.closest('tr').dataset.name)")
        self.assertEqual(unsaved, ["廃止"])
        self.assertIn("はい", pg.inner_text(row % "廃止" + " .inputhow"))
        pg.click("#easyColsSave")
        pg.wait_for_function("() => !document.querySelector('#easyCols tr .unsaved:not([hidden])')")

    def test_2_check_toggle_saves(self):
        """一覧: チェックの列は ✓ で出し、ダブルクリックで切り替えて保存する。"""
        self.open_list("作業")
        pg = self.page
        cells = pg.eval_on_selector_all(".grid__table tbody td.chk", "t => t.map(x => x.textContent.trim())")
        self.assertEqual(len(cells), 3)
        pg.dblclick(".grid__table tbody tr:nth-child(1) td.chk")
        pg.wait_for_function("() => /保存しました/.test(document.body.innerText)", timeout=10000)
        with sqlite3.connect(self.db) as c:
            got = c.execute("SELECT 廃止 FROM 作業 WHERE 管理番号 = 1").fetchone()[0]
        self.assertIn(str(got), ("1", "-1"))

    def test_3_drop_table_two_confirmations(self):
        """表を削除: 強い案内 → 名前を打つ窓、の 2 回が続けて出る(0.45.0 で 2 回目がすぐ閉じていた)。"""
        self.open_list("古い表")
        pg = self.page
        self.assertTrue(pg.is_visible("#actDropTable"))
        # 名前を間違えたら消さない
        pg.click("#actDropTable")
        pg.wait_for_selector(".askbox[open]")
        self.assertIn("元に戻せません", pg.inner_text(".askbox[open] .askbox__text"))
        pg.click('.askbox[open] [data-answer="yes"]')
        pg.wait_for_selector(".askbox[open] #askboxInput:not([hidden])")
        pg.wait_for_timeout(300)                         # 前の窓の閉じた知らせが届いても開いたまま
        self.assertTrue(pg.is_visible("#askboxInput"))
        pg.fill("#askboxInput", "古い")
        pg.click('.askbox[open] [data-answer="yes"]')
        pg.wait_for_timeout(500)
        self.assertIn("古い表", self.tables())
        # 合っていれば消す
        pg.click("#actDropTable")
        pg.wait_for_selector(".askbox[open]")
        pg.click('.askbox[open] [data-answer="yes"]')
        pg.wait_for_selector(".askbox[open] #askboxInput:not([hidden])")
        pg.fill("#askboxInput", "古い表")
        pg.click('.askbox[open] [data-answer="yes"]')
        pg.wait_for_function("() => /削除しました/.test(document.body.innerText)", timeout=10000)
        self.assertNotIn("古い表", self.tables())

    def test_4_cancel_confirmation(self):
        """確かめの窓で「やめる」なら何もしない。"""
        with sqlite3.connect(self.db) as c:
            c.execute("CREATE TABLE IF NOT EXISTS 残す表 (名前 TEXT)")
            c.execute("INSERT INTO 残す表 VALUES ('z')")
        self.open_list("残す表")
        pg = self.page
        pg.click("#actDropTable")
        pg.wait_for_selector(".askbox[open]")
        pg.click('.askbox[open] [data-answer="no"]')
        pg.wait_for_timeout(300)
        self.assertFalse(pg.is_visible(".askbox[open]"))
        self.assertIn("残す表", self.tables())

    def test_5_connection_folder_only(self):
        """接続先の登録: パスはフォルダだけ・名前がファイル名。設定ファイルにはフルパスで書く。"""
        pg = self.page
        pg.goto(self.base + "/settings?tab=source")
        pg.wait_for_selector("#dbregRows tr")
        folder = str(self.db.parent)
        values = pg.eval_on_selector_all("#dbregRows input", "t => t.map(x => x.value)")
        self.assertEqual(values[:2], [self.db.name, folder])
        new = "#dbregNew input"
        pg.fill(f"{new} >> nth=0", "別の.sqlite3")
        pg.fill(f"{new} >> nth=1", folder)
        pg.click("#dbregNew .btn--commit")
        pg.wait_for_function("() => document.querySelectorAll('#dbregRows tr').length >= 2", timeout=10000)
        with sqlite3.connect(self.settings_file) as c:
            paths = [r[0] for r in c.execute('SELECT "パス" FROM "接続先" ORDER BY "管理番号"')]
        self.assertEqual(Path(paths[-1].replace("\\", "/")).name, "別の.sqlite3")
        self.assertTrue(paths[-1].startswith(folder))


if __name__ == "__main__":
    unittest.main()
