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


@bp.post("/api/settings/reset-mode")
def reset_mode():
    """VBA Login の LOCKBT ダブルクリック(起動モード設定情報の初期化)。"""
    result = settings_presenter.reset_mode()
    trace.event("settings.reset_mode", result.message)
    return jsonify({"ok": True, "message": result.message, "mode_set": settings_presenter.mode_is_set()})


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
