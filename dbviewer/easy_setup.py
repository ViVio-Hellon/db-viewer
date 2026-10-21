"""かんたん設定(0.8.0): 決めるのは「見る DB」と「列ごとの 表示・入力・印刷」だけ

VBA 版は 全般設定・ID・<メイン>_設定 を Access で開いて決まった書き方で打っていて、誰も設定できなかった。
0.8.0 は次だけにした:

    見る DB          … 設定「接続先」で選ぶ(ここでは今の DB を見せるだけ)
    列ごとの設定      … 表を選び、列ごとに 表示 / 入力(文字・コンボボックス・スピンボタン・カレンダー)/ 印刷
                         → DB の中の隠した表「ツールの設定」に表ごとに書く(simple_table.py)
    権限             … 0.22.0 から 設定ファイルの「アクセス権限」(編集可 / 閲覧のみ。access_rights.py)。
                         ID の表・パスワード・Level は使わない

全般設定・<表>_設定 は要らない。<表>_設定 は画面では使わず(0.9.0)、入力の種類を取り込んで消せる(`retire_vba`)。
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from typing import Any

from . import config, shared_db, simple_table
from .logging_utils import get_logger
from .settings_tables import SystemSetup, q, table_names, user_tables

log = get_logger("easy_setup")

WRITE_LEVEL = 5                 # 編集可(auth.EDIT_LEVEL)

REFUSE_LEVEL = "level"
REFUSE_INPUT = "bad_input"
REFUSE_TABLE = "no_table"
REFUSE_FAILED = "failed"

MSG_LEVEL = "設定を保存するには 編集可 の権限が要ります(設定ファイルの「アクセス権限」に 権限 = 編集可 の行)。"


@dataclass
class EasyResult:
    ok: bool
    message: str
    reason: str = ""
    backup: str = ""
    level: int = 0


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
                "step": c.step, "print": c.print, "click": c.click, "place": c.place}
               for c in simple_table.load(conn, table)]
    vba = vba_setting_tables(conn)
    try:
        db = str(shared_db.db_path())
    except Exception:                                   # noqa: BLE001 - 出せないだけ
        db = ""
    return {
        "db": db, "store": simple_table.store_info(conn), "sample": sample_row(conn, table),
        "tables": tables, "table": table, "columns": columns,
        "inputs": [[k, label] for k, label in simple_table.INPUTS],
        "clicks": [[k, label] for k, label in simple_table.CLICKS],
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


# ------------------------------------------------------------------
# 場所の決まりを試す(0.26.0。かんたん設定の「試す」)
# ------------------------------------------------------------------
def sample_row(conn: sqlite3.Connection, table: str) -> dict[str, str]:
    """例に使う行: 空でない値がいちばん多い先頭のほうの行(画面の「例」に出す)。"""
    if not table:
        return {}
    try:
        cur = conn.execute(f"SELECT * FROM {q(table)} LIMIT 50")
    except sqlite3.Error:
        return {}
    names = [d[0] for d in cur.description]
    best: dict[str, str] = {}
    for r in cur.fetchall():
        row = {n: simple_table.text_of(v).strip() for n, v in zip(names, r)}
        if sum(1 for v in row.values() if v) > sum(1 for v in best.values() if v):
            best = row
    return best


def try_place(conn: sqlite3.Connection, table: str, column: str, place: str, base) -> dict[str, Any]:
    """その場所の決まりで、表の行を使って本当に探してみる(保存はしない)。

    行は「押す列」に値がある先頭の行(最大 20 行まで試して、見つかった最初の行を出す)。"""
    from .settings_tables import column_names, table_exists
    if not table_exists(conn, table) or column not in column_names(conn, table):
        return {"ok": False, "message": "その表・列がありません。"}
    problem = simple_table._place_problem(place)
    unknown = [m for m in simple_table.row_marks(place) if m not in column_names(conn, table)]
    if problem or unknown:
        return {"ok": False, "message": problem or f"{{{unknown[0]}}} という列は [{table}] にありません。"}
    setting = simple_table.ColumnSetting(column, click=simple_table.CLICK_PREVIEW, place=place)
    cur = conn.execute(f"SELECT * FROM {q(table)} WHERE {q(column)} IS NOT NULL AND CAST({q(column)} AS TEXT) <> '' "
                       f"LIMIT 20")
    names = [d[0] for d in cur.description]
    rows = [dict(zip(names, r)) for r in cur.fetchall()]
    if not rows:
        return {"ok": False, "message": f"「{column}」に値の入った行がありません。"}
    from . import local_paths
    hits, misses, tried, moved = [], [], 0, ""
    for row in rows:
        value = simple_table.text_of(row.get(column)).strip()
        kind, target = simple_table.click_target(setting, value, row)
        if kind == "url":
            return {"ok": True, "kind": "url", "value": value, "target": target, "tried": 1, "hit": 1,
                    "message": f"URL として開きます: {target}"}
        if kind != "file":
            continue
        tried += 1
        path = simple_table.file_target(target, base)
        if path is not None:
            path, note = local_paths.reroute(path)            # 届かない \\PC名\… を この PC の同じフォルダに
            if note and not moved:
                moved = str(path.parent)
        found = simple_table.find_files(path) if path is not None else []
        item = {"value": value, "target": str(path),
                "found": [{"name": f.name, "folder": _rel(f.parent, path.parent)} for f in found]}
        (hits if found else misses).append((item, path))
    if not tried:
        return {"ok": False, "message": "開ける値の行がありません(URL でもファイルの場所でもない)。"}
    out: dict[str, Any] = {"ok": True, "kind": "file", "tried": tried, "hit": len(hits),
                           "message": f"先頭の {tried} 行で試しました: {len(hits)} 行で見つかりました。"}
    if moved:
        # 書いた場所に届かず、この PC の同じフォルダで探した → 場所をそちらに直す案を出す
        name = re.split(r"[\\\\/]", place)[-1]
        sep = "\\" if "\\" in moved else "/"
        out["suggest"] = moved.rstrip("\\/") + sep + name
        out["message"] += f" 書いた場所に届かないので、この PC の {moved} で探しました。場所をそちらに直してください。"
    if hits:
        out["example"] = hits[0][0]
    if misses:
        item, path = misses[0]
        item["why"] = simple_table.explain_missing(path)
        item["near"] = [{"name": f.name, "folder": _rel(f.parent, path.parent)} for f in simple_table.near_files(path)]
        out["missing"] = item
    return out


def _rel(path, base) -> str:
    try:
        rel = path.relative_to(base).as_posix()
    except ValueError:
        return str(path)
    return rel if rel != "." else "(このフォルダ)"
