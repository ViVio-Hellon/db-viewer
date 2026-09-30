"""要求ごとに設定テーブルを読んで `SystemSetup` を渡す

VBA は起動時に一度読んで Public 変数に持っていたが、設定タブやマスタ管理で
直したものをその場で効かせるため、Web 版は**要求のたびに読む**(表は小さい)。
"""
from __future__ import annotations

import sqlite3

from flask import g

from . import settings_tables


def get_setup(conn: sqlite3.Connection) -> settings_tables.SystemSetup:
    if "setup" not in g:
        g.setup = settings_tables.load_setup(conn)
    return g.setup
