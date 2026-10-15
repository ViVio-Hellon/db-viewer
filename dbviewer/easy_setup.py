"""かんたん設定(0.8.0): 決めるのは「見る DB」と「列ごとの 表示・入力・印刷」だけ

VBA 版は 全般設定・ID・<メイン>_設定 を Access で開いて決まった書き方で打っていて、誰も設定できなかった。
0.8.0 は次だけにした:

    見る DB          … 設定「接続先」で選ぶ(ここでは今の DB を見せるだけ)
    列ごとの設定      … 表を選び、列ごとに 表示 / 入力(文字・コンボボックス・スピンボタン・カレンダー)/ 印刷
                         → DB の中の隠した表「ツールの設定」に表ごとに書く(simple_table.py)
    ログインを使う    … ID の表が無い DB は誰でも直せる。人とレベルを決めたいときだけ、自分を Level 5 で登録

全般設定・<表>_設定 は要らない。<表>_設定 は画面では使わず(0.9.0)、入力の種類を取り込んで消せる(`retire_vba`)。
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any

from . import config, shared_db, simple_table
from .logging_utils import get_logger
from .settings_tables import SystemSetup, q, table_exists, table_names, user_tables
from .vba_compat import to_narrow

log = get_logger("easy_setup")

WRITE_LEVEL = 5
ID_COLUMNS = (("番号", "INTEGER"), ("名前", "TEXT"), ("ID", "TEXT"), ("PC", "TEXT"), ("PASS", "TEXT"),
              ("Level", "TEXT"), ("備考", "TEXT"))

REFUSE_LEVEL = "level"
REFUSE_INPUT = "bad_input"
REFUSE_TABLE = "no_table"
REFUSE_HAS_ID = "has_id"
REFUSE_PASSWORD = "password"
REFUSE_FAILED = "failed"

MSG_LEVEL = f"設定を保存するには Level {WRITE_LEVEL} が必要です。"


@dataclass
class EasyResult:
    ok: bool
    message: str
    reason: str = ""
    backup: str = ""
    level: int = 0             # ログインを使い始めたときの新しいレベル


def _decl_types(conn: sqlite3.Connection, table: str) -> dict[str, str]:
    return {r[1]: (r[2] or "") for r in conn.execute(f"PRAGMA table_info({q(table)})")}


def state(conn: sqlite3.Connection, setup: SystemSetup, level: int, table: str = "") -> dict[str, Any]:
    tables = []
    for name in user_tables(conn):
        try:
            n = conn.execute(f"SELECT COUNT(*) FROM {q(name)}").fetchone()[0]
        except sqlite3.Error:
            n = -1
        tables.append({"name": name, "rows": n})
    names = [t["name"] for t in tables]
    table = table if table in names else setup.general.main_table
    types = _decl_types(conn, table)
    columns = [{"name": c.name, "type": types.get(c.name, ""), "show": c.show, "input": c.input,
                "step": c.step, "print": c.print} for c in simple_table.load(conn, table)]
    vba = vba_setting_tables(conn)
    try:
        db = str(shared_db.db_path())
    except Exception:                                   # noqa: BLE001 - 出せないだけ
        db = ""
    return {
        "db": db, "tables": tables, "table": table, "columns": columns,
        "inputs": [[k, label] for k, label in simple_table.INPUTS],
        "level": level, "can_write": level >= WRITE_LEVEL, "has_id": setup.has_id,
        # VBA 版の設定の表(<表>_設定)。画面では使わない。取り込んで消せる
        "vba_tables": vba,
        "from_vba": table + config.SETTING_SUFFIX in vba and not simple_table.has_settings(conn, table),
    }


def vba_setting_tables(conn: sqlite3.Connection) -> list[str]:
    """DB にある VBA 版の設定の表(<表>_設定)。"""
    return [n for n in table_names(conn) if n.endswith(config.SETTING_SUFFIX)]


def retire_vba(conn: sqlite3.Connection, *, level: int, user: str = "") -> EasyResult:
    """VBA 版の <表>_設定 を取り込んで消す。

    1. かんたん設定をまだ保存していない表は、<表>_設定 から作った設定(入力の種類)を「ツールの設定」に保存
    2. 書く前に本体の控えを取り、<表>_設定 の表をぜんぶ消す(1 つの取引)
    """
    if level < WRITE_LEVEL:
        return EasyResult(False, MSG_LEVEL, REFUSE_LEVEL)
    vba = vba_setting_tables(conn)
    if not vba:
        return EasyResult(False, "VBA 版の設定の表(_設定)はもうありません。", REFUSE_TABLE)
    try:
        backup = _backup(conn, "VBA 版の設定を消す前")
    except OSError as exc:
        return EasyResult(False, f"書く前の控えを取れなかったので、やめました: {exc}", REFUSE_FAILED)
    users = set(user_tables(conn))
    taken = []
    try:
        for name in vba:
            base = name[: -len(config.SETTING_SUFFIX)]
            if base in users and not simple_table.has_settings(conn, base):
                simple_table.save(conn, base, simple_table.load(conn, base), user=user)
                taken.append(base)
        conn.execute("BEGIN IMMEDIATE")
        for name in vba:
            conn.execute(f"DROP TABLE {q(name)}")
        conn.execute("COMMIT")
    except sqlite3.Error as exc:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        log.exception("VBA 版の設定を消せません")
        return EasyResult(False, f"消せませんでした: {exc}", REFUSE_FAILED, backup=backup)
    log.info("VBA 版の設定を消しました: %s(取り込み: %s)", vba, taken)
    msg = f"VBA 版の設定の表を消しました: {'・'.join(vba)}。"
    if taken:
        msg += f" 入力の種類は かんたん設定 に取り込みました({'・'.join(taken)})。"
    return EasyResult(True, msg, backup=backup)


def save_columns(conn: sqlite3.Connection, table: str, rows: list[dict[str, Any]], *, level: int,
                 user: str = "") -> EasyResult:
    if level < WRITE_LEVEL:
        return EasyResult(False, MSG_LEVEL, REFUSE_LEVEL)
    if table not in user_tables(conn):
        return EasyResult(False, f"[{table}] は設定できる表ではありません。", REFUSE_TABLE)
    settings, problem = simple_table.validate(conn, table, rows)
    if problem:
        return EasyResult(False, problem, REFUSE_INPUT)
    try:
        simple_table.save(conn, table, settings, user=user)
    except sqlite3.Error as exc:
        log.exception("列の設定を保存できません: %s", table)
        return EasyResult(False, f"保存できませんでした: {exc}", REFUSE_FAILED)
    log.info("列の設定を保存: %s(%d 列)", table, len(settings))
    shown = sum(1 for s in settings if s.show)
    return EasyResult(True, f"[{table}] の列の設定を保存しました(表示 {shown} / {len(settings)} 列)。")


def _backup(conn: sqlite3.Connection, why: str) -> str:
    from .master_admin import _backup as backup
    return backup(conn, why)


def start_login(conn: sqlite3.Connection, setup: SystemSetup, *, user_id: str, pc: str, name: str,
                password: str) -> EasyResult:
    """ID の表を作って、自分を Level 5 で登録する(このあとはログインで人とレベルを決める)。"""
    if setup.has_id or table_exists(conn, config.ID_TABLE):
        return EasyResult(False, "この DB には ID の表があります。人を足すのは マスタ管理 の ID で、"
                                 "Level 4 以上の人がします。", REFUSE_HAS_ID)
    if not user_id or not pc:
        return EasyResult(False, "この PC のログイン ID・PC 名が分かりません。", REFUSE_FAILED)
    if not password:
        return EasyResult(False, "パスワードを入れてください(次のログインから使います)。", REFUSE_PASSWORD)
    try:
        backup = _backup(conn, "ログインを使い始める前")
    except OSError as exc:
        return EasyResult(False, f"書く前の控えを取れなかったので、やめました: {exc}", REFUSE_FAILED)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(f"CREATE TABLE {q(config.ID_TABLE)} ({', '.join(f'{q(n)} {t}' for n, t in ID_COLUMNS)})")
        conn.execute(f"INSERT INTO {q(config.ID_TABLE)} ({', '.join(q(n) for n, _ in ID_COLUMNS)}) "
                     f"VALUES (?, ?, ?, ?, ?, ?, ?)",
                     (1, name or user_id, user_id, pc, to_narrow(password.lower()), str(WRITE_LEVEL),
                      "かんたん設定で登録"))
        conn.execute("COMMIT")
    except sqlite3.Error as exc:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        log.exception("ID の表を作れません")
        return EasyResult(False, f"書けませんでした: {exc}", REFUSE_FAILED, backup=backup)
    return EasyResult(True, f"{user_id} を Level {WRITE_LEVEL} で登録しました。次からはこの ID とパスワードで"
                            f"ログインします。人を足すときは マスタ管理 の ID で。", backup=backup, level=WRITE_LEVEL)
