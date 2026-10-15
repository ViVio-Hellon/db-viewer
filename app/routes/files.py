"""ファイルの列のプレビュー(0.10.0)

- `GET /api/file/preview?table=&column=&value=` … その表のその列に入っている値のファイルを、アプリの窓の中で見せる

ほかのアプリでは開かない(VBA の ShellExecute はやめた)。見せるのは PDF・画像・テキストだけ。
**その列に入っている値だけ**を見せる(どこのファイルでも読める口にしない)。相対パスは 設定「参照ファイルの起点」から。
ダウンロードはさせない(`Content-Disposition: inline`)。
"""
from __future__ import annotations

from flask import Blueprint, Response, request

from dbviewer import session_setup, settings_tables, shared_db, simple_table, trace
from dbviewer.logging_utils import get_logger

from .. import get_db
from .record import ref_base

log = get_logger("app.routes.files")

bp = Blueprint("files", __name__)


def _page(message: str, status: int) -> Response:
    """窓に出す断りの文(プレビューの窓の中に出るので、HTML の短い文)。"""
    from html import escape
    body = (f"<!doctype html><meta charset='utf-8'><title>プレビュー</title>"
            f"<body style='font-family:sans-serif;padding:24px'><p>{escape(message)}</p></body>")
    return Response(body, status=status, content_type="text/html; charset=utf-8")


@bp.get("/api/file/preview")
def preview():
    table = request.args.get("table", "")
    column = request.args.get("column", "")
    value = request.args.get("value", "")
    try:
        conn = get_db()
        session_setup.get_setup(conn)
    except (shared_db.DbNotFound, settings_tables.SetupError) as exc:
        return _page(str(exc), 409)
    problem = simple_table.preview_check(conn, table, column, value)
    if problem:
        return _page(problem, 403)
    target = simple_table.file_target(value, ref_base())
    if target is None or not target.is_file():
        return _page(f"ファイルが見つかりません: {target or value}", 404)
    mime = simple_table.PREVIEW_TYPES.get(target.suffix.lower())
    if mime is None:
        return _page(f"この形式({target.suffix or '拡張子なし'})はプレビューできません: {target.name}", 415)
    try:
        data = target.read_bytes()
    except OSError as exc:
        return _page(f"ファイルを読めません: {exc}", 404)
    if mime == "text/plain":
        # 社内のテキストは Shift_JIS のことが多い。読めた文字コードで UTF-8 にそろえて見せる
        for enc in ("utf-8-sig", "cp932"):
            try:
                data = data.decode(enc).encode("utf-8")
                break
            except UnicodeDecodeError:
                continue
        mime = "text/plain; charset=utf-8"
    trace.event("file.preview", target.name, data={"table": table, "column": column, "path": str(target)})
    from urllib.parse import quote
    resp = Response(data, content_type=mime)
    resp.headers["Content-Disposition"] = f"inline; filename*=UTF-8''{quote(target.name)}"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp
