"""かんたん設定(設定 → かんたん設定)

- `POST /api/easy/state`    … 見る DB・表・列ごとの設定(表示・入力・印刷)`{"table"}`
- `POST /api/easy/columns`  … 列ごとの設定を保存 `{"table", "rows": [{name, show, input, step, print}]}`
- `POST /api/easy/retire`   … VBA 版の <表>_設定 を取り込んで消す(編集可。控えを取る)
- `POST /api/easy/rights`   … 権限: この PC の 編集可 / 閲覧のみ・誰が編集可か・設定ファイル(0.16.0 / 0.22.0)
- `POST /api/easy/settings-file` … 設定ファイル(権限・かんたん設定・接続先)を決める `{"path"}`。無ければ作る(0.22.0。
                              編集可 か 管理者パスワードの鍵が要る)
- `POST /api/easy/access/unlock`   … 管理者パスワードで鍵を開ける `{"password"}` / 掛ける `{"lock": true}`(0.17.0)
- `POST /api/easy/access/rows`     … アクセス権限の全部の行(見るだけなら鍵は要らない)
- `POST /api/easy/access/save`     … 行を足す・直す `{"row", "seen"}`(鍵が要る)
- `POST /api/easy/access/delete`   … 行を消す `{"no", "seen"}`(鍵が要る)
- `POST /api/easy/access/password` … 管理者パスワードを変える `{"current", "new", "confirm"}`

保存は 編集可 の権限で(設定ファイルの「アクセス権限」に 権限 = 編集可 の行)。
"""
from __future__ import annotations

from flask import Blueprint, g, jsonify, request

from dbviewer import access_rights, admin_password, auth, config, easy_setup, session_setup, settings_file, \
    settings_tables, shared_db, trace, user_settings
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
    """権限の様子と、設定ファイル。"""
    s = auth.ensure()
    d = s.rights
    can_change = s.can_edit or admin_password.session.peek()
    me = d.to_dict() if d else {}
    me.setdefault("login_id", auth.network_id())
    me.setdefault("pc", auth.pc_name())
    me["label"], me["edit"] = s.label, s.can_edit
    if s.developer and d and not d.edit:
        me["reason"] = (me.get("reason") or "") + "(開発者の PC なので 編集可)"
    return jsonify({"me": me, "master": access_rights.overview(), "file": settings_file.info(),
                    "can_change": can_change,
                    "admin": admin_password.session.peek(), "custom_password": admin_password.is_custom()})


@bp.post("/api/easy/settings-file")
def choose_settings_file():
    """設定ファイルを決める(0.22.0)。無ければ作る。権限の出どころが替わるので、編集可 か 管理者パスワードの鍵が要る
    (閲覧のみの人が空のファイルに替えて 編集可 になれないように)。"""
    s = auth.ensure()
    if not (s.can_edit or admin_password.session.is_open()):
        return _refuse("level", "設定ファイルを替えるには 編集可 の権限か、管理者パスワードの鍵が要ります"
                                "(下の「アクセス権限を直す」で鍵を開けてください)。", 403)
    text = str((request.get_json(silent=True) or {}).get("path") or "").strip().strip('"')
    target, problem = settings_file.check_target(text)
    if problem:
        return _refuse("bad_path", problem)
    fresh = not target.exists()
    try:
        settings_file.ensure(target)
    except Exception as exc:                             # noqa: BLE001 - 作れない・開けない
        return _refuse("failed", f"設定ファイルを作れません: {exc}")
    user_settings.save(config.KEY_SETTINGS_FILE, str(target) if text else "")
    trace.event("settings.file", str(target), data={"path": str(target), "created": fresh, "by": s.user_id})
    auth.sign_in()                                      # 権限の出どころが替わったので読み直す
    now = auth.current()
    message = (f"設定ファイルを作りました: {target}" if fresh else f"設定ファイルを {target} にしました。")
    return jsonify({"ok": True, "message": message + f"(この PC は {now.label if now else '-'})",
                    "created": fresh, "file": settings_file.info()})


# ------------------------------------------------------------------
# アクセス権限を直す(管理者パスワードで鍵を開けたときだけ。0.17.0)
# ------------------------------------------------------------------
def _master():
    return access_rights.master_path(), None


def _need_admin():
    if not admin_password.session.is_open():
        return _refuse("locked", "管理者パスワードで鍵を開けてください。", 403)
    return None


@bp.post("/api/easy/access/unlock")
def access_unlock():
    body = request.get_json(silent=True) or {}
    g.trace_sent = {"lock": bool(body.get("lock")), "password": "***"}      # 記録にパスワードを残さない
    s = auth.ensure()
    if body.get("lock"):
        admin_password.session.lock()
        access_rights.forget_backups()
        return jsonify({"ok": True, "admin": False, "message": "鍵を掛けました。"})
    result = admin_password.session.unlock(str(body.get("password") or ""))
    trace.event("access.unlock", "鍵を開けました" if result.ok else result.message,
                level="INFO" if result.ok else "WARN", data={"by": s.user_id, "pc": auth.pc_name()})
    if not result.ok:
        return _refuse(result.reason, result.message, 403)
    return jsonify({"ok": True, "admin": True, "message": result.message})


@bp.post("/api/easy/access/rows")
def access_rows():
    path, err = _master()
    if err:
        return err
    try:
        rows = access_rights.all_rows(path)
    except Exception as exc:                             # noqa: BLE001 - 届かない・表が無い
        return _refuse("unreadable", f"設定ファイルのアクセス権限を読めません: {exc}", 409)
    return jsonify({"path": str(path), "rows": rows, "columns": list(access_rights.COLUMNS),
                    "permissions": access_rights.permissions_in_use(path), "admin": admin_password.session.peek(),
                    "me": {"login_id": auth.network_id(), "pc": auth.pc_name()}})


def _after_write(result, event: str, data: dict):
    trace.event(event, result.message, level="INFO" if result.ok else "WARN",
                data={**data, "reason": result.reason, "backup": result.backup, "by": auth.ensure().user_id})
    if result.ok:
        auth.sign_in()                                  # 自分の権限も読み直す(自分の行を直したかもしれない)
    status = 200 if result.ok else 409 if result.reason in ("conflict", "gone", "busy") else 422
    s = auth.current()
    return jsonify({"ok": result.ok, "message": result.message, "reason": result.reason, "no": result.no,
                    "backup": result.backup, "label": s.label if s else ""}), status


@bp.post("/api/easy/access/save")
def access_save():
    err = _need_admin()
    if err:
        return err
    path, err = _master()
    if err:
        return err
    body = request.get_json(silent=True) or {}
    row = body.get("row") if isinstance(body.get("row"), dict) else {}
    seen = body.get("seen") if isinstance(body.get("seen"), dict) else None
    result = access_rights.save_row(path, row, seen)
    return _after_write(result, "access.save", {k: row.get(k) for k in access_rights.COLUMNS})


@bp.post("/api/easy/access/delete")
def access_delete():
    err = _need_admin()
    if err:
        return err
    path, err = _master()
    if err:
        return err
    body = request.get_json(silent=True) or {}
    no = str(body.get("no") or "")
    if not no.isdigit():
        return _refuse("bad_input", "消す行が分かりません。")
    seen = body.get("seen") if isinstance(body.get("seen"), dict) else None
    result = access_rights.delete_row(path, int(no), seen)
    return _after_write(result, "access.delete", {"no": int(no), "row": seen})


@bp.post("/api/easy/access/password")
def access_password():
    body = request.get_json(silent=True) or {}
    g.trace_sent = {"current": "***", "new": "***", "confirm": "***"}     # 記録にパスワードを残さない
    result = admin_password.change(str(body.get("current") or ""), str(body.get("new") or ""),
                                   str(body.get("confirm") or ""))
    trace.event("access.password", result.message, level="INFO" if result.ok else "WARN",
                data={"by": auth.ensure().user_id})
    if not result.ok:
        return _refuse(result.reason, result.message, 403 if result.reason == "wrong_password" else 422)
    return jsonify({"ok": True, "message": result.message})


@bp.post("/api/easy/try-place")
def try_place():
    """場所の決まりを、表の行で試す(0.26.0)。保存はしない。見るだけなので 閲覧のみ でも使える。"""
    conn, _setup, err = _open()
    if err:
        return err
    from .record import ref_base
    body = request.get_json(silent=True) or {}
    out = easy_setup.try_place(conn, str(body.get("table") or ""), str(body.get("column") or ""),
                               str(body.get("place") or "").strip(), ref_base())
    return jsonify(out)


@bp.post("/api/easy/retire")
def retire():
    conn, _setup, err = _open()
    if err:
        return err
    s = auth.current()
    result = easy_setup.retire_vba(conn, level=auth.level(), user=s.user_id if s else "")
    return _reply(result, "easy.retire_vba", {})
