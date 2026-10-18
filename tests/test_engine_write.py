"""書き側のエンジン(VBA Login / REG_EXE / adoSQL / GetNewNo / GetNewCode / 履歴 / 入力形式)"""
from __future__ import annotations

import tempfile
import os
import unittest
from unittest import mock
from datetime import datetime
from pathlib import Path

from dbviewer import ado_sql, auth, editing, history, input_types, numbering, shared_db
from dbviewer.settings_tables import load_setup
from tests._fixture_db import connect, fresh_db_path


class InputTypesTest(unittest.TestCase):
    def test_kinds_and_colors(self):
        p = input_types.parse
        self.assertEqual((p("").kind, p("").color, p("").locked), ("free", "ivory", False))
        self.assertEqual((p("/3").kind, p("/3").lines), ("free", 3))
        n = p("数値2/0/500/1")
        self.assertEqual((n.kind, n.num_mode, n.num_min, n.num_max, n.num_step, n.locked), ("num", 2, 0.0, 500.0, 1.0, True))
        self.assertEqual((p("#UPUSER#").kind, p("#UPUSER#").enabled), ("auto", False))
        self.assertEqual(p("#view#").kind, "view")
        c = p("calender@過去/整理番号/GetNewCode/整理番号,日付")
        self.assertEqual((c.kind, c.date_limit, c.link_field, c.link_func, c.link_args), ("calender", "過去", "整理番号", "GetNewCode", ["整理番号", "日付"]))
        self.assertEqual((p("sタイプ").master, p("sタイプ").multi, p("m薬品").multi), ("タイプ", False, True))
        self.assertEqual((p("フィールド").kind, p("フォルダ").kind, p("数値変換").kind), ("field", "folder", "numconv"))
        f = p("$CoinInputFm/ソートコード,収入金額,支出金額")
        self.assertEqual((f.kind, f.form_name, f.form_args), ("form", "CoinInputFm", ["ソートコード", "収入金額", "支出金額"]))
        e = p("pe$P2/0/500/1")
        self.assertEqual((e.kind, e.message), ("error", input_types.MSG_RULE_ERROR))

    def test_auto_value(self):
        a = input_types.parse("#UPUSER#")
        self.assertEqual(input_types.auto_value(a, "修正", user="u", pc="p", now="n"), "u")
        self.assertIsNone(input_types.auto_value(a, "新規", user="u", pc="p", now="n"))
        self.assertEqual(input_types.auto_value(input_types.parse("#NEWDATE#"), "新規", user="u", pc="p", now="n"), "n")
        self.assertEqual(input_types.auto_value(input_types.parse("#USEPC#"), "修正", user="u", pc="p", now="n"), "p")


class AuthTest(unittest.TestCase):
    def test_developer_pc_can_edit(self):
        """0.16.0: パスワードは無い。開発者の ID・PC は、アクセス権限マスタに行が無くても 編集可(VBA DEVELOPERFLAG)。"""
        from dbviewer import access_rights
        view = access_rights.Decision(False, "master", "行がありません")
        with mock.patch.object(access_rights, "evaluate", return_value=view), \
                mock.patch.dict(os.environ, {"USERNAME": "satoshi", "COMPUTERNAME": "PC"}):
            r = auth.rights_result()
        self.assertEqual((r.level, r.developer), (auth.EDIT_LEVEL, True))
        with mock.patch.object(access_rights, "evaluate", return_value=view), \
                mock.patch.dict(os.environ, {"USERNAME": "someone", "COMPUTERNAME": "PC"}):
            self.assertEqual(auth.rights_result().level, auth.VIEW_LEVEL)

    def test_level_rules(self):
        self.assertEqual(auth.check_mode("新規", 3), ("新規", ""))
        self.assertEqual(auth.check_mode("新規", 2), ("", auth.MSG_NO_LEVEL.format(lv=2)))
        self.assertEqual(auth.check_mode("修正", 1, "DblClick"), ("参照", ""))
        self.assertEqual(auth.check_mode("修正", 1), ("", auth.MSG_NO_LEVEL.format(lv=1)))
        self.assertEqual(auth.check_mode("参照", 0), ("参照", ""))


class NumberingTest(unittest.TestCase):
    def test_new_no_smallest_gap(self):
        with tempfile.TemporaryDirectory() as tmp, shared_db.connect(fresh_db_path(Path(tmp))) as conn:
            self.assertEqual(numbering.get_new_no(conn, "作業標準管理"), 328)
            self.assertEqual(numbering.get_new_no(conn, "タイプ"), 7)          # 0..6 → 7
            conn.execute("CREATE TABLE T (管理番号 INTEGER)")
            self.assertEqual(numbering.get_new_no(conn, "T"), 1)
            conn.executemany("INSERT INTO T VALUES (?)", [(1,), (2,), (4,)])
            self.assertEqual(numbering.get_new_no(conn, "T"), 3)
            conn.rollback()

    def test_new_code(self):
        with tempfile.TemporaryDirectory() as tmp, shared_db.connect(fresh_db_path(Path(tmp))) as conn:
            conn.execute("CREATE TABLE M (管理番号 INTEGER, 整理番号 TEXT)")
            d = datetime(2024, 3, 5)
            self.assertEqual(numbering.get_new_code(conn, "M", "整理番号", d, ""), "20240305001")
            conn.executemany("INSERT INTO M VALUES (?, ?)", [(1, "20240305001"), (2, "20240305003")])
            self.assertEqual(numbering.get_new_code(conn, "M", "整理番号", d, ""), "20240305002")
            self.assertEqual(numbering.get_new_code(conn, "M", "整理番号", d, "20240305001"), "20240305001")
            conn.execute("INSERT INTO M VALUES (3, '20240305002')")
            self.assertEqual(numbering.get_new_code(conn, "M", "整理番号", d, ""), "20240305004")
            conn.rollback()


class HistoryTest(unittest.TestCase):
    def test_comp_str(self):
        self.assertEqual(history.comp_str("a,b,c", "b,c,d"), ("d", "a"))
        self.assertEqual(history.comp_str("x", "y"), ("y", "x"))
        self.assertEqual(history.comp_str("", "a"), ("a", ""))
        self.assertEqual(history.comp_str("a,b", "a,b"), ("", ""))


class AdoSqlTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.conn = shared_db.connect(fresh_db_path(Path(self.tmp.name)))
        self.addCleanup(self.conn.close)

    def test_question_then_addnew_update_delete(self):
        c = self.conn
        r = ado_sql.ado_sql(c, "タイプ", "新規", ["9", "TEST", ""])
        self.assertEqual((r.ok, r.reason, r.message), (False, "question", "新規登録しますか？"))
        r = ado_sql.ado_sql(c, "タイプ", "新規", ["9", "TEST", ""], confirmed=True, answer=False)
        self.assertEqual((r.ok, r.message), (False, "登録がキャンセルされました。"))
        r = ado_sql.ado_sql(c, "タイプ", "新規", ["9", "TEST", ""], confirmed=True, answer=True)
        self.assertEqual((r.ok, r.executed, r.message), (True, "ADDNEW", "データを新規登録しました！"))
        r = ado_sql.ado_sql(c, "タイプ", "新規", ["9", "TEST2", ""])
        self.assertEqual(r.message, "既に登録されているデータがあります。\n上書きしますか？")
        r = ado_sql.ado_sql(c, "タイプ", "修正", ["9", "TEST2", "x"], confirmed=True, answer=True)
        self.assertEqual((r.executed, r.message), ("UPDATE", "データを更新しました！"))
        self.assertEqual(c.execute("SELECT タイプ, 備考 FROM タイプ WHERE 管理番号=9").fetchone()[:], ("TEST2", "x"))
        r = ado_sql.ado_sql(c, "タイプ", "修正", ["99", "Z", ""])
        self.assertEqual(r.message, "登録データがありません。新規登録しますか？")
        r = ado_sql.ado_sql(c, "タイプ", "削除", ["9", "", ""])
        self.assertEqual(r.message, "登録データを削除しますか？")
        r = ado_sql.ado_sql(c, "タイプ", "削除", ["9", "", ""], confirmed=True, answer=True)
        self.assertEqual((r.executed, r.message), ("DELETE", "データを削除しました！"))
        r = ado_sql.ado_sql(c, "タイプ", "削除", ["9", "", ""])
        self.assertEqual(r.message, "登録されていないか、既に削除済みです。")

    def test_conflict_when_seen_differs(self):
        c = self.conn
        r = ado_sql.ado_sql(c, "タイプ", "修正", ["0", ">=", "changed"], confirmed=True, answer=True,
                            seen=["0", ">=", "somebody else"])
        self.assertEqual(r.reason, ado_sql.REFUSE_CONFLICT)
        r = ado_sql.ado_sql(c, "タイプ", "修正", ["0", ">=", "changed"], confirmed=True, answer=True,
                            seen=["0", ">=", "範囲指定:以上"])
        self.assertTrue(r.ok)

    def test_delete_only_first_duplicate_like_vba(self):
        c = self.conn
        c.execute("CREATE TABLE D (管理番号 INTEGER, v TEXT)")
        c.executemany("INSERT INTO D VALUES (?, ?)", [(1, "a"), (1, "b")])
        c.commit()
        r = ado_sql.ado_sql(c, "D", "削除", ["1", ""], confirmed=True, answer=True)
        self.assertTrue(r.ok)
        self.assertEqual(c.execute("SELECT v FROM D").fetchall()[0][0], "b")


class EditingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.conn = shared_db.connect(fresh_db_path(Path(self.tmp.name)))
        self.addCleanup(self.conn.close)
        self.setup = load_setup(self.conn)

    def test_form_new_and_edit(self):
        f = editing.build_form(self.conn, self.setup, "新規", "")
        self.assertEqual(f.key, "328")
        self.assertEqual(len(f.fields), 25)
        self.assertEqual(f.fields[0].value, "328")
        self.assertEqual(f.fields[3].spec.kind, "num")
        f = editing.build_form(self.conn, self.setup, "修正", "1")
        self.assertEqual(f.fields[1].value, "ﾊﾝﾏｰの取り扱い")
        self.assertEqual(f.keys[:3], ["1", "2", "3"])
        self.assertFalse(f.his_flag)

    def test_execute_update_flow(self):
        f = editing.build_form(self.conn, self.setup, "修正", "1")
        fields = editing.fields_from_payload(f, {"作業名": "改"})
        r = editing.execute(self.conn, self.setup, "修正", fields, user="U", pc="PC")
        self.assertEqual((r.ok, r.reason, r.message), (False, "question", "上書き登録しますか？"))
        r = editing.execute(self.conn, self.setup, "修正", fields, user="U", pc="PC", confirmed=True, answer=True)
        self.assertEqual((r.ok, r.executed), (True, "UPDATE"))
        row = self.conn.execute("SELECT 作業名, 登録者, 更新者 FROM 作業標準管理 WHERE 管理番号=1").fetchone()
        # 登録者・更新者 はどちらも #UPUSER# なので修正でどちらも入る(設定どおり)
        self.assertEqual(row[:], ("改", "U", "U"))

    def test_not_modified_and_required(self):
        f = editing.build_form(self.conn, self.setup, "修正", "1")
        r = editing.execute(self.conn, self.setup, "修正", f.fields, user="U", pc="PC")
        self.assertEqual((r.reason, r.message), ("not_modified", editing.MSG_NOT_MODIFIED))
        f.fields[1].required = True
        f.fields[1].value = ""
        r = editing.execute(self.conn, self.setup, "修正", f.fields, user="U", pc="PC")
        self.assertEqual((r.reason, r.fields_ng, r.message), ("required", [2], editing.MSG_REQUIRED))

    def test_num_convert(self):
        f = editing.build_form(self.conn, self.setup, "修正", "1")
        f.fields[1].spec = input_types.parse("数値変換")
        f.fields[1].value = "１２３"
        f.fields[2].spec = input_types.parse("数値変換")
        f.fields[2].value = "abc"
        editing.num_convert(f.fields)
        self.assertEqual((f.fields[1].value, f.fields[2].value), ("123", "0"))

    def test_delete_resets_filters(self):
        f = editing.build_form(self.conn, self.setup, "削除", "1")
        r = editing.execute(self.conn, self.setup, "削除", f.fields, user="U", pc="PC", confirmed=True, answer=True)
        self.assertTrue(r.ok and r.reset_filters)
        self.assertIsNone(self.conn.execute("SELECT 1 FROM 作業標準管理 WHERE 管理番号=1").fetchone())

    def test_history_written(self):
        c = self.conn
        c.execute("CREATE TABLE H_作業名 (管理番号 INTEGER, モード TEXT, 元データ TEXT, 変更前 TEXT, 変更後 TEXT, 追加 TEXT, 削除 TEXT, 修正元 TEXT, 修正者 TEXT, 修正日 TEXT)")
        c.execute("UPDATE 作業標準管理_設定 SET 履歴='H_作業名' WHERE 項目名='作業名'")
        c.commit()
        setup = load_setup(c)
        f = editing.build_form(c, setup, "修正", "2")
        self.assertTrue(f.his_flag)
        fields = editing.fields_from_payload(f, {"作業名": "新しい名前"})
        r = editing.execute(c, setup, "修正", fields, user="U", pc="PC", confirmed=True, answer=True)
        self.assertTrue(r.ok)
        h = c.execute("SELECT * FROM H_作業名").fetchone()
        self.assertEqual(tuple(h[:9]), (1, "修正", "2", "ﾃﾞｨｽｸｸﾞﾗｲﾝﾀﾞｰの取り扱い", "新しい名前", "新しい名前", "ﾃﾞｨｽｸｸﾞﾗｲﾝﾀﾞｰの取り扱い", "PC", "U"))


if __name__ == "__main__":
    unittest.main()
