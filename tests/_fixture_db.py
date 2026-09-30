"""試験用の業務 DB。基準データ(tests/fixtures/realdata)から一時フォルダに作る。"""
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

from dbviewer import shared_db
from dbviewer.accdb_bridge import importer

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "realdata"

_built: Path | None = None
_tmp: tempfile.TemporaryDirectory | None = None


def fixture_db_path() -> Path:
    """一度だけ作って使い回す(読み取り専用の試験用)。"""
    global _built, _tmp
    if _built is None:
        _tmp = tempfile.TemporaryDirectory(prefix="dbviewer_fixture_")
        _built = Path(_tmp.name) / "作業標準管理システム.sqlite3"
        importer.import_tsv_dir(FIXTURES, _built)
    return _built


def fresh_db_path(directory: Path) -> Path:
    """書く試験のために、指定フォルダに新しく作る。"""
    target = directory / "作業標準管理システム.sqlite3"
    importer.import_tsv_dir(FIXTURES, target)
    return target


def connect(path: Path | None = None) -> sqlite3.Connection:
    return shared_db.connect(path or fixture_db_path())
