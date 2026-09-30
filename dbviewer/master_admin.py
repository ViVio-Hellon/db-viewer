"""マスタメンテナンス(VBA `MF2` の移植)

【誰がどの表を直せるか(VBA `MF2.UserForm_Initialize`)】
    Level 5 以上 … メインテーブル以外の全部
    Level 4      … さらに タイプ を除く
    Level 3      … さらに ID を除く
    それ未満     … フィルタ だけ
`H_*`(履歴)は見るだけ(新規・修正・削除のボタンを出さない)。

【並び(VBA `sortTblArr`)】
    ID, 全般設定, <メイン>, <メイン>_設定, フィルタ, フィルタ_設定, タイプ, 残りは表の順

【編集の決まり】
- 1 列目(TB1)が鍵。新規は `GetNewNo(表)` で採番して固定、修正も固定
- `<表>_設定` があれば(メインとタイプを除く一覧の中に)その入力形式で欄を作る
- 実行は `adoSQL(表, ADDNEW/UPDATE, 値, "", False, "[1列目]=<鍵>")`(重複時は修正になる)
- 削除は「削除しますか？」→ 全列が一致する行を `adoSQL DELETE`(先頭 1 行)→「削除しました。」
- 閉じると一覧のフィルタは消える(VBA は MF1 を作り直す)

【VBA との意図的な違い】
- VBA は編集欄が 12 個までで、13 列以上の表は直せなかった。Web は列数に上限を置かない
- VBA は値に `[` `]` があると `'値'` と引用符ごと保存していた(バグ)。Web はそのまま保存する
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any, Optional

from . import ado_sql, config, input_types, numbering
from .settings_tables import (SystemSetup, column_names, field_dic, read_table, table_exists,
                              table_names)

MSG_NO_TARGET_EDIT = "修正対象データがありません。"
MSG_NO_TARGET_DELETE = "削除対象データがありません。"
MSG_DELETE_ASK = "削除しますか？"
MSG_DELETE_CANCEL = "キャンセルしました。"
MSG_DELETE_DONE = "削除しました。"
MSG_NOT_EDITABLE = "この表はこのレベルでは直せません。"


def editable_tables(conn: sqlite3.Connection, setup: SystemSetup, level: int) -> list[str]:
    """VBA MF2 の L1 に並ぶ表(先頭の空欄は除く)。"""
    main = setup.general.main_table
    names = table_names(conn)
    if level >= 5:
        remove = {"", main}
    elif level >= 4:
        remove = {"", main, config.TYPE_TABLE}
    elif level >= 3:
        remove = {"", main, config.TYPE_TABLE, config.ID_TABLE}
    else:
        return [config.FLT_TABLE] if config.FLT_TABLE in names else []
    pool = {n: i for i, n in enumerate(names) if n not in remove}
    order = [config.ID_TABLE, config.SET_TABLE, main, main + config.SETTING_SUFFIX,
             config.FLT_TABLE, config.FLT_TABLE + config.SETTING_SUFFIX, config.TYPE_TABLE]
    out = []
    for n in order:
        if n in pool:
            out.append(n)
            pool.pop(n)
    out.extend(pool.keys())
    return out


def is_history(table: str) -> bool:
    return table.startswith(config.HISTORY_PREFIX)


@dataclass
class MasterRows:
    table: str
    columns: list[str]
    rows: list[list[str]]
    read_only: bool
    page_label: str = "1 / 1"


def rows_of(conn: sqlite3.Connection, table: str) -> MasterRows:
    """VBA `LV再描画`: 全行を 1 ページで。"""
    cols, rows = read_table(conn, table)
    return MasterRows(table, cols, rows, is_history(table))


@dataclass
class MasterField:
    no: int
    name: str
    value: str
    original: str
    spec: input_types.InputSpec
    is_key: bool = False

    def to_dict(self) -> dict[str, Any]:
        s = self.spec
        return {"no": self.no, "name": self.name, "value": self.value, "original": self.original,
                "input_type": s.raw, "kind": s.kind, "color": s.color, "locked": s.locked,
                "enabled": s.enabled, "lines": s.lines, "auto_tag": s.auto_tag,
                "form_name": s.form_name, "form_args": s.form_args, "date_limit": s.date_limit,
                "link_field": s.link_field, "link_func": s.link_func, "link_args": s.link_args,
                "num": {"mode": s.num_mode, "min": s.num_min, "max": s.num_max, "step": s.num_step},
                "master": s.master, "multi": s.multi, "message": s.message, "is_key": self.is_key,
                "required": False, "his_rec": "", "dsp_type": "", "ms_join": "", "color_rule": "",
                "link_display": "", "file_kind": "", "file_dir": "", "icons": [], "is_date": False}


def setting_table_for(conn: sqlite3.Connection, setup: SystemSetup, table: str) -> Optional[str]:
    """VBA `ラベル書き換え`: メインとタイプを除いた表の中に `<表>_設定` があればそれ。"""
    name = table + config.SETTING_SUFFIX
    pool = [n for n in table_names(conn) if n not in ("", setup.general.main_table, config.TYPE_TABLE)]
    return name if name in pool else None


def build_fields(conn: sqlite3.Connection, setup: SystemSetup, table: str, mode: str,
                 row: Optional[list[str]] = None) -> list[MasterField]:
    cols = column_names(conn, table)
    conv_name = setting_table_for(conn, setup, table)
    specs: dict[int, input_types.InputSpec] = {}
    if conv_name:
        ccols, crows = read_table(conn, conv_name)
        fj = field_dic(ccols)
        j = fj.get(config.INP_TYPE, 0)
        for k, r in enumerate(crows, start=1):
            specs[k] = input_types.parse(r[j - 1] if j else "")
    fields = []
    if mode == "ADDNEW":
        key = str(numbering.get_new_no(conn, table))
    for k, name in enumerate(cols, start=1):
        spec = specs.get(k, input_types.parse(""))
        if mode == "ADDNEW":
            val = key if k == 1 else ""
        else:
            val = row[k - 1] if row and k - 1 < len(row) else ""
        fields.append(MasterField(k, name, val, val, spec, is_key=(k == 1)))
    return fields


def click_filter(columns: list[str], row: list[str]) -> str:
    """VBA `GetClickDataSQLFORM`: 全列が一致する行。"""
    return " AND ".join(f"[{c}]='{str(v).replace(chr(39), chr(39) * 2)}'" for c, v in zip(columns, row))


def execute(conn: sqlite3.Connection, table: str, mode: str, values: list[str], key: str, *,
            confirmed: bool = False, answer: Optional[bool] = None,
            seen: Optional[list[str]] = None) -> ado_sql.AdoResult:
    """VBA `マスタ実行`。`mode` は ADDNEW / UPDATE。"""
    cols = column_names(conn, table)
    key_filter = f"[{cols[0]}]={key}" if _is_number(key) else f"[{cols[0]}]='{key.replace(chr(39), chr(39) * 2)}'"
    return ado_sql.ado_sql(conn, table, mode, values, "", key_filter=key_filter,
                           confirmed=confirmed, answer=answer, seen=seen)


def _is_number(text: str) -> bool:
    try:
        float(text)
        return True
    except ValueError:
        return False


def delete(conn: sqlite3.Connection, table: str, row: list[str], *,
           confirmed: bool = False, answer: Optional[bool] = None) -> ado_sql.AdoResult:
    """VBA `マスタ削除`。"""
    if not confirmed:
        return ado_sql.AdoResult(False, MSG_DELETE_ASK, ado_sql.REFUSE_QUESTION, record_found=True)
    if answer is False:
        return ado_sql.AdoResult(False, MSG_DELETE_CANCEL, ado_sql.REFUSE_CANCEL)
    cols = column_names(conn, table)
    r = ado_sql.ado_sql(conn, table, "DELETE", row, "", skip_msg=True, key_filter=click_filter(cols, row))
    if r.ok:
        r.message = MSG_DELETE_DONE
    return r
