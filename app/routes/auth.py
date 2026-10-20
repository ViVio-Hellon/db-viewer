"""この PC の権限(0.16.0。VBA `Login` フォームの代わり)

- `GET  /login`        … 画面。 この PC の ID・PC 名・編集可 / 閲覧のみ
- `POST /api/login`    … 権限を読み直して使い始める(パスワードは無い。設定ファイルの「アクセス権限」)
- `POST /api/logout`   … いまの権限を捨てる(次の操作で読み直す)
- `GET  /api/session`  … いまの権限
"""
from __future__ import annotations

from flask import Blueprint, jsonify, render_template, request

from dbviewer import access_rights, auth, config, user_settings
from dbviewer.logging_utils import get_logger
from dbviewer import trace

from ..shell import shell_context

log = get_logger("app.routes.auth")

bp = Blueprint("auth", __name__)


@bp.get("/login")
def page():
    s = auth.current()
    rights = s.rights if s and s.rights else access_rights.evaluate(auth.network_id(), auth.pc_name())
    return render_template("login.html", rights=rights,
                           use_id=auth.network_id(), use_pc=auth.pc_name(),
                           last_user=user_settings.get(config.KEY_LAST_USER, "") or auth.network_id(),
                           next_url=request.args.get("next", "/list"),
                           **shell_context("list"))


@bp.get("/api/session")
def session():
    s = auth.current() or auth.ensure()
    return jsonify(_session_dict(s))


def _session_dict(s):
    if s is None:
        return {"logged_in": False, "user_id": "", "level": 0, "edit": False, "label": "", "developer": False}
    return {"logged_in": True, "user_id": s.user_id, "level": s.level, "edit": s.can_edit, "label": s.label,
            "developer": s.developer, "rights": s.rights.to_dict() if s.rights else None}


@bp.post("/api/login")
def login():
    """権限を読み直して使い始める。ID・パスワードは受け取っても使わない(この PC のログイン ID・PC 名で決める)。"""
    body = request.get_json(silent=True) or {}
    s = auth.sign_in()
    trace.event("login.ok", s.label + ("(開発者)" if s.developer else ""),
                data={"id": s.user_id, "pc": auth.pc_name(), "source": s.rights.source if s.rights else "",
                      "note": s.rights.reason if s.rights else ""})
    out = _session_dict(s)
    out["note"] = s.rights.reason if s.rights else ""
    out["next"] = str(body.get("next") or "/list")
    return jsonify(out)


@bp.post("/api/logout")
def logout():
    auth.end()
    return jsonify({"ok": True, "next": "/login"})                 # 次の操作で権限を読み直す
