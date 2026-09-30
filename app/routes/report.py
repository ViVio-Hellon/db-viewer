"""帳票(VBA MF1 の REPORT ボタン → Application.Run → PrintPreview)

- `POST /api/report/run`      … いまの一覧 SQL で帳票を作り、原紙 xlsx にシートを足す
- `GET  /report/preview?id=`  … 印刷用 HTML(A4 縦、余白 6mm、Ctrl+P で印刷)
- `GET  /report/table?table=` … 閲覧の表をそのまま印刷(VBA MF3 PrintForm の代わり)
"""
from __future__ import annotations

import threading
import uuid
from pathlib import Path

from flask import Blueprint, Response, jsonify, request

from dbviewer import config, records, reports, session_setup, settings_tables, shared_db, table_browse, user_settings
from dbviewer.logging_utils import get_logger
from dbviewer.reports import render, xlsx_append_report

from .. import get_db

log = get_logger("app.routes.report")

bp = Blueprint("report", __name__)

# 作った帳票の HTML(プレビュー用)。プロセスに数枚だけ持つ
_previews: dict[str, str] = {}
_lock = threading.Lock()
MAX_PREVIEWS = 10


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


def report_dir() -> Path:
    text = user_settings.get(config.KEY_REPORT_DIR, "") or ""
    db_dir = shared_db.db_path().parent
    return config.resolve_dir(text, db_dir) if text else db_dir


def _keep(html: str) -> str:
    pid = uuid.uuid4().hex
    with _lock:
        _previews[pid] = html
        while len(_previews) > MAX_PREVIEWS:
            _previews.pop(next(iter(_previews)))
    return pid


@bp.post("/api/report/run")
def run():
    conn, setup, err = _open()
    if err:
        return err
    body = request.get_json(silent=True) or {}
    try:
        spec = reports.parse(setup.general.report_str)
    except reports.ReportError as exc:
        return _refuse("report_setting", str(exc))
    template = spec.path(report_dir())
    if not template.is_file():
        return _refuse("report_file", reports.MSG_NO_FILE + f"\n{template}")
    try:
        builder = reports.builder_for(spec.proc)
    except reports.ReportError as exc:
        return _refuse("report_proc", str(exc))
    sql = str(body.get("sql") or "")
    if not sql:
        from dbviewer import sql_builder
        sql = sql_builder.build(setup.conv, setup.general.main_table).sql
    try:
        table = records.table_array(conn, sql)
    except records.ListError as exc:
        return _refuse("sql", str(exc))
    grid = builder(table, spec.args)
    sheet_name = render.default_sheet_name()
    try:
        written = xlsx_append_report(template, grid, sheet_name, template_sheet=spec.sheet)
    except Exception as exc:                          # noqa: BLE001 - 理由を画面に出す
        log.exception("帳票の xlsx 書き込みに失敗")
        return _refuse("report_xlsx", f"原紙に書けませんでした: {exc}")
    html = render.to_html(grid, f"{setup.general.main_table} {written}")
    pid = _keep(html)
    log.info("帳票: %s にシート %s を追加(%d 行)", template, written, len(grid.rows))
    return jsonify({"ok": True, "path": str(template), "sheet": written, "preview": f"/report/preview?id={pid}",
                    "rows": len(grid.rows)})


@bp.get("/report/preview")
def preview():
    pid = request.args.get("id", "")
    with _lock:
        html = _previews.get(pid)
    if html is None:
        return Response("この帳票のプレビューはもうありません。一覧から作り直してください。", status=404,
                        content_type="text/plain; charset=utf-8")
    return Response(html, content_type="text/html; charset=utf-8")


@bp.get("/report/table")
def table_print():
    conn, setup, err = _open()
    if err:
        return err
    table = request.args.get("table", "")
    if table not in table_browse.browse_tables(setup.general.browse_str):
        return _refuse("table", "その表は閲覧の対象ではありません。", 403)
    v = table_browse.view(conn, table)
    return Response(render.table_html(table, v.columns, v.rows), content_type="text/html; charset=utf-8")
