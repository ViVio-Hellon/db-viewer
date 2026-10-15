"""かんたん設定(設定 → かんたん設定)

- `POST /api/easy/state`    … 見る DB・表・列ごとの設定(表示・入力・印刷)`{"table"}`
- `POST /api/easy/columns`  … 列ごとの設定を保存 `{"table", "rows": [{name, show, input, step, print}]}`
- `POST /api/easy/start`    … ログインを使う(ID の表が無い DB で、自分を Level 5 で登録) `{"password", "name"}`
- `POST /api/easy/retire`   … VBA 版の <表>_設定 を取り込んで消す(Level 5。控えを取る)

どれもログインが要る(ID の表が無い DB は誰でも Level 5 で入れる)。保存は Level 5。
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from dbviewer import auth, easy_setup, session_setup, settings_tables, shared_db, trace
from dbviewer.logging_utils import get_logger

from .. import get_db

log = get_logger("app.routes.easy")

bp = Blueprint("easy", __name__)


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


def _reply(result: easy_setup.EasyResult, event: str, data: dict):
    trace.event(event, result.message, level="INFO" if result.ok else "WARN",
                data={**data, "reason": result.reason, "backup": result.backup})
    status = 200 if result.ok else 403 if result.reason == easy_setup.REFUSE_LEVEL else 422
    return jsonify({"ok": result.ok, "message": result.message, "reason": result.reason,
                    "backup": result.backup, "level": result.level}), status


@bp.post("/api/easy/state")
def state():
    conn, setup, err = _open()
    if err:
        return err
    table = str((request.get_json(silent=True) or {}).get("table") or "")
    return jsonify(easy_setup.state(conn, setup, auth.level(), table))


@bp.post("/api/easy/columns")
def columns():
    conn, setup, err = _open()
    if err:
        return err
    body = request.get_json(silent=True) or {}
    rows = body.get("rows") or []
    if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
        return _refuse("bad_rows", "送られた列の設定が読めません。")
    table = str(body.get("table") or "")
    s = auth.current()
    result = easy_setup.save_columns(conn, table, rows, level=auth.level(), user=s.user_id if s else "")
    return _reply(result, "easy.columns", {"table": table, "count": len(rows)})


@bp.post("/api/easy/start")
def start_login():
    conn, setup, err = _open()
    if err:
        return err
    body = request.get_json(silent=True) or {}
    s = auth.current()
    user_id = s.user_id if s else auth.network_id()
    result = easy_setup.start_login(conn, setup, user_id=user_id, pc=auth.pc_name(),
                                    name=str(body.get("name") or ""), password=str(body.get("password") or ""))
    if result.ok and s is not None:
        # 登録した本人なので、そのまま Level 5(ログインし直さなくてよい)
        auth.start(auth.LoginResult(True, user_id=s.user_id, level=result.level, developer=s.developer))
        log.info("ログインを使い始めました: %s を Level %s で登録", user_id, result.level)
    return _reply(result, "easy.start", {"id": user_id, "pc": auth.pc_name()})


@bp.post("/api/easy/retire")
def retire():
    conn, _setup, err = _open()
    if err:
        return err
    s = auth.current()
    result = easy_setup.retire_vba(conn, level=auth.level(), user=s.user_id if s else "")
    return _reply(result, "easy.retire_vba", {})
