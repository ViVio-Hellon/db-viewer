"""マスタ管理(VBA MF2)

- `GET  /master`                … 画面
- `POST /api/master/tables`     … このレベルで直せる表(VBA の L1)
- `POST /api/master/rows`       … 表の全行(1 ページ)
- `POST /api/master/form`       … 新規/修正の欄(入力形式つき)
- `POST /api/master/execute`    … 実行(質問 → はい/いいえ → 書く)。行を追加 = mode ADDNEW
- `POST /api/master/delete`     … 削除(「削除しますか？」→ 全列一致の先頭 1 行)
- `POST /api/master/add-column` … 列を追加(Level 5。本体なら <メイン>_設定 にも行)
"""
from __future__ import annotations

from flask import Blueprint, g, jsonify, render_template, request

from dbviewer import auth, master_admin, session_setup, settings_tables, shared_db, simple_table
from dbviewer.logging_utils import get_logger
from dbviewer import trace

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


# 表の列の並びで値が来る項目(ID 表なら PASS が入る)
_ROW_KEYS = ("values", "seen", "row")


@bp.before_request
def _hide_secrets():                            # noqa: ANN202 - Flaskのフック
    """**ID 表の PASS を記録に残さない。** 値は列名の無い並びで来るので、表の列を見て伏せる。"""
    body = request.get_json(silent=True) if request.method == "POST" else None
    if not isinstance(body, dict) or not any(k in body for k in _ROW_KEYS):
        return None
    try:
        cols = [r[1] for r in get_db().execute(
            f"PRAGMA table_info({settings_tables.q(str(body.get('table') or ''))})")] or None
    except Exception:                           # noqa: BLE001 - 分からなければ全部伏せる
        cols = None
    g.secret_cols = cols
    g.trace_sent = {k: trace.mask_by_columns(cols, v) if k in _ROW_KEYS else v for k, v in body.items()}
    return None


def _masked(values):
    return trace.mask_by_columns(g.get("secret_cols"), values)


def _allowed(conn, setup, table: str):
    """行を直せる表か(新規・修正・削除)。"""
    if table not in master_admin.editable_tables(conn, setup, auth.level()):
        if table == setup.general.main_table:
            return _refuse("main_rows", master_admin.MSG_ROWS_IN_LIST, 403)
        return _refuse("table", master_admin.MSG_NOT_EDITABLE, 403)
    return None


def _listed(conn, setup, table: str):
    """見てよい表か(左に並ぶ表。本体は Level 5 で列の追加のため)。"""
    if table not in master_admin.listed_tables(conn, setup, auth.level()):
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
    listed = master_admin.listed_tables(conn, setup, lv)
    why = dict(master_admin.used_tables(conn, setup))
    if setup.legacy:
        why[setup.general.main_table] = "業務データ(列の追加だけ)"
    return jsonify({"level": lv, "tables": listed,
                    # その表でできること(行を直す・列を足す)と、ひとこと
                    "caps": {t: master_admin.capabilities(setup, t, lv, conn) for t in listed},
                    # 「表を持ってくる」で足した表(一覧に「足」の印を付ける)
                    "brought": sorted(table_bring.brought_tables(conn)),
                    "why": why,
                    "title": f"マスタメンテナンス アクセスレベル:{lv}"})


@bp.post("/api/master/rows")
def rows():
    conn, setup, err = _open()
    if err:
        return err
    table = str((request.get_json(silent=True) or {}).get("table") or "")
    if (e := _listed(conn, setup, table)):
        return e
    v = master_admin.rows_of(conn, table)
    caps = master_admin.capabilities(setup, table, auth.level(), conn)
    return jsonify({"table": v.table, "columns": v.columns, "rows": v.rows,
                    "read_only": not caps["rows"], "caps": caps,
                    # 並びに意味がある(文字が既定)ので、辞書でなく並びで渡す(JSON は鍵を並べ替える)
                    "kinds": [[k, label] for k, (_t, label) in master_admin.COLUMN_KINDS.items()],
                    "page": v.page_label, "count": len(v.rows)})


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
    # かんたん設定の入力(スピン = 数・カレンダー = 日付の形)にそろえる
    values, problem = simple_table.convert_list(conn, table, values)
    if problem:
        return jsonify({"ok": False, "message": problem, "reason": "bad_value", "executed": ""}), 422
    key = str(body.get("key") or (values[0] if values else ""))
    seen = body.get("seen")
    answer = body.get("answer")
    confirmed = bool(body.get("confirmed"))
    answer = None if answer is None else bool(answer)
    if table == setup.general.conv_table and mode == "ADDNEW":
        # <メイン>_設定 に行を足す = 本体に項目(列)を足す
        r = master_admin.add_item(conn, setup, values, level=auth.level(), confirmed=confirmed, answer=answer)
    else:
        r = master_admin.guard_setting_change(conn, setup, table, mode, values, key) or \
            master_admin.execute(conn, table, mode, values, key, confirmed=confirmed, answer=answer,
                                 seen=[str(v) for v in seen] if seen else None)
    status = 200 if r.ok or r.reason == "question" else (
        409 if r.reason == "conflict" else 403 if r.reason == master_admin.REFUSE_LEVEL else 422)
    if r.reason != "question":
        trace.event("master.save", r.message, level="INFO" if r.ok else "WARN",
                    data={"table": table, "mode": mode, "key": key, "executed": r.executed,
                          "reason": r.reason, "before": _masked(seen), "after": _masked(values)})
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
    r = master_admin.guard_setting_change(conn, setup, table, "DELETE", row, row[0]) or \
        master_admin.delete(conn, table, row, confirmed=bool(body.get("confirmed")),
                            answer=None if answer is None else bool(answer))
    status = 200 if r.ok or r.reason in ("question", "cancelled") else 422
    if r.reason != "question":
        trace.event("master.delete", r.message, level="INFO" if r.ok or r.reason == "cancelled" else "WARN",
                    data={"table": table, "row": _masked(row), "reason": r.reason})
    return jsonify({"ok": r.ok, "message": r.message, "reason": r.reason}), status


@bp.post("/api/master/add-column")
def add_column():
    """列を追加。本体に足すときは <メイン>_設定 にも同じ名前の行を足す(同じ取引)。"""
    conn, setup, err = _open()
    if err:
        return err
    body = request.get_json(silent=True) or {}
    table = str(body.get("table") or "")
    if (e := _listed(conn, setup, table)):
        return e
    r = master_admin.add_column(conn, setup, table, str(body.get("name") or ""),
                                kind=str(body.get("kind") or "text"), fill=str(body.get("fill") or ""),
                                level=auth.level())
    trace.event("master.add_column", r.message, level="INFO" if r.ok else "WARN",
                data={"table": table, "name": body.get("name"), "kind": body.get("kind"),
                      "fill": body.get("fill"), "reason": r.reason, "setting_table": r.setting_table,
                      "setting_key": r.setting_key, "backup": r.backup})
    status = 200 if r.ok else 403 if r.reason == master_admin.REFUSE_LEVEL else 422
    return jsonify({"ok": r.ok, "message": r.message, "reason": r.reason, "column": r.column,
                    "setting_table": r.setting_table, "setting_key": r.setting_key,
                    "backup": r.backup}), status
