"""一覧の読み側(VBA `LV1Reflesh` / `LabelDblClickEvents` / `GetTableArr` の移植)

- (画面では使っていない。VBA の一覧と同じ結果になることの記録として、試験から呼ぶ)
- `list_view()`  … 一覧 SQL を組み、全行を読み(候補づくりのため)、ページぶんを切り出す
- `summary()`    … 集計モード(列の値 × 件数)
- `table_array()`… 出力・帳票用の 2 次元配列(1 行目が列名)
"""
from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass, field
from typing import Optional

from . import config, filters as flt, sql_builder
from .settings_tables import SystemSetup, field_dic, q, _text

# VBA `DBconnection.PageRows_` の既定。画面は高さから計算していたが(17.5pt/行)、
# Web 版は利用者が選ぶ(既定 24 行)
DEFAULT_PAGE_ROWS = 24


class ListError(RuntimeError):
    """VBA が Msg.Box で断っていた内容。文言はそのまま画面に出す。"""


@dataclass
class ListPage:
    vba_sql: str
    sql: str
    where_vba: str
    columns: list[str]
    fj: dict[str, int]
    all_rows: list[list[str]]
    page_rows: list[list[str]]
    page_no: int
    page_count: int
    row_count: int
    per_page: int
    candidates: dict[int, Optional[list[str]]] = field(default_factory=dict)


def run_query(conn: sqlite3.Connection, sql: str) -> tuple[list[str], list[list[str]]]:
    """SQL を流して (列名, 全行) を文字列で返す(VBA `DBconnection.init`)。"""
    try:
        cur = conn.execute(sql)
    except sqlite3.Error as exc:
        raise ListError(
            "エラーが発生しました。\n下記SQL文内で指定するﾌｨｰﾙﾄﾞ名が、マスタに存在しない可能性があります。\n\n"
            f"{sql}\n({exc})") from exc
    columns = [d[0] for d in cur.description]
    rows = [[_text(v) for v in r] for r in cur.fetchall()]
    return columns, rows


def full_list(conn: sqlite3.Connection, setup: SystemSetup) -> tuple[list[str], list[list[str]]]:
    """一覧の全行(Web 版の一覧・帳票・出力の元)。

    VBA の CODESQL(マスタ結合・追加表示・金額の書式・並び・全般設定の初期フィルタ)は
    そのまま通す。画面での絞り込み・並べ替えは画面の中で行う(`grid_data`)。
    """
    built = sql_builder.build(setup.conv, setup.general.main_table,
                              init_filter=setup.general.init_filter)
    return run_query(conn, built.sql)


def list_view(conn: sqlite3.Connection, setup: SystemSetup, states: list[flt.FilterState], *,
              page_no: int = 1, per_page: int = DEFAULT_PAGE_ROWS, page_flag: bool = False,
              event_no: int = 0, reset: bool = False, sort_spec: str = "") -> ListPage:
    """VBA `LV1Reflesh(lv, GetSQLFilterStr(GetfltArr, reset), pageNo, PageFlag, EventNo)`。"""
    where_vba, where_sql = flt.sql_filter_pair(states, reset=reset)
    built_vba = sql_builder.build(setup.conv, setup.general.main_table, sql_filter=where_vba,
                                  init_filter=setup.general.init_filter, sort_spec=sort_spec)
    built = sql_builder.build(setup.conv, setup.general.main_table, sql_filter=where_sql,
                              init_filter=setup.general.init_filter, sort_spec=sort_spec)
    columns, rows = run_query(conn, built.sql)
    fj = field_dic(columns)

    # フィルタの候補は絞り込んだ結果から(押したフィルタは作り直さない)
    cands = flt.all_candidates(conn, states, rows, fj, event_no=event_no)

    # ページ。VBA: 行数制限なし(初期表示が空)でフィルタ無しなら、ページ切替以外は 1 ページ目
    no_filter = all(f.value == "" for f in states if f.item != "")
    if not setup.general.row_limit and no_filter:
        page_no = page_no if page_flag else 1
    per_page = max(1, int(per_page or DEFAULT_PAGE_ROWS))
    row_count = len(rows)
    page_count = max(1, math.ceil(row_count / per_page)) if row_count else 1
    page_no = 1 if row_count < per_page else min(max(1, int(page_no or 1)), page_count)
    start = (page_no - 1) * per_page
    page_rows = rows[start:start + per_page]
    return ListPage(vba_sql=built_vba.vba_sql, sql=built.sql, where_vba=where_vba,
                    columns=columns, fj=fj, all_rows=rows, page_rows=page_rows,
                    page_no=page_no, page_count=page_count, row_count=row_count,
                    per_page=per_page, candidates=cands)


# ------------------------------------------------------------------
# 集計モード(VBA LabelDblClickEvents)
# ------------------------------------------------------------------
@dataclass
class SummaryView:
    vba_sql: str
    columns: list[str]
    rows: list[list[str]]


def summary(conn: sqlite3.Connection, setup: SystemSetup, item: str) -> SummaryView:
    """フィルタ見出しのダブルクリック。**画面のフィルタは効かない**(VBA も WHERE 無し)。"""
    if "(" in item or ")" in item:
        raise ListError("SQL関数で使えない文字列を含んでいます。\n" + item)
    key = item
    for r in setup.conv.rows:
        if key in r.ms_join and "/" in r.ms_join:
            raise ListError("結合テーブルに対する集計機能は未対応です。")
    it = "[" + item + "]"
    main = setup.general.main_table
    sql = f"SELECT {it} ,COUNT({it}) AS [カウント] FROM [{main}] GROUP BY {it}"
    columns, rows = run_query(conn, sql)
    return SummaryView(vba_sql=sql, columns=columns, rows=rows)


# ------------------------------------------------------------------
# 出力・帳票用(VBA GetTableArr)
# ------------------------------------------------------------------
def table_array(conn: sqlite3.Connection, sql: str) -> list[list[str]]:
    """1 行目が列名、2 行目以降がデータ(VBA `GetTableArr(MasterPath, CODEStr)`)。"""
    columns, rows = run_query(conn, sql)
    return [list(columns)] + rows


def record_by_key(conn: sqlite3.Connection, table: str, key_value: object,
                  key_item: str = config.KEY_ITEM) -> Optional[list[str]]:
    """`SELECT * FROM [table] WHERE [管理番号] = n`(VBA `GetRecordsArr(..., "[管理番号] = " & keyNo)`)。"""
    cur = conn.execute(f"SELECT * FROM {q(table)} WHERE {q(key_item)} = ?", (key_value,))
    row = cur.fetchone()
    return [_text(v) for v in row] if row else None
