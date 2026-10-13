"""1 行の登録・更新・削除(VBA `adoSQL` の移植)

VBA は ADO のレコードセットに `[鍵]='値'`(または呼び手が渡したフィルタ)を掛け、
行があるかどうかで **ADDNEW ↔ UPDATE を自動で切り替え**、確認の Msg.Box を出してから
トランザクションの中で書いていた。文言・順番はそのまま。

    モード      行なし → 聞く文                     行あり → 聞く文                          成功の文
    新規/ADDNEW 新規登録しますか？                   既に登録されているデータがあります。      データを新規登録しました！
                                                   上書きしますか？
    修正/UPDATE 登録データがありません。新規登録しますか？ 上書き登録しますか？             データを更新しました！
    削除/DELETE 登録されていないか、既に削除済みです。 登録データを削除しますか？             データを削除しました！
    キャンセル  登録がキャンセルされました。 / 削除がキャンセルされました。

【Web での聞き方】
Msg.Box は出せないので、`confirmed=False` で呼ぶと **聞く文を返して止まる**(`question`)。
画面が「はい」を受けたら `confirmed=True` で呼び直す。`skip_msg=True`(履歴の追記など)は
聞かずに書く。

【VBA の癖(そのまま)】
- UPDATE は 1 列目を除く全列を `'値'` で SET する(1 列目は鍵と決め打ち)
- ADDNEW は全列に `CStr(値)` を入れる
- DELETE はフィルタに合う **最初の 1 行だけ** 消す(`adoRs.Delete` は現在行のみ)
- 値は文字列で書く。SQLite の型親和性で INTEGER 列には数値として入る

【Web で足したこと】
- `seen`(画面が読んだときの行の値)を渡すと、いまの行と違っていれば書かずに断る
  (別の端末が先に直していた)。VBA には無い守りで、共有 sqlite3 を複数 PC で
  直接書く運用のため(判断事項 D6 / D8)
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Optional, Sequence

from .logging_utils import get_logger
from .settings_tables import column_names, q, _text

log = get_logger("ado_sql")

REFUSE_CONFLICT = "conflict"
REFUSE_QUESTION = "question"
REFUSE_CANCEL = "cancelled"
REFUSE_ERROR = "error"
MSG_CONFLICT = ("別の端末で先に変更されています。画面を読み込み直して、"
                "いまの内容を確かめてからやり直してください。")

_MSG = {
    "ADDNEW": ("登録がキャンセルされました。", "新規登録しますか？",
               "既に登録されているデータがあります。\n上書きしますか？", "データを新規登録しました！",
               "キーが重複しているため、登録がキャンセルされました。"),
    "UPDATE": ("登録がキャンセルされました。", "登録データがありません。新規登録しますか？",
               "上書き登録しますか？", "データを更新しました！", ""),
    "DELETE": ("削除がキャンセルされました。", "登録されていないか、既に削除済みです。",
               "登録データを削除しますか？", "データを削除しました！", ""),
}
_ALIAS = {"新規": "ADDNEW", "修正": "UPDATE", "削除": "DELETE"}


@dataclass
class AdoResult:
    ok: bool
    message: str = ""              # 画面に出す文(成功・断り・質問)
    reason: str = ""               # question / cancelled / conflict / error / ""
    executed: str = ""             # 実際にした操作 ADDNEW / UPDATE / DELETE / ""
    record_found: bool = False


def normalize_mode(mode: str) -> str:
    m = _ALIAS.get(mode, mode)
    if m not in _MSG:
        raise ValueError("正しい引数(モード)が入力されていません。")
    return m


def key_filter_of(columns: Sequence[str], key_field: str, values: Sequence[str]) -> str:
    """VBA `easyFlag` の既定フィルタ `[鍵]='値'`。鍵が無ければ 1 列目。"""
    key_no = columns.index(key_field) if key_field in columns else 0
    return f"{q(columns[key_no])}='{_esc(str(values[key_no]))}'"


def _esc(text: str) -> str:
    return text.replace("'", "''")


def _where(columns: Sequence[str], key_field: str, values: Sequence[str],
           key_filter: str) -> str:
    return key_filter if key_filter else key_filter_of(columns, key_field, values)


def find_rows(conn: sqlite3.Connection, table: str, where: str) -> list[tuple]:
    return conn.execute(f"SELECT rowid, * FROM {q(table)} WHERE {where} ORDER BY rowid").fetchall()


def ado_sql(conn: sqlite3.Connection, table: str, mode: str, values: Sequence[object],
            key_field: str = "", *, skip_msg: bool = False, key_filter: str = "",
            confirmed: bool = False, answer: Optional[bool] = None,
            seen: Optional[Sequence[object]] = None, update_all: bool = False) -> AdoResult:
    """`values` は表の列順(0 始まり)。`answer` は質問への返事(None = まだ聞いていない)。

    `update_all` … 修正で 1 列目も書く(1 列目が鍵でない表。行は `key_filter` の rowid で決める)。
    """
    exe = normalize_mode(mode)
    cancel_msg, no_rec_msg, has_rec_msg, done_msg, _ = _MSG[exe]
    columns = column_names(conn, table)
    vals = ["" if v is None else str(v) for v in values]
    if len(vals) < len(columns):
        vals += [""] * (len(columns) - len(vals))
    where = _where(columns, key_field, vals, key_filter)
    rows = find_rows(conn, table, where)
    no_rec = not rows

    # 質問(VBA の Msg.Box)。skip_msg なら「はい」扱い
    if not skip_msg and not confirmed:
        if exe == "DELETE":
            if no_rec:
                return AdoResult(False, no_rec_msg, REFUSE_CANCEL, record_found=False)
            return AdoResult(False, has_rec_msg, REFUSE_QUESTION, record_found=True)
        return AdoResult(False, no_rec_msg if no_rec else has_rec_msg, REFUSE_QUESTION,
                         record_found=not no_rec)
    if not skip_msg and confirmed and answer is False:
        return AdoResult(False, cancel_msg, REFUSE_CANCEL, record_found=not no_rec)

    # 実際にする操作
    if exe in ("ADDNEW", "UPDATE"):
        real = "ADDNEW" if no_rec else "UPDATE"
    else:
        real = "" if no_rec else "DELETE"
    if real == "":
        return AdoResult(False, no_rec_msg, REFUSE_CANCEL, record_found=False)

    # 見ていた行と違えば書かない(Web で足した守り)
    if seen is not None and rows:
        current = [_text(v) for v in rows[0][1:]]
        seen_text = ["" if v is None else str(v) for v in seen]
        if current[:len(seen_text)] != seen_text[:len(current)]:
            log.warning("競合: %s %s を別の端末が先に変更", table, where)
            return AdoResult(False, MSG_CONFLICT, REFUSE_CONFLICT, record_found=True)

    try:
        conn.execute("BEGIN IMMEDIATE")
        if real == "ADDNEW":
            marks = ", ".join("?" for _ in columns)
            conn.execute(f"INSERT INTO {q(table)} ({', '.join(q(c) for c in columns)}) VALUES ({marks})",
                         vals[:len(columns)])
        elif real == "UPDATE" and update_all:
            sets = ", ".join(f"{q(c)}=?" for c in columns)
            conn.execute(f"UPDATE {q(table)} SET {sets} WHERE rowid = ?", [*vals[:len(columns)], rows[0][0]])
        elif real == "UPDATE":
            sets = ", ".join(f"{q(c)}=?" for c in columns[1:])
            conn.execute(f"UPDATE {q(table)} SET {sets} WHERE {where}", vals[1:len(columns)])
        else:
            conn.execute(f"DELETE FROM {q(table)} WHERE rowid = ?", (rows[0][0],))
        conn.execute("COMMIT")
    except sqlite3.Error as exc:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        log.exception("adoSQL 失敗: %s %s", table, real)
        return AdoResult(False, f"エラー番号  :{type(exc).__name__}\nエラーの内容:{exc}", REFUSE_ERROR)
    log.info("adoSQL: %s %s (%s)", real, table, where)
    return AdoResult(True, "" if skip_msg else done_msg, "", executed=real, record_found=not no_rec)
