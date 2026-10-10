"""数を勝手に丸めて・切り捨てて書かない(python-web-tools で 2502.5 が 2502 になって書かれた失敗の再発防止)

調べた書き込みの道と、ここで確かめていること:
- Access 取り込み(importer): 整数の列に小数が来ても切り捨てない。地域の設定で小数点が "," でも数として読む
- 一覧のその場で直す(金額の列): 一覧は "#,##0"(丸め)で見せている。丸めた値から直し始めない・カンマ入りの文字で書かない
- 数値入力の窓(刻みに切り上げ): 2 進の誤差で 0.07 → 0.08、0.3 → 0.30000000000000004 にしない(node があれば JS を実行)
- 登録・マスタ管理: 小数はそのまま書いて、そのまま読める
- 見せ方: "#,##0" は VBA と同じく 0.5 を 0 から遠い側へ。帳票の小計は有効 15 桁
- 出力(xlsx): 16 桁以上の数字は文字のまま(Excel で下の桁が 0 に化けない)
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path

from dbviewer import auth, config, table_browse, user_settings, vba_compat
from dbviewer.accdb_bridge import importer
from tests._fixture_db import fresh_db_path
from tests._web import TOKEN, make_client

ROOT = Path(__file__).resolve().parent.parent


class ImportTest(unittest.TestCase):
    def conv(self, text, ado):
        return importer._convert(text, importer.Column("x", ado))

    def test_integer_column_keeps_decimals(self):
        self.assertEqual(self.conv("2502.5", 3), 2502.5)          # 以前は 2502
        self.assertEqual(self.conv("3.0", 3), 3)
        self.assertEqual(self.conv("42", 3), 42)
        self.assertEqual(self.conv("abc", 3), "abc")
        self.assertEqual(self.conv("2971.5", 5), 2971.5)          # Double
        self.assertEqual(self.conv("1234.5678", 6), 1234.5678)    # Currency

    def test_bridge_writes_dot_whatever_the_region(self):
        vbs = (ROOT / "dbviewer" / "accdb_bridge" / "bridge.vbs").read_text(encoding="utf-8", errors="replace")
        self.assertIn('Replace(CStr(v), DecimalMark(), ".")', vbs)
        self.assertIn("Case adSingle, adDouble, adCurrency, adDecimal, adNumeric", vbs)


class FormatTest(unittest.TestCase):
    def test_thousands_rounds_like_vba(self):
        f = vba_compat.format_thousands
        self.assertEqual([f("2.5"), f("3.5"), f("-2.5"), f("1234.5"), f("1234567")],
                         ["3", "4", "-3", "1,235", "1,234,567"])
        self.assertEqual(f("0.285"), "0")                         # 10 進のまま丸める
        self.assertEqual(f("abc"), "abc")

    def test_report_totals_are_15_digits(self):
        t = vba_compat.number_text
        self.assertEqual([t(0.1 + 0.2), t(2502.5), t(3.0), t(-1.5)], ["0.3", "2502.5", "3", "-1.5"])

    def test_xlsx_keeps_long_numbers_as_text(self):
        n = table_browse._number
        self.assertEqual(n("12345678901234567"), "12345678901234567")
        self.assertEqual(n("2502.5"), 2502.5)
        self.assertEqual(n("123456789012345"), 123456789012345)  # 15 桁までは数
        self.assertEqual(n("0123"), "0123")


@unittest.skipUnless(shutil.which("node"), "node が無い")
class StepCeilingTest(unittest.TestCase):
    """数値入力の窓の「刻みに切り上げ」(app/static/js/helpers.js の ceilToStep)。"""

    def test_no_binary_error(self):
        # helpers.js は画面の部品を読み込むので、関数だけを取り出して動かす
        src = (ROOT / "app/static/js/helpers.js").read_text(encoding="utf-8")
        start = src.index("function decimals(")
        end = src.index("\n}\n", src.index("function ceilToStep(")) + 3
        body = src[start:end]
        cases = [[0.07, 0.01], [1.11, 0.01], [0.3, 0.1], [0.7, 0.1], [1.05, 0.1], [7, 5], [2.51, 0.5], [12, 1]]
        js = body + f"\nconsole.log(JSON.stringify({json.dumps(cases)}.map(([v, s]) => ceilToStep(v, s))));"
        out = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(json.loads(out.stdout), [0.07, 1.11, 0.3, 0.7, 1.1, 10, 3, 12])


class WriteReadTest(unittest.TestCase):
    """一覧のその場で直す・登録で、書いた数がそのまま読める。金額の列も。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = fresh_db_path(Path(self.tmp.name))
        with sqlite3.connect(self.path) as c:
            # Ⅱ を「金額」の表示にして、小数の金額を入れておく
            c.execute("UPDATE [作業標準管理_設定] SET 表示='金額' WHERE 項目名='Ⅱ'")
            c.execute("UPDATE [作業標準管理] SET Ⅱ='1234.5' WHERE 管理番号=2")
        user_settings.save(config.KEY_ONLINE, False)
        user_settings.save(config.KEY_LOCAL_DIR, str(self.path.parent))
        user_settings.save(config.KEY_DB_FILE, self.path.name)
        auth.end()
        self.addCleanup(auth.end)
        auth.start(auth.LoginResult(True, user_id="Xfa-Ngyinazawa", level=5))
        self.c = make_client()
        self.h = {"X-Tool-Token": TOKEN}

    def db(self, col, key=2):
        with sqlite3.connect(self.path) as c:
            return c.execute(f"SELECT [{col}] FROM [作業標準管理] WHERE 管理番号=?", (key,)).fetchone()[0]

    def cell(self, column, value, seen, key="2"):
        return self.c.post("/api/list/cell", json={"key": key, "column": column, "value": value, "seen": seen},
                           headers=self.h)

    def test_money_starts_from_stored_value(self):
        d = self.c.post("/api/list/data", json={}, headers=self.h).get_json()
        names = [c["name"] for c in d["columns"]]
        row = next(r for r in d["rows"] if r[d["key_index"]] == "2")
        self.assertEqual(row[names.index("Ⅱ")], "1,235")                     # 一覧は丸めて見せる
        self.assertTrue(d["edit"]["columns"]["Ⅱ"]["money"])
        raw = self.c.post("/api/list/value", json={"key": "2", "column": "Ⅱ"}, headers=self.h).get_json()
        self.assertEqual(raw["value"], "1234.5")                              # 直すときは本体の値から

    def test_money_saves_number_not_text_with_commas(self):
        # 見せた形(1,235)を見ていても、本体の値(1234.5)を見ていても、競合とは言わない
        r = self.cell("Ⅱ", "1,500", "1,235")
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual(self.db("Ⅱ"), "1500")                               # "1,500" という文字で書かない
        r = self.cell("Ⅱ", "2502.5", "1500")
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual(self.db("Ⅱ"), "2502.5")                             # 小数を丸めない

    def test_plain_decimals_round_trip(self):
        r = self.cell("Ⅲ", "2502.5", self.db("Ⅲ") or "")
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual(self.db("Ⅲ"), "2502.5")
        d = self.c.post("/api/list/data", json={}, headers=self.h).get_json()
        names = [c["name"] for c in d["columns"]]
        row = next(r for r in d["rows"] if r[d["key_index"]] == "2")
        self.assertEqual(row[names.index("Ⅲ")], "2502.5")

    def test_real_column_round_trip_via_master(self):
        """数の型の列(REAL・INTEGER)に小数を書いても、そのまま読める。"""
        r = self.c.post("/api/master/add-column", json={"table": "ID", "name": "係数", "kind": "number"},
                        headers=self.h)
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        rows = self.c.post("/api/master/rows", json={"table": "ID"}, headers=self.h).get_json()
        row = list(rows["rows"][0])
        new = row[:-1] + ["2502.5"]
        r = self.c.post("/api/master/execute", headers=self.h, json={
            "table": "ID", "mode": "UPDATE", "values": new, "key": row[0], "seen": row,
            "confirmed": True, "answer": True})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        again = self.c.post("/api/master/rows", json={"table": "ID"}, headers=self.h).get_json()
        self.assertEqual(again["rows"][0][-1], "2502.5")


if __name__ == "__main__":
    unittest.main()
