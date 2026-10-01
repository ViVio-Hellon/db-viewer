"""接続先フォルダの中のファイルを見て、DBビューアで開けるかを分ける

設定「接続先」で、フォルダの中身を並べるために使う。

    open     … DBビューアの .sqlite3(全般設定・ID とメインテーブルがある)。強調して出す
    convert  … Access(.accdb / .mdb)。「Access 取り込み」で .sqlite3 にすると開ける(注意表示)
    other    … .sqlite3 だが DBビューアの形ではない(表が足りない)・読めない

読むのはファイルの中の表の名前と件数だけ。書かない。
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from . import config, source_db

KIND_OPEN = "open"
KIND_CONVERT = "convert"
KIND_OTHER = "other"

SQLITE_SUFFIXES = source_db.SUFFIXES            # .sqlite3 / .db
ACCESS_SUFFIXES = (".accdb", ".mdb")
MAX_FILES = 200


def inspect_sqlite(path: Path) -> dict[str, Any]:
    """DBビューアで開けるか。(kind, note, main_table, rows)"""
    try:
        tables = set(source_db.list_tables(path))
    except (source_db.SourceError, sqlite3.Error, OSError) as exc:
        return {"kind": KIND_OTHER, "note": f"読めません({exc})"}
    missing = [t for t in (config.SET_TABLE, config.ID_TABLE) if t not in tables]
    if missing:
        return {"kind": KIND_OTHER, "note": f"DBビューアの形ではありません({'・'.join(missing)} がありません)"}
    q = source_db.quote_identifier
    try:
        got = source_db.read_query(
            path, f"SELECT {q(config.PRESET)} AS v FROM {q(config.SET_TABLE)} WHERE {q(config.ITEM_NAME)} = ?",
            [config.GS_MAIN_TABLE])
    except (source_db.SourceError, sqlite3.Error):
        got = []
    main = str(got[0]["v"] or "") if got else ""
    if not main or main not in tables:
        return {"kind": KIND_OTHER, "note": "全般設定の「メインテーブル名」の表がありません"}
    rows = source_db.table_counts(path).get(main, 0)
    return {"kind": KIND_OPEN, "note": f"{main}({max(rows, 0):,} 件)", "main_table": main}


def scan(folder: Path, *, current: Optional[Path] = None) -> dict[str, Any]:
    """フォルダの中の .sqlite3 / .db / .accdb / .mdb を並べる。開けるもの → 変換できるもの → その他の順。"""
    out: dict[str, Any] = {"folder": str(folder), "exists": folder.is_dir(), "files": [], "message": ""}
    if not out["exists"]:
        out["message"] = "フォルダが見つかりません(共有フォルダならつながっているか確かめてください)。"
        return out
    try:
        entries = sorted((p for p in folder.iterdir() if p.is_file()), key=lambda p: p.name.lower())
    except OSError as exc:
        out["message"] = f"フォルダを読めません({exc})。"
        return out
    sqlite_stems = {p.stem.lower() for p in entries if p.suffix.lower() in SQLITE_SUFFIXES}
    files = []
    for p in entries[:MAX_FILES * 5]:
        suffix = p.suffix.lower()
        if suffix not in SQLITE_SUFFIXES + ACCESS_SUFFIXES:
            continue
        try:
            st = p.stat()
        except OSError:
            continue
        info: dict[str, Any] = {"name": p.name, "path": str(p), "size": st.st_size,
                                "mtime": datetime.fromtimestamp(st.st_mtime).strftime("%Y/%m/%d %H:%M"),
                                "current": bool(current and _same(p, current))}
        if suffix in ACCESS_SUFFIXES:
            done = p.stem.lower() in sqlite_stems
            info.update(kind=KIND_CONVERT,
                        note=("同じ名前の .sqlite3 があります(変換済み)。Access のほうが新しければ取り込み直してください"
                              if done else "「Access 取り込み」で .sqlite3 にすると開けます"),
                        target=str(p.with_suffix(".sqlite3")))
        else:
            info.update(inspect_sqlite(p))
        files.append(info)
        if len(files) >= MAX_FILES:
            break
    order = {KIND_OPEN: 0, KIND_CONVERT: 1, KIND_OTHER: 2}
    files.sort(key=lambda f: (order[f["kind"]], f["name"].lower()))
    out["files"] = files
    if not files:
        out["message"] = "このフォルダに .sqlite3 も Access のファイルもありません。"
    return out


def _same(a: Path, b: Path) -> bool:
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return str(a) == str(b)
