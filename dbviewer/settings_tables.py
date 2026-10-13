"""設定テーブル(全般設定 / <main>_設定 / フィルタ / タイプ / ID)の読み込みと整合性検査

VBA 版は起動のたびに `DBconnection` で各表を配列に読み、`MF1.UserForm_Initialize`
と `FormInitialize` で次の順に確かめていた:

    1. 全般設定 → メインテーブル名・初期表示・初期フィルタ・帳票・テーブル閲覧
    2. CheckColumns … 各表の 2 列目の列名が表名と同じか(除外: "", ID, 全般設定, フィルタ,
       メイン, *_設定, H_*)。NG があれば案内(起動は止めない)
    3. MasterFileCheck … メインと _設定 の項目数・項目名の一致、フィルタ列の存在。
       NG なら「起動を中止」
    4. formatCheck … _設定 に 11 列がそろっているか(無ければ案内だけ)

Web 版も同じ順・同じ文言で確かめ、結果を設定タブ「いまの状態」と一覧画面に出す。

【0.7.0 から: 設定が無い・合わない DB でも開く(かんたん設定)】
VBA 版は 4 つの表がそろって、本体と `_設定` の項目数・並びが 1 つも違わないと起動を止めていた。
Web 版は**止めずに、足りないところを自動で補って開く**。直したいときは 設定 → かんたん設定 で選ぶ。

    全般設定 が無い・メインテーブル名が空・その表が無い
        → この PC で選んだ表、無ければ業務の表の 1 つ目を出す(`auto_main`)
    <メイン>_設定 が無い                 → どの列も自由入力(`auto_conv`)
    <メイン>_設定 と本体の数・並びが違う → **項目名で合わせる**。足りない列は自由入力、
                                           本体に無い項目は使わない(どちらも `notices` で知らせる)
    ID が無い                            → 閲覧だけでログイン(auth.py)
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any, Optional

from . import config, user_settings
from .vba_compat import clean


# ------------------------------------------------------------------
# 小さな道具
# ------------------------------------------------------------------
def q(name: str) -> str:
    """列名・表名を `[ ]` で囲む(VBA と同じ書き方。SQLite も受ける)。"""
    return "[" + str(name).replace("]", "]]") + "]"


def table_names(conn: sqlite3.Connection) -> list[str]:
    """VBA `GetTblDic`(ADOX の TABLE 型)に相当。SQLite の内部表は除く。"""
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY rowid")
    # 「表を持ってくる」の控えの表は Web 版だけのもの(VBA の DB には無い)
    return [r[0] for r in rows if r[0] != config.BROUGHT_REGISTRY]


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return name in table_names(conn)


def column_names(conn: sqlite3.Connection, table: str) -> list[str]:
    """VBA `GetFieldsArr` / `adoFields`。表の定義順。"""
    return [r[1] for r in conn.execute(f"PRAGMA table_info({q(table)})")]


# システムが読む表(業務の表ではない)。かんたん設定で「表示する表」の候補から外す
SYSTEM_TABLES = (config.ID_TABLE, config.SET_TABLE, config.FLT_TABLE, config.TYPE_TABLE)


def is_system_table(name: str) -> bool:
    return (name in SYSTEM_TABLES or name.endswith(config.SETTING_SUFFIX)
            or name.startswith(config.HISTORY_PREFIX))


def user_tables(conn: sqlite3.Connection) -> list[str]:
    """業務の表(システムの表・_設定・H_ 履歴を除く)。表の順。"""
    return [n for n in table_names(conn) if not is_system_table(n)]


def db_key(conn: sqlite3.Connection) -> str:
    """この接続の .sqlite3 のパス(この PC で選んだ表を覚えておく鍵)。"""
    row = conn.execute("PRAGMA database_list").fetchone()
    return str(row[2] or "") if row else ""


def picked_table(conn: sqlite3.Connection) -> str:
    """全般設定に決まりが無いとき、この PC で選んだ「表示する表」。"""
    picks = user_settings.get(config.KEY_MAIN_PICK, {}) or {}
    return str(picks.get(db_key(conn), "") or "") if isinstance(picks, dict) else ""


def pick_table(conn: sqlite3.Connection, table: str) -> None:
    picks = user_settings.get(config.KEY_MAIN_PICK, {}) or {}
    picks = dict(picks) if isinstance(picks, dict) else {}
    picks[db_key(conn)] = table
    user_settings.save(config.KEY_MAIN_PICK, picks)


def field_dic(columns: list[str]) -> dict[str, int]:
    """VBA `GetFieldDic`: 列名 → 1 始まりの列番号(Clean+Trim した名前で引く)。"""
    return {clean(c): i + 1 for i, c in enumerate(columns)}


def read_table(conn: sqlite3.Connection, table: str, *, where: str = "",
               order_by: str = "") -> tuple[list[str], list[list[str]]]:
    """`SELECT * FROM [table]`。値は VBA と同じく**すべて文字列**(NULL は "")。"""
    sql = f"SELECT * FROM {q(table)}"
    if where:
        sql += " WHERE " + where
    if order_by:
        sql += " ORDER BY " + order_by
    cur = conn.execute(sql)
    cols = [d[0] for d in cur.description]
    rows = [[_text(v) for v in r] for r in cur.fetchall()]
    return cols, rows


def _text(value: Any) -> str:
    """VBA `CStr(Fields(j).Value)`(NULL は "")。整数は小数点なしで。"""
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


# ------------------------------------------------------------------
# 全般設定
# ------------------------------------------------------------------
@dataclass
class GeneralSettings:
    main_table: str = ""
    # VBA `indicateRowLimFlag = (setStr <> "")`: 初期表示が空なら行数制限なし
    init_view: str = ""
    init_filter: str = ""            # fltDefolt
    report_str: str = ""
    browse_str: str = ""
    raw: dict[str, str] = field(default_factory=dict)

    @property
    def row_limit(self) -> bool:
        return self.init_view != ""

    @property
    def report_flag(self) -> bool:
        return self.report_str != ""

    @property
    def browse_flag(self) -> bool:
        return self.browse_str != ""

    @property
    def conv_table(self) -> str:
        """VBA `ConvTable`: `<メインテーブル名>_設定`。"""
        return self.main_table + config.SETTING_SUFFIX


def load_general(conn: sqlite3.Connection) -> GeneralSettings:
    """VBA `MF1.UserForm_Initialize` の 全般設定 の読み方そのまま。表が無ければ空。"""
    gs = GeneralSettings()
    if not table_exists(conn, config.SET_TABLE):
        return gs
    cols, rows = read_table(conn, config.SET_TABLE)
    fj = field_dic(cols)
    if not fj.get(config.ITEM_NAME) or not fj.get(config.PRESET):
        return gs
    for r in rows:
        name = r[fj[config.ITEM_NAME] - 1]
        value = r[fj[config.PRESET] - 1]
        gs.raw[name] = value
        if name == config.GS_MAIN_TABLE:
            gs.main_table = value
        elif name == config.GS_INIT_VIEW:
            gs.init_view = value
        elif name == config.GS_INIT_FILTER:
            gs.init_filter = value
        elif name == config.GS_REPORT:
            gs.report_str = value
        elif name == config.GS_TABLE_BROWSE:
            gs.browse_str = value
    return gs


# ------------------------------------------------------------------
# <main>_設定(コンバートテーブル)
# ------------------------------------------------------------------
@dataclass
class ConvRow:
    """_設定 の 1 行 = メインテーブルの 1 列の決まりごと。"""

    no: int                       # 管理番号(行番号としても使う)
    item: str                     # 項目名
    color: str = ""               # 文字色設定
    input_type: str = ""          # 入力形式
    reg_con: str = ""             # 登録条件("必須")
    dsp_type: str = ""            # 参照形式
    ms_join: str = ""             # マスタ結合
    his_rec: str = ""             # 履歴(H_ テーブル名)
    ind_type: str = ""            # 表示("金額")
    add_ind: str = ""             # 追加表示
    sort: str = ""                # ソート("ASC"/"DESC")


@dataclass
class ConvTable:
    table: str
    columns: list[str]
    rows: list[ConvRow]
    fj: dict[str, int]

    @property
    def count(self) -> int:
        return len(self.rows)

    def by_item(self, item: str) -> Optional[ConvRow]:
        for r in self.rows:
            if r.item == item:
                return r
        return None

    def format_ok(self) -> bool:
        """VBA `formatCheck`: 11 列すべてが引けるか。"""
        need = (config.KEY_ITEM, config.ITEM_NAME, config.CHAR_COLOR_SET, config.INP_TYPE,
                config.REG_CON, config.DSP_TYPE, config.MS_JOIN, config.HIS_REC,
                config.IND_TYPE, config.ADD_IND, config.SORT_FLD)
        return all(self.fj.get(n, 0) != 0 for n in need)


def load_conv(conn: sqlite3.Connection, conv_table: str) -> ConvTable:
    """`SELECT * FROM [<main>_設定]`。行の順は表の順(VBA も `SELECT *`)。"""
    cols, rows = read_table(conn, conv_table)
    fj = field_dic(cols)

    def g(r: list[str], name: str) -> str:
        i = fj.get(name, 0)
        return r[i - 1] if i else ""

    out = []
    for r in rows:
        out.append(ConvRow(
            no=int(float(g(r, config.KEY_ITEM) or 0)) if (g(r, config.KEY_ITEM) or "").strip() else 0,
            item=g(r, config.ITEM_NAME), color=g(r, config.CHAR_COLOR_SET),
            input_type=g(r, config.INP_TYPE), reg_con=g(r, config.REG_CON),
            dsp_type=g(r, config.DSP_TYPE), ms_join=g(r, config.MS_JOIN),
            his_rec=g(r, config.HIS_REC), ind_type=g(r, config.IND_TYPE),
            add_ind=g(r, config.ADD_IND), sort=g(r, config.SORT_FLD)))
    return ConvTable(conv_table, cols, out, fj)


# ------------------------------------------------------------------
# フィルタ
# ------------------------------------------------------------------
@dataclass
class FilterRow:
    no: int
    item: str          # フィルタ(メインテーブルの列名)
    ftype: str         # タイプ(>=, <=, >, <, YYYY, YYYYMM, "" または マスタ名)
    width: str         # 指定幅
    note: str = ""


def load_filters(conn: sqlite3.Connection) -> list[FilterRow]:
    """VBA `GetfltArr`: `WHERE [フィルタ] IS NOT NULL ORDER BY 管理番号`。

    空文字の行も残る(NULL ではない)。画面に出すかどうかは `item != ""`。
    """
    cols, rows = read_table(conn, config.FLT_TABLE,
                            where=f"{q(config.FILTER_ITEM)} IS NOT NULL",
                            order_by=q(config.KEY_ITEM))
    fj = field_dic(cols)

    def g(r, name):
        i = fj.get(name, 0)
        return r[i - 1] if i else ""

    out = []
    for r in rows:
        no_text = g(r, config.KEY_ITEM)
        out.append(FilterRow(no=int(float(no_text)) if no_text.strip() else 0,
                             item=g(r, config.FILTER_ITEM), ftype=g(r, config.FILTER_TYPE),
                             width=g(r, "指定幅"), note=g(r, "備考")))
    return out


def align_conv(conv: ConvTable, main_cols: list[str], *, exists: bool = True) -> tuple[ConvTable, list[str]]:
    """`_設定` を本体の列の並びに**項目名で**合わせる。(合わせた表, 知らせること)

    本体の列ごとに、同じ項目名の行(はじめの 1 つ)を使う。無ければ空の行(= 自由入力)。
    本体に無い項目の行は使わない。並びが違っても、名前が合っていればそのまま動く。
    """
    by_name: dict[str, ConvRow] = {}
    for r in conv.rows:
        by_name.setdefault(r.item, r)
    rows = [by_name.get(c) or ConvRow(i, c) for i, c in enumerate(main_cols, start=1)]
    notices: list[str] = []
    if not exists:
        if main_cols:
            notices.append(f"[{conv.table}] が無いので、どの列も自由入力で動いています。"
                           f"入力の種類・必須などは 設定 → かんたん設定 で決められます。")
        return ConvTable(conv.table, conv.columns, rows, conv.fj), notices
    missing = [c for c in main_cols if c not in by_name]
    extra = [r.item for r in conv.rows if r.item not in set(main_cols)]
    if missing:
        notices.append(f"[{conv.table}] に無い列(自由入力で動きます): {'、'.join(missing)}")
    if extra:
        notices.append(f"[{conv.table}] にあって本体に無い項目(使いません): {'、'.join(x or '(空)' for x in extra)}")
    return ConvTable(conv.table, conv.columns, rows, conv.fj), notices


# ------------------------------------------------------------------
# 整合性検査(VBA と同じ文言)
# ------------------------------------------------------------------
def check_columns(conn: sqlite3.Connection, exclusions: set[str], k: int = 2) -> list[str]:
    """VBA `DBconnection.CheckColumns`。NG の表があれば案内文(行の一覧)を返す。

    戻り値は VBA の Msg.Box に出していた行そのもの。NG が無ければ空。
    """
    lines: list[str] = []
    ng = False
    for name in table_names(conn):
        if name in exclusions:
            continue
        cols = column_names(conn, name)
        col = cols[k - 1] if len(cols) >= k else ""
        if col == name:
            lines.append(f"OK　ﾃｰﾌﾞﾙ名:{name}, ｶﾗﾑ{k}名称:{col}")
        else:
            lines.append(f"NG　ﾃｰﾌﾞﾙ名:{name}, ｶﾗﾑ{k}名称:{col}")
            ng = True
    if not ng:
        return []
    lines.append(f"ﾃｰﾌﾞﾙ名と同じｶﾗﾑ名を{k}列目に設定してください。想定外の挙動になる可能性があります。")
    return lines


def exclusion_set(conn: sqlite3.Connection, main_table: str) -> set[str]:
    """VBA `MF1.UserForm_Initialize` の除外表(excArr)。"""
    ex = {"", config.ID_TABLE, config.SET_TABLE, config.FLT_TABLE, main_table}
    for name in table_names(conn):
        if name.endswith(config.SETTING_SUFFIX) or name.startswith(config.HISTORY_PREFIX):
            ex.add(name)
    return ex


def master_file_check(conn: sqlite3.Connection, table: str) -> str:
    """VBA `MasterFileCheck`。問題があれば文言、無ければ ""。"""
    conv_name = table + config.SETTING_SUFFIX
    if not table_exists(conn, table):
        return f"メインテーブル [{table}] がありません。"
    if not table_exists(conn, conv_name):
        return f"設定テーブル [{conv_name}] がありません。"
    main_cols = column_names(conn, table)
    fj = field_dic(main_cols)
    conv = load_conv(conn, conv_name)
    # VBA はここでフィルタ表の列も照合していた。Web の一覧はフィルタ表を使わない
    # (どの列でも絞り込める)ので、照合しない。項目数と並びの照合は今までどおり
    if conv.count != len(main_cols):
        return "メインテーブルとその設定テーブルで登録している項目数が一致していません。"
    for k, r in enumerate(conv.rows, start=1):
        if r.item != main_cols[k - 1]:
            return (f"不一致項目あり [{conv_name}]マスタ {k}番目の項目 "
                    f"{r.item} ≠ {main_cols[k - 1]}")
    return ""


# ------------------------------------------------------------------
# まとめて読む(起動時に 1 度、以後は要求ごと)
# ------------------------------------------------------------------
@dataclass
class SystemSetup:
    general: GeneralSettings
    conv: ConvTable                   # 本体の列の並びに合わせたもの(足りない列は自由入力)
    filters: list[FilterRow]
    main_columns: list[str]
    fj: dict[str, int]                # メインテーブルの列名 → 列番号(1 始まり)
    column_problems: list[str]        # CheckColumns の案内(空なら問題なし)
    master_problem: str               # 開けない理由(0.7.0 から: 表示する表が無いときだけ)
    format_ok: bool                   # formatCheck
    notices: list[str] = field(default_factory=list)   # 止めずに知らせること(かんたん設定で直せる)
    auto_main: bool = False           # 全般設定に決まりが無く、表示する表を自動で選んだ
    auto_conv: bool = False           # <メイン>_設定 が無い(どの列も自由入力)
    has_id: bool = True               # ID の表がある(無ければ閲覧だけ)
    has_general: bool = True          # 全般設定 の表がある

    @property
    def ok(self) -> bool:
        return self.master_problem == ""

    @property
    def key_ok(self) -> bool:
        """本体に 管理番号 の列がある(一覧・登録で行を直す・足すのに要る)。"""
        return config.KEY_ITEM in self.main_columns

    @property
    def filter_count(self) -> int:
        """VBA `fltcnt`(= 設定表の行数、空欄の行も含む)。"""
        return len(self.filters)

    @property
    def active_filters(self) -> list[FilterRow]:
        """VBA `fltoncnt` の対象(項目名が空でない行)。"""
        return [f for f in self.filters if f.item != ""]


class SetupError(RuntimeError):
    pass


MSG_NO_TABLES = "表示できる表がありません(業務の表が 1 つもありません)。"


def resolve_main(conn: sqlite3.Connection, general: GeneralSettings) -> tuple[str, bool, list[str]]:
    """表示する表。(表, 自動で選んだか, 知らせること)

    全般設定の「メインテーブル名」があればそれ。無い・その表が無いときは、この PC で選んだ表、
    それも無ければ業務の表の 1 つ目。
    """
    if general.main_table and table_exists(conn, general.main_table):
        return general.main_table, False, []
    tables = user_tables(conn)
    notices = []
    if general.main_table:
        notices.append(f"全般設定の「メインテーブル名」の表 [{general.main_table}] がありません。")
    if not tables:
        raise SetupError(MSG_NO_TABLES if not general.main_table
                         else f"メインテーブル [{general.main_table}] がありません。")
    pick = picked_table(conn)
    table = pick if pick in tables else tables[0]
    notices.append(f"表示する表は [{table}] です(自動)。変えるときは 設定 → かんたん設定。")
    return table, True, notices


def load_setup(conn: sqlite3.Connection) -> SystemSetup:
    # 一覧を Web の表にしたので、フィルタ・タイプは要らなくなった(あっても読むだけ)
    has_general = table_exists(conn, config.SET_TABLE)
    general = load_general(conn)
    main, auto_main, notices = resolve_main(conn, general)
    general.main_table = main
    main_columns = column_names(conn, main)
    conv_exists = table_exists(conn, general.conv_table)
    raw_conv = (load_conv(conn, general.conv_table) if conv_exists
                else ConvTable(general.conv_table, [], [], {}))
    conv, conv_notices = align_conv(raw_conv, main_columns, exists=conv_exists)
    notices += conv_notices
    # CheckColumns は VBA の形の DB(全般設定がある)だけ。ほかの DB では関係ない
    column_problems = check_columns(conn, exclusion_set(conn, main), 2) if has_general else []
    filters = load_filters(conn) if table_exists(conn, config.FLT_TABLE) else []
    has_id = table_exists(conn, config.ID_TABLE)
    if not has_id:
        notices.append("ID の表が無いので、閲覧だけで動いています。直すには 設定 → かんたん設定 → 「編集を始める」。")
    if config.KEY_ITEM not in main_columns:
        notices.append(f"[{main}] に「{config.KEY_ITEM}」の列が無いので、一覧では行を直せません"
                       f"(かんたん設定で足せます)。")
    return SystemSetup(general=general, conv=conv, filters=filters,
                       main_columns=main_columns, fj=field_dic(main_columns),
                       column_problems=column_problems, master_problem="",
                       format_ok=raw_conv.format_ok() if conv_exists and raw_conv.rows else not conv_exists,
                       notices=notices, auto_main=auto_main, auto_conv=not conv_exists,
                       has_id=has_id, has_general=has_general)
