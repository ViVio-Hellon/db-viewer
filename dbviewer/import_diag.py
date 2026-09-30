"""取り込みの診断記録(取り込み診断_YYYYMMDD.log)

python-web-tools の `packaging_tool/import_diag.py` から、「表を持ってくる」が使う
部分(`write` / `path_for` / `latest_path`)だけを写したもの。

何を読んで、何を足し・入れ替え・作り直したか、書く前の控えはどこか、を
1 か所に残す。ふだんのログ(dbviewer_YYYYMMDD.log)からは組み立てにくいため。
**書けなくても処理は止めない。**
"""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Optional

from . import config
from .logging_utils import get_logger

log = get_logger("import_diag")


def path_for(day: Optional[date] = None) -> Path:
    return config.LOG_DIR / f"取り込み診断_{(day or date.today()):%Y%m%d}.log"


def latest_path() -> Optional[Path]:
    """いちばん新しい記録。今日のが無ければ、前の日のうち最新のもの。"""
    today = path_for()
    if today.exists():
        return today
    try:
        found = sorted(today.parent.glob("取り込み診断*.log"),
                       key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return None
    return found[0] if found else None


def write(text: str) -> None:
    """1行(複数行でもよい)を足す。書けなくても処理は止めない。"""
    try:
        path = path_for()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            for line in str(text).splitlines() or [""]:
                fh.write(f"{datetime.now():%H:%M:%S} {line}\n")
    except Exception as exc:                       # noqa: BLE001 - 診断は止めない
        log.debug("取り込み診断を書けません: %s", exc)
