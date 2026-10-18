"""かんたん設定(設定 → かんたん設定)

- `POST /api/easy/state`    … 見る DB・表・列ごとの設定(表示・入力・印刷)`{"table"}`
- `POST /api/easy/columns`  … 列ごとの設定を保存 `{"table", "rows": [{name, show, input, step, print}]}`
- `POST /api/easy/retire`   … VBA 版の <表>_設定 を取り込んで消す(編集可。控えを取る)
- `POST /api/easy/rights`   … 権限: アクセス権限マスタ・この PC の 編集可 / 閲覧のみ・誰が編集可か(0.16.0)
                              `{"path", "save": true}` でアクセス権限マスタの場所を保存

保存は 編集可 の権限で(梱包資材マスタの「アクセス権限」に dbviewer:edit の行)。
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from dbviewer import access_rights, auth, config, easy_setup, session_setup, settings_tables, shared_db, trace, \
    user_settings
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


@bp.post("/api/easy/rights")
def rights():
    """権限の様子。保存は「編集可」か、アクセス権限マスタをまだ決めていない・読めないときだけ
    (読めないままだと誰も直せなくなるので、場所を直す道は残す)。"""
    body = request.get_json(silent=True) or {}
    s = auth.ensure()
    d = s.rights
    can_change = s.can_edit or d is None or d.source in (access_rights.SOURCE_UNSET, access_rights.SOURCE_UNREACHABLE)
    if body.get("save"):
        if not can_change:
            return _refuse("level", "アクセス権限マスタの場所を変えるには 編集可 の権限が要ります。", 403)
        text = str(body.get("path") or "").strip().strip('"')
        problem = access_rights.check_path(text)
        if problem:
            return _refuse("bad_path", problem)
        user_settings.save(config.KEY_ACCESS_DB, text)
        trace.event("settings.access_db", text or "(同じフォルダを探す)", data={"path": text, "by": s.user_id})
        message = (f"アクセス権限マスタを {text} にしました。" if text
                   else "アクセス権限マスタは、見ている DB と同じフォルダの 梱包資材マスタ.sqlite3 を探します。")
        return jsonify({"ok": True, "message": message})
    me = d.to_dict() if d else {}
    me.setdefault("login_id", auth.network_id())
    me.setdefault("pc", auth.pc_name())
    me["label"], me["edit"] = s.label, s.can_edit
    if s.developer and d and not d.edit:
        me["reason"] = (me.get("reason") or "") + "(開発者の PC なので 編集可)"
    return jsonify({"me": me, "master": access_rights.overview(), "can_change": can_change})


@bp.post("/api/easy/retire")
def retire():
    conn, _setup, err = _open()
    if err:
        return err
    s = auth.current()
    result = easy_setup.retire_vba(conn, level=auth.level(), user=s.user_id if s else "")
    return _reply(result, "easy.retire_vba", {})
