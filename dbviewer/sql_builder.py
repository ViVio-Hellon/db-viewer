"""一覧の SELECT 文を組み立てる(VBA `CODESQL` / `GetStrMidArr` の移植)

VBA と同じ文字列を作る(`vba_sql`)。ゴールデンテストはこの文字列を VBA の
`MF1.SQL` と突き合わせる。SQLite で実行する文(`sql`)は、Access 方言の
日付リテラル `#yyyy/mm/dd#` だけを `VBADATE([col]) >= 'yyyy/mm/dd'` に直したもの
(`FORMAT(...)` は `shared_db.register_functions` が同名の関数を用意する)。

【CODESQL の決まり(VBA のまま)】
- SELECT 句は _設定 の順に `A.[項目名]`。表示="金額" なら `FORMAT(A.[項目名], '#,##0') AS 項目名`
- マスタ結合 `マスタ名/結合列/表示列[/金額]` は `LEFT JOIN [マスタ名] AS Bi ON A.[結合列] = Bi.[結合列]`
  (i は _設定 の行番号)と、`Bi.[表示列] AS 表示列` を項目名の**右隣**に足す
- 追加表示 `[挿入位置]/演算式/別名/表示変換` は演算式の `[` を `A.[` にして挿入位置の右隣に足す。
  挿入位置は **`[項目名]` の形でしか一致しない**(VBA の辞書の鍵が `[..]` 付きのため)。
  `/` 無しの書き方は項目名を裸で入れるので、VBA ではどこにも挿入されない。その癖ごと再現する
- ソートは _設定 で最後に指定のあった列(空なら 管理番号)。"DESC" 以外は ASC。
  `SORTSPEC` を渡されればそちら
- 初期フィルタ `列/演算子/値` は `[列] 演算子 '値'`。画面のフィルタがあれば `初期 AND 画面`
- WHERE の `[列]` には表の別名を補う(結合列は Bi、それ以外は A)。補ったあと
  `A.A.` / `B.iB.i` の重複を消す(VBA の癖)
- FROM は `FROM (((( [main]  AS A LEFT JOIN ... ON ... ) ...` と、結合数ぶん `(` を前置
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import config
from .settings_tables import ConvTable
from .vba_compat import normalize_date_text


@dataclass
class BuiltSql:
    vba_sql: str                       # VBA `CODESQL` が返す文字列
    sql: str                           # SQLite で実行する文
    select_items: list[str]            # SELECT 句の各項目(結合・追加表示込み)
    table_alias: dict[str, str]        # 列名 → 別名(A / Bi)
    sort_item: str
    sort_mode: str
    join_count: int
    where_vba: str = ""
    where_sql: str = ""
    columns: list[str] = field(default_factory=list)   # 結果の列名(左から)


_BE_ADD = "FORMAT("
_AF_ADD = ", '#,##0')"


def get_str_mid_arr(text: str) -> list[str]:
    """VBA `GetStrMidArr`: `[..]` の中身を順に。先頭に空要素が入る癖ごと。"""
    out = [""]
    if "[" in text:
        for part in text.split("[")[1:]:
            if "]" in part:
                out.append(part.split("]")[0])
    return out


def build(conv: ConvTable, main_table: str, *, sql_filter: str = "",
          init_filter: str = "", sort_spec: str = "",
          key_item: str = config.KEY_ITEM) -> BuiltSql:
    rows = conv.rows
    loopcnt = len(rows)
    item_str: list[str] = [""] * (loopcnt * 2 + 1)
    item_arr: list[str] = [""] * (loopcnt * 2 + 1)
    add_arr: list[list[str]] = [["", "", "", "", ""] for _ in range(loopcnt * 2 + 1)]
    item_dic: dict[str, int] = {}
    table_dic: dict[str, str] = {}
    join_code = ""
    cnt = 0
    n = 0

    # 1. マスタ結合
    for i in range(1, loopcnt + 1):
        r = rows[i - 1]
        item_str[i] = r.item
        item_dic["[" + r.item + "]"] = i
        ms = r.ms_join
        if "/" in ms:
            cnt += 1
            n += 1
            ms_arr = ms.split("/")
            t_b = "[" + ms_arr[0] + "]"
            bx = f"B{i}"
            left_join = f"LEFT JOIN {t_b} AS {bx} "
            on_code = f"ON A.[{ms_arr[1]}] = {bx}.[{ms_arr[1]}] ) "
            join_code += left_join + on_code
            table_dic[ms_arr[1]] = bx
            add_arr[cnt][1] = "[" + r.item + "]"
            add_arr[cnt][2] = f"{bx}.[{ms_arr[2]}]"
            table_dic[ms_arr[2]] = bx
            if len(ms_arr) > 3 and ms_arr[3] == "金額":
                add_arr[cnt][2] = _BE_ADD + add_arr[cnt][2] + _AF_ADD
            add_arr[cnt][2] += " AS " + ms_arr[2]

    # 2. 表示・ソート・追加表示
    # 既定の並びは 管理番号。その列が無い表(設定の無い DB)は 1 列目
    sort_str = key_item if any(r.item == key_item for r in rows) or not rows else rows[0].item
    sort_mode = "ASC"
    for i in range(1, loopcnt + 1):
        r = rows[i - 1]
        item_arr[i] = f"A.[{r.item}]"
        table_dic[item_arr[i]] = "A"
        if r.ind_type == "金額":
            item_arr[i] = _BE_ADD + item_arr[i] + _AF_ADD + " AS " + item_str[i]
        if r.sort != "":
            sort_str = r.item
            sort_mode = "DESC" if r.sort == "DESC" else "ASC"
        ind = r.add_ind
        if ind != "":
            cnt += 1
            if "/" in ind:
                ind_arr = ind.split("/")
                add_arr[cnt][1] = ind_arr[0]
                add_arr[cnt][2] = ind_arr[1]
                add_arr[cnt][3] = ind_arr[3] if len(ind_arr) > 3 else ""
                add_arr[cnt][4] = ind_arr[2] if len(ind_arr) > 2 else ""
            else:
                add_arr[cnt][1] = r.item
                add_arr[cnt][2] = ind
                add_arr[cnt][3] = ""
                add_arr[cnt][4] = ""
            add_arr[cnt][2] = add_arr[cnt][2].replace("[", "A.[")
            if add_arr[cnt][3] == "金額":
                add_arr[cnt][2] = _BE_ADD + add_arr[cnt][2] + _AF_ADD
            if add_arr[cnt][4] != "":
                add_arr[cnt][2] += " AS " + add_arr[cnt][4]

    # 3. 追加列を挿入位置の右隣へ
    x = 0
    for i in range(1, loopcnt + cnt + 1):
        if i > loopcnt:
            x += 1
            if add_arr[x][1] in item_dic:
                f = item_dic[add_arr[x][1]]
                item_arr[f] = item_arr[f] + "," + add_arr[x][2]

    select_items = [item_arr[i] for i in range(1, loopcnt + cnt + 1) if item_arr[i] != ""]
    sql_fld_item = ",".join(select_items)

    # 4. 初期フィルタと WHERE
    flt_default = init_filter
    defo_flag = flt_default != "" and "/" in flt_default
    if defo_flag:
        d = flt_default.split("/")
        defo_str = f"[{d[0]}] {d[1]} '{d[2]}'"
    else:
        defo_str = flt_default
    if sql_filter == "" and flt_default == "":
        where_code = ""
    elif sql_filter == "" and flt_default != "":
        sql_filter = defo_str
    elif sql_filter != "" and flt_default != "":
        sql_filter = defo_str + " AND " + sql_filter
    where_code = ""
    if not (sql_filter == "" and flt_default == ""):
        tmp = get_str_mid_arr(sql_filter)
        for name in tmp[1:]:
            field_name = "[" + name + "]"
            x_table = (table_dic[name] + ".") if name in table_dic else "A."
            sql_filter = sql_filter.replace(field_name, x_table + field_name)
        for _ in tmp[1:]:
            sql_filter = sql_filter.replace("A.A.", "A.")
            for j in range(1, loopcnt + 1):
                delstr = f"B.{j}"
                sql_filter = sql_filter.replace(delstr + delstr, delstr)
        where_code = "WHERE " + sql_filter + " "

    select_code = "SELECT " + sql_fld_item
    from_code = "FROM " + "(" * n + " [" + main_table + "] " + " AS A "
    if sort_spec != "":
        sort_mode = sort_spec
    order_by = f"ORDER BY A.[{sort_str}] {sort_mode} "
    vba_sql = select_code + " " + from_code + join_code + " " + where_code + order_by

    sqlite_where = to_sqlite(where_code)
    sql = select_code + " " + from_code + join_code + " " + sqlite_where + order_by
    return BuiltSql(vba_sql=vba_sql, sql=sql, select_items=select_items,
                    table_alias=table_dic, sort_item=sort_str, sort_mode=sort_mode,
                    join_count=n, where_vba=where_code, where_sql=sqlite_where,
                    columns=result_columns(select_items))


_DATE_LIT = re.compile(r"((?:[A-Za-z]\w*\.)?\[[^\]]+\])\s*(>=|<=|<>|=|<|>)\s*#([^#]*)#")


def to_sqlite(where_code: str) -> str:
    """Access の日付リテラルを SQLite 向けに直す。それ以外は触らない。"""
    def rep(m: re.Match) -> str:
        col, op, lit = m.group(1), m.group(2), m.group(3)
        return f"VBADATE({col}){op}'{normalize_date_text(lit)}'"
    return _DATE_LIT.sub(rep, where_code)


_AS_ALIAS = re.compile(r"\s+AS\s+(.+)$", re.I)


def result_columns(select_items: list[str]) -> list[str]:
    """SELECT 句から結果の列名を読む(`A.[x]` → x、`... AS y` → y)。

    1 項目に `,` で 2 列入っていることがある(結合・追加表示)ので、
    かっこの外の `,` で切る。
    """
    out: list[str] = []
    for item in select_items:
        for part in _split_top_level(item):
            part = part.strip()
            m = _AS_ALIAS.search(part)
            if m:
                out.append(m.group(1).strip().strip("[]"))
                continue
            mm = re.search(r"\[([^\]]+)\]\s*$", part)
            out.append(mm.group(1) if mm else part)
    return out


def _split_top_level(text: str) -> list[str]:
    parts, depth, cur = [], 0, []
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    parts.append("".join(cur))
    return parts
