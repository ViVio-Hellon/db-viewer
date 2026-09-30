"""一覧のフィルタ(VBA `GetSQLFilterStr` / `SetListBoxItems` / `ListBoxReset` /
`NonDuplist` / `convInpData` / `GetWidthArr` の移植)

画面のフィルタは `フィルタ` テーブルの行ごとに 1 つ。行の `タイプ` で
候補の作り方と WHERE の書き方が変わる:

    ""            … 一致。値が日付に読めれば `#日付#`、"-" を含むか日付でなければ `'文字列'`
    YYYY / YYYYMM … 年・年月。候補は日付を Format した値
    >= <= > <     … 範囲。候補は降順(VBA は配列を逆順に読む)
    それ以外      … マスタ名。候補はそのマスタの同名列、WHERE は `LIKE '%値%'`

【候補は「いまの一覧に出ている行」から作る】
VBA は `LV1Reflesh` のたびに、絞り込んだ結果(`DBclsM.Records`)から
重複を除いて各フィルタの候補を作り直す。押したフィルタ(`EventNo`)だけは
作り直さない(押した瞬間に選択が外れるため)。
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Optional

from . import config
from .settings_tables import FilterRow, field_dic, q, read_table, table_exists
from .vba_compat import (cdate, clean, date_add_month, format_date, is_date,
                         vba_sort_key)

COMPARE_TYPES = (">=", "<=", "<", ">")
DATE_TYPES = ("YYYY", "YYYYMM")


@dataclass
class FilterState:
    """画面のフィルタ 1 つ(VBA の L/FL/FLL コントロールにあたる)。"""

    no: int                     # 1 始まり(VBA の i)
    item: str                   # 列名(L.Tag / L.Caption)
    ftype: str                  # タイプ
    value: str = ""             # 選んでいる値(FL.Caption)。"" は未選択
    width: str = ""             # 指定幅

    @property
    def label(self) -> str:
        """VBA `SetListBoxItems` が付ける見出し(`項目 : 以上` など)。"""
        if self.ftype in COMPARE_TYPES:
            return f"{self.item} : {compare_label(self.ftype)}"
        if self.ftype in DATE_TYPES or self.ftype == "":
            return self.item
        return f"{self.item} : マスタ参照"

    @property
    def tip(self) -> str:
        if self.ftype in COMPARE_TYPES:
            return compare_label(self.ftype)
        if self.ftype in DATE_TYPES or self.ftype == "":
            return ""
        return "マスタ参照"

    @property
    def kind(self) -> str:
        if self.ftype in COMPARE_TYPES:
            return "compare"
        if self.ftype in DATE_TYPES:
            return "date"
        if self.ftype == "":
            return "equal"
        return "master"


def compare_label(ftype: str) -> str:
    return {">": "超える", ">=": "以上", "<=": "以下", "<": "未満"}.get(ftype, "")


def states_from_rows(rows: list[FilterRow], values: Optional[dict[int, str]] = None) -> list[FilterState]:
    """設定表の行 → 画面のフィルタ。空欄の行も番号を保ったまま残す(VBA と同じ)。"""
    values = values or {}
    return [FilterState(no=i, item=r.item, ftype=r.ftype, value=str(values.get(i, "") or ""),
                        width=r.width) for i, r in enumerate(rows, start=1)]


# ------------------------------------------------------------------
# WHERE(VBA GetSQLFilterStr)
# ------------------------------------------------------------------
def sql_filter_str(filters: list[FilterState], *, reset: bool = False) -> str:
    """VBA `GetSQLFilterStr`。戻り値は VBA が `MF1.SQL` に出す文字列そのもの。

    `reset` のときは VBA が全フィルタの表示を消すのと同じで、空を返す。
    **`mk` は VBA と同じくループの外で持つ**(一度 `'` になると、あとの
    フィルタも `'` のまま。これは VBA の癖で、意図的に残している)。
    """
    tmp = ""
    mk = "#"
    if reset:
        return ""
    for f in filters:
        istr = f.value
        if istr == "":
            continue
        tmp += ("" if tmp == "" else " AND ") + "[" + f.item + "]"
        ntype = f.ftype
        if ntype == "YYYY":
            tmp += f">={mk}{istr}/01/01{mk} AND [{f.item}] <={mk}{istr}/12/31{mk}"
        elif ntype == "YYYYMM":
            x = cdate(f"{istr[:4]}/{istr[-2:]}/01")
            if x is None:
                x = cdate(istr)
            tmp += f">={mk}{format_date(x, 'YYYY/MM/DD')}{mk} AND [{f.item}]"
            tmp += f"<{mk}{format_date(date_add_month(x, 1), 'YYYY/MM/DD')}{mk}"
        elif ntype in COMPARE_TYPES:
            d = cdate(istr)
            text = format_date(d, "YYYY/MM/DD") if d is not None else istr
            tmp += f"{ntype}{mk}{text}{mk}"
        elif ntype == "":
            if "-" in istr:
                mk = "'"
            elif not is_date(istr):
                mk = "'"
            tmp += f"={mk}{istr}{mk}"
        else:
            tmp += f" LIKE '%{istr}%'"
    return tmp


def escape_for_sqlite(where: str) -> str:
    """VBA は値の `'` をそのまま SQL に入れる(壊れる)。Web 版は `''` にする。

    リテラルの中だけを直す。`'` … `'` の内側にある `'` は VBA の文字列では
    見分けられないので、値の側(`FilterState.value`)で先に escape しておく
    (`sql_filter_str_for_sqlite`)。
    """
    return where


def sql_filter_pair(filters: list[FilterState], *, reset: bool = False) -> tuple[str, str]:
    """(VBA どおりの文字列, SQLite で実行する文字列)。"""
    vba = sql_filter_str(filters, reset=reset)
    escaped = [FilterState(f.no, f.item, f.ftype, f.value.replace("'", "''"), f.width) for f in filters]
    return vba, sql_filter_str(escaped, reset=reset)


# ------------------------------------------------------------------
# 候補(VBA SetListBoxItems / ListBoxReset / NonDuplist)
# ------------------------------------------------------------------
def non_dup_list(records: list[list[str]], col: int) -> list[str]:
    """VBA `NonDuplist(RecordArr, Listj, , , , True)`。

    - `Trim` した値で重複を見る。登録する鍵は `Clean(Trim(値))`
    - 空文字を最後に足す(blankFlag)
    - 文字コード順に並べ替える(QuickSort、Option Compare Binary)
    - 列番号が 0/-1 なら空(空文字だけ)
    """
    keys: dict[str, int] = {}
    if col > 0:
        for i, row in enumerate(records, start=1):
            raw = row[col - 1] if col - 1 < len(row) else ""
            buf = (raw or "").strip(" ")
            if buf not in keys:
                keys[clean(raw)] = i
    keys[""] = len(keys) + 1
    return sorted(keys.keys(), key=vba_sort_key)


def conv_inp_data(text: str, ftype: str) -> str:
    """VBA `convInpData`: YYYY / YYYYMM のときだけ日付を Format。"""
    if ftype in DATE_TYPES:
        d = cdate(text)
        if d is not None:
            return format_date(d, ftype)
    return text


def candidates(conn: sqlite3.Connection, f: FilterState, records: list[list[str]],
               result_fj: dict[str, int]) -> list[str]:
    """VBA `ListBoxReset` が ListBox に並べる値(先頭・末尾の空欄ダミーは除く)。

    `result_fj` は一覧 SQL の結果の列辞書(`fldj`)。フィルタ列が結果に無ければ
    VBA は `fldj(...) = 0` → 候補は空。
    """
    col = result_fj.get(f.item, 0)
    ftype = f.ftype
    if ftype in DATE_TYPES or ftype == "" or ftype in COMPARE_TYPES:
        data = non_dup_list(records, col)
        if not data:
            return []
        seq = list(reversed(data)) if ftype != "" else data
        seen: dict[str, int] = {"": 0}
        for i, v in enumerate(seq):
            key = conv_inp_data(v, ftype) if ftype not in COMPARE_TYPES else v
            if key not in seen:
                seen[key] = i
        return [k for k in seen if k != ""]
    # マスタ参照: マスタ表の同名列
    if not table_exists(conn, ftype):
        return []
    cols, rows = read_table(conn, ftype)
    fj = field_dic(cols)
    j = fj.get(ftype, 0)
    if j == 0:
        return []
    return [r[j - 1] for r in rows]


def all_candidates(conn: sqlite3.Connection, filters: list[FilterState],
                   records: list[list[str]], result_fj: dict[str, int],
                   event_no: int = 0) -> dict[int, Optional[list[str]]]:
    """VBA `SetListBoxItems`。`event_no` のフィルタは作り直さない(`None`)。"""
    out: dict[int, Optional[list[str]]] = {}
    for f in filters:
        if f.item == "":
            continue
        if event_no != 0 and f.no == event_no:
            out[f.no] = None
        else:
            out[f.no] = candidates(conn, f, records, result_fj)
    return out


# ------------------------------------------------------------------
# 幅(VBA GetWidthArr)
# ------------------------------------------------------------------
def width_array(widths: list[str], base_width: float) -> list[float]:
    """VBA `GetWidthArr`。数値指定はそのまま、空欄は残りを均等割り。

    指定の合計が全体を超えるときは、指定列を比率で縮め、空欄列には
    「全体 × 空欄数/列数」を均等に配る(VBA のとおり)。
    """
    n = len(widths)
    out: list[float] = [0.0] * n
    blanks = 0
    total = 0.0
    for i, w in enumerate(widths):
        try:
            v = float(w)
        except (TypeError, ValueError):
            blanks += 1
            out[i] = -1
            continue
        out[i] = v
        total += v
    if blanks > 0:
        if total > base_width:
            v_cnt = n - blanks
            v_width = base_width * (v_cnt / n)
            x_width = (base_width - v_width) / blanks
            for i, w in enumerate(widths):
                if out[i] != -1:
                    out[i] = float(w) / total * v_width
                else:
                    out[i] = x_width
        else:
            x_width = (base_width - total) / blanks
            for i in range(n):
                if out[i] == -1:
                    out[i] = x_width
    return out
