"""設定画面

**この端末の設定をここに集める。** 接続先(プロファイル・オンライン/
オフライン・フォルダ・本体ファイル)、参照・出力(保存先・形式)、動作、
Access 取り込み、この端末。

【フォルダの「参照...」について】
ブラウザのファイル選択ダイアログは**クライアント側**のパスしか返さない
ので、サーバ(=このPC)から見たフォルダは選べない。代わりに
`GET /api/fs/list` がサーバ側のフォルダを一覧する(python-web-tools と同じ)。
"""
from __future__ import annotations

from flask import Blueprint, jsonify, render_template, request

from dbviewer.logging_utils import get_logger
from dbviewer import trace
from dbviewer.presenters import fs_browse
from dbviewer.presenters import settings as settings_presenter

from ..shell import shell_context

log = get_logger("app.routes.settings")

bp = Blueprint("settings", __name__)


@bp.get("/settings")
def page():
    view = settings_presenter.build()
    state = settings_presenter.to_dict(view)
    return render_template(
        "settings.html",
        view=view, state=state,
        tabs=settings_presenter.TABS,
        default_tab=settings_presenter.DEFAULT_TAB,
        tab_badges=state["tab_badges"],
        level_label=settings_presenter.LEVEL_LABEL,
        **shell_context("settings", badges=_badge(view)),
    )


def _badge(view) -> dict[str, tuple[str, str]]:
    if view.level in (settings_presenter.WARN, settings_presenter.NG):
        return {"settings": (settings_presenter.LEVEL_LABEL[view.level], "todo")}
    return {}


@bp.get("/api/settings/state")
def state():
    return jsonify(settings_presenter.to_dict(settings_presenter.build()))


@bp.post("/api/settings/save")
def save():
    body = request.get_json(silent=True) or {}
    result = settings_presenter.save(body)
    trace.event("settings.save", result.message, level="INFO" if result.ok else "WARN", data=body)
    if not result.ok:
        return jsonify({"error": {"code": result.reason, "message": result.message,
                                  "field": result.field}}), 400
    out = settings_presenter.to_dict(settings_presenter.build())
    out["message"] = result.message
    return jsonify(out)


@bp.get("/api/settings/db-files")
def db_files_list():
    """接続先フォルダの中身(開ける .sqlite3 / 変換すれば開ける Access / その他)。`?path=` でフォルダ指定。"""
    from pathlib import Path

    from dbviewer import config as cfg, db_files, shared_db
    text = request.args.get("path", "").strip()
    folder = cfg.resolve_dir(text) if text else shared_db.server_dir()
    return jsonify(db_files.scan(folder, current=shared_db.db_path()))


@bp.get("/api/settings/db-choices")
def db_choices():
    """選べる DB: 設定ファイルの接続先と、同じフォルダの .sqlite3(足せる人だけ)。上の帯の「DB」と 接続先 の一覧。"""
    from dbviewer import auth, db_files
    return jsonify(db_files.choices(can_add=auth.ensure().can_edit))


@bp.post("/api/settings/db-forget")
def db_forget():
    """接続先から外す(ファイルは消さない)。編集可 の人だけ。いま開いている DB は外せない。"""
    from dbviewer import auth, db_files, settings_file, shared_db
    path = str((request.get_json(silent=True) or {}).get("path", ""))
    s = auth.ensure()
    if not _may_edit_dbs():
        return jsonify({"error": {"code": "level", "message": MSG_DB_LOCKED}}), 403
    current = shared_db.is_set() and settings_file.same_file(path, shared_db.configured())
    if not settings_file.remove_db(path):
        return jsonify({"error": {"code": "gone", "message": "その接続先はもうありません。"}}), 404
    if current:
        # いま開いている DB を外した → この PC の選択も外す(0.31.0。外したのに開いたまま、にしない)
        _clear_selection()
    trace.event("db.forget", "接続先から外した", data={"path": path, "by": s.user_id, "current": current})
    out = db_files.choices(can_add=_can_edit())
    out["cleared"] = current
    if current:
        out["message_done"] = "いま開いていた DB を接続先から外し、選択も外しました(ファイルは消していません)。"
    return jsonify(out)


def _clear_selection() -> None:
    from dbviewer import config, user_settings
    # 空の文字で覚える(無い = まだ選んでいない、だと接続先のいちばん上を自動で選んでしまう)
    user_settings.save(config.KEY_DB_PATH, "")


@bp.post("/api/settings/db-clear")
def db_clear():
    """見る DB の選択を外す(0.31.0)。この PC だけの設定なので誰でも。接続先の一覧はそのまま。"""
    from dbviewer import db_files, shared_db
    was = shared_db.configured()
    _clear_selection()
    trace.event("db.clear", "見る DB の選択を外した", data={"path": was})
    out = db_files.choices(can_add=_can_edit())
    out["message_done"] = "見る DB の選択を外しました(接続先の一覧はそのまま)。" if was else "もともと選んでいません。"
    return jsonify(out)


def _can_edit() -> bool:
    from dbviewer import auth
    try:
        return bool(auth.ensure().can_edit)
    except Exception:                                   # noqa: BLE001 - 分からなければ足す欄を出さないだけ
        return False


@bp.post("/api/settings/open-db")
def open_db():
    """その .sqlite3 を接続先にする。"""
    body = request.get_json(silent=True) or {}
    result = settings_presenter.open_db(str(body.get("path", "")))
    trace.event("db.open", result.message, level="INFO" if result.ok else "WARN", data={"path": body.get("path")})
    if not result.ok:
        return jsonify({"error": {"code": result.reason, "message": result.message, "field": result.field}}), 400
    out = settings_presenter.to_dict(settings_presenter.build())
    out["message"] = result.message
    return jsonify(out)


# ------------------------------------------------------------------
# 表を持ってくる(python-web-tools の table_bring を移植)
# ------------------------------------------------------------------
# 本体の表を足す・入れ替えるので、VBA のいちばん上のレベル(マスタ管理で
# 全部の表を直せる Level 5)でログインしているときだけ通す。
# python-web-tools の「管理者パスワード」の関門にあたる
BRING_LEVEL = 5


def _bring_refused():
    from dbviewer import auth
    s = auth.ensure()                              # 画面を開いた PC の権限(0.16.0。パスワードは無い)
    if s.level < BRING_LEVEL:
        return jsonify({"error": {"code": "not_allowed", "message":
                                  "表を持ってくる・入れ替えるには 編集可 の権限が要ります"
                                  f"(いまは {s.label if s else '未確認'})。"}}), 403
    return None


@bp.get("/api/settings/table-bring/plan")
def table_bring_plan():
    """Access で作った表を持ってくる ── まず中を見る(書かない)。`?path=`"""
    from dbviewer import table_bring
    return jsonify(table_bring.plan_dict(table_bring.plan(request.args.get("path", ""))))


@bp.post("/api/settings/table-bring/upload")
def table_bring_upload():
    """ドラッグ&ドロップされたファイルを受け取る。返すのは置いた場所。

    ブラウザはファイルの**場所**を教えてくれない(中身だけ渡す)。このツールは
    同じPCで動いているので、手元の作業フォルダへ置いて、そこから読む。
    """
    from dbviewer import table_bring
    upload = request.files.get("file")
    if upload is None or not upload.filename:
        return jsonify({"error": {"code": "no_file", "message": "ファイルが届きませんでした"}}), 400
    saved, why = table_bring.save_upload(upload.filename, upload.stream)
    if saved is None:
        return jsonify({"error": {"code": "bad_file", "message": why}}), 400
    return jsonify({"path": str(saved)})


@bp.post("/api/settings/table-bring")
def table_bring_run():
    """選んだ表を本体へ足す。`{"path":…, "tables":[…]}`"""
    from dbviewer import table_bring
    refused = _bring_refused()
    if refused:
        return refused
    body = request.get_json(silent=True) or {}
    tables = body.get("tables")
    if not isinstance(tables, list):
        return jsonify({"error": {"code": "bad_tables", "message": "持ってくる表の指定が正しくありません"}}), 400
    result = table_bring.bring(str(body.get("path", "")), [str(t) for t in tables])
    trace.event("table.bring", result.message, level="INFO" if result.ok else "WARN",
                data={"path": body.get("path"), "tables": tables, "backup": result.backup, "reason": result.reason})
    payload = {"ok": result.ok, "message": result.message,
               "brought": [{"name": n, "rows": r} for n, r in result.brought],
               "backup": result.backup,
               "plan": table_bring.plan_dict(table_bring.plan(str(body.get("path", ""))))}
    if result.ok:
        return jsonify(payload)
    payload["error"] = {"code": result.reason, "message": result.message}
    status = {table_bring.REFUSE_EXISTS: 409,
              table_bring.REFUSE_NOTHING: 400}.get(result.reason, 422)
    return jsonify(payload), status


@bp.post("/api/settings/table-refresh")
def table_refresh_run():
    """もうある表を Access の最新にする(入れ替える・作り直す)。`{"path":…, "tables":[…]}`"""
    from dbviewer import table_bring
    refused = _bring_refused()
    if refused:
        return refused
    body = request.get_json(silent=True) or {}
    tables = body.get("tables")
    if not isinstance(tables, list):
        return jsonify({"error": {"code": "bad_tables", "message": "入れ替える表の指定が正しくありません"}}), 400
    result = table_bring.refresh(str(body.get("path", "")), [str(t) for t in tables])
    trace.event("table.refresh", result.message, level="INFO" if result.ok else "WARN",
                data={"path": body.get("path"), "tables": tables, "backup": result.backup, "reason": result.reason})
    payload = {"ok": result.ok, "message": result.message,
               "refreshed": [{"name": n, "before": b, "after": a} for n, b, a in result.refreshed],
               "kept_old": [{"name": n, "old": o} for n, o in result.kept_old],
               "backup": result.backup,
               "plan": table_bring.plan_dict(table_bring.plan(str(body.get("path", ""))))}
    if result.ok:
        return jsonify(payload)
    payload["error"] = {"code": result.reason, "message": result.message}
    status = {table_bring.REFUSE_NOTHING: 400}.get(result.reason, 422)
    return jsonify(payload), status


@bp.post("/api/settings/accdb-import")
def accdb_import():
    """Access(.accdb)をいまの接続先の .sqlite3 に取り込む(長時間処理)。"""
    body = request.get_json(silent=True) or {}
    job, result = settings_presenter.start_accdb_import(str(body.get("accdb_source", "")),
                                                        str(body.get("accdb_target", "")))
    trace.event("accdb.convert", result.message, level="INFO" if result.ok else "WARN",
                data={"source": body.get("accdb_source"), "target": body.get("accdb_target"), "reason": result.reason})
    if not result.ok:
        status = 409 if result.reason == settings_presenter.REFUSE_BUSY else 400
        return jsonify({"error": {"code": result.reason, "message": result.message,
                                  "field": result.field}}), status
    return jsonify({"ok": True, "message": result.message,
                    "job": settings_presenter.job_dict(job)})


@bp.get("/api/fs/list")
def fs_list():
    """サーバ側のフォルダを一覧する(「参照…」)。"""
    view = fs_browse.browse(request.args.get("path", ""),
                            access=request.args.get("access") == "1")
    return jsonify(fs_browse.to_dict(view))


# ------------------------------------------------------------------
# 配布設定(0.30.0。`dbviewer/distribution.py`)。書き出す・読み込み直す・消すは管理者パスワード
# ------------------------------------------------------------------
def _dist_reply(result):
    from dbviewer import distribution
    trace.event("distribution", result.message, level="INFO" if result.ok else "WARN")
    if not result.ok:
        status = {distribution.REFUSE_NEED_PASSWORD: 403, distribution.REFUSE_FAILED: 500}.get(result.reason, 400)
        return jsonify({"error": {"code": result.reason, "message": result.message}}), status
    return jsonify({"dist": distribution.summary(), "message": result.message})


@bp.get("/api/dist/state")
def dist_state():
    from dbviewer import distribution
    return jsonify({"dist": distribution.summary()})


@bp.post("/api/dist/export")
def dist_export():
    """この PC のいまの設定を、配布設定として書き出す。"""
    from dbviewer import distribution
    body = request.get_json(silent=True) or {}
    items = body.get("items")
    if not isinstance(items, list) or not all(isinstance(x, str) for x in items):
        return jsonify({"error": {"code": "bad_input", "message": "入れる項目の形が違います。"}}), 400
    return _dist_reply(distribution.export(str(body.get("password", "")), items, bool(body.get("contents"))))


@bp.post("/api/dist/reapply")
def dist_reapply():
    from dbviewer import distribution
    body = request.get_json(silent=True) or {}
    return _dist_reply(distribution.reapply(str(body.get("password", ""))))


@bp.post("/api/dist/remove")
def dist_remove():
    from dbviewer import distribution
    body = request.get_json(silent=True) or {}
    return _dist_reply(distribution.remove(str(body.get("password", ""))))


# ------------------------------------------------------------------
# 接続先の登録(0.32.0)── 設定ファイルの「接続先」表をそのまま見て、足す・直す・消す
# ------------------------------------------------------------------
MSG_DB_LOCKED = ("接続先を直すのは 編集可 の人か、管理者パスワードで鍵を開けたときだけです"
                 "(かんたん設定 →「アクセス権限」の鍵と同じ)。")


def _may_edit_dbs() -> bool:
    from dbviewer import admin_password, auth
    try:
        if auth.ensure().can_edit:
            return True
    except Exception:                                   # noqa: BLE001 - 分からなければ鍵で判断
        pass
    return admin_password.session.is_open()


def _db_table() -> dict:
    from dbviewer import admin_password, config, db_files, settings_file, shared_db
    now = shared_db.configured()
    rows = []
    for r in settings_file.db_rows():
        text = str(r["パス"] or "").strip()
        p = config.resolve_dir(text) if text else None
        try:
            exists = bool(p and p.is_file())
        except OSError:
            exists = False
        rows.append({**r, "exists": exists, "current": bool(now and text) and settings_file.same_file(text, now)})
    return {"rows": rows, "file": str(settings_file.path()), "can_change": _may_edit_dbs(),
            "admin": admin_password.session.peek(), "current": now}


@bp.get("/api/settings/dbs")
def dbs_table():
    try:
        return jsonify(_db_table())
    except Exception as exc:                             # noqa: BLE001 - 届かない・壊れている
        return jsonify({"error": {"code": "unreadable", "message": f"設定ファイルの接続先を読めません: {exc}"}}), 409


@bp.post("/api/settings/dbs/save")
def dbs_save():
    """接続先の行を足す・直す(編集可 か 管理者パスワードの鍵)。"""
    from dbviewer import settings_file, shared_db
    if not _may_edit_dbs():
        return jsonify({"error": {"code": "locked", "message": MSG_DB_LOCKED}}), 403
    body = request.get_json(silent=True) or {}
    row = body.get("row") if isinstance(body.get("row"), dict) else {}
    seen = body.get("seen") if isinstance(body.get("seen"), dict) else None
    was_current = bool(seen) and shared_db.is_set() and settings_file.same_file(str(seen.get("パス") or ""),
                                                                                 shared_db.configured())
    try:
        ok, message, no = settings_file.save_db_row(row, seen)
    except Exception as exc:                             # noqa: BLE001 - ほかの PC が使っている など
        ok, message, no = False, f"書けませんでした(ほかの PC が使っているかもしれません): {exc}", 0
    trace.event("dbs.save", message, level="INFO" if ok else "WARN",
                data={"no": no, "row": {k: row.get(k) for k in settings_file.DB_COLUMNS}})
    if not ok:
        return jsonify({"error": {"code": "refused", "message": message}}), 422
    if was_current and not settings_file.same_file(str(row.get("パス") or ""), shared_db.configured()):
        # いま開いている DB のパスを直した → この PC の選択も新しいパスに(古いパスを見たまま、にしない)
        from dbviewer import config, user_settings
        user_settings.save(config.KEY_DB_PATH, str(row.get("パス") or "").strip().strip('"'))
        message += " いま開いている DB だったので、この PC の選択も新しいパスにしました。"
    out = _db_table()
    out["message"] = message
    out["reload"] = was_current
    return jsonify(out)


@bp.post("/api/settings/dbs/delete")
def dbs_delete():
    """接続先の行を消す(ファイルは消さない)。いま開いている DB なら選択も外す。"""
    from dbviewer import settings_file, shared_db
    if not _may_edit_dbs():
        return jsonify({"error": {"code": "locked", "message": MSG_DB_LOCKED}}), 403
    body = request.get_json(silent=True) or {}
    no = str(body.get("no") or "")
    if not no.isdigit():
        return jsonify({"error": {"code": "bad_input", "message": "消す行が分かりません。"}}), 400
    seen = body.get("seen") if isinstance(body.get("seen"), dict) else None
    try:
        ok, message, gone = settings_file.delete_db_row(int(no), seen)
    except Exception as exc:                             # noqa: BLE001
        ok, message, gone = False, f"消せませんでした(ほかの PC が使っているかもしれません): {exc}", None
    trace.event("dbs.delete", message, level="INFO" if ok else "WARN", data={"no": int(no), "row": gone or seen})
    if not ok:
        return jsonify({"error": {"code": "refused", "message": message}}), 409
    current = bool(gone) and shared_db.is_set() and settings_file.same_file(str(gone.get("パス") or ""),
                                                                             shared_db.configured())
    if current:
        _clear_selection()
        message += " いま開いていた DB だったので、見る DB の選択も外しました。"
    out = _db_table()
    out["message"] = message
    out["reload"] = current
    return jsonify(out)
