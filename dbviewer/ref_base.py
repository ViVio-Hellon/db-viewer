"""参照ファイル(PDF・画像など)の相対パスの起点(0.45.0 で接続先ごとに)

決め方(上から順に、入っているものを使う):
    1. 接続先の「参照フォルダ」(設定ファイルの接続先の表。いま開いている DB の行)
    2. この PC の「参照ファイルの起点」(設定 → 参照・出力。0.44.0 までは これ 1 つだけで、どの DB でも同じだった)
    3. DB ファイルのあるフォルダ(VBA の SrvPath)
参照フォルダが相対パスなら DB ファイルのあるフォルダから見る。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from . import config, shared_db, user_settings
from .logging_utils import get_logger

log = get_logger("ref_base")

FROM_CONNECTION = "connection"
FROM_PC = "pc"
FROM_DB = "db"


@dataclass
class RefBase:
    path: Path
    source: str
    text: str = ""                       # 入れてあった文字
    db_no: Optional[int] = None          # 接続先の管理番号(接続先のとき)

    @property
    def label(self) -> str:
        if self.source == FROM_CONNECTION:
            return f"接続先 {self.db_no} 番の「参照フォルダ」"
        if self.source == FROM_PC:
            return "この PC の「参照ファイルの起点」"
        return "DB ファイルのあるフォルダ"


def connection_ref() -> tuple[str, Optional[int]]:
    """いま開いている DB の接続先に書いた参照フォルダ。設定ファイルを読めなければ空(起点を決めるのは止めない)。"""
    db_text = shared_db.configured()
    if not db_text:
        return "", None
    try:
        from . import settings_file
        return settings_file.ref_dir_of(db_text)
    except Exception as exc:                       # noqa: BLE001 - 設定ファイルに届かないだけ
        log.warning("接続先の参照フォルダを読めませんでした: %s", exc)
        return "", None


def current() -> RefBase:
    server = shared_db.server_dir()
    conn_ref, no = connection_ref()
    if conn_ref:
        return RefBase(config.resolve_dir(conn_ref, server), FROM_CONNECTION, conn_ref, no)
    pc = str(user_settings.get(config.KEY_REF_DIR, "") or "").strip()
    if pc:
        return RefBase(config.resolve_dir(pc, server), FROM_PC, pc)
    return RefBase(server, FROM_DB)
