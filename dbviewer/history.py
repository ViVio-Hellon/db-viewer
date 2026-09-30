"""履歴テーブル(H_*)への追記(VBA `GetHisRecArr` / `CompStrTbx` / `hisrecFlag`)

_設定 の「履歴」に `H_xxx` と書かれた項目は、値が変わった登録のたびに
その表へ 10 列の行を足す:

    管理番号(採番) / モード / 元データ(メインの管理番号) / 変更前 / 変更後 /
    追加(差分) / 削除(差分) / 修正元(PC) / 修正者 / 修正日
"""
from __future__ import annotations

import sqlite3
from typing import Iterable

from . import config, numbering
from .settings_tables import ConvTable, table_names
from .vba_compat import now_text


def comp_str(before: str, after: str) -> tuple[str, str]:
    """VBA `CompStrTbx`: `,` 区切りの集合として (追加, 削除) を返す。

    `,` を含まない値は 1 要素。順序は登場順(Dictionary の順)。
    """
    arr_a = before.split(",") if "," in before else [before]
    arr_b = after.split(",") if "," in after else [after]
    inc: dict[str, int] = {}
    dec: dict[str, int] = {}
    for i, v in enumerate(arr_b):
        inc[v] = i
    for i, v in enumerate(arr_a):
        dec[v] = i
    for v in arr_a:
        inc.pop(v, None)
    for v in arr_b:
        dec.pop(v, None)
    return ",".join(inc.keys()), ",".join(dec.keys())


def his_rec_array(conn: sqlite3.Connection, table: str, exe_mode: str, source_key: str,
                  before: str, after: str, delete: bool, *, pc: str, user: str) -> list[str]:
    """VBA `GetHisRecArr`。10 要素。"""
    inc, dec = comp_str(before, after)
    return [
        str(numbering.get_new_no(conn, table)),
        exe_mode,
        source_key,
        before,
        "" if delete else after,
        inc,
        dec,
        pc,
        user,
        now_text(),
    ]


def hisrec_flag(conn: sqlite3.Connection, conv: ConvTable) -> bool:
    """VBA `hisrecFlag`: 履歴の設定が 1 つでもあり、その表が全部あるときだけ真。"""
    names = set(table_names(conn))
    flag = False
    for r in conv.rows:
        if r.his_rec != "":
            flag = True
            if r.his_rec not in names:
                return False
    return flag
