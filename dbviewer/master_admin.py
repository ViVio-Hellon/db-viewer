"""表に列を足す(0.5.0。一覧の「＋ 列を追加」)

0.18.0 でマスタ管理の画面をなくした(同じ DB を一覧・マスタ管理・閲覧で別々に見せていただけだったので、
一覧に一本化した)。ここに残るのは **列の追加** と、書く前に本体の控えを取る `_backup` だけ。
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from . import config
from .logging_utils import get_logger
from .settings_tables import SystemSetup, column_names, q, table_exists
from .vba_compat import val_int

log = get_logger("master_admin")

def is_history(table: str) -> bool:
    return table.startswith(config.HISTORY_PREFIX)


# ------------------------------------------------------------------
# 列の追加(Web で足した)
# ------------------------------------------------------------------
"""
【なぜ要るか】脱 .accdb で本体が .sqlite3 になり、Access を開いて列を足すことができなくなった。

【列を追加】選んだ表の**末尾に**列を足す(SQLite の ALTER TABLE ADD COLUMN)。
- 本体(メインテーブル)に足すときは、`<メイン>_設定` があれば同じ名前の行を**同じ取引の中で**足す。
  (0.7.0 から本体と `_設定` は項目名で合わせるので、無くても動く。足した行は入力形式などが空 = 自由入力。
  あとでその行を直すか、かんたん設定で決める)
- `<表>_設定` を持つマスタも同じく、その表に行を足す
- 列が決まっている表(全般設定・_設定・H_ 履歴・タイプ・フィルタ)には足さない(システムが読む形)
- 書けるのは 編集可(表を持ってくると同じ。表の形を変えるため)。書く前に本体の控えを取る
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
REFUSE_FAILED = "failed"

MSG_COLUMN_LEVEL = "列を足すには 編集可 の権限が要ります(表の形を変えるため)。"


@dataclass
class ColumnResult:
    ok: bool
    message: str
    reason: str = ""
    column: str = ""
    setting_table: str = ""      # 一緒に行を足した設定の表
    setting_key: str = ""        # その行の 管理番号
    backup: str = ""


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
    # 0.9.0: <表>_設定 は使わない(列の設定は かんたん設定)。列を足しても行は足さない
    return None


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
        if conv and table_exists(conn, conv):
            key = _append_setting_row(conn, conv, name)
        else:
            conv = ""
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
        msg += f" [{conv}] にも行(番号 {key})を足しました。入力の種類などは 設定 → かんたん設定 で決められます。"
    return ColumnResult(True, msg, column=name, setting_table=conv or "", setting_key=key, backup=backup)


