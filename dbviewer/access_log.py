"""Access.log(VBA `LogMessage` と同じ TSV を同じ場所に書く)

    日時 \\t PC名 \\t OS \\t ユーザー \\t メッセージ \\t VERxxxxxxxx

VBA は `ThisWorkbook.Name` から `(yyyymmdd)` を抜いて VER にしていた。
Web 版は `config/app.json` の version を入れる。
設定タブ「動作」で書かないようにもできる(既定は書く)。
"""
from __future__ import annotations

import os
import platform
import socket
from pathlib import Path

from . import app_config, config, user_settings
from .logging_utils import get_logger
from .vba_compat import now_text

log = get_logger("access_log")


def os_info() -> str:
    """VBA `GetOsInfo`(WMI の Caption と Version)。Windows 以外でも落ちない。"""
    try:
        if os.name == "nt":
            return f"{platform.system()} {platform.release()} ({platform.version()})"
        return platform.platform()
    except Exception:                                  # noqa: BLE001
        return ""


def write(message: str, *, target_dir: Path | None = None) -> bool:
    """1 行追記する。書けなくても業務は止めない(戻り値で知らせる)。"""
    if not user_settings.get(config.KEY_ACCESS_LOG, True):
        return False
    if target_dir is None:
        from . import shared_db
        target_dir = shared_db.server_dir()
    path = Path(target_dir) / config.ACCESS_LOG_NAME
    line = "\t".join([
        now_text(), socket.gethostname(), os_info(),
        os.environ.get("USERNAME") or os.environ.get("USER") or "",
        message, app_config.version_label(),
    ])
    try:
        with open(path, "a", encoding="cp932", errors="replace", newline="") as f:
            f.write(line + "\r\n")
        return True
    except OSError as exc:
        log.warning("Access.log に書けませんでした(%s): %s", path, exc)
        return False
