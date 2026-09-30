"""この要求で使う業務 DB の開き方(骨格)

VBA 版は `adoConnection(MasterPath)` で共有フォルダの .accdb を要求のたびに
開いて閉じていた。Web 版も同じく **要求ごとに開いて閉じる**
(`app/__init__.py` の `get_db` / `teardown`)。

開く先(共有 sqlite3)の決め方と、読みをスナップショットで行う仕組みは
Phase 2 の `shared_db` に置く。ここは `get_connection()` の入口だけ。
"""
from __future__ import annotations

import sqlite3

from . import shared_db


def get_connection() -> sqlite3.Connection:
    """いまの設定が指す業務 DB を開く。無ければ例外(理由つき)。"""
    return shared_db.open_current()


def apply_schema(conn: sqlite3.Connection) -> None:
    """試験用の空 DB に最低限の表を作る。業務の表は設定テーブルが決めるので何も作らない。"""
    conn.execute("PRAGMA journal_mode=DELETE")
