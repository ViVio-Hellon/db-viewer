"""ログイン(VBA `Login` フォーム)

- `GET  /login`        … 画面。ID の初期値はこの PC のログイン ID(VBA と同じ)
- `POST /api/login`    … 認証。断りの文言は VBA の Msg.Box と同じ
- `POST /api/logout`   … ログアウト(VBA には無い。画面を閉じる代わり)
- `GET  /api/session`  … いまのログイン
"""
from __future__ import annotations

from flask import Blueprint, jsonify, redirect, render_template, request

from dbviewer import auth, config, session_setup, settings_tables, shared_db, user_settings
from dbviewer.logging_utils import get_logger
from dbviewer import trace
from dbviewer.presenters import settings as settings_presenter

from .. import get_db
from ..shell import shell_context

log = get_logger("app.routes.auth")

bp = Blueprint("auth", __name__)


@bp.get("/login")
def page():
    return render_template("login.html",
                           use_id=auth.network_id(), use_pc=auth.pc_name(),
                           last_user=user_settings.get(config.KEY_LAST_USER, "") or auth.network_id(),
                           next_url=request.args.get("next", "/list"),
                           # VBA `初期設定値確認`: 起動モードが未設定なら先に regUF(オンライン/オフライン)
                           mode_set=settings_presenter.mode_is_set(),
                           reset_ask=settings_presenter.MSG_RESET_ASK,
                           **shell_context("list"))


@bp.get("/api/session")
def session():
    s = auth.current()
    return jsonify(_session_dict(s))


def _session_dict(s):
    if s is None:
        return {"logged_in": False, "user_id": "", "level": 0, "developer": False}
    return {"logged_in": True, "user_id": s.user_id, "level": s.level, "developer": s.developer}


@bp.post("/api/login")
def login():
    body = request.get_json(silent=True) or {}
    inp_id = str(body.get("id") or "")
    inp_pass = str(body.get("password") or "")
    # VBA: 本体のフォルダに届くか → 本体があるか(無ければ同じフォルダの .sqlite3 を並べる)
    path = shared_db.db_path()
    if not path.parent.is_dir():
        return jsonify({"error": {"code": "no_folder",
                                  "message": "対象ファイルに接続できません。接続状態を確認してください"}}), 409
    if not path.is_file():
        others = sorted(p.name for p in path.parent.glob("*" + config.DB_SUFFIX))
        msg = "対象ファイルに接続できません。\n"
        if others:
            msg += f"×：{path.name}\n〇：{'/'.join(others)}\n"
        return jsonify({"error": {"code": "db_not_found", "message": msg + "処理を中断します。\n"}}), 409
    try:
        conn = get_db()
        session_setup.get_setup(conn)
    except settings_tables.SetupError as exc:
        return jsonify({"error": {"code": "setup", "message": str(exc)}}), 409
    result = auth.authenticate(conn, inp_id, inp_pass)
    if not result.ok:
        trace.event("login.ng", result.message, level="WARN",
                    data={"id": inp_id, "os_user": auth.network_id(), "pc": auth.pc_name()})
        return jsonify({"error": {"code": "login_failed", "message": result.message}}), 403
    s = auth.start(result)
    trace.event("login.ok", f"Level {s.level}" + ("(開発者)" if s.developer else ""),
                data={"id": s.user_id, "note": result.note, "db": str(path)})
    log.info("ログイン: %s Level=%s%s", s.user_id, s.level, "(開発者)" if s.developer else "")
    out = _session_dict(s)
    out["note"] = result.note
    out["next"] = str(body.get("next") or "/list")
    return jsonify(out)


@bp.post("/api/logout")
def logout():
    auth.end()
    return jsonify({"ok": True, "next": "/login"})
