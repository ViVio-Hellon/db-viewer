"""かんたん設定(0.7.0): 設定の表を手で書かずに、画面で選んで決める

VBA 版は 全般設定・ID・<メイン>_設定 を Access で開いて、決まった書き方の文字を打っていた。
入力形式は `calender@過去` や `s担当者` のような書き方を覚える必要があり、`_設定` の行の数・並びが
本体の列と 1 つでも違うと起動しなかった。ここでは:

    表示する表        … 業務の表から選ぶ(全般設定「メインテーブル名」に書く。無ければ表ごと作る)
    列ごとの決まり    … 入力の種類・必須・金額で見せる・並べる列 を選ぶ(<メイン>_設定 に書く。無ければ作る)
    編集を始める      … ID の表が無い DB で、自分を Level 5 で登録する(ID の表を作る)
    管理番号を足す    … 管理番号 の列が無い表に足す(一覧で行を直す・足すのに要る)

書くのは選んだところだけ。`_設定` のほかの列(参照形式・マスタ結合・履歴・追加表示・文字色設定)は触らない。
今までの書き方(`_設定` を マスタ管理 で直す)もそのまま使える。書く前に本体の控えを取る。
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any

from . import config, input_types
from .logging_utils import get_logger
from .settings_tables import SystemSetup, column_names, pick_table, q, table_exists, table_names, user_tables
from .vba_compat import to_narrow

log = get_logger("easy_setup")

WRITE_LEVEL = 5

# 入力の種類(画面の選択肢)。(値, 呼び名, マスタの表を選ぶか)。値は _設定「入力形式」にそのまま書く
INPUT_CHOICES: tuple[tuple[str, str, bool], ...] = (
    ("", "自由に入力", False),
    ("/3", "長い文(3 行)", False),
    ("数値変換", "数(打った文字を数にそろえる)", False),
    ("calender", "日付(カレンダーから)", False),
    ("calender@過去", "日付(今日まで)", False),
    ("calender@未来", "日付(今日から)", False),
    ("s", "表から 1 つ選ぶ", True),
    ("m", "表から いくつか選ぶ", True),
    ("#VIEW#", "見るだけ(直せない)", False),
    ("#NEWUSER#", "自動: 登録した人", False),
    ("#NEWDATE#", "自動: 登録した日時", False),
    ("#UPUSER#", "自動: 直した人", False),
    ("#UPDATE#", "自動: 直した日時", False),
)
CUSTOM = "custom"          # 上に無い書き方(今までの _設定 のまま)。画面では「そのまま」
SORT_CHOICES = ("", "ASC", "DESC")

# 新しく作る表の形(VBA 版の DB と同じ列)
CONV_COLUMNS = (config.KEY_ITEM, config.ITEM_NAME, config.CHAR_COLOR_SET, config.INP_TYPE, config.REG_CON,
                config.DSP_TYPE, config.MS_JOIN, config.HIS_REC, config.IND_TYPE, config.ADD_IND, config.SORT_FLD)
ID_COLUMNS = (("番号", "INTEGER"), ("名前", "TEXT"), ("ID", "TEXT"), ("PC", "TEXT"), ("PASS", "TEXT"),
              ("Level", "TEXT"), ("備考", "TEXT"))

REFUSE_LEVEL = "level"
REFUSE_INPUT = "bad_input"
REFUSE_TABLE = "no_table"
REFUSE_HAS_ID = "has_id"
REFUSE_PASSWORD = "password"
REFUSE_HAS_KEY = "has_key"
REFUSE_FAILED = "failed"

MSG_LEVEL = f"設定を DB に書くには Level {WRITE_LEVEL} が必要です。"


@dataclass
class EasyResult:
    ok: bool
    message: str
    reason: str = ""
    backup: str = ""
    level: int = 0             # 編集を始めたときの新しいレベル


# ------------------------------------------------------------------
# 読む
# ------------------------------------------------------------------
def split_input(raw: str) -> tuple[str, str]:
    """`_設定`「入力形式」→ (選択肢の値, マスタの表)。選択肢に無ければ (CUSTOM, "")。"""
    raw = raw or ""
    for value, _label, needs_master in INPUT_CHOICES:
        if needs_master:
            if raw[:1] == value and len(raw) > 1:
                return value, raw[1:]
        elif raw == value:
            return value, ""
    return CUSTOM, ""


def _decl_types(conn: sqlite3.Connection, table: str) -> dict[str, str]:
    return {r[1]: (r[2] or "") for r in conn.execute(f"PRAGMA table_info({q(table)})")}


def state(conn: sqlite3.Connection, setup: SystemSetup, level: int) -> dict[str, Any]:
    main = setup.general.main_table
    types = _decl_types(conn, main)
    tables = []
    for name in user_tables(conn):
        try:
            n = conn.execute(f"SELECT COUNT(*) FROM {q(name)}").fetchone()[0]
        except sqlite3.Error:
            n = -1
        tables.append({"name": name, "rows": n})
    columns = []
    for r in setup.conv.rows:
        choice, master = split_input(r.input_type)
        columns.append({"name": r.item, "type": types.get(r.item, ""), "input": choice, "master": master,
                        "raw": r.input_type, "required": r.reg_con == "必須", "money": r.ind_type == "金額",
                        "sorted": r.sort != "", "sort": r.sort if r.sort in ("ASC", "DESC") else "ASC",
                        "key": r.item == config.KEY_ITEM})
    return {
        "main": main, "auto_main": setup.auto_main, "has_general": setup.has_general,
        "conv_table": setup.general.conv_table, "auto_conv": setup.auto_conv,
        "has_id": setup.has_id, "key_ok": setup.key_ok, "key_item": config.KEY_ITEM,
        "level": level, "can_write": level >= WRITE_LEVEL, "can_start": not setup.has_id,
        "tables": tables, "columns": columns, "notices": setup.notices,
        "inputs": [[v, label, m] for v, label, m in INPUT_CHOICES],
        # マスタに選べる表(業務の表 + タイプなどシステムの表のうち、ID と _設定・H_ を除く)
        "masters": [n for n in _master_candidates(conn) if n != main],
    }


def _master_candidates(conn: sqlite3.Connection) -> list[str]:
    return [n for n in table_names(conn)
            if n not in (config.ID_TABLE, config.SET_TABLE)
            and not n.endswith(config.SETTING_SUFFIX) and not n.startswith(config.HISTORY_PREFIX)]


# ------------------------------------------------------------------
# 書く
# ------------------------------------------------------------------
def _backup(conn: sqlite3.Connection, why: str) -> str:
    from .master_admin import _backup as backup
    return backup(conn, why)


def _write(conn: sqlite3.Connection, why: str, work) -> EasyResult:
    """控えを取って、1 つの取引で書く。`work(conn)` は EasyResult を返す(ok でなければ戻す)。"""
    try:
        backup = _backup(conn, why)
    except OSError as exc:
        return EasyResult(False, f"書く前の控えを取れなかったので、やめました: {exc}", REFUSE_FAILED)
    try:
        conn.execute("BEGIN IMMEDIATE")
        result = work(conn)
        conn.execute("COMMIT" if result.ok else "ROLLBACK")
    except sqlite3.Error as exc:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        log.exception("かんたん設定の書き込みに失敗: %s", why)
        return EasyResult(False, f"書けませんでした: {exc}", REFUSE_FAILED, backup=backup)
    result.backup = backup
    return result


def _ensure_general(conn: sqlite3.Connection) -> None:
    if not table_exists(conn, config.SET_TABLE):
        conn.execute(f"CREATE TABLE {q(config.SET_TABLE)} ({q(config.KEY_ITEM)} INTEGER, {q(config.ITEM_NAME)} TEXT, "
                     f"{q(config.PRESET)} TEXT, {q('備考')} TEXT)")


def _set_general(conn: sqlite3.Connection, item: str, value: str, note: str = "") -> None:
    _ensure_general(conn)
    cols = column_names(conn, config.SET_TABLE)
    if config.ITEM_NAME not in cols or config.PRESET not in cols:
        raise sqlite3.OperationalError(f"[{config.SET_TABLE}] に「{config.ITEM_NAME}」「{config.PRESET}」の列がありません")
    hit = conn.execute(f"SELECT COUNT(*) FROM {q(config.SET_TABLE)} WHERE {q(config.ITEM_NAME)} = ?", (item,)).fetchone()[0]
    if hit:
        conn.execute(f"UPDATE {q(config.SET_TABLE)} SET {q(config.PRESET)} = ? WHERE {q(config.ITEM_NAME)} = ?",
                     (value, item))
        return
    row = {config.ITEM_NAME: item, config.PRESET: value}
    if config.KEY_ITEM in cols:
        used = [r[0] for r in conn.execute(f"SELECT {q(config.KEY_ITEM)} FROM {q(config.SET_TABLE)}")]
        row[config.KEY_ITEM] = max([_int(v) for v in used], default=0) + 1
    if note and "備考" in cols:
        row["備考"] = note
    names = list(row)
    conn.execute(f"INSERT INTO {q(config.SET_TABLE)} ({', '.join(q(c) for c in names)}) "
                 f"VALUES ({', '.join('?' for _ in names)})", [row[c] for c in names])


def _int(v: Any) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


def set_main(conn: sqlite3.Connection, setup: SystemSetup, table: str, *, level: int) -> EasyResult:
    """表示する表を変える。Level 5 は DB に決める(全員)。それ未満は、決まりが無い DB だけこの PC で選ぶ。"""
    if table not in user_tables(conn):
        return EasyResult(False, f"[{table}] は表示できる表ではありません。", REFUSE_TABLE)
    if level < WRITE_LEVEL:
        if not setup.auto_main:
            return EasyResult(False, MSG_LEVEL + "(表示する表は全般設定で決まっています)", REFUSE_LEVEL)
        pick_table(conn, table)
        return EasyResult(True, f"この PC では [{table}] を表示します。")

    def work(c):
        _set_general(c, config.GS_MAIN_TABLE, table, "かんたん設定")
        return EasyResult(True, f"表示する表を [{table}] にしました(全般設定「{config.GS_MAIN_TABLE}」)。")
    result = _write(conn, "表示する表を変える前", work)
    if result.ok:
        pick_table(conn, table)
    return result


def _ensure_conv(conn: sqlite3.Connection, conv: str, main_cols: list[str]) -> None:
    """`<メイン>_設定` が無ければ、本体の列の並びで作る(どの行も空 = 自由入力)。"""
    if table_exists(conn, conv):
        return
    conn.execute(f"CREATE TABLE {q(conv)} ({', '.join(q(c) + ' TEXT' for c in CONV_COLUMNS)})")
    conn.executemany(f"INSERT INTO {q(conv)} ({q(config.KEY_ITEM)}, {q(config.ITEM_NAME)}) VALUES (?, ?)",
                     [(str(i), c) for i, c in enumerate(main_cols, start=1)])


def _validate_rows(conn: sqlite3.Connection, setup: SystemSetup,
                   rows: list[dict[str, Any]]) -> tuple[dict[str, dict[str, str]], str]:
    """画面の行 → {列名: {入力形式, 登録条件, 表示, ソート}}。だめなら理由。"""
    known = set(setup.main_columns)
    masters = set(_master_candidates(conn))
    out: dict[str, dict[str, str]] = {}
    sorted_cols = []
    for r in rows:
        name = str(r.get("name") or "")
        if name not in known:
            return {}, f"本体に無い列です: {name}"
        choice = str(r.get("input") or "")
        if choice == CUSTOM:
            raw = str(r.get("raw") or "")
        else:
            entry = next((x for x in INPUT_CHOICES if x[0] == choice), None)
            if entry is None:
                return {}, f"「{name}」の入力の種類が分かりません。"
            if entry[2]:
                master = str(r.get("master") or "")
                if master not in masters:
                    return {}, f"「{name}」で選ぶ表を選んでください。"
                raw = choice + master
            else:
                raw = choice
        if input_types.parse(raw).kind == "error":
            return {}, f"「{name}」の入力形式「{raw}」は読めない書き方です。"
        sort = str(r.get("sort") or "")
        if sort not in SORT_CHOICES:
            return {}, f"「{name}」の並びは 昇順・降順 のどちらかです。"
        if r.get("sorted"):
            sorted_cols.append(name)
        out[name] = {config.INP_TYPE: raw, config.REG_CON: "必須" if r.get("required") else "",
                     config.IND_TYPE: "金額" if r.get("money") else "",
                     config.SORT_FLD: (sort or "ASC") if r.get("sorted") else ""}
    if len(sorted_cols) > 1:
        return {}, "並べる列は 1 つだけ選べます: " + "、".join(sorted_cols)
    return out, ""


def save_columns(conn: sqlite3.Connection, setup: SystemSetup, rows: list[dict[str, Any]], *,
                 level: int) -> EasyResult:
    """列ごとの決まりを `<メイン>_設定` に書く(無ければ作る)。選んだ 4 つの列だけ書く。"""
    if level < WRITE_LEVEL:
        return EasyResult(False, MSG_LEVEL, REFUSE_LEVEL)
    values, problem = _validate_rows(conn, setup, rows)
    if problem:
        return EasyResult(False, problem, REFUSE_INPUT)
    conv = setup.general.conv_table
    main = setup.general.main_table

    def work(c):
        _ensure_conv(c, conv, setup.main_columns)
        cols = column_names(c, conv)
        if config.ITEM_NAME not in cols:
            return EasyResult(False, f"[{conv}] に「{config.ITEM_NAME}」の列がありません。", REFUSE_FAILED)
        # 足りない列(VBA 版の古い形)は足す
        for col in (config.INP_TYPE, config.REG_CON, config.IND_TYPE, config.SORT_FLD):
            if col not in cols:
                c.execute(f"ALTER TABLE {q(conv)} ADD COLUMN {q(col)} TEXT")
        have = {r[0] for r in c.execute(f"SELECT {q(config.ITEM_NAME)} FROM {q(conv)}")}
        for name, v in values.items():
            if name not in have:
                from .master_admin import _append_setting_row
                _append_setting_row(c, conv, name)
            sets = ", ".join(f"{q(k)} = ?" for k in v)
            c.execute(f"UPDATE {q(conv)} SET {sets} WHERE {q(config.ITEM_NAME)} = ?", [*v.values(), name])
        return EasyResult(True, f"列の決まりを保存しました([{conv}] の {len(values)} 列)。")
    result = _write(conn, "列の決まりを変える前", work)
    if result.ok:
        log.info("かんたん設定: %s の列の決まりを保存(%d 列)", main, len(values))
    return result


def start_editing(conn: sqlite3.Connection, setup: SystemSetup, *, user_id: str, pc: str, name: str,
                  password: str) -> EasyResult:
    """ID の表が無い DB で、自分を Level 5 で登録する。ID の表があるときは断る(勝手に足せない)。"""
    if setup.has_id or table_exists(conn, config.ID_TABLE):
        return EasyResult(False, "この DB には ID の表があります。登録は マスタ管理 の ID で、"
                                 "Level 4 以上の人がします。", REFUSE_HAS_ID)
    if not user_id or not pc:
        return EasyResult(False, "この PC のログイン ID・PC 名が分かりません。", REFUSE_FAILED)
    if not password:
        return EasyResult(False, "パスワードを入れてください(次のログインから使います)。", REFUSE_PASSWORD)
    main = setup.general.main_table

    def work(c):
        c.execute(f"CREATE TABLE {q(config.ID_TABLE)} ({', '.join(f'{q(n)} {t}' for n, t in ID_COLUMNS)})")
        c.execute(f"INSERT INTO {q(config.ID_TABLE)} ({', '.join(q(n) for n, _ in ID_COLUMNS)}) "
                  f"VALUES (?, ?, ?, ?, ?, ?, ?)",
                  (1, name or user_id, user_id, pc, to_narrow(password.lower()), str(WRITE_LEVEL), "かんたん設定で登録"))
        # 表示する表も決めておく(次から自動で選ばない)
        if not setup.has_general or setup.auto_main:
            _set_general(c, config.GS_MAIN_TABLE, main, "かんたん設定")
        return EasyResult(True, f"{user_id} を Level {WRITE_LEVEL} で登録しました。次からはこの ID とパスワードで"
                                f"ログインします。人を足すときは マスタ管理 の ID で。", level=WRITE_LEVEL)
    return _write(conn, "編集を始める前", work)


def add_key_column(conn: sqlite3.Connection, setup: SystemSetup, *, level: int) -> EasyResult:
    """本体に 管理番号 の列を足し、いまの行に 1 から番号を振る(表の並びの順)。"""
    if level < WRITE_LEVEL:
        return EasyResult(False, MSG_LEVEL, REFUSE_LEVEL)
    if setup.key_ok:
        return EasyResult(False, f"「{config.KEY_ITEM}」の列はもうあります。", REFUSE_HAS_KEY)
    main = setup.general.main_table
    conv = setup.general.conv_table
    if config.KEY_ITEM.casefold() in {c.casefold() for c in setup.main_columns}:
        return EasyResult(False, f"「{config.KEY_ITEM}」と同じ名前の列があります。", REFUSE_HAS_KEY)

    def work(c):
        c.execute(f"ALTER TABLE {q(main)} ADD COLUMN {q(config.KEY_ITEM)} INTEGER")
        rowids = [r[0] for r in c.execute(f"SELECT rowid FROM {q(main)} ORDER BY rowid")]
        c.executemany(f"UPDATE {q(main)} SET {q(config.KEY_ITEM)} = ? WHERE rowid = ?",
                      [(i, rid) for i, rid in enumerate(rowids, start=1)])
        if table_exists(c, conv):
            from .master_admin import _append_setting_row
            _append_setting_row(c, conv, config.KEY_ITEM)
        return EasyResult(True, f"[{main}] に「{config.KEY_ITEM}」を足し、{len(rowids):,} 行に番号を振りました"
                                f"(いちばん右の列)。一覧で行を直せるようになります。")
    return _write(conn, "管理番号を足す前", work)
