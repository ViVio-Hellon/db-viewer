"""かんたん設定(設定 → かんたん設定)

- `POST /api/easy/state`    … 表示する表・業務の表・列ごとの決まり・選べる入力の種類
- `POST /api/easy/main`     … 表示する表を変える `{"table"}`
- `POST /api/easy/columns`  … 列ごとの決まりを保存 `{"rows": [{name, input, master, raw, required, money, sorted, sort}]}`
- `POST /api/easy/start`    … 編集を始める(ID の表が無い DB で、自分を Level 5 で登録) `{"password", "name"}`
- `POST /api/easy/key`      … 本体に 管理番号 の列を足す

どれもログインが要る(ID の表が無い DB は閲覧だけでログインできる)。書くのは Level 5。
「編集を始める」だけは ID の表が無いときに限り、誰でもできる(その人が最初の管理者になる)。
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
    return jsonify(easy_setup.state(conn, setup, auth.level()))


@bp.post("/api/easy/main")
def main_table():
    conn, setup, err = _open()
    if err:
        return err
    table = str((request.get_json(silent=True) or {}).get("table") or "")
    return _reply(easy_setup.set_main(conn, setup, table, level=auth.level()), "easy.main", {"table": table})


@bp.post("/api/easy/columns")
def columns():
    conn, setup, err = _open()
    if err:
        return err
    rows = (request.get_json(silent=True) or {}).get("rows") or []
    if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
        return _refuse("bad_rows", "送られた列の決まりが読めません。")
    result = easy_setup.save_columns(conn, setup, rows, level=auth.level())
    return _reply(result, "easy.columns", {"table": setup.general.main_table, "count": len(rows)})


@bp.post("/api/easy/start")
def start_editing():
    conn, setup, err = _open()
    if err:
        return err
    body = request.get_json(silent=True) or {}
    s = auth.current()
    user_id = s.user_id if s else auth.network_id()
    result = easy_setup.start_editing(conn, setup, user_id=user_id, pc=auth.pc_name(),
                                      name=str(body.get("name") or ""), password=str(body.get("password") or ""))
    if result.ok and s is not None:
        # 登録した本人なので、そのまま Level 5 にする(ログインし直さなくてよい)
        auth.start(auth.LoginResult(True, user_id=s.user_id, level=result.level, developer=s.developer))
        log.info("編集を始めました: %s を Level %s で登録", user_id, result.level)
    return _reply(result, "easy.start", {"id": user_id, "pc": auth.pc_name()})


@bp.post("/api/easy/key")
def key_column():
    conn, setup, err = _open()
    if err:
        return err
    return _reply(easy_setup.add_key_column(conn, setup, level=auth.level()), "easy.key",
                  {"table": setup.general.main_table})
