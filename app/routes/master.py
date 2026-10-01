"""マスタ管理(VBA MF2)

- `GET  /master`                … 画面
- `POST /api/master/tables`     … このレベルで直せる表(VBA の L1)
- `POST /api/master/rows`       … 表の全行(1 ページ)
- `POST /api/master/form`       … 新規/修正の欄(入力形式つき)
- `POST /api/master/execute`    … 実行(質問 → はい/いいえ → 書く)
- `POST /api/master/delete`     … 削除(「削除しますか？」→ 全列一致の先頭 1 行)
"""
from __future__ import annotations

from flask import Blueprint, jsonify, render_template, request

from dbviewer import auth, master_admin, session_setup, settings_tables, shared_db
from dbviewer.logging_utils import get_logger

from .. import get_db
from ..shell import shell_context

log = get_logger("app.routes.master")

bp = Blueprint("master", __name__)


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


def _allowed(conn, setup, table: str):
    if table not in master_admin.editable_tables(conn, setup, auth.level()):
        return _refuse("table", master_admin.MSG_NOT_EDITABLE, 403)
    return None


@bp.get("/master")
def page():
    return render_template("master.html", **shell_context("master"))


@bp.post("/api/master/tables")
def tables():
    conn, setup, err = _open()
    if err:
        return err
    lv = auth.level()
    from dbviewer import table_bring
    return jsonify({"level": lv, "tables": master_admin.editable_tables(conn, setup, lv),
                    # 「表を持ってくる」で足した表(一覧に「足」の印を付ける)
                    "brought": sorted(table_bring.brought_tables(conn)),
                    "why": dict(master_admin.used_tables(conn, setup)),
                    "title": f"マスタメンテナンス アクセスレベル:{lv}"})


@bp.post("/api/master/rows")
def rows():
    conn, setup, err = _open()
    if err:
        return err
    table = str((request.get_json(silent=True) or {}).get("table") or "")
    if (e := _allowed(conn, setup, table)):
        return e
    v = master_admin.rows_of(conn, table)
    return jsonify({"table": v.table, "columns": v.columns, "rows": v.rows,
                    "read_only": v.read_only, "page": v.page_label, "count": len(v.rows)})


@bp.post("/api/master/form")
def form():
    conn, setup, err = _open()
    if err:
        return err
    body = request.get_json(silent=True) or {}
    table = str(body.get("table") or "")
    mode = str(body.get("mode") or "UPDATE")
    if (e := _allowed(conn, setup, table)):
        return e
    if master_admin.is_history(table):
        return _refuse("history", "履歴テーブルは直せません。", 403)
    row = body.get("row")
    if mode == "UPDATE" and not row:
        return _refuse("no_target", master_admin.MSG_NO_TARGET_EDIT)
    fields = master_admin.build_fields(conn, setup, table, mode, row)
    return jsonify({"table": table, "mode": mode, "fields": [f.to_dict() for f in fields]})


@bp.post("/api/master/execute")
def execute():
    conn, setup, err = _open()
    if err:
        return err
    body = request.get_json(silent=True) or {}
    table = str(body.get("table") or "")
    mode = str(body.get("mode") or "UPDATE")
    if (e := _allowed(conn, setup, table)):
        return e
    if master_admin.is_history(table):
        return _refuse("history", "履歴テーブルは直せません。", 403)
    values = [str(v) for v in (body.get("values") or [])]
    key = str(body.get("key") or (values[0] if values else ""))
    seen = body.get("seen")
    answer = body.get("answer")
    r = master_admin.execute(conn, table, mode, values, key, confirmed=bool(body.get("confirmed")),
                             answer=None if answer is None else bool(answer),
                             seen=[str(v) for v in seen] if seen else None)
    status = 200 if r.ok or r.reason == "question" else (409 if r.reason == "conflict" else 422)
    return jsonify({"ok": r.ok, "message": r.message, "reason": r.reason, "executed": r.executed}), status


@bp.post("/api/master/delete")
def delete():
    conn, setup, err = _open()
    if err:
        return err
    body = request.get_json(silent=True) or {}
    table = str(body.get("table") or "")
    if (e := _allowed(conn, setup, table)):
        return e
    if master_admin.is_history(table):
        return _refuse("history", "履歴テーブルは直せません。", 403)
    row = [str(v) for v in (body.get("row") or [])]
    if not row:
        return _refuse("no_target", master_admin.MSG_NO_TARGET_DELETE)
    answer = body.get("answer")
    r = master_admin.delete(conn, table, row, confirmed=bool(body.get("confirmed")),
                            answer=None if answer is None else bool(answer))
    status = 200 if r.ok or r.reason in ("question", "cancelled") else 422
    return jsonify({"ok": r.ok, "message": r.message, "reason": r.reason}), status
