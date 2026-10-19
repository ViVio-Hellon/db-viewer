"""接続先フォルダの中のファイルを見て、DBビューアで開けるかを分ける

設定「接続先」で、フォルダの中身を並べるために使う。

    open     … 開ける .sqlite3。全般設定・ID がそろっていれば DBビューアの形。
               0.7.0 から、設定の表が無くても業務の表があれば開ける(0.8.0: どの表も一覧で選んで見て直せる)
    convert  … Access(.accdb / .mdb)。「Access 取り込み」で .sqlite3 にすると開ける(注意表示)
    other    … 表が 1 つも無い・読めない

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
    from .settings_tables import is_system_table
    q = source_db.quote_identifier
    main = ""
    if config.SET_TABLE in tables:
        try:
            got = source_db.read_query(
                path, f"SELECT {q(config.PRESET)} AS v FROM {q(config.SET_TABLE)} WHERE {q(config.ITEM_NAME)} = ?",
                [config.GS_MAIN_TABLE])
        except (source_db.SourceError, sqlite3.Error):
            got = []
        main = str(got[0]["v"] or "") if got else ""
    if not main or main not in tables:
        user = [t for t in source_db.list_tables(path) if not is_system_table(t) and t != config.BROUGHT_REGISTRY]
        if not user:
            return {"kind": KIND_OTHER, "note": "表がありません"}
        missing = [t for t in (config.SET_TABLE, config.ID_TABLE) if t not in tables]
        why = f"{'・'.join(missing)} が無い" if missing else "メインテーブル名の決まりが無い"
        return {"kind": KIND_OPEN, "note": f"表 {len(user)} 個({why}。一覧で表を選んで見て直せます)",
                "main_table": "", "simple": True}
    rows = source_db.table_counts(path).get(main, 0)
    note = f"{main}({max(rows, 0):,} 件)" + ("" if config.ID_TABLE in tables else "・ID が無いので誰でも直せる")
    return {"kind": KIND_OPEN, "note": note, "main_table": main}


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


# ------------------------------------------------------------------
# DB を選び直す(0.20.0。上の帯の「DB」と 設定 → 接続先 の「最近開いた DB」)
# ------------------------------------------------------------------
MAX_SAME_FOLDER = 40


def _entry(path: Path, *, text: str = "", current: bool = False) -> dict[str, Any]:
    """1 つの候補。中は読まない(帯のメニューは速く出したい。開けるかは開くときに確かめる)。"""
    try:
        st = path.stat()
        exists, mtime = path.is_file(), datetime.fromtimestamp(st.st_mtime).strftime("%Y/%m/%d %H:%M")
    except OSError:
        exists, mtime = False, ""
    return {"name": path.name, "path": text or str(path), "folder": str(path.parent),
            "exists": exists, "mtime": mtime, "current": current}


def choices() -> dict[str, Any]:
    """選び直せる DB: 最近開いたもの(新しい順)と、いまの DB と同じフォルダの .sqlite3。"""
    from . import shared_db
    now = shared_db.configured()
    texts = shared_db.recent()
    if now and not any(shared_db.same_file(t, now) for t in texts):
        texts = [now] + texts                          # 0.19.0 からの人は「最近」がまだ空
    recent = [_entry(config.resolve_dir(t), text=t, current=bool(now) and shared_db.same_file(t, now))
              for t in texts]
    out: dict[str, Any] = {"current": now, "recent": recent, "folder": "", "same_folder": [], "message": ""}
    if not now:
        return out
    folder = config.resolve_dir(now).parent
    out["folder"] = str(folder)
    try:
        found = sorted((p for p in folder.iterdir()
                        if p.suffix.lower() in SQLITE_SUFFIXES and p.is_file()), key=lambda p: p.name.lower())
    except OSError as exc:
        out["message"] = f"フォルダを読めません({exc})。"
        return out
    out["same_folder"] = [_entry(p) for p in found
                          if not any(shared_db.same_file(str(p), t) for t in texts)][:MAX_SAME_FOLDER]
    return out
