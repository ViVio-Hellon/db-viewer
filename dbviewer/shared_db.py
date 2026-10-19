"""見る DB(.sqlite3)を開く。0.19.0 から接続先は「DB ファイル」1 つ(フルパス)だけ。"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

from . import config, user_settings, vba_compat
from .logging_utils import get_logger

log = get_logger("shared_db")

BUSY_TIMEOUT_MS = 10_000


class DbNotFound(RuntimeError):
    """本体ファイルが無い・開けない。文言はそのまま画面に出す。"""


MSG_NOT_SET = "見る DB ファイルを決めていません。設定 →「接続先」で DB ファイルを選んでください。"


def configured() -> str:
    """設定の「DB ファイル」(フルパス)。0.19.0 から接続先はこの 1 つだけ。

    以前の設定(システム・オンライン/オフライン・共有フォルダ・手元のフォルダ・本体ファイル名)しか
    無い PC は、それが指していたファイルを 1 回だけ「DB ファイル」に移す(前と同じ DB が開く)。
    """
    text = user_settings.get(config.KEY_DB_PATH, None)
    if text is None:
        text = _migrate_legacy()
    return str(text or "").strip()


def _migrate_legacy() -> str:
    """0.18.0 までの 5 つの設定から、指していたファイルのフルパスを作って移す。何も無ければ ""。"""
    keys = (config.KEY_PROG_NO, config.KEY_ONLINE, config.KEY_SERVER_DIR, config.KEY_LOCAL_DIR, config.KEY_DB_FILE)
    data = user_settings.load_all()
    if not any(k in data for k in keys):
        return ""
    try:
        prof = config.profile(data.get(config.KEY_PROG_NO, config.DEFAULT_PROG_NO))
    except (TypeError, ValueError):
        prof = config.profile(config.DEFAULT_PROG_NO)
    if data.get(config.KEY_ONLINE, True) is False:
        folder = config.resolve_dir(data.get(config.KEY_LOCAL_DIR, "") or "")
    else:
        folder = config.resolve_dir(data.get(config.KEY_SERVER_DIR, "") or prof.server_dir)
    path = str(folder / (data.get(config.KEY_DB_FILE, "") or prof.db_file))
    user_settings.save(config.KEY_DB_PATH, path)
    for k in keys:
        user_settings.delete(k)
    log.info("接続先を DB ファイル 1 つに移しました: %s", path)
    return path


def is_set() -> bool:
    return bool(configured())


def db_path() -> Path:
    """見る DB(VBA `MasterPath`)。決めていなければ Path("")(存在しない)。"""
    text = configured()
    return config.resolve_dir(text) if text else Path("")


def server_dir() -> Path:
    """DB ファイルのあるフォルダ(VBA `SrvPath`)。参照ファイルの起点・Access.log の置き場所。

    DB を決めていなければアプリのフォルダ。
    """
    return db_path().parent if is_set() else config.BASE_DIR


def connect(path: Path, *, read_only: bool = False) -> sqlite3.Connection:
    if str(path) in ("", "."):
        raise DbNotFound(MSG_NOT_SET)
    if not path.is_file():
        raise DbNotFound(f"アクセス許可がないか、ファイルが存在しないため参照できません。\n{path}")
    conn = sqlite3.connect(str(path), timeout=BUSY_TIMEOUT_MS / 1000)
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    register_functions(conn)
    return conn


def register_functions(conn: sqlite3.Connection) -> None:
    """VBA/Access の SQL をそのまま通すための関数。

    - `FORMAT(x, '#,##0')` … Access の `Format`。CODESQL が「金額」列に付ける
    - `VBADATE(text)`     … TEXT 列と日付リテラルの比較を Access と同じにする
                            (Access は TEXT を日付に変換してから比べる)
    """
    conn.create_function("FORMAT", 2, _sql_format, deterministic=True)
    conn.create_function("VBADATE", 1, vba_compat.normalize_date_text, deterministic=True)


def _sql_format(value, fmt):
    if fmt == "#,##0":
        return vba_compat.format_thousands(value)
    return "" if value is None else str(value)


def open_current() -> sqlite3.Connection:
    return connect(db_path())


def probe() -> dict:
    """設定タブ「いまの状態」に出す情報。"""
    path = db_path()
    info = {"path": str(path) if is_set() else "", "exists": is_set() and path.is_file(), "size": 0, "mtime": None}
    if info["exists"]:
        st = path.stat()
        info["size"] = st.st_size
        info["mtime"] = st.st_mtime
    return info
