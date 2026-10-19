"""一覧

VBA MF1 の一覧ページ(フィルタ列・ListView・ページ)は捨て、Web の表(グリッド)にした。
全行をまとめて渡し、絞り込み・並べ替え・列の表示/並び/幅は画面の中で行う。

- `GET  /list`             … 画面
- `POST /api/list/data`    … 全行・列の種類・この PC で保存した列の見せ方
- `POST /api/list/view`    … 列の見せ方を保存する(`{"reset": true}` で初期に戻す)
- `POST /api/list/add-column` … 選んでいる表に列を足す(編集可)

0.8.0 から一覧で表を選べる(`{"table"}` を付けると、その表をこの PC で覚える)。0.9.0 からどの表も
simple_table の道(rowid で直す。列の設定は かんたん設定)。`/api/list/cell`・`/api/list/value` は VBA 版の
エンジンの API として残す(画面からは使わない):
- `POST /api/list/form`        … 行の入力欄(表示する列・入力の種類・コンボの選択肢)
- `POST /api/list/row-save`    … 行(またはマス)を直す `{"table","rowid","values","seen"}`
- `POST /api/list/row-add`     … 行を足す `{"table","values"}`
- `POST /api/list/row-delete`  … 行を消す `{"table","rowid","seen"}`
"""
from __future__ import annotations

from flask import Blueprint, jsonify, render_template, request

from dbviewer import (auth, cell_edit, grid_data, master_admin, session_setup, settings_tables, shared_db,
                      simple_table)
from dbviewer.logging_utils import get_logger
from dbviewer import trace

from .. import get_db
from ..shell import shell_context

log = get_logger("app.routes.list")

bp = Blueprint("list", __name__)


def _refuse(code: str, message: str, status: int = 422):
    return jsonify({"error": {"code": code, "message": message}}), status


def _open():
    """DB と設定テーブル。開けなければ理由を返す(VBA の Msg.Box の文言)。"""
    try:
        conn = get_db()
        setup = session_setup.get_setup(conn)
    except shared_db.DbNotFound as exc:
        return None, None, _refuse("db_not_found", str(exc), 409)
    except settings_tables.SetupError as exc:
        return None, None, _refuse("setup", str(exc), 409)
    if setup.master_problem:
        return None, None, _refuse(
            "master_file",
            setup.master_problem + "\nマスタファイルに修正が必要です。\n起動を中止します。", 409)
    return conn, setup, None


@bp.get("/list")
def page():
    return render_template("list.html", **shell_context("list"))


@bp.get("/master")
@bp.get("/browse")
def old_screens():
    """0.18.0 でなくした画面(マスタ管理・閲覧・出力)。古いショートカット・ブックマークは一覧へ。"""
    from flask import redirect
    return redirect("/list")


def _view_key(setup) -> str:
    return grid_data.view_key("list", shared_db.current_profile().prog_no, setup.general.main_table)


def _pick_from_body(conn) -> None:
    """`{"table"}` があれば、一覧に出す表としてこの PC で覚える(業務の表だけ)。"""
    table = str((request.get_json(silent=True) or {}).get("table") or "")
    if table and table in settings_tables.user_tables(conn):
        settings_tables.pick_table(conn, table)


def _tables(conn) -> list[dict]:
    from dbviewer import table_bring
    brought = set(table_bring.brought_tables(conn))
    out = []
    for name in settings_tables.user_tables(conn):
        try:
            n = conn.execute(f"SELECT COUNT(*) FROM {settings_tables.q(name)}").fetchone()[0]
        except Exception:                               # noqa: BLE001 - 数えられなければ ?
            n = -1
        out.append({"name": name, "rows": n, "brought": name in brought})
    return out


def _simple_data(conn, setup):
    """VBA 版でない表: 表示する列・末尾に rowid・入力の種類(rowid で直す)。"""
    table = setup.general.main_table
    v = simple_table.view(conn, table)
    meta = grid_data.columns_meta(v["columns"], v["rows"])
    settings = simple_table.by_name(conn, table)
    lv = auth.level()
    return jsonify({
        "mode": "simple", "table": table, "tables": _tables(conn),
        "columns": meta, "rows": v["rows"], "key_index": v["key_index"], "count": len(v["rows"]),
        "view": grid_data.load_view(_view_key(setup), v["columns"]),
        "edit": {"allowed": lv >= auth.required_level("修正"),
                 "columns": {c: simple_table.edit_spec(conn, table, settings[c]) for c in v["columns"]}},
        "can": {"add": lv >= auth.required_level("新規"), "delete": lv >= auth.required_level("削除")},
        # 印刷・出力(保存・コピー)に入れる列(かんたん設定の「印刷」。0.18.0: 閲覧・出力の画面から一覧へ)
        "print_columns": v["print_columns"],
        # マスを押したときにすること(かんたん設定の「クリック」)
        "actions": simple_table.actions(settings.values(), table),
        "db_mtime": shared_db.probe().get("mtime"),
        "setup": _setup_dict(setup),
    })


@bp.post("/api/list/data")
def api_data():
    try:
        _pick_from_body(get_db())
    except shared_db.DbNotFound:
        pass
    conn, setup, err = _open()
    if err:
        return err
    return _simple_data(conn, setup)


@bp.post("/api/list/cell")
def api_cell():
    """一覧の 1 マスを直して、すぐ本体に書く。`{"key", "column", "value", "seen"}`

    確認の問い合わせはしない。決まり(レベル・入力形式・必須・自動項目・履歴)は登録画面と同じ。
    """
    conn, setup, err = _open()
    if err:
        return err
    mode, refuse = auth.check_mode("修正", auth.level())
    if refuse:
        return _refuse("level", refuse, 403)
    body = request.get_json(silent=True) or {}
    s = auth.current()
    result = cell_edit.save_cell(conn, setup, str(body.get("key") or ""), str(body.get("column") or ""),
                                 "" if body.get("value") is None else str(body.get("value")),
                                 "" if body.get("seen") is None else str(body.get("seen")),
                                 user=s.user_id if s else auth.network_id(), pc=auth.pc_name())
    status = 200 if result.ok else (409 if result.reason == "conflict" else 422)
    if result.reason != "unchanged":
        trace.event("cell.save", result.message, level="INFO" if result.ok else "WARN",
                    data={"key": body.get("key"), "column": body.get("column"), "before": body.get("seen"),
                          "after": body.get("value"), "reason": result.reason, "changed": result.changed})
    return jsonify({"ok": result.ok, "message": result.message, "reason": result.reason,
                    "row": result.row, "changed": result.changed,
                    "db_mtime": shared_db.probe().get("mtime")}), status


@bp.post("/api/list/value")
def api_value():
    """本体の 1 マスの値(一覧の見せ方の前)。`{"key", "column"}` → `{"value"}`

    金額の列は一覧で丸めて見せているので、その場で直すときはこの値から始める。
    """
    conn, setup, err = _open()
    if err:
        return err
    body = request.get_json(silent=True) or {}
    value = cell_edit.raw_value(conn, setup, str(body.get("key") or ""), str(body.get("column") or ""))
    if value is None:
        return _refuse("gone", "その行はもうありません(ほかの端末が消しました)。", 404)
    return jsonify({"value": value})


@bp.post("/api/list/add-column")
def api_add_column():
    """一覧の表の末尾に列を足す(編集可。決まりは master_admin.add_column)。"""
    conn, setup, err = _open()
    if err:
        return err
    body = request.get_json(silent=True) or {}
    table = setup.general.main_table
    r = master_admin.add_column(conn, setup, table, str(body.get("name") or ""),
                                kind=str(body.get("kind") or "text"), fill=str(body.get("fill") or ""),
                                level=auth.level())
    trace.event("list.add_column", r.message, level="INFO" if r.ok else "WARN",
                data={"table": table, "name": body.get("name"), "kind": body.get("kind"),
                      "reason": r.reason, "setting_table": r.setting_table, "backup": r.backup})
    status = 200 if r.ok else 403 if r.reason == master_admin.REFUSE_LEVEL else 422
    return jsonify({"ok": r.ok, "message": r.message, "reason": r.reason, "column": r.column,
                    "setting_table": r.setting_table, "setting_key": r.setting_key,
                    "backup": r.backup}), status


# ------------------------------------------------------------------
# VBA 版でない表(simple_table の道): 行は rowid で決める
# ------------------------------------------------------------------
def _simple_open(need: str):
    conn, setup, err = _open()
    if err:
        return None, None, None, err
    body = request.get_json(silent=True) or {}
    table = str(body.get("table") or "")
    if table != setup.general.main_table:
        return None, None, None, _refuse("table", "一覧の表が替わりました。読み込み直してください。", 409)
    _mode, refuse = auth.check_mode(need, auth.level())
    if refuse:
        return None, None, None, _refuse("level", refuse, 403)
    return conn, table, body, None


def _shown(conn, table) -> list[str]:
    return [c.name for c in simple_table.load(conn, table) if c.show]


def _write_reply(conn, table, r: simple_table.WriteResult, event: str, data: dict):
    if r.reason != "unchanged":
        trace.event(event, r.message, level="INFO" if r.ok else "WARN",
                    data={"table": table, "rowid": r.rowid, "reason": r.reason, **data})
    status = 200 if r.ok else 409 if r.reason in ("conflict", "gone") else 422
    row = simple_table.row_values(conn, table, r.rowid, _shown(conn, table)) if r.rowid else None
    return jsonify({"ok": r.ok, "message": r.message, "reason": r.reason, "rowid": r.rowid, "row": row,
                    "db_mtime": shared_db.probe().get("mtime")}), status


def _user() -> str:
    """自動で入れる人(ログインした ID)。"""
    s = auth.current()
    return s.user_id if s and s.user_id else auth.network_id()


def _int(v) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


@bp.post("/api/list/form")
def api_form():
    """行の入力欄。`rowid` があればその行の値(無ければ新しい行)。"""
    conn, table, body, err = _simple_open("参照")
    if err:
        return err
    settings = simple_table.load(conn, table)
    values = {}
    rowid = _int(body.get("rowid"))
    if rowid:
        row = simple_table.row_values(conn, table, rowid, [c.name for c in settings])
        if row is None:
            return _refuse("gone", simple_table.MSG_GONE, 404)
        values = dict(zip([c.name for c in settings], row))
    fields = []
    for c in settings:
        if not c.show:
            continue
        f = simple_table.edit_spec(conn, table, c)
        f.update(name=c.name, value=values.get(c.name, ""))
        fields.append(f)
    return jsonify({"table": table, "rowid": rowid, "fields": fields})


@bp.post("/api/list/row-save")
def api_row_save():
    conn, table, body, err = _simple_open("修正")
    if err:
        return err
    values = body.get("values") if isinstance(body.get("values"), dict) else {}
    seen = body.get("seen") if isinstance(body.get("seen"), dict) else {}
    r = simple_table.update(conn, table, _int(body.get("rowid")), values, seen, user=_user())
    return _write_reply(conn, table, r, "list.row_save", {"before": seen, "after": values})


@bp.post("/api/list/row-add")
def api_row_add():
    conn, table, body, err = _simple_open("新規")
    if err:
        return err
    values = body.get("values") if isinstance(body.get("values"), dict) else {}
    r = simple_table.insert(conn, table, values, user=_user())
    return _write_reply(conn, table, r, "list.row_add", {"after": values})


@bp.post("/api/list/row-delete")
def api_row_delete():
    conn, table, body, err = _simple_open("削除")
    if err:
        return err
    seen = body.get("seen") if isinstance(body.get("seen"), dict) else {}
    r = simple_table.delete(conn, table, _int(body.get("rowid")), seen)
    trace.event("list.row_delete", r.message, level="INFO" if r.ok else "WARN",
                data={"table": table, "rowid": r.rowid, "reason": r.reason, "before": seen})
    status = 200 if r.ok else 409 if r.reason in ("conflict", "gone") else 422
    return jsonify({"ok": r.ok, "message": r.message, "reason": r.reason,
                    "db_mtime": shared_db.probe().get("mtime")}), status


@bp.post("/api/list/view")
def api_view():
    conn, setup, err = _open()
    if err:
        return err
    columns = _shown(conn, setup.general.main_table)
    body = request.get_json(silent=True) or {}
    if body.get("reset"):
        return jsonify({"view": grid_data.reset_view(_view_key(setup), columns)})
    return jsonify({"view": grid_data.save_view(_view_key(setup), columns, body)})


def _setup_dict(setup) -> dict:
    conn = get_db()
    return {
        "main_table": setup.general.main_table,
        "key_ok": setup.key_ok,
        "notices": setup.notices,
        "auto_main": setup.auto_main,
        "can_add_column": auth.level() >= master_admin.COLUMN_LEVEL,
        "kinds": [[k, label] for k, (_t, label) in master_admin.COLUMN_KINDS.items()],
        "setting_table": "",
        # 帳票は全般設定「帳票」があって、その「メインテーブル名」の表を見ているときだけ
        "report_flag": setup.general.report_flag and setup.general.main_table == setup.general.configured_main,
        "browse_flag": setup.general.browse_flag,
        "column_problems": setup.column_problems,
        "format_ok": setup.format_ok,
    }
