"""記録(なぜなぜ分析のため) ── 設定「ログ」の中身

- `POST /api/log/client` … ブラウザの中で起きたエラー(JavaScript)を残す
- `GET  /api/log/events` … 最近の記録(`?days=7&only=problems|all&q=&limit=300`)
- `GET  /api/log/chain`  … 記録番号の前後(`?rid=`)となぜなぜの下書き
- `POST /api/log/open`   … ログのフォルダをエクスプローラーで開く
- `GET  /api/log/sheet`  … なぜなぜシート(`?rid=`)。`POST` で残す `{rid, status, whys[5], measure, by}`
- `POST /api/log/export` … いまの絞り込みのまま CSV(ログの出力先の 書き出し へ。ダウンロードしない)
- `GET  /api/log/summary`… まだ対策済にしていないエラーの数(レールの見出し)

ログイン無しでも通す(`LOGIN_EXEMPT_PREFIXES`)。**ログインできないこと自体を
追うため**。設定画面と同じく起動トークンは要る(よそのページからは読めない)。
"""
from __future__ import annotations

import os

from flask import Blueprint, g, jsonify, request

from dbviewer import auth, config, trace, why_sheet
from dbviewer.presenters import log_view

bp = Blueprint("log", __name__)

# 1 回の報告で受け取る項目(これ以外は捨てる)
_CLIENT_KEYS = ("kind", "message", "source", "line", "col", "stack", "page", "where", "crumbs")


@bp.post("/api/log/client")
def client_error():
    body = request.get_json(silent=True) or {}
    data = {k: body.get(k) for k in _CLIENT_KEYS if body.get(k) not in (None, "")}
    page = trace.clean_path(str(body.get("page") or "")[:200])
    data.pop("page", None)
    # 「どこで」は送ってきた画面。この要求(POST /api/log/client)ではない
    trace.event("error.client", str(body.get("message") or "(文言なし)")[:400], level="ERROR",
                data=data, path=page or None, method="")
    g.recorded = True
    return jsonify({"ok": True, "rid": g.get("rid", "")})


def _int(name: str, default: int, lo: int, hi: int) -> int:
    try:
        return min(hi, max(lo, int(request.args.get(name, default))))
    except (TypeError, ValueError):
        return default


@bp.get("/api/log/events")
def events():
    days = _int("days", log_view.DEFAULT_DAYS, 1, 366)
    limit = _int("limit", 300, 1, 2000)
    only = request.args.get("only", "problems")
    q = request.args.get("q", "").strip()[:100]
    pc = request.args.get("pc", "").strip()[:60]
    return jsonify(log_view.events(days, only=only, q=q, limit=limit, pc=pc))


@bp.get("/api/log/chain")
def chain():
    rid = request.args.get("rid", "").strip()[:16]
    if not rid:
        return jsonify({"error": {"code": "bad_input", "message": "記録番号を入れてください。"}}), 400
    return jsonify(log_view.chain(rid))


@bp.post("/api/log/open")
def open_folder():
    folder = config.LOG_DIR
    if os.name != "nt":
        return jsonify({"error": {"code": "not_windows",
                                  "message": f"この PC ではフォルダを開けません。場所: {folder}"}}), 501
    try:
        folder.mkdir(parents=True, exist_ok=True)
        os.startfile(str(folder))                       # noqa: S606 - 同じ PC のフォルダを開くだけ
    except OSError as exc:
        return jsonify({"error": {"code": "open", "message": f"フォルダを開けませんでした: {exc}"}}), 400
    return jsonify({"ok": True, "dir": str(folder)})


@bp.get("/api/log/sheet")
def sheet():
    rid = request.args.get("rid", "").strip()[:16]
    return jsonify({"rid": rid, "sheet": why_sheet.get(rid) if rid else None,
                    "statuses": [[k, v] for k, v in why_sheet.STATUS_LABELS.items()]})


@bp.post("/api/log/sheet")
def save_sheet():
    body = request.get_json(silent=True) or {}
    rid = str(body.get("rid") or "").strip()[:16]
    if not rid:
        return jsonify({"error": {"code": "bad_input", "message": "記録番号がありません。"}}), 400
    s = auth.current()
    try:
        rec = why_sheet.save(rid, body, by=s.user_id if s else auth.network_id())
    except OSError as exc:
        return jsonify({"error": {"code": "write", "message": f"なぜなぜを残せませんでした: {exc}"}}), 409
    why_sheet.forget_cache()
    return jsonify({"ok": True, "sheet": rec, "status_label": why_sheet.STATUS_LABELS[rec["status"]],
                    "open_errors": why_sheet.open_errors()})


@bp.post("/api/log/export")
def export():
    body = request.get_json(silent=True) or {}
    try:
        days = min(366, max(1, int(body.get("days") or log_view.DEFAULT_DAYS)))
    except (TypeError, ValueError):
        days = log_view.DEFAULT_DAYS
    try:
        out = log_view.export(days, only=str(body.get("only") or "problems"),
                              q=str(body.get("q") or "").strip()[:100], pc=str(body.get("pc") or "").strip()[:60])
    except OSError as exc:
        return jsonify({"error": {"code": "write", "message": f"CSV を書けませんでした: {exc}"}}), 409
    trace.event("log.export", f"エラー一覧を書き出した({out['rows']} 行)", data=out)
    return jsonify({"ok": True, **out, "can_open": os.name == "nt"})


@bp.get("/api/log/summary")
def summary():
    return jsonify({"open_errors": why_sheet.open_errors_cached(), "days": why_sheet.OPEN_DAYS})
