"""ファイルの列のプレビュー(0.10.0)と、クリックで開く(0.13.0)

- `GET  /api/file/preview?table=&column=&value=` … アプリの窓の中で見る(プレビュー)
      PDF・画像・テキストはそのまま、Excel(.xlsx/.xlsm)・Word(.docx)は表と文にして(`office_preview`)、
      URL は窓の中の枠に出す。古い .xls/.doc は、その PC の Excel・Word に PDF にしてもらう(`office_bridge`。
      all-tools 点検表と同じ方式)。Python の標準で読めなかった .xlsx/.docx も同じ道に回す
- `POST /api/file/open {table, column, value}` … 現物を開く(Excel・Word・PDF を、その PC の既定のアプリで)。
      URL は画面が既定のブラウザで開くので、ここでは行き先を返すだけ

**その列に入っている値だけ**を開く(どこのファイルでも開ける口にしない)。現物を開く のは決まった形式だけ
(Excel・Word・PDF・画像・テキスト。実行ファイルなどは開かない)。相対パスは 設定「参照ファイルの起点」から。
ダウンロードはさせない(`Content-Disposition: inline`)。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time

from flask import Blueprint, Response, jsonify, request

from dbviewer import office_bridge, office_preview, session_setup, settings_tables, shared_db, simple_table, trace
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


def _url_page(url: str) -> Response:
    """URL のプレビュー: アプリの窓の中の枠に出す。相手が枠に出すのを断るときのために、既定のブラウザで開く道も置く。"""
    from html import escape
    from urllib.parse import unquote
    u = escape(url, quote=True)
    shown = escape(unquote(url))
    body = f"""<!doctype html><html lang="ja"><meta charset="utf-8"><title>{escape(url)}</title>
<style>html,body{{margin:0;height:100%;font:13px system-ui,"Yu Gothic UI",sans-serif}}
header{{display:flex;gap:10px;align-items:center;padding:6px 12px;border-bottom:1px solid #ccd5db;background:#eef3f5;color:#1b2730}}
header code{{overflow-wrap:anywhere}} a{{color:#0e6b84}} iframe{{border:0;width:100%;height:calc(100% - 40px)}}
@media (prefers-color-scheme: dark){{header{{background:#1a252c;color:#e2eaef;border-color:#2b3942}} a{{color:#5cc0d6}}}}</style>
<header><b>URL</b><code>{shown}</code><a id="ext" href="{u}" target="_blank" rel="noopener">既定のブラウザで開く</a>
<small>出ないときは、相手のページが枠の中に出すのを断っています。</small></header>
<iframe src="{u}" referrerpolicy="no-referrer"></iframe>
<script>/* デスクトップ版の窓は新しいタブを持たないので、外枠に既定のブラウザで開いてもらう */
document.getElementById("ext").addEventListener("click", function (e) {{
  var t = window.__TAURI__;
  if (t && t.core && t.core.invoke) {{ e.preventDefault(); t.core.invoke("open_external", {{ url: this.href }}); }}
}});</script></html>"""
    return Response(body, content_type="text/html; charset=utf-8")


def _rel(path, base) -> str:
    """候補の名札(探し始めたフォルダからの場所。/ で区切る)。選んだものを確かめるのに使う。"""
    try:
        return path.relative_to(base).as_posix()
    except ValueError:
        return path.name


def _where(table: str, column: str, value: str, need: str, rowid=None, pick: str = ""):
    """(種類, 行き先, 断りの応答)。種類は "url" / "file" / "choose"(同じ名前がいくつものフォルダにある。0.24.0)。

    rowid は場所の決まりに {列名} があるとき(その行の値で埋める。0.23.0)。pick は選んだ候補の名札
    (サーバが探し直した候補の中にあるものだけ受け付ける)。"""
    try:
        conn = get_db()
        session_setup.get_setup(conn)
    except (shared_db.DbNotFound, settings_tables.SetupError) as exc:
        return "", None, (str(exc), 409)
    kind, target, problem = simple_table.resolve(conn, table, column, value, ref_base(), need=need, rowid=rowid)
    if problem:
        return "", None, (problem, 403)
    if kind == "url":
        return kind, target, None
    if target is None:
        return "", None, (f"ファイルが見つかりません: {value}", 404)
    found = simple_table.find_files(target)
    if pick:
        # 選べるのは、サーバが探し直した候補(同じ名前)か、似た名前の候補だけ
        pool = found or simple_table.near_files(target)
        chosen = next((f for f in pool if _rel(f, target.parent) == pick), None)
        if chosen is None:
            return "", None, ("選んだファイルが見つかりません(動かされたかもしれません。もう一度押してください)。", 404)
        return "file", chosen, None
    if len(found) > 1:
        return "choose", (target, found, ""), None
    if not found:
        why = simple_table.explain_missing(target)
        log.info("ファイルが見つかりません: %s / %s", target, why)
        near = simple_table.near_files(target)
        if near:                                         # 似た名前があれば、選ばせる(勝手には開かない)
            return "choose", (target, near, "同じ名前のファイルはありませんでした。似た名前のファイルがあります。" + why), None
        return "", None, (f"ファイルが見つかりません: {target.name}。{why}", 404)
    return "file", found[0], None


def _choices(target, found) -> list[dict]:
    from datetime import datetime
    out = []
    for f in found:
        try:
            when = datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y/%m/%d %H:%M")
        except OSError:
            when = ""
        folder = _rel(f.parent, target.parent) if f.parent != target.parent else ""
        out.append({"pick": _rel(f, target.parent), "name": f.name, "folder": folder or "(このフォルダ)",
                    "mtime": when, "what": simple_table.open_kind(f)})
    return out


def _choose_page(target, found, note: str = "") -> Response:
    """同じ名前がいくつものフォルダにあるとき(または似た名前しか無いとき)、プレビューの窓で選ばせる(新しい順)。"""
    from html import escape
    from urllib.parse import urlencode
    args = {k: v for k, v in request.args.items() if k not in ("pick", "go", "sheet")}
    rows = "".join(
        f"<li><a href='{escape(request.path + '?' + urlencode({**args, 'pick': c['pick']}), quote=True)}'>"
        f"<b>{escape(c['folder'])}</b> / {escape(c['name'])}</a><small>{escape(c['mtime'])}・{escape(c['what'])}</small></li>"
        for c in _choices(target, found))
    body = (f"<!doctype html><html lang='ja'><meta charset='utf-8'><title>{escape(target.name)} を選ぶ</title>"
            "<style>body{font:14px system-ui,'Yu Gothic UI',sans-serif;padding:24px;color:#1b2730;background:#f6f8f9}"
            "@media (prefers-color-scheme: dark){body{color:#e2eaef;background:#11191f} a{color:#7cc4e0}}"
            "ul{list-style:none;padding:0;margin:12px 0} li{margin:0 0 8px}"
            "a{display:inline-block;padding:6px 10px;border:1px solid #9bb3bf;border-radius:6px;text-decoration:none;color:#0e6b84}"
            "small{margin-left:10px;color:#6b7c86}</style>"
            + (f"<p>{escape(note)}</p><p>見るものを選んでください(新しい順)。</p>" if note else
               f"<p>「{escape(target.name)}」と同じ名前のファイルが <b>{len(found)} か所</b>にあります。見るものを選んでください"
               "(新しい順)。</p>") +
            f"<ul>{rows}</ul></html>")
    return Response(body, content_type="text/html; charset=utf-8")


@bp.get("/api/file/preview")
def preview():
    table = request.args.get("table", "")
    column = request.args.get("column", "")
    value = request.args.get("value", "")
    kind, target, refuse = _where(table, column, value, simple_table.CLICK_PREVIEW, request.args.get("rowid"),
                                  request.args.get("pick", ""))
    if refuse:
        return _page(*refuse)
    if kind == "choose":
        return _choose_page(*target)
    if kind == "url":
        trace.event("file.preview", target, data={"table": table, "column": column, "url": target})
        return _url_page(target)
    suffix = target.suffix.lower()
    if suffix in simple_table.OFFICE_PREVIEW:
        try:
            html = office_preview.xlsx_html(target) if suffix != ".docx" else office_preview.docx_html(target)
        except office_preview.PreviewError as exc:
            if not office_bridge.available():
                return _page(f"{target.name}: {exc}", 415)
            log.info("標準では読めないので Office に頼みます: %s (%s)", target, exc)
            return _via_office(target, table, column)
        trace.event("file.preview", target.name, data={"table": table, "column": column, "path": str(target)})
        return Response(html, content_type="text/html; charset=utf-8")
    if office_bridge.kind_of(target):
        return _via_office(target, table, column)
    mime = simple_table.PREVIEW_TYPES.get(suffix)
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


def _waiting(target, app: str) -> Response:
    """Excel・Word が PDF にするあいだの画面。すぐ同じ所を go=1 で開き直す(答えが来るまでこの画面が残る)。"""
    from html import escape
    body = (f"<!doctype html><html lang='ja'><meta charset='utf-8'><title>{escape(target.name)}</title>"
            f"<style>body{{font:14px system-ui,'Yu Gothic UI',sans-serif;padding:32px;color:#1b2730;background:#f6f8f9}}"
            f"@media (prefers-color-scheme: dark){{body{{color:#e2eaef;background:#11191f}}}}</style>"
            f"<p><b>{escape(target.name)}</b></p><p>{app} で PDF にしています… "
            f"(はじめは 10〜30 秒かかります。この窓は閉じずにお待ちください)</p>"
            "<script>var u=new URL(location.href);u.searchParams.set('go','1');location.replace(u.toString());</script></html>")
    return Response(body, content_type="text/html; charset=utf-8")


def _via_office(target, table: str, column: str) -> Response:
    """.xls / .doc(や標準で読めなかったもの): その PC の Excel・Word に PDF にしてもらう。"""
    app = office_bridge.APP_LABEL[office_bridge.kind_of(target)]
    if not office_bridge.available():
        return _page(f"{target.name} は {app} の形式です。この PC では {app} に頼めないので、プレビューできません"
                     f"(Windows で動かしてください)。「現物を開く」で見てください。", 415)
    if request.args.get("go") != "1":
        return _waiting(target, app)
    started = time.monotonic()
    try:
        got = office_bridge.convert(target)
    except office_bridge.ConvertError as exc:
        trace.event("file.preview_failed", target.name, level="WARN",
                    data={"table": table, "column": column, "path": str(target), "code": exc.code, "detail": exc.detail})
        return _page(f"{target.name}: {exc}", 422)
    trace.event("file.preview", target.name, data={"table": table, "column": column, "path": str(target),
                                                   "via": app, "as": got.kind,
                                                   "sec": round(time.monotonic() - started, 1)})
    if got.kind == "sheets":
        return _sheets(target, got)
    if got.kind == "pdf":
        return _pdf(got.data, target.stem)
    banner = f"{app} で PDF にできなかったので、中身だけを出しています(書式・図は出しません)。"
    try:
        html = (office_preview.xlsx_html(target, got.data, banner=banner) if got.kind == "xlsx"
                else office_preview.docx_html(target, got.data, banner=banner))
    except office_preview.PreviewError as exc:
        return _page(f"{target.name}: {exc}", 415)
    return Response(html, content_type="text/html; charset=utf-8")


def _pdf(data: bytes, stem: str) -> Response:
    from urllib.parse import quote
    resp = Response(data, content_type="application/pdf")
    resp.headers["Content-Disposition"] = f"inline; filename*=UTF-8''{quote(stem)}.pdf"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


def _sheets(target, got) -> Response:
    """Excel がシートごとに作った PDF(0.15.0)。`sheet` が無ければシートのタブの画面、あればそのシートの PDF。
    PDF は覚えてあるので、タブを押すたびに Excel を起こさない。"""
    from urllib.parse import urlencode
    raw = request.args.get("sheet")
    if raw is not None:
        if not raw.isdigit() or int(raw) >= len(got.sheets):
            return _page("そのシートはありません。", 404)
        sh = got.sheets[int(raw)]
        if not sh.data:
            return _page(f"{sh.name}: {sh.note or 'PDF にできませんでした'}", 422)
        return _pdf(sh.data, f"{target.stem}_{sh.name}")
    args = {k: v for k, v in request.args.items() if k != "sheet"}
    args["go"] = "1"

    def src(i: int) -> str:
        return f"{request.path}?{urlencode({**args, 'sheet': str(i)})}"

    rows = [{"name": sh.name, "ok": bool(sh.data), "note": "" if sh.data else "空"} for sh in got.sheets]
    html = office_preview.pdf_sheets_html(target, rows, src, hidden=got.hidden_sheets)
    return Response(html, content_type="text/html; charset=utf-8")


def launch(path) -> None:
    """その PC の既定のアプリで開く(Windows は Excel・Word・PDF の読み手)。"""
    if os.name == "nt":
        os.startfile(str(path))                         # noqa: S606 - 形式は OPEN_KINDS だけに絞ってある
        return
    opener = shutil.which("xdg-open") or shutil.which("open")
    if not opener:
        raise OSError("この PC には開くアプリがありません")
    subprocess.Popen([opener, str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)


@bp.post("/api/file/open")
def open_file():
    body = request.get_json(silent=True) or {}
    table, column = str(body.get("table") or ""), str(body.get("column") or "")
    value = "" if body.get("value") is None else str(body.get("value"))
    kind, target, refuse = _where(table, column, value, simple_table.CLICK_OPEN, body.get("rowid"),
                                  str(body.get("pick") or ""))
    if refuse:
        message, status = refuse
        return jsonify({"error": {"code": "open", "message": message}}), status
    if kind == "choose":                                  # 同じ名前がいくつものフォルダに → 画面で選ばせる
        base, found, note = target
        return jsonify({"ok": False, "kind": "choose", "choices": _choices(base, found),
                        "message": (note + " 開くものを選んでください。") if note else
                        f"「{base.name}」と同じ名前のファイルが {len(found)} か所にあります。開くものを選んでください。"})
    if kind == "url":
        return jsonify({"ok": True, "kind": "url", "url": target})
    what = simple_table.open_kind(target)
    if not what:
        return jsonify({"error": {"code": "open", "message":
                                  f"この形式({target.suffix or '拡張子なし'})は開きません: {target.name}"
                                  "(開けるのは Excel・Word・PDF・画像・テキスト)"}}), 415
    try:
        launch(target)
    except OSError as exc:
        log.warning("開けませんでした: %s (%s)", target, exc)
        return jsonify({"error": {"code": "open", "message": f"開けませんでした: {exc}"}}), 500
    trace.event("file.open", target.name, data={"table": table, "column": column, "path": str(target), "kind": what})
    return jsonify({"ok": True, "kind": "file", "what": what, "name": target.name,
                    "message": f"{what}で開きました: {target.name}"})
