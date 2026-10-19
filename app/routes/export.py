"""出力(一覧の「出力」: ファイルに保存)

- `GET  /api/export/settings`  … 出力先・既定の形式・文字コード
- `POST /api/export`           … 画面に出している表をファイルに書く(ダウンロードはしない)
- `POST /api/export/open`      … 出力先のフォルダを開く(Windows のエクスプローラー)

0.18.0: 閲覧・出力の画面をなくしたので、ここだけ残した(一覧の「出力」が使う)。
"""
from __future__ import annotations

import os

from flask import Blueprint, jsonify, request

from dbviewer import config, table_browse, trace, user_settings
from dbviewer.logging_utils import get_logger

log = get_logger("app.routes.export")

bp = Blueprint("export", __name__)

# 一度に書く行の上限(画面から送る表の大きさの歯止め)
MAX_EXPORT_ROWS = 200_000


def _refuse(code: str, message: str, status: int = 422):
    return jsonify({"error": {"code": code, "message": message}}), status


export_dir = table_browse.export_dir


def export_settings() -> dict:
    fmt = str(user_settings.get(config.KEY_EXPORT_FORMAT, "") or table_browse.DEFAULT_FORMAT)
    enc = str(user_settings.get(config.KEY_EXPORT_ENCODING, "") or table_browse.DEFAULT_ENCODING)
    return {"dir": str(export_dir()),
            "format": fmt if fmt in table_browse.FORMATS else table_browse.DEFAULT_FORMAT,
            "encoding": enc if enc in table_browse.ENCODINGS else table_browse.DEFAULT_ENCODING,
            "encodings": table_browse.ENCODINGS,
            "can_open": os.name == "nt"}


@bp.get("/api/export/settings")
def export_info():
    return jsonify(export_settings())


@bp.post("/api/export")
def export():
    """画面に出している表(列と行)を書く。`{"columns":[…],"rows":[[…]],"name":…,"format":…,"encoding":…}`"""
    body = request.get_json(silent=True) or {}
    columns = body.get("columns")
    rows = body.get("rows")
    if not isinstance(columns, list) or not isinstance(rows, list) or not columns:
        return _refuse("bad_table", "出す表の指定が正しくありません。", 400)
    if not rows:
        return _refuse("empty", table_browse.MSG_EMPTY)
    if len(rows) > MAX_EXPORT_ROWS:
        return _refuse("too_many", f"一度に出せるのは {MAX_EXPORT_ROWS:,} 行までです。")
    columns = [str(c) for c in columns]
    rows = [["" if v is None else str(v) for v in (r if isinstance(r, list) else [])] for r in rows]
    info = export_settings()
    fmt = str(body.get("format") or info["format"])
    enc = str(body.get("encoding") or info["encoding"])
    name = str(body.get("name") or "").strip() or table_browse.default_name()
    try:
        path = table_browse.export(columns, rows, export_dir(), name, fmt, enc)
    except (ValueError, OSError) as exc:
        return _refuse("export", f"出力できませんでした: {exc}")
    log.info("出力: %s (%d 行 × %d 列)", path, len(rows), len(columns))
    trace.event("export", str(path), data={"rows": len(rows), "columns": columns, "format": fmt, "encoding": enc})
    return jsonify({"ok": True, "message": table_browse.MSG_DONE, "path": str(path),
                    "dir": str(path.parent), "rows": len(rows), "can_open": info["can_open"]})


@bp.post("/api/export/open")
def open_folder():
    """出力先のフォルダをエクスプローラーで開く(このアプリは同じ PC で動いている)。"""
    folder = export_dir()
    if os.name != "nt":
        return _refuse("not_windows", f"この PC ではフォルダを開けません。場所: {folder}", 501)
    try:
        folder.mkdir(parents=True, exist_ok=True)
        os.startfile(str(folder))                       # noqa: S606 - 同じ PC のフォルダを開くだけ
    except OSError as exc:
        return _refuse("open", f"フォルダを開けませんでした: {exc}")
    return jsonify({"ok": True, "dir": str(folder)})
