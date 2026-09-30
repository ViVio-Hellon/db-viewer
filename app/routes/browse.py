"""テーブル閲覧・出力(VBA MF3 / ExportData)

- `GET  /browse`              … 画面
- `POST /api/browse/tables`   … 全般設定「テーブル閲覧」の表
- `POST /api/browse/rows`     … 表の全行
- `POST /api/export`          … xlsx / txt / csv に書く(元は 一覧の SQL か 表)
- `GET  /report/table?table=` … 表の印刷用 HTML(VBA MF3 の PrintForm の代わり)
"""
from __future__ import annotations

from pathlib import Path

from flask import Blueprint, jsonify, render_template, request

from dbviewer import (app_config, config, records, session_setup, settings_tables, shared_db,
                      table_browse, user_settings)
from dbviewer.logging_utils import get_logger

from .. import get_db
from ..shell import shell_context

log = get_logger("app.routes.browse")

bp = Blueprint("browse", __name__)


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


def export_dir() -> Path:
    text = user_settings.get(config.KEY_EXPORT_DIR, "") or ""
    return config.resolve_dir(text, app_config.local_dir("work")) if text else app_config.local_dir("work")


@bp.get("/browse")
def page():
    return render_template("browse.html", **shell_context("browse"))


@bp.post("/api/browse/tables")
def tables():
    conn, setup, err = _open()
    if err:
        return err
    names = table_browse.browse_tables(setup.general.browse_str)
    return jsonify({"tables": names, "browse_flag": setup.general.browse_flag,
                    "export_dir": str(export_dir()), "default_name": table_browse.default_name()})


@bp.post("/api/browse/rows")
def rows():
    conn, setup, err = _open()
    if err:
        return err
    table = str((request.get_json(silent=True) or {}).get("table") or "")
    if table not in table_browse.browse_tables(setup.general.browse_str):
        return _refuse("table", "その表は閲覧の対象ではありません。", 403)
    try:
        v = table_browse.view(conn, table)
    except ValueError as exc:
        return _refuse("table", str(exc))
    return jsonify({"table": v.table, "columns": v.columns, "rows": v.rows, "count": len(v.rows)})


@bp.post("/api/export")
def export():
    """VBA `ExportData`。`source` は list(一覧の SQL) か table(表)。"""
    conn, setup, err = _open()
    if err:
        return err
    body = request.get_json(silent=True) or {}
    source = str(body.get("source") or "list")
    ext = str(body.get("ext") or "xlsx")
    name = str(body.get("name") or "").strip() or table_browse.default_name()
    if source == "table":
        table = str(body.get("table") or "")
        if table not in table_browse.browse_tables(setup.general.browse_str):
            return _refuse("table", "その表は閲覧の対象ではありません。", 403)
        v = table_browse.view(conn, table)
        data = [list(v.columns)] + v.rows
    else:
        sql = str(body.get("sql") or "")
        if not sql:
            from dbviewer import sql_builder
            sql = sql_builder.build(setup.conv, setup.general.main_table).sql
        try:
            data = records.table_array(conn, sql)
        except records.ListError as exc:
            return _refuse("sql", str(exc))
    if len(data) <= 1 and not body.get("force"):
        return jsonify({"ok": False, "reason": "question", "message": table_browse.MSG_EMPTY_ASK})
    try:
        path = table_browse.export(data, export_dir(), name, ext)
    except (ValueError, OSError) as exc:
        return _refuse("export", f"出力できませんでした: {exc}")
    log.info("出力: %s (%d 行)", path, len(data) - 1)
    return jsonify({"ok": True, "message": table_browse.MSG_DONE, "path": str(path),
                    "rows": len(data) - 1})
