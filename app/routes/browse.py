"""テーブル閲覧と出力

- `GET  /browse`               … 画面
- `POST /api/browse/tables`    … 全般設定「テーブル閲覧」の表
- `POST /api/browse/data`      … 表の全行・列の種類・列の見せ方(一覧と同じグリッドで見る)
- `POST /api/browse/view`      … 列の見せ方を保存する
- `GET  /api/export/settings`  … 出力先・既定の形式・文字コード
- `POST /api/export`           … 画面に出している表をファイルに書く(ダウンロードはしない)
- `POST /api/export/open`      … 出力先のフォルダを開く(Windows のエクスプローラー)
"""
from __future__ import annotations

import os
from pathlib import Path

from flask import Blueprint, jsonify, render_template, request

from dbviewer import (app_config, config, grid_data, session_setup, settings_tables, shared_db,
                      table_browse, user_settings)
from dbviewer.logging_utils import get_logger

from .. import get_db
from ..shell import shell_context

log = get_logger("app.routes.browse")

bp = Blueprint("browse", __name__)

# 一度に書く行の上限(画面から送る表の大きさの歯止め)
MAX_EXPORT_ROWS = 200_000


def _refuse(code: str, message: str, status: int = 422):
    return jsonify({"error": {"code": code, "message": message}}), status


def _open():
    try:
        conn = get_db()
        setup = session_setup.get_setup(conn)
    except shared_db.DbNotFound as exc:
        return None, None, _refuse("db_not_found", str(exc), 409)
    except settings_tables.SetupError as exc:
        return None, None, _refuse("setup", str(exc), 409)
    return conn, setup, None


export_dir = table_browse.export_dir


def export_settings() -> dict:
    fmt = str(user_settings.get(config.KEY_EXPORT_FORMAT, "") or table_browse.DEFAULT_FORMAT)
    enc = str(user_settings.get(config.KEY_EXPORT_ENCODING, "") or table_browse.DEFAULT_ENCODING)
    return {"dir": str(export_dir()),
            "format": fmt if fmt in table_browse.FORMATS else table_browse.DEFAULT_FORMAT,
            "encoding": enc if enc in table_browse.ENCODINGS else table_browse.DEFAULT_ENCODING,
            "encodings": table_browse.ENCODINGS,
            "can_open": os.name == "nt"}


@bp.get("/browse")
def page():
    return render_template("browse.html", **shell_context("browse"))


@bp.post("/api/browse/tables")
def tables():
    conn, setup, err = _open()
    if err:
        return err
    names = table_browse.browse_tables(setup.general.browse_str)
    return jsonify({"tables": names, "browse_flag": setup.general.browse_flag})


def _table(setup) -> tuple[str, object]:
    table = str((request.get_json(silent=True) or {}).get("table") or "")
    if table not in table_browse.browse_tables(setup.general.browse_str):
        return table, _refuse("table", "その表は閲覧の対象ではありません。", 403)
    return table, None


def _view_key(table: str) -> str:
    return grid_data.view_key("browse", shared_db.current_profile().prog_no, table)


@bp.post("/api/browse/data")
def data():
    conn, setup, err = _open()
    if err:
        return err
    table, err = _table(setup)
    if err:
        return err
    try:
        v = table_browse.view(conn, table)
    except ValueError as exc:
        return _refuse("table", str(exc))
    return jsonify({"table": v.table, "columns": grid_data.columns_meta(v.columns, v.rows),
                    "rows": v.rows, "count": len(v.rows), "key_index": -1,
                    "view": grid_data.load_view(_view_key(table), v.columns)})


@bp.post("/api/browse/view")
def view():
    conn, setup, err = _open()
    if err:
        return err
    table, err = _table(setup)
    if err:
        return err
    v = table_browse.view(conn, table)
    body = request.get_json(silent=True) or {}
    if body.get("reset"):
        return jsonify({"view": grid_data.reset_view(_view_key(table), v.columns)})
    return jsonify({"view": grid_data.save_view(_view_key(table), v.columns, body)})


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
