"""テーブル閲覧と出力

- `GET  /browse`               … 画面
- `POST /api/browse/tables`    … 業務の表ぜんぶ(0.8.0。全般設定「テーブル閲覧」の表も)
- `POST /api/browse/add-column` … 見ている表に列を足す(Level 5。マスタ管理の「列を追加」と同じ決まり)
- `POST /api/browse/data`      … 表の全行(かんたん設定の「表示」の列)・列の種類・列の見せ方・印刷する列
- `POST /api/browse/view`      … 列の見せ方を保存する
- `GET  /api/export/settings`  … 出力先・既定の形式・文字コード
- `POST /api/export`           … 画面に出している表をファイルに書く(ダウンロードはしない)
- `POST /api/export/open`      … 出力先のフォルダを開く(Windows のエクスプローラー)
"""
from __future__ import annotations

import os
from pathlib import Path

from flask import Blueprint, jsonify, render_template, request

from dbviewer import (app_config, auth, config, grid_data, master_admin, session_setup, settings_tables,
                      shared_db, simple_table, table_browse, user_settings)
from dbviewer.logging_utils import get_logger
from dbviewer import trace

from .. import get_db
from ..shell import shell_context

log = get_logger("app.routes.browse")

bp = Blueprint("browse", __name__)

# 一度に書く行の上限(画面から送る表の大きさの歯止め)
MAX_EXPORT_ROWS = 200_000


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


export_dir = table_browse.export_dir


def export_settings() -> dict:
    fmt = str(user_settings.get(config.KEY_EXPORT_FORMAT, "") or table_browse.DEFAULT_FORMAT)
    enc = str(user_settings.get(config.KEY_EXPORT_ENCODING, "") or table_browse.DEFAULT_ENCODING)
    return {"dir": str(export_dir()),
            "format": fmt if fmt in table_browse.FORMATS else table_browse.DEFAULT_FORMAT,
            "encoding": enc if enc in table_browse.ENCODINGS else table_browse.DEFAULT_ENCODING,
            "encodings": table_browse.ENCODINGS,
            "can_open": os.name == "nt"}


@bp.get("/browse")
def page():
    return render_template("browse.html", **shell_context("browse"))


@bp.post("/api/browse/tables")
def tables():
    conn, setup, err = _open()
    if err:
        return err
    names = _browse_tables(conn, setup)
    return jsonify({"tables": names, "browse_flag": True,
                    "configured": setup.general.browse_flag,
                    "can_add_column": auth.level() >= master_admin.COLUMN_LEVEL,
                    "kinds": [[k, label] for k, (_t, label) in master_admin.COLUMN_KINDS.items()]})


def _browse_tables(conn, setup) -> list[str]:
    """業務の表ぜんぶ(0.8.0)。全般設定「テーブル閲覧」に書いた表(タイプなど)があれば、それも足す。"""
    names = settings_tables.user_tables(conn)
    have = set(settings_tables.table_names(conn))
    extra = [t for t in table_browse.browse_tables(setup.general.browse_str) if t in have and t not in names]
    return names + extra


def _table(setup) -> tuple[str, object]:
    table = str((request.get_json(silent=True) or {}).get("table") or "")
    if table not in _browse_tables(get_db(), setup):
        return table, _refuse("table", "その表は閲覧の対象ではありません。", 403)
    return table, None


def _view_key(table: str) -> str:
    return grid_data.view_key("browse", shared_db.current_profile().prog_no, table)


@bp.post("/api/browse/data")
def data():
    conn, setup, err = _open()
    if err:
        return err
    table, err = _table(setup)
    if err:
        return err
    try:
        v = simple_table.view(conn, table)
    except ValueError as exc:
        return _refuse("table", str(exc))
    rows = [r[:-1] for r in v["rows"]]                    # 末尾の rowid は閲覧では要らない
    return jsonify({"table": table, "columns": grid_data.columns_meta(v["columns"], rows),
                    "rows": rows, "count": len(rows), "key_index": -1,
                    # かんたん設定の「印刷」: 印刷・保存・コピーに入れる列
                    "print_columns": v["print_columns"],
                    "actions": simple_table.actions(simple_table.load(conn, table)),
                    "view": grid_data.load_view(_view_key(table), v["columns"])})


@bp.post("/api/browse/view")
def view():
    conn, setup, err = _open()
    if err:
        return err
    table, err = _table(setup)
    if err:
        return err
    cols = simple_table.view(conn, table)["columns"]
    body = request.get_json(silent=True) or {}
    if body.get("reset"):
        return jsonify({"view": grid_data.reset_view(_view_key(table), cols)})
    return jsonify({"view": grid_data.save_view(_view_key(table), cols, body)})


@bp.post("/api/browse/add-column")
def add_column():
    """見ている表の末尾に列を足す。本体なら <メイン>_設定 にも行を足す(マスタ管理と同じ)。"""
    conn, setup, err = _open()
    if err:
        return err
    table, err = _table(setup)
    if err:
        return err
    body = request.get_json(silent=True) or {}
    r = master_admin.add_column(conn, setup, table, str(body.get("name") or ""),
                                kind=str(body.get("kind") or "text"), fill=str(body.get("fill") or ""),
                                level=auth.level())
    trace.event("browse.add_column", r.message, level="INFO" if r.ok else "WARN",
                data={"table": table, "name": body.get("name"), "kind": body.get("kind"),
                      "reason": r.reason, "setting_table": r.setting_table, "backup": r.backup})
    status = 200 if r.ok else 403 if r.reason == master_admin.REFUSE_LEVEL else 422
    return jsonify({"ok": r.ok, "message": r.message, "reason": r.reason, "column": r.column,
                    "setting_table": r.setting_table, "setting_key": r.setting_key,
                    "backup": r.backup}), status


@bp.get("/api/export/settings")
def export_info():
    return jsonify(export_settings())


@bp.post("/api/export")
def export():
    """画面に出している表(列と行)を書く。`{"columns":[…],"rows":[[…]],"name":…,"format":…,"encoding":…}`"""
    body = request.get_json(silent=True) or {}
    columns = body.get("columns")
    rows = body.get("rows")
    if not isinstance(columns, list) or not isinstance(rows, list) or not columns:
        return _refuse("bad_table", "出す表の指定が正しくありません。", 400)
    if not rows:
        return _refuse("empty", table_browse.MSG_EMPTY)
    if len(rows) > MAX_EXPORT_ROWS:
        return _refuse("too_many", f"一度に出せるのは {MAX_EXPORT_ROWS:,} 行までです。")
    columns = [str(c) for c in columns]
    rows = [["" if v is None else str(v) for v in (r if isinstance(r, list) else [])] for r in rows]
    info = export_settings()
    fmt = str(body.get("format") or info["format"])
    enc = str(body.get("encoding") or info["encoding"])
    name = str(body.get("name") or "").strip() or table_browse.default_name()
    try:
        path = table_browse.export(columns, rows, export_dir(), name, fmt, enc)
    except (ValueError, OSError) as exc:
        return _refuse("export", f"出力できませんでした: {exc}")
    log.info("出力: %s (%d 行 × %d 列)", path, len(rows), len(columns))
    trace.event("export", str(path), data={"rows": len(rows), "columns": columns, "format": fmt, "encoding": enc})
    return jsonify({"ok": True, "message": table_browse.MSG_DONE, "path": str(path),
                    "dir": str(path.parent), "rows": len(rows), "can_open": info["can_open"]})


@bp.post("/api/export/open")
def open_folder():
    """出力先のフォルダをエクスプローラーで開く(このアプリは同じ PC で動いている)。"""
    folder = export_dir()
    if os.name != "nt":
        return _refuse("not_windows", f"この PC ではフォルダを開けません。場所: {folder}", 501)
    try:
        folder.mkdir(parents=True, exist_ok=True)
        os.startfile(str(folder))                       # noqa: S606 - 同じ PC のフォルダを開くだけ
    except OSError as exc:
        return _refuse("open", f"フォルダを開けませんでした: {exc}")
    return jsonify({"ok": True, "dir": str(folder)})
