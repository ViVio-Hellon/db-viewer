"""読み側のエンジン(VBA CODESQL / GetSQLFilterStr / SetListBoxItems / LV1Reflesh)のゴールデンテスト"""
from __future__ import annotations

import unittest

from dbviewer import config, filters as flt, records, sql_builder, vba_compat
from dbviewer.settings_tables import ConvRow, ConvTable, load_setup, master_file_check
import tempfile
from pathlib import Path

from dbviewer import shared_db
from tests._fixture_db import connect, fresh_db_path

EXPECTED_BASE_SQL = (
    "SELECT A.[管理番号],A.[作業名],A.[作業NO],A.[Ⅰ],A.[Ⅱ],A.[Ⅲ],A.[Ⅳ],A.[RA],A.[PC化],A.[担当],"
    "A.[危険度分類ランク],A.[A],A.[B],A.[C],A.[危険源分析],A.[区分],A.[完了],A.[廃止],A.[文書番号],"
    "A.[登録者],A.[更新者],A.[登録日],A.[更新日],A.[完了日],A.[課長承認] "
    "FROM  [作業標準管理]  AS A  ORDER BY A.[管理番号] ASC ")


def conv(*rows: dict) -> ConvTable:
    cols = [config.KEY_ITEM, config.ITEM_NAME, config.CHAR_COLOR_SET, config.INP_TYPE, config.REG_CON,
            config.DSP_TYPE, config.MS_JOIN, config.HIS_REC, config.IND_TYPE, config.ADD_IND, config.SORT_FLD]
    out = [ConvRow(no=i + 1, item=r["item"], ms_join=r.get("ms_join", ""), ind_type=r.get("ind_type", ""),
                   add_ind=r.get("add_ind", ""), sort=r.get("sort", "")) for i, r in enumerate(rows)]
    return ConvTable("T_設定", cols, out, {c: i + 1 for i, c in enumerate(cols)})


class VbaCompatTest(unittest.TestCase):
    def test_is_date_like_vba(self):
        self.assertTrue(vba_compat.is_date("1-2-3"))          # 2001/02/03
        self.assertFalse(vba_compat.is_date("0-1-3-1"))
        self.assertTrue(vba_compat.is_date("2021/7/22"))
        self.assertFalse(vba_compat.is_date("B"))
        self.assertEqual(vba_compat.normalize_date_text("2021/7/22"), "2021/07/22")

    def test_format(self):
        self.assertEqual(vba_compat.format_thousands("1234567"), "1,234,567")
        self.assertEqual(vba_compat.format_000(7), "007")
        self.assertEqual(vba_compat.format_date(vba_compat.cdate("2024/3/5 9:07:01"), "YYYY/MM/DD hh:mm:ss"),
                         "2024/03/05 09:07:01")


class SetupTest(unittest.TestCase):
    def test_loads_real_config(self):
        with connect() as conn:
            s = load_setup(conn)
        self.assertEqual(s.general.main_table, "作業標準管理")
        self.assertEqual(s.general.report_str, "/出力原紙.xlsx/作業標準/REPORT")
        self.assertTrue(s.general.row_limit)                  # 初期表示 = 一括
        self.assertTrue(s.ok)
        self.assertEqual(s.column_problems, [])
        self.assertTrue(s.format_ok)
        self.assertEqual([f.item for f in s.active_filters],
                         ["作業名", "作業NO", "危険度分類ランク", "区分", "RA", "PC化", "完了", "廃止",
                          "危険源分析", "担当", "課長承認"])
        self.assertEqual(s.conv.by_item("Ⅰ").input_type, "数値2/0/500/1")
        self.assertEqual(s.conv.by_item("登録者").input_type, "#UPUSER#")
        self.assertEqual(s.conv.by_item("登録日").input_type, "calender")

    def test_master_file_check_messages(self):
        with tempfile.TemporaryDirectory() as tmp, shared_db.connect(fresh_db_path(Path(tmp))) as conn:
            conn.execute("CREATE TABLE T (管理番号 INTEGER, a TEXT, b TEXT)")
            conn.execute("CREATE TABLE T_設定 (管理番号 INTEGER, 項目名 TEXT, 文字色設定 TEXT, 入力形式 TEXT, 登録条件 TEXT, 参照形式 TEXT, マスタ結合 TEXT, 履歴 TEXT, 表示 TEXT, 追加表示 TEXT, ソート TEXT)")
            conn.execute("INSERT INTO T_設定 (管理番号, 項目名) VALUES (1, '管理番号'), (2, 'a')")
            self.assertEqual(master_file_check(conn, "T"),
                             "メインテーブルとその設定テーブルで登録している項目数が一致していません。")
            conn.execute("INSERT INTO T_設定 (管理番号, 項目名) VALUES (3, 'x')")
            self.assertEqual(master_file_check(conn, "T"), "不一致項目あり [T_設定]マスタ 3番目の項目 x ≠ b")
            conn.execute("UPDATE T_設定 SET 項目名='b' WHERE 管理番号=3")
            # フィルタ表の列は照合しない(一覧がフィルタ表を使わなくなった)
            self.assertEqual(master_file_check(conn, "T"), "")
            # フィルタ・タイプが無くても起動できる
            conn.execute("DROP TABLE フィルタ"); conn.execute("DROP TABLE タイプ")
            self.assertEqual(load_setup(conn).filters, [])
            conn.rollback()


class CodeSqlTest(unittest.TestCase):
    def test_real_config_matches_vba_string(self):
        with connect() as conn:
            s = load_setup(conn)
        b = sql_builder.build(s.conv, s.general.main_table)
        self.assertEqual(b.vba_sql, EXPECTED_BASE_SQL)
        self.assertEqual(b.sql, EXPECTED_BASE_SQL)             # 日付リテラル無しなら同じ
        self.assertEqual(len(b.columns), 25)

    def test_join_amount_addind_sort_and_alias(self):
        c = conv({"item": "管理番号"},
                 {"item": "薬品コード", "ms_join": "薬品/薬品コード/薬品名/金額"},
                 {"item": "数量", "ind_type": "金額", "add_ind": "[数量]/[数量]*2/合計/金額", "sort": "DESC"})
        b = sql_builder.build(c, "台帳", sql_filter="[薬品名] LIKE '%A%' AND [数量]>=#2024/01/01#",
                              init_filter="区分/=/有効")
        self.assertEqual(
            b.vba_sql,
            "SELECT A.[管理番号],A.[薬品コード],FORMAT(B2.[薬品名], '#,##0') AS 薬品名,"
            "FORMAT(A.[数量], '#,##0') AS 数量,FORMAT(A.[数量]*2, '#,##0') AS 合計 "
            "FROM ( [台帳]  AS A LEFT JOIN [薬品] AS B2 ON A.[薬品コード] = B2.[薬品コード] )  "
            "WHERE A.[区分] = '有効' AND B2.[薬品名] LIKE '%A%' AND A.[数量]>=#2024/01/01# "
            "ORDER BY A.[数量] DESC ")
        self.assertIn("VBADATE(A.[数量])>='2024/01/01'", b.sql)
        self.assertEqual(b.columns, ["管理番号", "薬品コード", "薬品名", "数量", "合計"])
        self.assertEqual(b.join_count, 1)

    def test_add_ind_without_slash_is_never_inserted(self):
        # VBA の癖: 挿入位置は `[項目名]` の形でしか一致しない(`/` 無しの追加表示は
        # 項目名を裸で入れるので、どこにも挿入されない)。VBA どおりに再現する
        c = conv({"item": "管理番号"}, {"item": "単価", "add_ind": "[単価]*1.1"})
        b = sql_builder.build(c, "T")
        self.assertEqual(b.vba_sql, "SELECT A.[管理番号],A.[単価] FROM  [T]  AS A  ORDER BY A.[管理番号] ASC ")
        c = conv({"item": "管理番号"}, {"item": "単価", "add_ind": "[単価]/[単価]*1.1/税込/"})
        b = sql_builder.build(c, "T")
        self.assertEqual(b.vba_sql, "SELECT A.[管理番号],A.[単価],A.[単価]*1.1 AS 税込 FROM  [T]  AS A  ORDER BY A.[管理番号] ASC ")

    def test_sort_spec_overrides(self):
        c = conv({"item": "管理番号"})
        self.assertTrue(sql_builder.build(c, "T", sort_spec="DESC").vba_sql.endswith("ORDER BY A.[管理番号] DESC "))


class FilterStrTest(unittest.TestCase):
    def s(self, item, ftype, value, no=1):
        return flt.FilterState(no, item, ftype, value)

    def test_each_type(self):
        self.assertEqual(flt.sql_filter_str([self.s("更新日", "YYYY", "2024")]),
                         "[更新日]>=#2024/01/01# AND [更新日] <=#2024/12/31#")
        self.assertEqual(flt.sql_filter_str([self.s("更新日", "YYYYMM", "202401")]),
                         "[更新日]>=#2024/01/01# AND [更新日]<#2024/02/01#")
        self.assertEqual(flt.sql_filter_str([self.s("更新日", ">=", "2024/3/5")]), "[更新日]>=#2024/03/05#")
        self.assertEqual(flt.sql_filter_str([self.s("作業NO", "", "0-1-3")]), "[作業NO]='0-1-3'")
        self.assertEqual(flt.sql_filter_str([self.s("更新日", "", "2024/03/05")]), "[更新日]=#2024/03/05#")
        self.assertEqual(flt.sql_filter_str([self.s("ランク", "", "B")]), "[ランク]='B'")
        self.assertEqual(flt.sql_filter_str([self.s("薬品名", "薬品", "酸")]), "[薬品名] LIKE '%酸%'")

    def test_mk_sticks_like_vba(self):
        # VBA の癖: 一度 ' になると後ろの日付も ' のまま
        out = flt.sql_filter_str([self.s("作業NO", "", "0-1-3", 1), self.s("更新日", "", "2024/03/05", 2)])
        self.assertEqual(out, "[作業NO]='0-1-3' AND [更新日]='2024/03/05'")

    def test_reset_and_blank(self):
        self.assertEqual(flt.sql_filter_str([self.s("a", "", "x")], reset=True), "")
        self.assertEqual(flt.sql_filter_str([self.s("a", "", "")]), "")

    def test_sqlite_pair_escapes_quotes(self):
        vba, sq = flt.sql_filter_pair([self.s("a", "", "it's")])
        self.assertEqual(vba, "[a]='it's'")
        self.assertEqual(sq, "[a]='it''s'")


class CandidatesTest(unittest.TestCase):
    def test_non_dup_list_order_and_blank(self):
        rows = [["b "], ["a"], ["b"], [""], ["A"], ["\x07c"]]
        self.assertEqual(flt.non_dup_list(rows, 1), ["", "A", "a", "b", "c"])
        self.assertEqual(flt.non_dup_list(rows, 0), [""])

    def test_candidates_by_type(self):
        rows = [["2024/03/05"], ["2023/12/31"], ["2024/01/10"], ["x"]]
        fj = {"日": 1}
        with connect() as conn:
            self.assertEqual(flt.candidates(conn, flt.FilterState(1, "日", "YYYY", ""), rows, fj),
                             ["x", "2024", "2023"])
            self.assertEqual(flt.candidates(conn, flt.FilterState(1, "日", ">=", ""), rows, fj),
                             ["x", "2024/03/05", "2024/01/10", "2023/12/31"])
            self.assertEqual(flt.candidates(conn, flt.FilterState(1, "日", "", ""), rows, fj),
                             ["2023/12/31", "2024/01/10", "2024/03/05", "x"])
            # マスタ参照: タイプ表の「タイプ」列
            self.assertEqual(flt.candidates(conn, flt.FilterState(1, "タイプ", "タイプ", ""), rows, fj),
                             [">=", "<=", ">", "<", "YYYY", "YYYYMM", "作業NO"])

    def test_width_array(self):
        self.assertEqual(flt.width_array(["100", "", "35"], 500), [100.0, 365.0, 35.0])
        self.assertEqual(flt.width_array(["", ""], 200), [100.0, 100.0])
        self.assertEqual(flt.width_array(["300", "300", ""], 300), [100.0, 100.0, 100.0])


class ListViewTest(unittest.TestCase):
    def test_paging_and_candidates(self):
        with connect() as conn:
            s = load_setup(conn)
            states = flt.states_from_rows(s.filters)
            p = records.list_view(conn, s, states, per_page=24)
            self.assertEqual((p.row_count, p.page_count, p.page_no, len(p.page_rows)), (327, 14, 1, 24))
            self.assertEqual(p.candidates[3], ["A", "B", "C"])
            p2 = records.list_view(conn, s, states, page_no=14, per_page=24, page_flag=True)
            self.assertEqual((p2.page_no, len(p2.page_rows)), (14, 327 - 13 * 24))
            states = flt.states_from_rows(s.filters, {3: "B", 2: "0-1-3"})
            p3 = records.list_view(conn, s, states, event_no=3)
            self.assertEqual(p3.where_vba, "[作業NO]='0-1-3' AND [危険度分類ランク]='B'")
            self.assertEqual(p3.row_count, 1)
            self.assertIsNone(p3.candidates[3])
            self.assertEqual(p3.candidates[2], ["0-1-3"])

    def test_summary(self):
        with connect() as conn:
            s = load_setup(conn)
            v = records.summary(conn, s, "課長承認")
            self.assertEqual(v.vba_sql, "SELECT [課長承認] ,COUNT([課長承認]) AS [カウント] FROM [作業標準管理] GROUP BY [課長承認]")
            self.assertEqual(v.rows, [["", "16"], ["未", "184"], ["済", "127"]])
            with self.assertRaises(records.ListError):
                records.summary(conn, s, "a(b)")


if __name__ == "__main__":
    unittest.main()
