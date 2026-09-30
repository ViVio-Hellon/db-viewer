"""一覧(VBA MF1 の一覧ページ): フィルタ・一覧・ページ・集計・SQL 表示

- `GET  /list`             … 画面
- `POST /api/list`         … 一覧ぜんぶ(フィルタ候補・行・ページ・SQL)
- `POST /api/list/summary` … 集計モード(フィルタ見出しのダブルクリック)
"""
from __future__ import annotations

from flask import Blueprint, jsonify, render_template, request

from dbviewer import records, session_setup, settings_tables, shared_db
from dbviewer.logging_utils import get_logger
from dbviewer.presenters import list as list_presenter

from .. import get_db
from ..shell import shell_context

log = get_logger("app.routes.list")

bp = Blueprint("list", __name__)


def _refuse(code: str, message: str, status: int = 422):
    return jsonify({"error": {"code": code, "message": message}}), status


def _open():
    """DB と設定テーブル。開けなければ理由を返す(VBA の Msg.Box の文言)。"""
    try:
        conn = get_db()
        setup = session_setup.get_setup(conn)
    except shared_db.DbNotFound as exc:
        return None, None, _refuse("db_not_found", str(exc), 409)
    except settings_tables.SetupError as exc:
        return None, None, _refuse("setup", str(exc), 409)
    if setup.master_problem:
        return None, None, _refuse(
            "master_file",
            setup.master_problem + "\nマスタファイルに修正が必要です。\n起動を中止します。", 409)
    return conn, setup, None


@bp.get("/list")
def page():
    return render_template("list.html", **shell_context("list"))


@bp.post("/api/list")
def api_list():
    conn, setup, err = _open()
    if err:
        return err
    body = request.get_json(silent=True) or {}
    try:
        view = list_presenter.build(
            conn, setup,
            values=list_presenter.parse_filter_values(body.get("filters") or {}),
            page_no=int(body.get("page") or 1),
            per_page=int(body.get("per_page") or records.DEFAULT_PAGE_ROWS),
            page_flag=bool(body.get("page_flag")),
            event_no=int(body.get("event_no") or 0),
            reset=bool(body.get("reset")))
    except records.ListError as exc:
        return _refuse("list", str(exc))
    view["setup"] = _setup_dict(setup)
    return jsonify(view)


@bp.post("/api/list/summary")
def api_summary():
    conn, setup, err = _open()
    if err:
        return err
    body = request.get_json(silent=True) or {}
    try:
        view = list_presenter.summary_view(conn, setup, str(body.get("item") or ""))
    except records.ListError as exc:
        return _refuse("summary", str(exc))
    return jsonify(view)


def _setup_dict(setup) -> dict:
    return {
        "main_table": setup.general.main_table,
        "report_flag": setup.general.report_flag,
        "browse_flag": setup.general.browse_flag,
        "column_problems": setup.column_problems,
        "format_ok": setup.format_ok,
    }
