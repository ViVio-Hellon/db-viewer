"""設定画面

**この端末の設定をここに集める。** 接続先(プロファイル・オンライン/
オフライン・フォルダ・本体ファイル)、参照・出力・帳票のパス、動作、
Access 取り込み、この端末。

【フォルダの「参照...」について】
ブラウザのファイル選択ダイアログは**クライアント側**のパスしか返さない
ので、サーバ(=このPC)から見たフォルダは選べない。代わりに
`GET /api/fs/list` がサーバ側のフォルダを一覧する(python-web-tools と同じ)。
"""
from __future__ import annotations

from flask import Blueprint, jsonify, render_template, request

from dbviewer.logging_utils import get_logger
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
    if not result.ok:
        return jsonify({"error": {"code": result.reason, "message": result.message,
                                  "field": result.field}}), 400
    out = settings_presenter.to_dict(settings_presenter.build())
    out["message"] = result.message
    return jsonify(out)


@bp.get("/api/fs/list")
def fs_list():
    """サーバ側のフォルダを一覧する(「参照…」)。"""
    view = fs_browse.browse(request.args.get("path", ""),
                            access=request.args.get("access") == "1")
    return jsonify(fs_browse.to_dict(view))
