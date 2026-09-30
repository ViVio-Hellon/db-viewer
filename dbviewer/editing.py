"""レコードの編集画面の組立と登録(VBA `SET_BEF` / `SetDataToCtrl` / `REG_EXE` /
`GetArrDataFromCtrl` / `TextEnterEvents` / `reqConInputFlag` / `checkmodifiedflag` の移植)

画面(MF1 の編集ページ)は _設定 の行ごとに 1 欄。欄の見た目と入力の仕方は
`input_types.parse` が決め、値の決まり(必須・履歴・自動・連動・参照形式・文字色)は
ここで 1 欄ずつ `Field` にまとめる。
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from . import ado_sql, choices, config, history, input_types, numbering
from .settings_tables import ConvRow, SystemSetup, q, read_table, _text
from .vba_compat import cdate, cv, is_date, is_numeric, now_text, to_narrow

# VBA: データの中の改行は "|||||" で持つ(`NLConv`)
NL_MARK = "|||||"

MSG_NOT_SELECTED = "対象が選択されていません。"
MSG_REQUIRED = "必須箇所が埋まっていません。"
MSG_NOT_MODIFIED = "修正箇所がないため、キャンセルします。"
MSG_DATE_LIMIT = "{limit}しか入力できません。入力がキャンセルされます。"
MSG_EDIT_FORBIDDEN = "編集禁止項目です。"
MSG_CLEAR_ASK = "入力制限がある項目です。削除しますか?"

FILE_KINDS = ("PDF", "TXT", "XLS", "ZIP", "CSV", "DOCX")


@dataclass
class Field:
    no: int                       # 1 始まり(VBA の IL/ITB の番号)
    name: str                     # 項目名 = 列名
    value: str                    # いまの値(VBA ITB.Value)
    original: str                 # 読んだときの値(VBA ITB.Tag = NLConv 済み)
    spec: input_types.InputSpec
    required: bool = False        # 登録条件 = 必須
    his_rec: str = ""             # 履歴テーブル名
    dsp_type: str = ""            # 参照形式
    ms_join: str = ""             # マスタ結合
    color_rule: str = ""          # 文字色設定
    is_key: bool = False          # 管理番号
    link_display: str = ""        # 連動表示(USERJOIN)
    file_kind: str = ""           # PDF/TXT/... (参照モードのアイコン)
    file_dir: str = ""
    icons: list[dict] = field(default_factory=list)   # ICON 画像
    lines: int = 1

    def to_dict(self) -> dict[str, Any]:
        s = self.spec
        return {
            "no": self.no, "name": self.name, "value": self.value, "original": self.original,
            "input_type": s.raw, "kind": s.kind, "color": s.color, "locked": s.locked,
            "enabled": s.enabled, "lines": self.lines, "auto_tag": s.auto_tag,
            "form_name": s.form_name, "form_args": s.form_args,
            "date_limit": s.date_limit, "link_field": s.link_field, "link_func": s.link_func,
            "link_args": s.link_args,
            "num": {"mode": s.num_mode, "min": s.num_min, "max": s.num_max, "step": s.num_step},
            "master": s.master, "multi": s.multi, "message": s.message,
            "required": self.required, "his_rec": self.his_rec, "dsp_type": self.dsp_type,
            "ms_join": self.ms_join, "color_rule": self.color_rule, "is_key": self.is_key,
            "link_display": self.link_display, "file_kind": self.file_kind,
            "file_dir": self.file_dir, "icons": self.icons,
            "is_date": is_date(self.value),
        }


@dataclass
class RecordForm:
    mode: str
    key: str                       # 管理番号
    fields: list[Field]
    keys: list[str]                # 一覧に出ている全レコードの管理番号(NUM/SPN 用)
    his_flag: bool
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"mode": self.mode, "key": self.key, "fields": [f.to_dict() for f in self.fields],
                "keys": self.keys, "his_flag": self.his_flag, "message": self.message}


def nl_conv(text: str) -> str:
    return (text or "").replace(NL_MARK, "\r\n")


# ------------------------------------------------------------------
# 画面の組立(SET_BEF + SetDataToCtrl)
# ------------------------------------------------------------------
def build_form(conn: sqlite3.Connection, setup: SystemSetup, mode: str, key: str, *,
               list_sql: str = "", base_dir: Optional[Path] = None,
               server_dir: Optional[Path] = None, ref_dir: Optional[Path] = None) -> RecordForm:
    main = setup.general.main_table
    cols = setup.main_columns
    new = mode == "新規"
    record: Optional[list[str]] = None
    if new:
        key = str(numbering.get_new_no(conn, main))
    else:
        if key == "":
            raise ado_sql.sqlite3.OperationalError(MSG_NOT_SELECTED)
        cur = conn.execute(f"SELECT * FROM {q(main)} WHERE {q(config.KEY_ITEM)} = ?", (key,))
        row = cur.fetchone()
        record = [_text(v) for v in row] if row else None
        if record is None:
            # VBA は `GetRecordsArr` が Empty → 欄が空のまま(Enabled=False)。同じにする
            pass

    keys = _all_keys(conn, setup, list_sql)
    fields: list[Field] = []
    for i, name in enumerate(cols, start=1):
        r = setup.conv.rows[i - 1] if i - 1 < len(setup.conv.rows) else ConvRow(i, name)
        spec = input_types.parse(r.input_type)
        is_key = (setup.fj.get(config.KEY_ITEM, 0) == i)
        if is_key:
            val = key
        elif record is not None:
            val = record[i - 1]
        else:
            val = ""
        f = Field(no=i, name=name, value=val, original=nl_conv(val), spec=spec,
                  required=(r.reg_con == "必須"), his_rec=r.his_rec, dsp_type=r.dsp_type,
                  ms_join=r.ms_join, color_rule=r.color, is_key=is_key, lines=spec.lines)
        if new and is_key:
            f.original = key
        info = choices.tag_info(r.dsp_type, base_dir=base_dir or config.BASE_DIR,
                                server_dir=server_dir or config.BASE_DIR)
        if info.kind == "連動":
            f.link_display = choices.link_display(conn, r.ms_join, val)
        if mode == "参照":
            if info.kind in FILE_KINDS:
                f.file_kind = info.kind
                f.file_dir = str(info.directory)
            elif info.kind == "ICON":
                names = val.split(",") if "," in val else [val]
                for n in names:
                    if n != "":
                        p = info.directory / f"{n}.jpg"
                        f.icons.append({"name": n, "path": str(p), "exists": p.is_file()})
        fields.append(f)
    return RecordForm(mode=mode, key=key, fields=fields, keys=keys,
                      his_flag=history.hisrec_flag(conn, setup.conv))


def _all_keys(conn: sqlite3.Connection, setup: SystemSetup, list_sql: str) -> list[str]:
    """VBA `outputArr`(いまの一覧 SQL の全行)の管理番号。無ければメイン表の全部。"""
    k = setup.fj.get(config.KEY_ITEM, 0)
    if not k:
        return []
    try:
        if list_sql:
            rows = conn.execute(list_sql).fetchall()
            return [_text(r[k - 1]) for r in rows]
    except sqlite3.Error:
        pass
    rows = conn.execute(f"SELECT {q(config.KEY_ITEM)} FROM {q(setup.general.main_table)} "
                        f"ORDER BY {q(config.KEY_ITEM)}").fetchall()
    return [_text(r[0]) for r in rows]


# ------------------------------------------------------------------
# 登録(REG_EXE + GetArrDataFromCtrl + adoSQL)
# ------------------------------------------------------------------
@dataclass
class ExecResult:
    ok: bool
    message: str = ""
    reason: str = ""              # question / cancelled / conflict / error / required / not_modified / ...
    fields_ng: list[int] = field(default_factory=list)
    values: dict[str, str] = field(default_factory=dict)   # 数値変換などで変わった値
    reset_filters: bool = False   # 削除のあとは VBA がフィルタを消す
    executed: str = ""


def num_convert(fields: list[Field]) -> None:
    """VBA `TextEnterEvents(1, True)`: 数値変換の欄は数値なら半角に、数値でなく空でなければ 0。"""
    for f in fields:
        if f.spec.kind == "numconv":
            if is_numeric(f.value):
                f.value = to_narrow(f.value)
            elif f.value != "":
                f.value = "0"


def required_ok(fields: list[Field]) -> list[int]:
    """VBA `reqConInputFlag`: 必須で空の欄の番号。"""
    return [f.no for f in fields if f.required and f.value == ""]


def modified(fields: list[Field]) -> bool:
    """VBA `Not checkmodifiedflag`: 1 つでも Tag と違えば修正あり。"""
    return any(f.value != f.original for f in fields)


def date_limit_problem(f: Field) -> str:
    """VBA `TextDblClickEvents` の @過去/@未来 の入力制限。"""
    limit = f.spec.date_limit
    if not limit or not is_date(f.value):
        return ""
    d = cdate(f.value)
    from datetime import datetime
    now = datetime.now()
    if limit == "過去" and d > now:
        return MSG_DATE_LIMIT.format(limit=limit)
    if limit == "未来" and d < now:
        return MSG_DATE_LIMIT.format(limit=limit)
    return ""


def values_for_db(fields: list[Field], mode: str, *, user: str, pc: str,
                  now: Optional[str] = None) -> list[str]:
    """VBA `GetArrDataFromCtrl`: 列順の値。自動項目はここで入れる。"""
    now = now or now_text()
    out = []
    for f in fields:
        auto = input_types.auto_value(f.spec, mode, user=user, pc=pc, now=now)
        out.append(auto if auto is not None else f.value)
    return out


def execute(conn: sqlite3.Connection, setup: SystemSetup, mode: str, fields: list[Field], *,
            user: str, pc: str, confirmed: bool = False, answer: Optional[bool] = None,
            seen: Optional[list[str]] = None) -> ExecResult:
    """VBA `REG_EXE`。`mode` は 新規 / 修正 / 削除。"""
    main = setup.general.main_table
    key_field = fields[setup.fj[config.KEY_ITEM] - 1] if setup.fj.get(config.KEY_ITEM) else fields[0]
    key = key_field.value
    sql_filter = f"[{config.KEY_ITEM}] = {key}"          # VBA: 数値なので囲まない
    delete = mode == "削除"

    if mode in ("新規", "修正"):
        num_convert(fields)
        ng = required_ok(fields)
        if ng:
            return ExecResult(False, MSG_REQUIRED, "required", fields_ng=ng,
                              values={f.name: f.value for f in fields})
        if mode == "修正" and not modified(fields):
            return ExecResult(False, MSG_NOT_MODIFIED, "not_modified")
        for f in fields:
            p = date_limit_problem(f)
            if p:
                return ExecResult(False, p, "date_limit", fields_ng=[f.no])
    elif mode == "参照":
        return ExecResult(False, "", "view")

    values = values_for_db(fields, mode, user=user, pc=pc)

    # まず質問(VBA は adoSQL の中で Msg.Box)。答えが無ければここで止まる
    probe = ado_sql.ado_sql(conn, main, mode, values, "", key_filter=sql_filter,
                            confirmed=confirmed, answer=answer, seen=None) \
        if not (confirmed and answer) else None
    if probe is not None and not probe.ok:
        return ExecResult(False, probe.message, probe.reason, values={f.name: f.value for f in fields})

    # 履歴(先に書く。VBA と同じ順)
    if history.hisrec_flag(conn, setup.conv):
        for f in fields:
            if f.value != f.original and f.his_rec.startswith(config.HISTORY_PREFIX):
                arr = history.his_rec_array(conn, f.his_rec, mode, key, f.original, f.value,
                                            delete, pc=pc, user=user)
                r = ado_sql.ado_sql(conn, f.his_rec, "ADDNEW", arr, config.KEY_ITEM,
                                    skip_msg=True, key_filter=f"[{config.KEY_ITEM}] = '{arr[0]}'")
                if not r.ok:
                    return ExecResult(False, r.message, r.reason)

    r = ado_sql.ado_sql(conn, main, mode, values, "", key_filter=sql_filter,
                        confirmed=True, answer=True, seen=seen)
    if not r.ok:
        return ExecResult(False, r.message, r.reason)
    return ExecResult(True, r.message, "", values={f.name: f.value for f in fields},
                      reset_filters=delete, executed=r.executed)


def fields_from_payload(form: RecordForm, values: dict[str, Any],
                        originals: Optional[dict[str, Any]] = None) -> list[Field]:
    """画面から戻ってきた値を欄に入れる(`original` は画面が読んだときの値)。"""
    for f in form.fields:
        if f.name in values and not f.is_key:
            f.value = "" if values[f.name] is None else str(values[f.name])
        if originals and f.name in originals:
            f.original = "" if originals[f.name] is None else str(originals[f.name])
    return form.fields
