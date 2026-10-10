"""一覧

VBA MF1 の一覧ページ(フィルタ列・ListView・ページ)は捨て、Web の表(グリッド)にした。
全行をまとめて渡し、絞り込み・並べ替え・列の表示/並び/幅は画面の中で行う。

- `GET  /list`             … 画面
- `POST /api/list/data`    … 全行・列の種類・この PC で保存した列の見せ方
- `POST /api/list/view`    … 列の見せ方を保存する(`{"reset": true}` で初期に戻す)
"""
from __future__ import annotations

from flask import Blueprint, jsonify, render_template, request

from dbviewer import auth, cell_edit, config, grid_data, records, session_setup, settings_tables, shared_db
from dbviewer.logging_utils import get_logger
from dbviewer import trace

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


def _view_key(setup) -> str:
    return grid_data.view_key("list", shared_db.current_profile().prog_no, setup.general.main_table)


@bp.post("/api/list/data")
def api_data():
    conn, setup, err = _open()
    if err:
        return err
    try:
        columns, rows = records.full_list(conn, setup)
    except records.ListError as exc:
        return _refuse("list", str(exc))
    key_index = columns.index(config.KEY_ITEM) if config.KEY_ITEM in columns else -1
    return jsonify({
        "columns": grid_data.columns_meta(columns, rows),
        "rows": rows,
        "key_index": key_index,
        "count": len(rows),
        "view": grid_data.load_view(_view_key(setup), columns),
        # その場で直せる列(レベルが足りなければ全部だめ)
        "edit": {"allowed": auth.level() >= auth.required_level("修正"),
                 "columns": cell_edit.edit_specs(conn, setup)},
        "db_mtime": shared_db.probe().get("mtime"),
        "setup": _setup_dict(setup),
    })


@bp.post("/api/list/cell")
def api_cell():
    """一覧の 1 マスを直して、すぐ本体に書く。`{"key", "column", "value", "seen"}`

    確認の問い合わせはしない。決まり(レベル・入力形式・必須・自動項目・履歴)は登録画面と同じ。
    """
    conn, setup, err = _open()
    if err:
        return err
    mode, refuse = auth.check_mode("修正", auth.level())
    if refuse:
        return _refuse("level", refuse, 403)
    body = request.get_json(silent=True) or {}
    s = auth.current()
    result = cell_edit.save_cell(conn, setup, str(body.get("key") or ""), str(body.get("column") or ""),
                                 "" if body.get("value") is None else str(body.get("value")),
                                 "" if body.get("seen") is None else str(body.get("seen")),
                                 user=s.user_id if s else auth.network_id(), pc=auth.pc_name())
    status = 200 if result.ok else (409 if result.reason == "conflict" else 422)
    if result.reason != "unchanged":
        trace.event("cell.save", result.message, level="INFO" if result.ok else "WARN",
                    data={"key": body.get("key"), "column": body.get("column"), "before": body.get("seen"),
                          "after": body.get("value"), "reason": result.reason, "changed": result.changed})
    return jsonify({"ok": result.ok, "message": result.message, "reason": result.reason,
                    "row": result.row, "changed": result.changed,
                    "db_mtime": shared_db.probe().get("mtime")}), status


@bp.post("/api/list/value")
def api_value():
    """本体の 1 マスの値(一覧の見せ方の前)。`{"key", "column"}` → `{"value"}`

    金額の列は一覧で丸めて見せているので、その場で直すときはこの値から始める。
    """
    conn, setup, err = _open()
    if err:
        return err
    body = request.get_json(silent=True) or {}
    value = cell_edit.raw_value(conn, setup, str(body.get("key") or ""), str(body.get("column") or ""))
    if value is None:
        return _refuse("gone", "その行はもうありません(ほかの端末が消しました)。", 404)
    return jsonify({"value": value})


@bp.post("/api/list/view")
def api_view():
    conn, setup, err = _open()
    if err:
        return err
    try:
        columns, _rows = records.full_list(conn, setup)
    except records.ListError as exc:
        return _refuse("list", str(exc))
    body = request.get_json(silent=True) or {}
    if body.get("reset"):
        return jsonify({"view": grid_data.reset_view(_view_key(setup), columns)})
    return jsonify({"view": grid_data.save_view(_view_key(setup), columns, body)})


def _setup_dict(setup) -> dict:
    return {
        "main_table": setup.general.main_table,
        "report_flag": setup.general.report_flag,
        "browse_flag": setup.general.browse_flag,
        "column_problems": setup.column_problems,
        "format_ok": setup.format_ok,
    }
