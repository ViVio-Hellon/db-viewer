"""レコードの編集画面(VBA MF1 の編集ページ: 新規 / 修正 / 削除 / 参照)

- `GET  /record?mode=&key=&click=`  … 画面
- `POST /api/record/form`           … 欄の一式(レベル判定つき)
- `POST /api/record/execute`        … 実行(質問 → はい/いいえ → 書く)
- `POST /api/record/choices`        … 補助入力の選択肢(マスタ / 連動 / フィールド / フォルダ)
- `POST /api/record/new-code`       … calender 連動の採番(GetNewCode)
- `POST /api/record/open-file`      … 参照モードのファイルを開く(Windows で ShellExecute)
- `GET  /api/record/icon?path=`     … ICON 画像
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from flask import Blueprint, jsonify, render_template, request, send_file

from dbviewer import (auth, choices, config, editing, numbering, records, session_setup,
                      settings_tables, shared_db, user_settings)
from dbviewer.logging_utils import get_logger
from dbviewer import trace
from dbviewer.vba_compat import cdate

from .. import get_db
from ..shell import shell_context

log = get_logger("app.routes.record")

bp = Blueprint("record", __name__)


def _refuse(code: str, message: str, status: int = 422, **extra):
    body = {"error": {"code": code, "message": message}}
    body.update(extra)
    return jsonify(body), status


def _open():
    try:
        conn = get_db()
        setup = session_setup.get_setup(conn)
    except shared_db.DbNotFound as exc:
        return None, None, _refuse("db_not_found", str(exc), 409)
    except settings_tables.SetupError as exc:
        return None, None, _refuse("setup", str(exc), 409)
    return conn, setup, None


def ref_base() -> Path:
    """参照形式の**相対パスの起点**。設定「参照ファイルの起点」、空なら VBA と同じ SrvPath。"""
    ref = str(user_settings.get(config.KEY_REF_DIR, "") or "").strip()
    server = shared_db.server_dir()
    return config.resolve_dir(ref, server) if ref else server


def _dirs() -> dict:
    base = ref_base()
    # server_dir = 相対パスの起点(VBA `SrvPath & "\" & 相対パス`)。設定で変えられる
    return {"base_dir": config.BASE_DIR, "server_dir": base, "ref_dir": base}


@bp.get("/record")
def page():
    ctx = shell_context("list")
    ctx.update(rec_mode=request.args.get("mode", "参照"), rec_key=request.args.get("key", ""),
               rec_click=request.args.get("click", "ButtonClick"))
    return render_template("record.html", **ctx)


@bp.post("/api/record/form")
def form():
    conn, setup, err = _open()
    if err:
        return err
    body = request.get_json(silent=True) or {}
    mode = str(body.get("mode") or "参照")
    key = str(body.get("key") or "")
    click = str(body.get("click") or "ButtonClick")
    mode, refuse = auth.check_mode(mode, auth.level(), click)
    if refuse:
        return _refuse("level", refuse, 403)
    if mode != "新規" and key == "":
        return _refuse("not_selected", editing.MSG_NOT_SELECTED)
    d = _dirs()
    raw_keys = body.get("list_keys")
    list_keys = [str(x) for x in raw_keys][:50_000] if isinstance(raw_keys, list) else None
    form = editing.build_form(conn, setup, mode, key, list_sql=str(body.get("list_sql") or ""),
                              list_keys=list_keys,
                              base_dir=d["base_dir"], server_dir=d["server_dir"])
    out = form.to_dict()
    out["level"] = auth.level()
    out["label"] = auth.current().label if auth.current() else ""
    out["main_table"] = setup.general.main_table
    return jsonify(out)


@bp.post("/api/record/execute")
def execute():
    conn, setup, err = _open()
    if err:
        return err
    body = request.get_json(silent=True) or {}
    mode = str(body.get("mode") or "")
    key = str(body.get("key") or "")
    mode, refuse = auth.check_mode(mode, auth.level())
    if refuse:
        return _refuse("level", refuse, 403)
    if mode not in ("新規", "修正", "削除"):
        return _refuse("mode", "参照モードでは実行できません。")
    s = auth.current()
    form = editing.build_form(conn, setup, "修正" if mode != "新規" else "新規", key)
    if mode == "新規":
        # 画面が採番した番号をそのまま使う(VBA も SET_BEF で採番した値を ITB に持つ)
        form.key = key or form.key
        for f in form.fields:
            if f.is_key:
                f.value = f.original = form.key
    fields = editing.fields_from_payload(form, body.get("values") or {}, body.get("originals") or {})
    seen = None
    if mode in ("修正", "削除") and body.get("originals"):
        seen = [str((body.get("originals") or {}).get(f.name, f.original)) for f in fields]
        # 管理番号は画面の値
        if setup.fj.get(config.KEY_ITEM):
            seen[setup.fj[config.KEY_ITEM] - 1] = key
    answer = body.get("answer")
    result = editing.execute(conn, setup, mode, fields, user=s.user_id if s else auth.network_id(),
                             pc=auth.pc_name(), confirmed=bool(body.get("confirmed")),
                             answer=None if answer is None else bool(answer), seen=seen)
    status = 200 if result.ok or result.reason == "question" else (409 if result.reason == "conflict" else 422)
    if result.reason != "question":
        changes = {f.name: [f.original, f.value] for f in fields if f.value != f.original}
        trace.event("record.save", result.message, level="INFO" if result.ok else "WARN",
                    data={"mode": mode, "key": form.key, "executed": result.executed,
                          "reason": result.reason, "changes": changes, "fields_ng": result.fields_ng})
    return jsonify({"ok": result.ok, "message": result.message, "reason": result.reason,
                    "fields_ng": result.fields_ng, "values": result.values,
                    "reset_filters": result.reset_filters, "executed": result.executed,
                    "key": form.key}), status


@bp.post("/api/record/choices")
def record_choices():
    conn, setup, err = _open()
    if err:
        return err
    body = request.get_json(silent=True) or {}
    kind = str(body.get("kind") or "")
    if kind == "master":
        m = choices.master(conn, str(body.get("name") or ""), multi=bool(body.get("multi")),
                           parent=str(body.get("parent") or "MF1"))
    elif kind == "link":
        m = choices.master_link(conn, str(body.get("ms_join") or ""), multi=bool(body.get("multi")))
    elif kind == "field":
        # 一覧の列(マスタ結合・追加表示の列も)。VBA は「いまの一覧の列名」
        list_sql = str(body.get("list_sql") or "")
        cols = list(setup.main_columns)
        try:
            cols, _ = records.run_query(conn, list_sql) if list_sql else records.full_list(conn, setup)
        except (records.ListError, TypeError):
            pass
        return jsonify({"kind": kind, "items": cols})
    elif kind == "folder":
        d = _dirs()
        info = choices.tag_info(str(body.get("dsp_type") or ""), base_dir=d["base_dir"],
                                server_dir=d["server_dir"])
        return jsonify({"kind": kind, "items": choices.folder_files(info.directory, info.kind),
                        "directory": str(info.directory)})
    else:
        return _refuse("kind", "未知の選択肢です。")
    if m.message:
        return _refuse("master", m.message)
    return jsonify({"kind": kind, "name": m.name, "columns": m.columns, "rows": m.rows,
                    "value_col": m.value_col, "link_col": m.link_col, "multi": m.multi})


@bp.post("/api/record/new-code")
def new_code():
    conn, setup, err = _open()
    if err:
        return err
    body = request.get_json(silent=True) or {}
    d = cdate(body.get("date"))
    if d is None:
        return _refuse("date", "日付が読めません。")
    code = numbering.get_new_code(conn, setup.general.main_table, str(body.get("field") or ""),
                                  d, str(body.get("current") or ""))
    return jsonify({"code": code})


@bp.post("/api/record/open-file")
def open_file():
    """参照モードのファイルアイコン(VBA `TextDblClickWatchMode` → ShellExecute)。"""
    body = request.get_json(silent=True) or {}
    directory = Path(str(body.get("dir") or ""))
    name = str(body.get("name") or "")
    kind = str(body.get("kind") or "")
    target = directory / f"{name}.{kind}" if kind else directory / name
    if not target.is_file():
        return _refuse("open", "ファイル参照に失敗しました。アドレスを再チェックしてください。")
    try:
        if sys.platform.startswith("win"):
            os.startfile(str(target))                        # noqa: S606 - VBA ShellExecute と同じ
        else:
            return _refuse("open", f"この OS では開けません: {target}")
    except OSError as exc:
        return _refuse("open", f"ファイル参照に失敗しました。アドレスを再チェックしてください。\n{exc}")
    return jsonify({"ok": True, "path": str(target)})


@bp.get("/api/record/icon")
def icon():
    """ICON 画像。参照フォルダの中の .jpg だけ返す。"""
    path = Path(request.args.get("path", ""))
    d = _dirs()
    allowed = [Path(d["server_dir"]), Path(d["ref_dir"]), Path(d["base_dir"])]
    try:
        resolved = path.resolve()
        if resolved.suffix.lower() not in (".jpg", ".jpeg", ".png") or not any(
                str(resolved).startswith(str(a.resolve())) for a in allowed):
            return _refuse("icon", "その場所の画像は出せません。", 403)
        return send_file(str(resolved))
    except OSError:
        return _refuse("icon", "画像がありません。", 404)
