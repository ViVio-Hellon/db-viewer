"""一覧の表で 1 マスを直して、すぐ本体(.sqlite3)に書く

登録画面(`editing.execute`)と同じ決まりを通す: レベル・入力形式(自動項目・表示だけ・
入力制限)・数値変換・必須・日付の制限・自動項目(更新者・更新日)・履歴(H_)。
違うのは「確認の問い合わせをしない」ことだけ(押した時点で書く)。

ほかの端末が先に同じマスを変えていたら書かない(`CONFLICT`)。比べるのは、画面が
見ていた値と、いまの本体の値。
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any, Optional

from . import config, editing, records, sql_builder
from .settings_tables import SystemSetup
from .vba_compat import format_thousands, is_numeric, to_narrow

# 一覧の中で直せる欄の種類。ほかは登録画面で直す
INLINE_TEXT = ("free", "numconv")                       # その場で文字を打つ
INLINE_PICK = ("master", "calender", "num", "folder", "field")   # 選ぶ窓を開く
NOT_EDITABLE_WHY = {
    "auto": "自動で入る項目です(登録者・更新日など)。",
    "view": "表示だけの項目です。",
    "form": "専用フォームの項目です。修正画面で直してください。",
    "error": "入力形式の設定が正しくありません(ルール適用外)。",
}
MSG_DONE = "保存しました。"
MSG_CONFLICT = "他の端末が先にこのマスを変えていました。いまの値に置き換えたので、もう一度直してください。"
MSG_NOT_LIST_COLUMN = "この列は一覧で作った列(マスタ結合・追加表示)なので直せません。元の列を直してください。"


@dataclass
class CellResult:
    ok: bool
    message: str = ""
    reason: str = ""                      # not_editable / conflict / required / date_limit / ... / unchanged
    row: Optional[list[str]] = None       # 書いたあとの一覧の 1 行(画面はこれで置き換える)
    changed: dict[str, str] = field(default_factory=dict)


def edit_specs(conn: sqlite3.Connection, setup: SystemSetup) -> dict[str, dict[str, Any]]:
    """一覧の列ごとの「その場で直せるか」と、選ぶ窓に要る決まり(値は入れない)。"""
    form = editing.build_form(conn, setup, "新規", "")
    out: dict[str, dict[str, Any]] = {}
    for f in form.fields:
        d = f.to_dict()
        for k in ("value", "original", "icons", "is_date", "link_display"):
            d.pop(k, None)
        kind = f.spec.kind
        why = ""
        if f.is_key:
            why = "管理番号は直せません。"
        elif kind in NOT_EDITABLE_WHY:
            why = NOT_EDITABLE_WHY[kind]
        elif f.spec.link_field:
            why = "ほかの欄と連動する項目です。修正画面で直してください。"
        elif kind not in INLINE_TEXT + INLINE_PICK:
            why = "この形式は修正画面で直してください。"
        d["inline"] = "" if why else ("text" if kind in INLINE_TEXT else "pick")
        d["why"] = why
        # 一覧は 金額 を "#,##0"(丸め・カンマ)で見せている。直すときは本体の値から始める
        conv = setup.conv.by_item(f.name)
        d["money"] = bool(conv and conv.ind_type == "金額")
        out[f.name] = d
    return out


def list_row(conn: sqlite3.Connection, setup: SystemSetup, key: str) -> Optional[list[str]]:
    """一覧と同じ形(マスタ結合・追加表示・書式つき)の 1 行。"""
    built = sql_builder.build(setup.conv, setup.general.main_table,
                              sql_filter=f"[{config.KEY_ITEM}] = {int(key)}",
                              init_filter="")
    _cols, rows = records.run_query(conn, built.sql)
    return rows[0] if rows else None


def _same(a: str, b: str, money: bool) -> bool:
    """画面が見ていた値と本体の値が同じか。

    金額の列は、一覧では "#,##0"(カンマ・**整数に丸め**)で見せている。本体の値(1234.5)と
    見せた形(1,235)のどちらで来ても同じとみなす。以前はカンマだけ外して比べていたので、
    小数のある金額は「ほかの端末が変えた」と誤って断っていた。
    """
    a, b = a.replace("\r\n", "\n"), b.replace("\r\n", "\n")
    if a == b:
        return True
    if money:
        return a.replace(",", "") == b.replace(",", "") or format_thousands(a) == b
    return False


def money_value(value: str) -> str:
    """金額の欄に打った値。"1,500" のようにカンマ入りで打っても、**数として**書く。

    そのまま書くと "1,500" という文字で本体に入り、合計・並べ替え・帳票で数として扱えなくなる。
    数でないものは打ったまま(登録の決まりの数値変換などに任せる)。
    """
    plain = to_narrow(value.strip()).replace(",", "")
    return plain if plain and is_numeric(plain) else value


def raw_value(conn: sqlite3.Connection, setup: SystemSetup, key: str, column: str) -> Optional[str]:
    """本体の値(一覧の見せ方の前)。金額の列を直し始めるとき、丸めた数から始めないため。"""
    if not str(key).strip().lstrip("-").isdigit():
        return None
    form = editing.build_form(conn, setup, "修正", str(key))
    target = next((f for f in form.fields if f.name == column), None)
    return None if target is None else target.original


def save_cell(conn: sqlite3.Connection, setup: SystemSetup, key: str, column: str, value: str,
              seen: str, *, user: str, pc: str) -> CellResult:
    if not str(key).strip().lstrip("-").isdigit():
        return CellResult(False, editing.MSG_NOT_SELECTED, "not_selected")
    specs = edit_specs(conn, setup)
    if column not in specs:
        return CellResult(False, MSG_NOT_LIST_COLUMN, "not_editable")
    if specs[column]["why"]:
        return CellResult(False, specs[column]["why"], "not_editable")
    form = editing.build_form(conn, setup, "修正", str(key))
    target = next((f for f in form.fields if f.name == column), None)
    if target is None or not any(f.original for f in form.fields if f.is_key):
        return CellResult(False, "その行はもうありません(ほかの端末が消しました)。", "gone")
    conv = setup.conv.by_item(column)
    money = bool(conv and conv.ind_type == "金額")
    if not _same(target.original, seen, money):
        return CellResult(False, MSG_CONFLICT, "conflict", row=list_row(conn, setup, str(key)))
    if money:
        value = money_value(value)
    target.value = value
    if _same(target.original, value, False):
        return CellResult(True, "", "unchanged", row=list_row(conn, setup, str(key)))
    db_row = [f.original for f in form.fields]
    result = editing.execute(conn, setup, "修正", form.fields, user=user, pc=pc,
                             confirmed=True, answer=True, seen=db_row)
    if not result.ok:
        reason = result.reason or "error"
        row = list_row(conn, setup, str(key)) if reason == "conflict" else None
        return CellResult(False, result.message or "保存できませんでした。", reason, row=row)
    changed = {f.name: f.value for f in form.fields if f.value != f.original}
    return CellResult(True, MSG_DONE, "", row=list_row(conn, setup, str(key)), changed=changed)
