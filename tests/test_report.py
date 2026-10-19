"""帳票(REPORT)の移植を VBA の実出力と突き合わせる

`tests/fixtures/realdata/expected/帳票出力例_2024-03-28.tsv` は VBA が 2024-03-28 に
出した帳票シートをそのまま TSV にしたもの(判断事項 #19: 当時の全 319 件)。
Ⅰ〜Ⅳ・小計・Total・GrandTotal・空行の位置が同じであることを確かめる。

更新者・更新日(12〜13 列目)は受領 DB の並び直し(#19)で値が違うため比べない。
作業名はどちらも `StrConv(vbNarrow)` 相当を通して比べる(#20: 半角カナの表記差)。
"""
from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from dbviewer import auth, config, printing, records, reports, shared_db, sql_builder, user_settings
from dbviewer.reports import others, render, sagyo_hyojun
from dbviewer.settings_tables import load_setup
from dbviewer.vba_compat import to_narrow
from tests._fixture_db import FIXTURES, connect, fresh_db_path
from tests._web import TOKEN, make_client

EXPECTED_TSV = FIXTURES / "expected" / "帳票出力例_2024-03-28.tsv"


def _expected_rows() -> list[list[str]]:
    text = EXPECTED_TSV.read_bytes().decode("utf-8")
    rows = [line.split("\t") for line in text.split("\r\n")]
    while rows and rows[-1] == [""]:
        rows.pop()
    return rows


def _build_golden() -> sagyo_hyojun.Grid:
    with connect() as conn:
        setup = load_setup(conn)
        built = sql_builder.build(setup.conv, setup.general.main_table, sql_filter="[管理番号]<=319")
        table = records.table_array(conn, built.sql)
    return sagyo_hyojun.build(table)


def _comparable(row: list[str]) -> list[str]:
    """作業名〜文書番号(11 列)と GrandTotal(14 列目)。VBA 出力は作業NO に末尾空白が残る。"""
    row = list(row) + [""] * (14 - len(row))
    return [to_narrow(row[0]).strip()] + [x.strip() for x in row[1:11]] + [row[13]]


class GoldenReportTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.grid = _build_golden()
        cls.expected = _expected_rows()

    def test_same_rows_as_vba_output(self):
        got = self.grid.text_rows()
        self.assertEqual(len(got), len(self.expected))
        diffs = [(i, _comparable(e), _comparable(g)) for i, (e, g) in enumerate(zip(self.expected, got))
                 if _comparable(e) != _comparable(g)]
        self.assertEqual(diffs, [])

    def test_totals(self):
        total = self.grid.rows[-1]
        self.assertEqual(total.kind, "total")
        self.assertEqual([c.value for c in total.cells[:7]], ["Total", "", "151", "1106", "382", "66", ""])
        self.assertEqual((total.cells[12].value, total.cells[13].value), ("GrandTotal", "1705"))
        self.assertEqual(self.grid.grand_total, 1705)
        self.assertTrue(all(total.cells[i].number for i in (2, 3, 4, 5, 13)))

    def test_subtotals_and_borders(self):
        rows = self.grid.rows
        kinds = [r.kind for r in rows]
        self.assertEqual((kinds[0], kinds.count("data"), kinds.count("subtotal")), ("header", 319, 16))
        # 小計行: 「小計」+ Ⅰ〜Ⅳ の小計、下に細線。直前の行は下が二重線
        for i, r in enumerate(rows):
            if r.kind == "subtotal":
                self.assertEqual(r.cells[0].value, "小計")
                self.assertTrue(r.bottom_thin and not r.border_all)
                self.assertTrue(rows[i - 1].bottom_double and rows[i - 1].border_all)
            elif r.kind == "data":
                self.assertTrue(r.border_all)
        # 空行は 作業NO の "-" より前が変わるところにだけ入る
        for i in range(2, len(rows)):
            if rows[i].kind == "data" and rows[i - 1].kind == "data":
                self.assertEqual(sagyo_hyojun.prefix(rows[i].cells[1].value),
                                 sagyo_hyojun.prefix(rows[i - 1].cells[1].value))

    def test_select_columns_by_name(self):
        table = [["管理番号", "作業NO", "作業名", "Ⅰ", "Ⅱ", "Ⅲ", "Ⅳ"], ["1", "0-1-1", "作業", "1", "2", "3", "4"]]
        sel = sagyo_hyojun.select_columns(table)
        self.assertEqual(sel[0], list(sagyo_hyojun.COLUMNS))
        self.assertEqual(sel[1][:6], ["作業", "0-1-1", "1", "2", "3", "4"])
        self.assertEqual(sel[1][6:], [""] * 7)
        g = sagyo_hyojun.build(table)
        self.assertEqual(g.grand_total, 10)
        self.assertEqual([c.value for c in g.rows[-1].cells[2:6]], ["1", "2", "3", "4"])


class RegistryTest(unittest.TestCase):
    def test_parse(self):
        spec = reports.parse("/出力原紙.xlsx/作業標準/REPORT")
        self.assertEqual((spec.folder, spec.file, spec.sheet, spec.proc), ("", "出力原紙.xlsx", "作業標準", "REPORT"))
        self.assertEqual(spec.path(Path("/base")), Path("/base/出力原紙.xlsx"))
        spec = reports.parse(r"C:\x/台帳.xlsx/原紙/DAITYOREPORT/10")
        self.assertEqual((spec.folder, spec.proc, spec.args[4]), (r"C:\x", "DAITYOREPORT", "10"))
        for bad in ("REPORT", "a/b"):
            with self.assertRaises(reports.ReportError) as cm:
                reports.parse(bad)
            self.assertEqual(str(cm.exception), reports.MSG_BAD_SETTING)

    def test_builder_for(self):
        self.assertIs(reports.builder_for("DAITYOREPORT"), others.daityo)
        with self.assertRaises(reports.ReportError) as cm:
            reports.builder_for("NOPE")
        self.assertIn("指定プログラム : NOPE", str(cm.exception))
        self.assertIn("REPORT,SUITOUREPORT,DAITYOREPORT,GIKAKUREPORT", str(cm.exception))

    def test_mode_value(self):
        self.assertEqual(others.mode_value(["3", "4", "4", "3", "3"]), "3")
        self.assertEqual(others.mode_value(["4", "3"]), "3")
        self.assertEqual(others.mode_value([]), "")

    def test_others_do_not_crash_on_empty(self):
        self.assertEqual(others.gikaku([["a"]], []).rows, [])
        g = others.daityo([["文書番号", "最新版"], ["D-1", "R2"]], "//x/DAITYOREPORT/3".split("/"))
        self.assertEqual(len(g.rows[0].cells), 8)
        self.assertEqual([c.value for c in g.rows[2].cells[:2]], ["D-1", "R2"])


class RenderTest(unittest.TestCase):
    def test_sheet_name(self):
        self.assertEqual(render.default_sheet_name(datetime(2024, 3, 28)), "2024-03-28")

    def test_html_is_a4_portrait_6mm(self):
        g = sagyo_hyojun.build([["作業名", "作業NO", "Ⅰ", "Ⅱ", "Ⅲ", "Ⅳ"],
                                ["a", "0-1", "1", "0", "0", "0"], ["b", "1-1", "2", "0", "0", "0"]])
        html = render.to_html(g, "作業標準管理 2024-03-28")
        self.assertIn("A4 portrait", html)
        self.assertIn("margin: 6.0mm", html)
        self.assertIn("メイリオ", html)
        self.assertIn("小計", html)
        self.assertIn("GrandTotal", html)
        self.assertIn('class="all dbl"', html)
        # 14 列 × 幅 10.25 は A4 縦(印刷幅 198mm)に収まらない → 縮小して収める
        self.assertIn('style="zoom:0.', html)

    def test_html_fits_page_width(self):
        html = render.to_html(_build_golden(), "作業標準管理 2024-03-28")
        self.assertIn('style="zoom:0.', html)
        setup = printing.PageSetup(paper="A4", orientation="portrait", margin_mm=6)
        self.assertEqual(render.fit_zoom([10.25] * 5, setup), 1.0)
        self.assertAlmostEqual(render.fit_zoom([100.0, 100.0], setup), 198 / 380, places=3)

    def test_column_widths(self):
        g = sagyo_hyojun.build([["作業名", "作業NO", "Ⅰ", "Ⅱ", "Ⅲ", "Ⅳ"], ["ハンマーの取り扱い" * 3, "0-1", "1", "0", "0", "0"]])
        w = render.column_widths(g)
        self.assertEqual(len(w), 14)                 # A:M + GrandTotal の N
        self.assertGreater(w[0], render.MIN_WIDTH)
        self.assertLessEqual(w[0], render.MAX_WIDTH)
        self.assertEqual(w[5], render.MIN_WIDTH)


class ReportWebTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        path = fresh_db_path(Path(self.tmp.name))
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

    def test_run_without_excel_template(self):
        """原紙(出力原紙.xlsx)が無くても帳票が出る。Excel は使わない。"""
        self.c.post("/api/login", json={}, headers=self.h)                 # 0.16.0: 画面を開けば自動で入る
        self.assertFalse(any(Path(self.tmp.name).glob("*.xlsx")))
        r = self.c.post("/api/report/run", json={}, headers=self.h)
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        d = r.get_json()
        self.assertEqual(d["sheet"], render.default_sheet_name())
        self.assertEqual((d["rows"], d["source_rows"]), (346, 327))   # 全件 + 見出し + 小計 17 + 空行 + Total
        self.assertFalse(any(Path(self.tmp.name).glob("*.xlsx")))
        p = self.c.get(d["preview"], headers=self.h)
        self.assertEqual(p.status_code, 200)
        self.assertIn("GrandTotal", p.get_data(as_text=True))
        self.assertEqual(self.c.get("/report/preview?id=zzz", headers=self.h).status_code, 404)

    def test_run_with_keys_in_screen_order(self):
        """画面で絞り込んだ行(管理番号)で作る。VBA の出力例と同じ 319 件で照合。"""
        self.c.post("/api/login", json={"id": "Xfa-Ngyinazawa", "password": ""}, headers=self.h)
        keys = [str(k) for k in range(1, 320)]
        d = self.c.post("/api/report/run", json={"keys": keys}, headers=self.h).get_json()
        self.assertEqual(d["source_rows"], 319)
        html = self.c.get(d["preview"], headers=self.h).get_data(as_text=True)
        self.assertIn(">1705<", html)                                   # GrandTotal は VBA と同じ
        d = self.c.post("/api/report/run", json={"keys": ["3", "1", "999"]}, headers=self.h).get_json()
        self.assertEqual(d["source_rows"], 2)
        # 画面で逆順に並べ替えていても、帳票の並び(小計の区切り)は VBA と同じ
        rev = list(reversed(keys))
        html2 = self.c.get(self.c.post("/api/report/run", json={"keys": rev}, headers=self.h).get_json()["preview"],
                           headers=self.h).get_data(as_text=True)
        self.assertEqual(html2.count("小計"), html.count("小計"))
        self.assertLess(html2.index("ﾊﾝﾏｰの取り扱い"), html2.index("ﾃﾞｨｽｸｸﾞﾗｲﾝﾀﾞｰ"))
        r = self.c.post("/api/report/run", json={"keys": []}, headers=self.h)
        self.assertEqual(r.status_code, 422)

    def test_print_view(self):
        self.c.post("/api/login", json={"id": "Xfa-Ngyinazawa", "password": ""}, headers=self.h)
        body = {"title": "作業標準管理", "note": "327 件中 2 件", "columns": ["作業NO", "Ⅰ"],
                "rows": [["0-1-1", "0"], ["0-1-2", "7"]], "numeric": [False, True]}
        d = self.c.post("/api/print", json=body, headers=self.h).get_json()
        html = self.c.get(d["preview"], headers=self.h).get_data(as_text=True)
        self.assertIn("A4 landscape", html)
        self.assertIn("margin: 6.0mm", html)
        self.assertIn("327 件中 2 件", html)
        self.assertIn('<td class="n">7</td>', html)
        self.assertNotIn("zoom:", html)
        wide = {**body, "columns": [f"列{i}" for i in range(40)], "rows": [["あいうえおかきくけこ"] * 40]}
        html = self.c.get(self.c.post("/api/print", json=wide, headers=self.h).get_json()["preview"], headers=self.h).get_data(as_text=True)
        self.assertIn("zoom:0.", html)                                  # 紙の幅に縮める
        self.assertEqual(self.c.post("/api/print", json={**body, "rows": []}, headers=self.h).status_code, 422)


if __name__ == "__main__":
    unittest.main()
