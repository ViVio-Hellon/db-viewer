"""採番(VBA `GetNewNo` / `GetNewCode`)"""
from __future__ import annotations

import sqlite3
from datetime import datetime

from . import config
from .settings_tables import field_dic, q, read_table
from .vba_compat import format_000, format_date, val_int


def get_new_no(conn: sqlite3.Connection, table: str, key_item: str = config.KEY_ITEM) -> int:
    """1 から順に見て、無い番号の最小(欠番があればそれ)。空なら 1。

    VBA は `Val(管理番号)` の集合に対して `1..件数+1` を探す。
    """
    cols, rows = read_table(conn, table)
    if not rows:
        return 1
    fj = field_dic(cols)
    # 管理番号 列が無い表(ID の「番号」など)は 1 列目を番号とみなす。
    # VBA はここで添字エラーになっていた(マスタ管理の新規で落ちる)ので、
    # 1 列目を鍵にするマスタ管理の作り(TB1 = 鍵)にそろえる
    k = fj.get(key_item, 0) or 1
    used = {str(val_int(r[k - 1])) for r in rows}
    for i in range(1, len(rows) + 2):
        if str(i) not in used:
            return i
    return len(rows) + 1


def get_new_code(conn: sqlite3.Connection, main_table: str, code_field: str,
                 target_date: datetime, now_code: str) -> str:
    """日付 + 3 桁連番。同じ日のコードを集め、欠番があればそれ、無ければ最大+1。

    いま登録済みの自分のコード(`now_code`)はそのまま許す。
    """
    dstr = format_date(target_date, "YYYYMMDD")
    cols, rows = read_table(conn, main_table, where=f"{q(code_field)} LIKE '{dstr}%'")
    if not rows:
        return dstr + "001"
    fj = field_dic(cols)
    j = fj.get(code_field, 0)
    ck: dict[str, int] = {}
    xnum = 0
    for i, r in enumerate(rows, start=1):
        code = r[j - 1] if j else ""
        dnum = val_int(code[-3:])
        ck[str(dnum)] = i
        if xnum < dnum:
            xnum = dnum
    for i in range(1, xnum + 2):
        if str(i) not in ck or dstr + format_000(i) == now_code:
            xnum = i
            break
    return dstr + format_000(xnum)
