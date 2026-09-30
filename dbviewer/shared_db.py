"""共有フォルダ上の sqlite3 を、VBA と同じ運用で開く(骨格。Phase 2 で中身を足す)"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

from . import config, user_settings
from .logging_utils import get_logger

log = get_logger("shared_db")

BUSY_TIMEOUT_MS = 10_000


class DbNotFound(RuntimeError):
    """本体ファイルが無い・開けない。文言はそのまま画面に出す。"""


def current_profile() -> config.Profile:
    return config.profile(user_settings.get(config.KEY_PROG_NO, config.DEFAULT_PROG_NO))


def server_dir() -> Path:
    """VBA `SrvPath`。オンラインならプロファイル(または設定)のフォルダ、オフラインなら手元。"""
    prof = current_profile()
    if user_settings.get(config.KEY_ONLINE, True):
        return config.resolve_dir(user_settings.get(config.KEY_SERVER_DIR, "") or prof.server_dir)
    return config.resolve_dir(user_settings.get(config.KEY_LOCAL_DIR, ""))


def db_path() -> Path:
    """VBA `MasterPath`。"""
    prof = current_profile()
    name = user_settings.get(config.KEY_DB_FILE, "") or prof.db_file
    return server_dir() / name


def connect(path: Path, *, read_only: bool = False) -> sqlite3.Connection:
    if not path.is_file():
        raise DbNotFound(f"アクセス許可がないか、ファイルが存在しないため参照できません。\n{path}")
    conn = sqlite3.connect(str(path), timeout=BUSY_TIMEOUT_MS / 1000)
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    return conn


def open_current() -> sqlite3.Connection:
    return connect(db_path())


def probe() -> dict:
    """設定タブ「いまの状態」に出す情報。"""
    path = db_path()
    info = {"path": str(path), "exists": path.is_file(), "size": 0, "mtime": None}
    if info["exists"]:
        st = path.stat()
        info["size"] = st.st_size
        info["mtime"] = st.st_mtime
    return info
