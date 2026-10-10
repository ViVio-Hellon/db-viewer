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
- **列を追加・行を追加**(Web で足した)。VBA の頃は列を足すのに Access を開いていたが、
  脱 .accdb で .sqlite3 になったので、Access では足せなくなった。下の「列・行の追加」を参照
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from . import ado_sql, config, input_types, numbering
from .logging_utils import get_logger
from .settings_tables import (SystemSetup, column_names, field_dic, master_file_check, q, read_table,
                              table_exists, table_names)
from .vba_compat import val_int

log = get_logger("master_admin")

MSG_NO_TARGET_EDIT = "修正対象データがありません。"
MSG_NO_TARGET_DELETE = "削除対象データがありません。"
MSG_DELETE_ASK = "削除しますか？"
MSG_DELETE_CANCEL = "キャンセルしました。"
MSG_DELETE_DONE = "削除しました。"
MSG_NOT_EDITABLE = "この表はこのレベルでは直せません。"


def used_tables(conn: sqlite3.Connection, setup: SystemSetup) -> list[tuple[str, str]]:
    """マスタ管理に出す表と、出す理由。**いま使っている表だけ。**

    一覧を Web の表にしたので、フィルタ・フィルタ_設定・タイプは使わなくなった(出さない)。
    残るのは:
      ID                 … ログイン(誰が・どの PC で・どのレベルか)
      全般設定           … メインテーブル名・帳票・テーブル閲覧・初期フィルタ
      <メイン>_設定      … 登録画面の決まり(入力形式・必須・履歴・マスタ結合…)
      <メイン>_設定 が参照するマスタ(入力形式 s/m、マスタ結合)… 選ぶ窓の中身
      「表を持ってくる」で足した表
      <メイン>_設定 の「履歴」に書いた H_ 表(見るだけ)
    """
    from . import table_bring
    names = set(table_names(conn))
    main = setup.general.main_table
    out: list[tuple[str, str]] = []

    def add(name: str, why: str) -> None:
        if name and name in names and name != main and name not in {n for n, _ in out}:
            out.append((name, why))

    add(config.ID_TABLE, "ログイン")
    add(config.SET_TABLE, "メインテーブル名・帳票など")
    add(setup.general.conv_table, "登録画面の決まり")
    for r in setup.conv.rows:
        spec = input_types.parse(r.input_type)
        if spec.master:
            add(spec.master, f"「{r.item}」で選ぶマスタ")
        if "/" in (r.ms_join or ""):
            add(r.ms_join.split("/")[0], f"「{r.item}」に結合するマスタ")
    for name in sorted(table_bring.brought_tables(conn)):
        add(name, "表を持ってくるで足した表")
    for r in setup.conv.rows:
        if r.his_rec:
            add(r.his_rec, f"「{r.item}」の変更履歴(見るだけ)")
    return out


def editable_tables(conn: sqlite3.Connection, setup: SystemSetup, level: int) -> list[str]:
    """マスタ管理に並ぶ表。レベルの決まりは VBA MF2 と同じ:
    Level 5 は全部、4 は ID まで、3 は ID 以外、2 以下は直せる表なし
    (VBA の Level 2 以下はフィルタ表だけ直せたが、フィルタ表は使わなくなった)。"""
    if level < 3:
        return []
    tables = [n for n, _ in used_tables(conn, setup)]
    if level < 4:
        tables = [n for n in tables if n != config.ID_TABLE]
    return tables


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
    """VBA `GetClickDataSQLFORM`: 全列が一致する行。

    **空の欄は NULL とも一致させる。** 画面は NULL を "" で見せるので、`[備考]=''` だけだと
    Access から写した行(空の欄が NULL)は「登録されていないか、既に削除済みです」で消せなかった。
    """
    def one(c: str, v: str) -> str:
        text = str(v)
        if text == "":
            return f"([{c}]='' OR [{c}] IS NULL)"
        return f"[{c}]='{text.replace(chr(39), chr(39) * 2)}'"
    return " AND ".join(one(c, v) for c, v in zip(columns, row))


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


# ------------------------------------------------------------------
# 列・行の追加(Web で足した)
# ------------------------------------------------------------------
"""
【なぜ要るか】脱 .accdb で本体が .sqlite3 になり、Access を開いて列を足すことができなくなった。

【列を追加】選んだ表の**末尾に**列を足す(SQLite の ALTER TABLE ADD COLUMN)。
- 本体(メインテーブル)に足すときは、`<メイン>_設定` にも同じ名前の行を**同じ取引の中で**足す。
  本体の列と `_設定` の行は 1 対 1 で、数か並びが合わないと起動が止まる(VBA `MasterFileCheck`)。
  足した行は入力形式などが空(= 自由入力)。あとでその行を直して決める
- `<表>_設定` を持つマスタも同じく、その表に行を足す
- 列が決まっている表(全般設定・_設定・H_ 履歴・タイプ・フィルタ)には足さない(システムが読む形)
- 書けるのは Level 5(表を持ってくると同じ。表の形を変えるため)。書く前に本体の控えを取る

【行を追加】今までの「新規」(1 列目は自動の番号)。ただし `<メイン>_設定` に行を足すのは
**本体に項目(列)を足すこと**なので、本体にも同じ名前の列を足す(Level 5)。
`<メイン>_設定` の 項目名 の変更と行の削除は断る(本体の列と合わなくなり、起動が止まる)。
"""

COLUMN_LEVEL = 5
# 種類 → (SQLite の型, 画面の呼び名)
COLUMN_KINDS = {"text": ("TEXT", "文字"), "integer": ("INTEGER", "整数"), "number": ("REAL", "数値(小数も)")}
MAX_COLUMN_NAME = 64
_BAD_NAME = re.compile(r'[\[\]"`\x00-\x1f\x7f]')
# 列が決まっている表(システムが読む形)
_FIXED_TABLES = (config.SET_TABLE, config.TYPE_TABLE, config.FLT_TABLE)

REFUSE_LEVEL = "level"
REFUSE_FIXED = "fixed_columns"
REFUSE_NAME = "bad_name"
REFUSE_DUPLICATE = "duplicate"
REFUSE_FILL = "bad_fill"
REFUSE_NO_TABLE = "no_table"
REFUSE_RENAME_ITEM = "rename_item"
REFUSE_DELETE_ITEM = "delete_item"
REFUSE_NO_ROW = "no_row"
REFUSE_FAILED = "failed"

MSG_COLUMN_LEVEL = f"列を足すには Level {COLUMN_LEVEL} が必要です(表の形を変えるため)。"
MSG_ROWS_IN_LIST = "本体の行は一覧で直します(登録の決まり・履歴を通すため)。ここでは列の追加だけです。"


@dataclass
class ColumnResult:
    ok: bool
    message: str
    reason: str = ""
    column: str = ""
    setting_table: str = ""      # 一緒に行を足した設定の表
    setting_key: str = ""        # その行の 管理番号
    backup: str = ""


def listed_tables(conn: sqlite3.Connection, setup: SystemSetup, level: int) -> list[str]:
    """マスタ管理の左に並べる表。直せる表 + Level 5 なら本体(列の追加だけ)。並びは VBA `sortTblArr` と同じく
    ID, 全般設定, <メイン>, <メイン>_設定, …"""
    tables = editable_tables(conn, setup, level)
    main = setup.general.main_table
    if level >= COLUMN_LEVEL and main and table_exists(conn, main) and main not in tables:
        at = next((i for i, t in enumerate(tables) if t not in (config.ID_TABLE, config.SET_TABLE)), len(tables))
        tables.insert(at, main)
    return tables


def fixed_columns_why(setup: SystemSetup, table: str) -> str:
    """列を足せない表なら理由。足せるなら ""。"""
    if is_history(table):
        return "履歴の表は 10 列で決まっています。"
    if table == setup.general.conv_table:
        return (f"この表の列は決まっています(11 列)。項目を足すときは、本体({setup.general.main_table})に"
                f"列を追加するか、この表に行を追加してください。")
    if table.endswith(config.SETTING_SUFFIX) or table in _FIXED_TABLES:
        return "この表の列は決まっています(システムが読む形)。"
    return ""


def capabilities(setup: SystemSetup, table: str, level: int,
                 conn: Optional[sqlite3.Connection] = None) -> dict[str, Any]:
    """その表でできること(画面のボタンを出す・隠す)。判断はここ。

    `setting_table` … 列を足すと一緒に行を足す設定の表(画面で前もって知らせる)
    """
    main = setup.general.main_table
    fixed = fixed_columns_why(setup, table)
    can_columns = level >= COLUMN_LEVEL and not fixed
    setting = (_setting_table_of(conn, setup, table) or "") if conn is not None and can_columns else ""
    if table == main:
        return {"rows": False, "columns": can_columns, "note": MSG_ROWS_IN_LIST, "setting_table": setting}
    note = ""
    if table == setup.general.conv_table:
        note = (f"1 行 = 本体({main})の 1 列。行を追加すると本体にも同じ名前の列ができます"
                f"(Level {COLUMN_LEVEL})。項目名の変更と行の削除はできません。")
    elif is_history(table):
        note = "履歴は見るだけです。"
    elif fixed:
        note = fixed
    elif level < COLUMN_LEVEL:
        note = MSG_COLUMN_LEVEL
    return {"rows": not is_history(table), "columns": can_columns, "note": note, "setting_table": setting}


def validate_column_name(conn: sqlite3.Connection, setup: SystemSetup, table: str, name: str) -> str:
    """列名として使えるか。だめなら理由。"""
    if not name:
        return "列名を入れてください。"
    if name != name.strip():
        return "列名の前後に空白は入れられません。"
    if len(name) > MAX_COLUMN_NAME:
        return f"列名は {MAX_COLUMN_NAME} 字までにしてください。"
    if _BAD_NAME.search(name):
        return "列名に [ ] \" ` や改行は使えません。"
    have = {c.casefold() for c in column_names(conn, table)}
    if name.casefold() in have:
        return f"「{name}」はもうあります(大文字・小文字の違いは同じ名前とみなします)。"
    if table == setup.general.main_table:
        # 一覧では マスタ結合の列・追加表示の別名も列として並ぶ。同じ名前だと見分けられない
        for r in setup.conv.rows:
            joined = (r.ms_join or "").split("/")
            if len(joined) >= 3 and joined[2].strip().casefold() == name.casefold():
                return f"「{name}」は「{r.item}」のマスタ結合で一覧に出している名前です。"
            added = (r.add_ind or "").split("/")
            if len(added) >= 3 and added[2].strip().casefold() == name.casefold():
                return f"「{name}」は「{r.item}」の追加表示で一覧に出している名前です。"
    return ""


def _fill_value(kind: str, fill: str) -> tuple[Any, str]:
    """いまある行に入れる値。(値, だめなら理由)"""
    if fill == "":
        return None, ""
    if kind == "integer":
        try:
            return int(fill.replace(",", "")), ""
        except ValueError:
            return None, "整数の列なので、入れる値も整数にしてください。"
    if kind == "number":
        try:
            return float(fill.replace(",", "")), ""
        except ValueError:
            return None, "数値の列なので、入れる値も数値にしてください。"
    return fill, ""


def _db_file(conn: sqlite3.Connection) -> Optional[Path]:
    row = conn.execute("PRAGMA database_list").fetchone()
    path = row[2] if row else ""
    return Path(path) if path else None


def _backup(conn: sqlite3.Connection, why: str) -> str:
    from .table_bring import backup_copy
    path = _db_file(conn)
    if path is None or not path.is_file():
        return ""
    return str(backup_copy(path, why))


def _setting_table_of(conn: sqlite3.Connection, setup: SystemSetup, table: str) -> Optional[str]:
    """列を足すと一緒に行を足す設定の表。本体なら <メイン>_設定、ほかは <表>_設定(あれば)。"""
    if table == setup.general.main_table:
        return setup.general.conv_table
    return setting_table_for(conn, setup, table)


def _next_key(conn: sqlite3.Connection, table: str) -> tuple[str, str]:
    """(鍵の列, 次の番号)。設定の表は**並びが意味を持つ**ので、欠番は埋めずに最大 + 1。"""
    cols = column_names(conn, table)
    key_col = config.KEY_ITEM if config.KEY_ITEM in cols else cols[0]
    used = [val_int(r[0]) for r in conn.execute(f"SELECT {q(key_col)} FROM {q(table)}")]
    return key_col, str(max(used, default=0) + 1)


def _append_setting_row(conn: sqlite3.Connection, conv: str, item: str,
                        values: Optional[list[str]] = None) -> str:
    """設定の表の**末尾に**行を足す(本体の列の並びと合わせる)。足した行の番号を返す。"""
    cols = column_names(conn, conv)
    if config.ITEM_NAME not in cols:
        raise sqlite3.OperationalError(f"[{conv}] に「{config.ITEM_NAME}」の列がありません")
    key_col, key = _next_key(conn, conv)
    # 値が無ければ 番号と項目名だけ(ほかは空 = 自由入力)。画面で入れた値があればそれも
    row = dict(zip(cols, [str(v) for v in values])) if values else {}
    row[key_col] = key
    row[config.ITEM_NAME] = item
    names = list(row)
    conn.execute(f"INSERT INTO {q(conv)} ({', '.join(q(c) for c in names)}) "
                 f"VALUES ({', '.join('?' for _ in names)})", [row[c] for c in names])
    return key


def _check_main(conn: sqlite3.Connection, setup: SystemSetup) -> str:
    """取引の中で、本体と <メイン>_設定 がまだ合っているか(合わなければ戻す)。"""
    main = setup.general.main_table
    return master_file_check(conn, main) if main and table_exists(conn, main) else ""


def add_column(conn: sqlite3.Connection, setup: SystemSetup, table: str, name: str, *,
               kind: str = "text", fill: str = "", level: int = 0) -> ColumnResult:
    """表の末尾に列を足す。本体なら <メイン>_設定 にも行を足す(同じ取引)。"""
    name = str(name or "")
    if level < COLUMN_LEVEL:
        return ColumnResult(False, MSG_COLUMN_LEVEL, REFUSE_LEVEL)
    if not table or not table_exists(conn, table):
        return ColumnResult(False, f"表 [{table}] が見つかりません。", REFUSE_NO_TABLE)
    why = fixed_columns_why(setup, table)
    if why:
        return ColumnResult(False, why, REFUSE_FIXED)
    problem = validate_column_name(conn, setup, table, name)
    if problem:
        return ColumnResult(False, problem, REFUSE_DUPLICATE if "もうあります" in problem else REFUSE_NAME)
    if kind not in COLUMN_KINDS:
        return ColumnResult(False, "種類は 文字・整数・数値 のどれかにしてください。", REFUSE_NAME)
    value, problem = _fill_value(kind, str(fill or ""))
    if problem:
        return ColumnResult(False, problem, REFUSE_FILL)

    conv = _setting_table_of(conn, setup, table)
    try:
        backup = _backup(conn, "列を追加する前")
    except OSError as exc:
        return ColumnResult(False, f"書く前の控えを取れなかったので、やめました: {exc}", REFUSE_FAILED)
    key = ""
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(f"ALTER TABLE {q(table)} ADD COLUMN {q(name)} {COLUMN_KINDS[kind][0]}")
        if value is not None:
            conn.execute(f"UPDATE {q(table)} SET {q(name)} = ?", (value,))
        if conv:
            key = _append_setting_row(conn, conv, name)
        bad = _check_main(conn, setup)
        if bad:
            conn.execute("ROLLBACK")
            return ColumnResult(False, f"足すと起動できなくなるので、やめました: {bad}", REFUSE_FAILED, backup=backup)
        conn.execute("COMMIT")
    except sqlite3.Error as exc:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        log.exception("列の追加に失敗: %s %s", table, name)
        return ColumnResult(False, f"列を足せませんでした: {exc}", REFUSE_FAILED, backup=backup)
    log.info("列を追加: %s.%s (%s) 設定=%s", table, name, kind, conv or "-")
    msg = f"[{table}] に列「{name}」を足しました。"
    if conv:
        msg += f" [{conv}] にも行(番号 {key})を足しました。入力形式などはその行を直して決めます。"
    return ColumnResult(True, msg, column=name, setting_table=conv or "", setting_key=key, backup=backup)


def add_item(conn: sqlite3.Connection, setup: SystemSetup, values: list[str], *, level: int = 0,
             confirmed: bool = False, answer: Optional[bool] = None) -> ado_sql.AdoResult:
    """`<メイン>_設定` に行を足す = 本体に項目(列)を足す。行の値は画面で入れたもの。"""
    conv, main = setup.general.conv_table, setup.general.main_table
    if level < COLUMN_LEVEL:
        return ado_sql.AdoResult(False, f"項目(本体の列)を足すには Level {COLUMN_LEVEL} が必要です。", REFUSE_LEVEL)
    cols = column_names(conn, conv)
    fj = field_dic(cols)
    j = fj.get(config.ITEM_NAME, 0)
    item = str(values[j - 1]) if j and j - 1 < len(values) else ""
    problem = validate_column_name(conn, setup, main, item)
    if problem:
        return ado_sql.AdoResult(False, problem.replace("列名", "項目名"), REFUSE_NAME)
    if not confirmed:
        return ado_sql.AdoResult(False, f"「{item}」を {main} の列(項目)として足します。よろしいですか？",
                                 ado_sql.REFUSE_QUESTION)
    if answer is False:
        return ado_sql.AdoResult(False, MSG_DELETE_CANCEL, ado_sql.REFUSE_CANCEL)
    try:
        backup = _backup(conn, "項目を追加する前")
    except OSError as exc:
        return ado_sql.AdoResult(False, f"書く前の控えを取れなかったので、やめました: {exc}", REFUSE_FAILED)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(f"ALTER TABLE {q(main)} ADD COLUMN {q(item)} TEXT")
        key = _append_setting_row(conn, conv, item, values)
        bad = _check_main(conn, setup)
        if bad:
            conn.execute("ROLLBACK")
            return ado_sql.AdoResult(False, f"足すと起動できなくなるので、やめました: {bad}", REFUSE_FAILED)
        conn.execute("COMMIT")
    except sqlite3.Error as exc:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        log.exception("項目の追加に失敗: %s", item)
        return ado_sql.AdoResult(False, f"項目を足せませんでした: {exc}", REFUSE_FAILED)
    log.info("項目を追加: %s.%s(%s の行 %s)控え=%s", main, item, conv, key, backup)
    return ado_sql.AdoResult(True, f"項目「{item}」を足しました({main} に列、{conv} に行 {key})。",
                             "", executed="ADDNEW", record_found=False)


def guard_setting_change(conn: sqlite3.Connection, setup: SystemSetup, table: str, mode: str,
                         values: list[str], key: str) -> Optional[ado_sql.AdoResult]:
    """`<メイン>_設定` の 項目名 の変更・行の削除・無い行の修正を断る(本体と合わなくなる)。"""
    conv, main = setup.general.conv_table, setup.general.main_table
    if table != conv:
        return None
    if mode == "DELETE":
        return ado_sql.AdoResult(False, f"項目の行は削除できません。本体({main})の列と 1 対 1 で対応していて、"
                                        f"消すと起動できなくなります。", REFUSE_DELETE_ITEM)
    cols = column_names(conn, conv)
    fj = field_dic(cols)
    kj, ij = fj.get(config.KEY_ITEM, 1), fj.get(config.ITEM_NAME, 0)
    current = conn.execute(f"SELECT {q(cols[ij - 1]) if ij else 'NULL'} FROM {q(conv)} "
                           f"WHERE CAST({q(cols[kj - 1])} AS TEXT) = ?", (str(key),)).fetchone()
    if current is None:
        return ado_sql.AdoResult(False, "対象の行がありません(ほかの端末で変わったかもしれません)。", REFUSE_NO_ROW)
    new_item = str(values[ij - 1]) if ij and ij - 1 < len(values) else ""
    if ij and str(current[0] or "") != new_item:
        return ado_sql.AdoResult(False, f"項目名は変えられません。本体({main})の列名と 1 対 1 で対応していて、"
                                        f"変えると起動できなくなります。", REFUSE_RENAME_ITEM)
    return None
