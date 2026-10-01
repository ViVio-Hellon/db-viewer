"""帳票と印刷(Excel は使わない)

- `POST /api/report/run`      … 帳票(全般設定「帳票」のプロシージャ。作業標準管理は REPORT)。
                                 画面で絞り込んだ行(`keys` = 管理番号)で作る。並びは VBA と同じ
- `POST /api/print`           … 画面に出している表をそのまま印刷(A4 横)
- `GET  /report/preview?id=`  … 印刷用 HTML(余白 6mm、Ctrl+P で印刷)

VBA は原紙ブック(出力原紙.xlsx)のシートを複製して書き込んでいた。Web 版は原紙を
使わず、同じ中身(空行・小計・Total・GrandTotal・罫線)を印刷用の画面に出す。
"""
from __future__ import annotations

import threading
import uuid

from flask import Blueprint, Response, jsonify, request

from dbviewer import config, grid_data, records, reports, session_setup, settings_tables, shared_db
from dbviewer.logging_utils import get_logger
from dbviewer.reports import render

from .. import get_db

log = get_logger("app.routes.report")

bp = Blueprint("report", __name__)

# 作った印刷用 HTML。プロセスに数枚だけ持つ
_previews: dict[str, str] = {}
_lock = threading.Lock()
MAX_PREVIEWS = 10
MAX_PRINT_ROWS = 20_000


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
        builder = reports.builder_for(spec.proc)
    except reports.ReportError as exc:
        return _refuse("report_setting", str(exc))
    try:
        columns, rows = records.full_list(conn, setup)
    except records.ListError as exc:
        return _refuse("sql", str(exc))
    keys = body.get("keys")
    if isinstance(keys, list):
        key_index = columns.index(config.KEY_ITEM) if config.KEY_ITEM in columns else -1
        # 帳票は作業NO の区切りで小計を入れるので、並びは帳票の決まり(一覧 SQL の順 =
        # VBA と同じ)のまま。画面で並べ替えていても、使うのは「どの行か」だけ
        columns, rows = grid_data.pick(columns, rows, keys=[str(k) for k in keys], key_index=key_index,
                                       keep_order=True)
    if not rows:
        return _refuse("empty", "帳票にする行がありません。絞り込みを見直してください。")
    grid = builder([columns] + rows, spec.args)
    sheet = render.default_sheet_name()
    html = render.to_html(grid, f"{setup.general.main_table} {sheet}")
    pid = _keep(html)
    log.info("帳票: %s %d 行", spec.proc, len(rows))
    return jsonify({"ok": True, "preview": f"/report/preview?id={pid}", "rows": len(grid.rows),
                    "source_rows": len(rows), "sheet": sheet})


@bp.post("/api/print")
def print_view():
    """画面の表を印刷する。`{"title":…, "note":…, "columns":[…], "rows":[[…]], "numeric":[bool…]}`"""
    body = request.get_json(silent=True) or {}
    columns, rows = body.get("columns"), body.get("rows")
    if not isinstance(columns, list) or not isinstance(rows, list) or not columns:
        return _refuse("bad_table", "印刷する表の指定が正しくありません。", 400)
    if not rows:
        return _refuse("empty", "印刷する行がありません。")
    if len(rows) > MAX_PRINT_ROWS:
        return _refuse("too_many", f"一度に印刷できるのは {MAX_PRINT_ROWS:,} 行までです。絞り込んでください。")
    columns = [str(c) for c in columns]
    rows = [["" if v is None else str(v) for v in (r if isinstance(r, list) else [])] for r in rows]
    numeric = [bool(x) for x in body.get("numeric") or []]
    html = render.view_html(str(body.get("title") or "一覧"), columns, rows,
                            note=str(body.get("note") or ""), numeric=numeric)
    return jsonify({"ok": True, "preview": f"/report/preview?id={_keep(html)}", "rows": len(rows)})


@bp.get("/report/preview")
def preview():
    pid = request.args.get("id", "")
    with _lock:
        html = _previews.get(pid)
    if html is None:
        return Response("この印刷用の画面はもうありません。一覧から作り直してください。", status=404,
                        content_type="text/plain; charset=utf-8")
    return Response(html, content_type="text/html; charset=utf-8")
