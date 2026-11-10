"""DB の控え(0.46.0 で 1 つに)

書く前に本体の控えを取る。以前は 2 通りあった:
    ファイルの丸写し(shutil.copyfile)… 表を持ってくる・列の追加・表の削除・見えない文字の取り除き・VBA 設定の取り込み
    SQLite の backup                   … アクセス権限
丸写しは、ほかの PC が書いている最中だと**書きかけを写した(壊れた)控え**になることがある。
SQLite の backup は書き終わった中身だけを写す。そこで全部 backup に揃える。
backup できないとき(DB でないファイル・長く書かれ続けている など)は、今までどおり丸写しにする
(控えが無いより良い。記録に残す)。
"""
from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

from .logging_utils import get_logger

log = get_logger("db_backup")

TIMEOUT_SEC = 10


def ro_uri(path: Path) -> str:
    """読むだけで開く URI(ファイル名の % ? # を逃がす)。"""
    text = str(path).replace("%", "%25").replace("?", "%3F").replace("#", "%23")
    return f"file:{text}?mode=ro"


def copy_db(src: Path, target: Path) -> str:
    """src の控えを target に作る。使った方法("backup" / "copy")を返す。失敗は OSError。"""
    src, target = Path(src), Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        source = sqlite3.connect(ro_uri(src), uri=True, timeout=TIMEOUT_SEC)
        try:
            dest = sqlite3.connect(str(target))
            try:
                source.backup(dest)
            finally:
                dest.close()
        finally:
            source.close()
        return "backup"
    except sqlite3.Error as exc:
        log.warning("SQLite の backup で控えを取れませんでした(%s)。ファイルを写します: %s", exc, src)
        try:
            target.unlink()
        except OSError:
            pass
    shutil.copyfile(src, target)
    return "copy"
