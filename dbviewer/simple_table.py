"""どの表でも見て直す(0.8.0)。設定は「列ごとに 表示・入力・印刷」だけ

VBA 版の決まり(全般設定・<表>_設定・管理番号・入力形式の書き方)は設定が難しすぎて、誰も設定できなかった。
ここでは表を選ぶだけで、どの表も一覧で見て直せる(0.18.0 からは一覧だけ)。決めるのは列ごとに 3 つだけ:

    表示   … 一覧・閲覧に出すか
    入力   … 文字 / コンボボックス(その列に入っている値から選ぶ・打つ) / スピンボタン(数。刻み) / カレンダー(日付)
    印刷   … 一覧の 印刷・出力(保存・コピー)に入れるか
    クリック … マスの値を押したときに すること(なし / 値をコピー / 現物を開く / プレビュー)(0.12.0・0.13.0)
               開くもの(Excel・Word・PDF・URL)は値(または「場所」の決まり)から決まる

【どこに覚えるか】DB の中の隠した表「ツールの設定」に、表ごとに 1 行(JSON)。DB を使う全員で同じ。
**列名で**覚えるので、列の並びが変わっても・列が増えても(既定: 表示・文字・印刷)ずれない。
DB を替えれば、その DB の設定になる(無ければ既定)。

【行の決め方】SQLite の rowid(どの表にもある行の番号)。管理番号 の列は要らない。
直すときは「画面が見ていた値」と今の値を比べ、ほかの端末が先に変えていたら書かない。

【VBA 版の <表>_設定】0.9.0 から画面では使わない。かんたん設定を一度も保存していない表は、<表>_設定 の
入力形式から最初の設定を作る(`from_vba`: calender → カレンダー、数値N/最小/最大/刻み → スピンボタン、
s/m マスタ → コンボボックス、ほかは文字)。かんたん設定の「取り込んで消す」で保存して <表>_設定 を消せる。
"""
from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Optional

from . import config
from .settings_tables import column_names, q, table_exists

SETTINGS_TABLE = config.TOOL_SETTINGS_TABLE

INPUTS: tuple[tuple[str, str], ...] = (
    ("text", "文字"),
    ("combo", "コンボボックス(入っている値から選ぶ・打つ)"),
    ("spin", "スピンボタン(数)"),
    ("calendar", "カレンダー(日付)"),
    ("auto_new_user", "自動: 登録した人(足したとき)"),
    ("auto_upd_user", "自動: 直した人(足した・直したとき)"),
    ("file", "ファイル(PDF などをプレビュー)"),
)
# 自動で入る列(画面では直せない。書くときにログインした人の ID を入れる)
AUTO_NEW = "auto_new_user"
AUTO_UPD = "auto_upd_user"
AUTO_INPUTS = (AUTO_NEW, AUTO_UPD)
# ファイルの列のプレビュー(アプリの窓の中で見る。ほかのアプリでは開かない)
PREVIEW_TYPES = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                 ".gif": "image/gif", ".bmp": "image/bmp", ".webp": "image/webp",
                 ".txt": "text/plain", ".csv": "text/plain", ".log": "text/plain"}
INPUT_KEYS = tuple(k for k, _ in INPUTS)

# マスを押したときにすること(0.12.0・0.13.0)。一覧で
CLICK_COPY, CLICK_OPEN, CLICK_PREVIEW = "copy", "open", "preview"
CLICKS: tuple[tuple[str, str], ...] = (
    ("", "なし"),
    (CLICK_COPY, "値をコピー"),
    (CLICK_OPEN, "現物を開く(Excel・Word・PDF・URL)"),
    (CLICK_PREVIEW, "プレビュー(アプリの窓の中で見る)"),
)
CLICK_KEYS = tuple(k for k, _ in CLICKS)
OPENS = (CLICK_OPEN, CLICK_PREVIEW)
# 「場所」の決まりの中で、マスの値に置き換える印。無ければ末尾に付ける
PLACE_MARK = "{値}"
PLACE_MAX = 500
_URL_OK = re.compile(r"^https?://[^\s]+$", re.IGNORECASE)
_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*:")
_DRIVE = re.compile(r"^[A-Za-z]:[\\/]|^\\\\")
# 開くもの(拡張子 → 種類)。現物を開く のはこれだけ(実行ファイルなどは開かない)
OPEN_KINDS = {".xlsx": "Excel", ".xlsm": "Excel", ".xls": "Excel", ".csv": "Excel",
              ".docx": "Word", ".docm": "Word", ".doc": "Word", ".pdf": "PDF",
              ".png": "画像", ".jpg": "画像", ".jpeg": "画像", ".gif": "画像", ".bmp": "画像", ".webp": "画像",
              ".txt": "テキスト", ".log": "テキスト"}
# アプリの窓の中で HTML にして見せるもの(office_preview)
OFFICE_PREVIEW = {".xlsx", ".xlsm", ".docx"}
MAX_CHOICES = 500

MSG_CONFLICT = "ほかの端末が先にこの行を変えていました。いまの値に置き換えたので、もう一度直してください。"
MSG_GONE = "その行はもうありません(ほかの端末が消しました)。"


@dataclass
class ColumnSetting:
    name: str
    show: bool = True
    input: str = "text"
    step: str = "1"
    print: bool = True
    click: str = ""
    place: str = ""

    def to_json(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("name")
        return d


# ------------------------------------------------------------------
# 設定を読む・書く
# ------------------------------------------------------------------
def _ensure(conn: sqlite3.Connection) -> None:
    conn.execute(f"CREATE TABLE IF NOT EXISTS {q(SETTINGS_TABLE)} "
                 f"({q('表')} TEXT PRIMARY KEY, {q('設定')} TEXT, {q('更新者')} TEXT, {q('更新日時')} TEXT)")


def _raw(conn: sqlite3.Connection, table: str) -> dict[str, Any]:
    if not table_exists_any(conn, SETTINGS_TABLE):
        return {}
    row = conn.execute(f"SELECT {q('設定')} FROM {q(SETTINGS_TABLE)} WHERE {q('表')} = ?", (table,)).fetchone()
    if not row or not row[0]:
        return {}
    try:
        data = json.loads(row[0])
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def table_exists_any(conn: sqlite3.Connection, name: str) -> bool:
    """隠した表も含めて、あるか(`table_names` は隠した表を除く)。"""
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def has_settings(conn: sqlite3.Connection, table: str) -> bool:
    return bool(_raw(conn, table).get("columns"))


def vba_input(raw: str) -> tuple[str, str]:
    """VBA 版の入力形式 → (入力, 刻み)。かんたん設定に無いもの(日時の自動・専用フォームなど)は文字。"""
    from .vba_compat import cv
    text = raw or ""
    c = cv(text)
    if c == "#NEWUSER#":
        return AUTO_NEW, "1"
    if c == "#UPUSER#":
        return AUTO_UPD, "1"
    if c.startswith("CALENDER"):
        return "calendar", "1"
    if c.startswith("数値") and c != "数値変換":
        parts = text.split("/")
        step = parts[3].strip() if len(parts) > 3 and _number(parts[3]) else "1"
        return "spin", step
    if c[:1] in ("S", "M") and len(text) > 1:
        return "combo", "1"
    return "text", "1"


def from_vba(conn: sqlite3.Connection, table: str) -> dict[str, ColumnSetting]:
    """<表>_設定 があれば、その入力形式から作った設定(列名ごと)。無ければ空。"""
    from .settings_tables import load_conv
    conv_name = table + config.SETTING_SUFFIX
    if not table_exists(conn, conv_name):
        return {}
    out = {}
    for r in load_conv(conn, conv_name).rows:
        if r.item and r.item not in out:
            inp, step = vba_input(r.input_type)
            out[r.item] = ColumnSetting(r.item, input=inp, step=step)
    return out


def load(conn: sqlite3.Connection, table: str) -> list[ColumnSetting]:
    """いまの列の並びで、列ごとの設定。決めていない列は既定(表示・文字・印刷)。
    かんたん設定を一度も保存していない表は、VBA 版の <表>_設定 から作る。"""
    saved = _raw(conn, table).get("columns") or {}
    vba = {} if saved else from_vba(conn, table)
    out = []
    for name in column_names(conn, table):
        s = saved.get(name) if isinstance(saved, dict) else None
        c = ColumnSetting(name)
        if name in vba:
            c = vba[name]
        if isinstance(s, dict):
            c.show = bool(s.get("show", True))
            c.input = s.get("input") if s.get("input") in INPUT_KEYS else "text"
            c.step = str(s.get("step") or "1")
            c.print = bool(s.get("print", True))
            click = "open" if s.get("click") == "url" else s.get("click")       # 0.12.0 の「URL を開く」
            c.click = click if click in CLICK_KEYS else ""
            c.place = str(s.get("place") or s.get("url") or "") if c.click in OPENS else ""
        out.append(c)
    return out


def by_name(conn: sqlite3.Connection, table: str) -> dict[str, ColumnSetting]:
    return {c.name: c for c in load(conn, table)}


def validate(conn: sqlite3.Connection, table: str, rows: list[dict[str, Any]]) -> tuple[list[ColumnSetting], str]:
    known = column_names(conn, table)
    got: dict[str, ColumnSetting] = {}
    for r in rows:
        name = str(r.get("name") or "")
        if name not in known:
            return [], f"[{table}] に無い列です: {name}"
        inp = str(r.get("input") or "text")
        if inp not in INPUT_KEYS:
            return [], f"「{name}」の入力の種類が分かりません。"
        step = str(r.get("step") or "1").strip()
        if inp == "spin":
            n = _number(step)
            if n is None or n <= 0:
                return [], f"「{name}」の刻みは 0 より大きい数にしてください。"
        click = str(r.get("click") or "")
        if click not in CLICK_KEYS:
            return [], f"「{name}」のクリックの種類が分かりません。"
        place = str(r.get("place") or "").strip().strip('"') if click in OPENS else ""
        if place and (len(place) > PLACE_MAX or any(ord(ch) < 32 for ch in place)
                      or (_SCHEME.match(place) and not _DRIVE.match(place) and not _URL_OK.match(place))):
            return [], (f"「{name}」の場所は、http:// か https:// で始まる URL か、ファイルの場所にしてください"
                        f"(マスの値を入れる所は {PLACE_MARK}。空なら値そのもの)。")
        got[name] = ColumnSetting(name, bool(r.get("show", True)), inp, step or "1", bool(r.get("print", True)),
                                  click, place)
    if got and not any(c.show for c in got.values()):
        return [], "表示する列を 1 つ以上選んでください。"
    return [got.get(n) or ColumnSetting(n) for n in known], ""


def save(conn: sqlite3.Connection, table: str, settings: list[ColumnSetting], *, user: str = "") -> None:
    """表ごとの設定を書く(1 つの取引)。いまの列に無い名前の設定は残す(列を戻したとき用)。"""
    data = _raw(conn, table)
    cols = data.get("columns") if isinstance(data.get("columns"), dict) else {}
    for c in settings:
        cols[c.name] = c.to_json()
    data["columns"] = cols
    conn.execute("BEGIN IMMEDIATE")
    try:
        _ensure(conn)
        conn.execute(f"INSERT OR REPLACE INTO {q(SETTINGS_TABLE)} VALUES (?, ?, ?, ?)",
                     (table, json.dumps(data, ensure_ascii=False), user,
                      datetime.now().strftime("%Y/%m/%d %H:%M:%S")))
        conn.execute("COMMIT")
    except sqlite3.Error:
        conn.execute("ROLLBACK")
        raise


# ------------------------------------------------------------------
# 値をそろえる(入力の種類どおり)
# ------------------------------------------------------------------
def _number(text: str) -> Optional[float]:
    t = unicodedata.normalize("NFKC", str(text or "")).replace(",", "").strip()
    if t == "":
        return None
    try:
        return float(t)
    except ValueError:
        return None


_DATE = re.compile(r"^\s*(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})日?\s*$")
_DATE8 = re.compile(r"^\s*(\d{4})(\d{2})(\d{2})\s*$")


def date_format(conn: sqlite3.Connection, table: str, column: str) -> str:
    """その列の日付の書き方。入っている値が 2026-10-15 ならそれ、ほかは VBA と同じ 2026/10/15。"""
    row = conn.execute(f"SELECT {q(column)} FROM {q(table)} WHERE {q(column)} IS NOT NULL AND {q(column)} <> '' "
                       f"LIMIT 1").fetchone()
    return "-" if row and isinstance(row[0], str) and re.match(r"^\d{4}-\d", row[0]) else "/"


def _decl(conn: sqlite3.Connection, table: str) -> dict[str, str]:
    return {r[1]: (r[2] or "").upper() for r in conn.execute(f"PRAGMA table_info({q(table)})")}


def convert(value: Any, setting: ColumnSetting, decl: str, sep: str = "/") -> tuple[Any, str]:
    """画面の値 → 書く値。(値, だめなら理由)。空は NULL。"""
    text = "" if value is None else str(value)
    if text.strip() == "":
        return None, ""
    if setting.input == "spin":
        n = _number(text)
        if n is None:
            return None, f"「{setting.name}」は数を入れてください。"
        return (int(n) if n.is_integer() and "." not in setting.step else n), ""
    if setting.input == "calendar":
        m = _DATE.match(text) or _DATE8.match(text)
        if not m:
            return None, f"「{setting.name}」は日付(2026/10/15 の形)を入れてください。"
        y, mo, d = (int(x) for x in m.groups())
        try:
            datetime(y, mo, d)
        except ValueError:
            return None, f"「{setting.name}」の日付がありません: {text}"
        return f"{y:04d}{sep}{mo:02d}{sep}{d:02d}", ""
    # 文字・コンボ: 数の型の列で数なら数として書く(INTEGER の列に "12" を文字で入れない)
    if "INT" in decl or "REAL" in decl or "NUM" in decl or "DOUB" in decl or "FLOA" in decl:
        n = _number(text)
        if n is not None:
            return (int(n) if n.is_integer() and "INT" in decl else n), ""
    return text, ""


def text_of(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def convert_row(conn: sqlite3.Connection, table: str, values: dict[str, Any]) -> tuple[dict[str, Any], str]:
    settings = by_name(conn, table)
    decl = _decl(conn, table)
    out = {}
    for name, v in values.items():
        s = settings.get(name)
        if s is None:
            return {}, f"[{table}] に無い列です: {name}"
        sep = date_format(conn, table, name) if s.input == "calendar" else "/"
        out[name], problem = convert(v, s, decl.get(name, ""), sep)
        if problem:
            return {}, problem
    return out, ""


# ------------------------------------------------------------------
# 一覧・閲覧に渡す形
# ------------------------------------------------------------------
def choices(conn: sqlite3.Connection, table: str, column: str) -> list[str]:
    """コンボボックスの選択肢 = その列に入っている値(重ならない・並べて・500 まで)。"""
    rows = conn.execute(f"SELECT DISTINCT {q(column)} FROM {q(table)} WHERE {q(column)} IS NOT NULL "
                        f"AND CAST({q(column)} AS TEXT) <> '' ORDER BY 1 LIMIT {MAX_CHOICES}").fetchall()
    return [text_of(r[0]) for r in rows]


def actions(settings, table: str = "") -> dict[str, dict[str, str]]:
    """マスを押したときにすること(クリックを決めた列だけ)。画面へ渡す。"""
    return {c.name: {"click": c.click, "place": c.place, "table": table, "column": c.name}
            for c in settings if c.click}


def click_target(setting: ColumnSetting, value: Any) -> tuple[str, str]:
    """開く列の、そのマスの行き先 → (種類, 行き先)。種類は "url" / "file" / ""(開けない)。

    画面(`cellaction.js`)と同じ決まり。場所の決まりが空なら値そのもの、{値} があればそこへ、
    無ければ末尾に付ける。URL の決まりなら値は URL 用に符号化する。
    `javascript:` などの URL でもファイルでもないものは開かない。"""
    from urllib.parse import quote
    text = text_of(value).strip().strip('"')
    if setting.click not in OPENS or not text:
        return "", ""
    place = setting.place
    if not place:
        target = text
    else:
        put = quote(text, safe="") if _URL_OK.match(place) else text
        target = place.replace(PLACE_MARK, put) if PLACE_MARK in place else place + put
    if _URL_OK.match(target):
        return "url", target
    if _SCHEME.match(target) and not _DRIVE.match(target):
        return "", ""
    return "file", target


def open_kind(path) -> str:
    """ファイルの種類(Excel・Word・PDF…)。開けない形式なら空。"""
    from pathlib import PurePath
    return OPEN_KINDS.get(PurePath(str(path).replace("\\", "/")).suffix.lower(), "")


def edit_spec(conn: sqlite3.Connection, table: str, s: ColumnSetting) -> dict[str, Any]:
    if s.input in AUTO_INPUTS:
        return {"inline": "", "input": s.input, "step": s.step,
                "why": "自動で入る項目です(" + ("足した人" if s.input == AUTO_NEW else "直した人") + ")。"}
    if s.input == "file":
        # マスのダブルクリックはプレビュー。ファイルの場所を直すのは行の窓で
        return {"inline": "file", "input": "file", "step": s.step}
    d: dict[str, Any] = {"inline": "simple", "input": s.input, "step": s.step}
    if s.input == "combo":
        d["choices"] = choices(conn, table, s.name)
    return d


def view(conn: sqlite3.Connection, table: str, *, shown_only: bool = True) -> dict[str, Any]:
    """表の行。列は 表示 の列だけ(並びは表のまま)、行の末尾に rowid(画面には出さない鍵)。"""
    if not table_exists(conn, table):
        raise ValueError(f"表 [{table}] がありません。")
    settings = load(conn, table)
    cols = [s.name for s in settings if s.show or not shown_only]
    sel = ", ".join(q(c) for c in cols)
    rows = [[text_of(v) for v in r[1:]] + [str(r[0])]
            for r in conn.execute(f"SELECT rowid, {sel} FROM {q(table)} ORDER BY rowid")] if cols else []
    return {"table": table, "columns": cols, "rows": rows, "key_index": len(cols),
            "print_columns": [s.name for s in settings if s.show and s.print],
            "settings": {s.name: s.to_json() for s in settings}}


def row_values(conn: sqlite3.Connection, table: str, rowid: int, cols: list[str]) -> Optional[list[str]]:
    sel = ", ".join(q(c) for c in cols) if cols else "NULL"
    r = conn.execute(f"SELECT {sel} FROM {q(table)} WHERE rowid = ?", (rowid,)).fetchone()
    return None if r is None else [text_of(v) for v in r] + [str(rowid)]


# ------------------------------------------------------------------
# 書く(行は rowid で決める)
# ------------------------------------------------------------------
@dataclass
class WriteResult:
    ok: bool
    message: str = ""
    reason: str = ""             # conflict / gone / bad_value / unchanged / failed
    rowid: int = 0


def _current(conn: sqlite3.Connection, table: str, rowid: int) -> Optional[dict[str, str]]:
    cur = conn.execute(f"SELECT * FROM {q(table)} WHERE rowid = ?", (rowid,))
    r = cur.fetchone()
    if r is None:
        return None
    return {d[0]: text_of(v) for d, v in zip(cur.description, r)}


def _write(conn: sqlite3.Connection, sql: str, args: list[Any]) -> Optional[str]:
    try:
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.execute(sql, args)
        conn.execute("COMMIT")
        return str(cur.lastrowid or "")
    except sqlite3.Error as exc:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise exc


def _auto_columns(conn: sqlite3.Connection, table: str) -> dict[str, str]:
    """自動で入る列 → 入力の種類。"""
    return {c.name: c.input for c in load(conn, table) if c.input in AUTO_INPUTS}


def update(conn: sqlite3.Connection, table: str, rowid: int, values: dict[str, Any],
           seen: dict[str, Any], *, user: str = "") -> WriteResult:
    """行を直す。`seen` = 画面が見ていた値(直す列ぶん)。今の値と違えば書かない。
    何か変わったら「自動: 直した人」の列にログインした人を入れる(自動の列は画面の値を使わない)。"""
    auto = _auto_columns(conn, table)
    values = {k: v for k, v in values.items() if k not in auto}
    seen = {k: v for k, v in seen.items() if k not in auto}
    now = _current(conn, table, rowid)
    if now is None:
        return WriteResult(False, MSG_GONE, "gone")
    for name, v in seen.items():
        if name in now and now[name] != ("" if v is None else str(v)):
            return WriteResult(False, MSG_CONFLICT, "conflict", rowid)
    conv, problem = convert_row(conn, table, values)
    if problem:
        return WriteResult(False, problem, "bad_value", rowid)
    changed = {k: v for k, v in conv.items() if text_of(v) != now.get(k, "")}
    if not changed:
        return WriteResult(True, "変わっていません。", "unchanged", rowid)
    changed.update({k: user for k, kind in auto.items() if kind == AUTO_UPD and user})
    sets = ", ".join(f"{q(k)} = ?" for k in changed)
    try:
        _write(conn, f"UPDATE {q(table)} SET {sets} WHERE rowid = ?", [*changed.values(), rowid])
    except sqlite3.Error as exc:
        return WriteResult(False, f"書けませんでした: {exc}", "failed", rowid)
    return WriteResult(True, "保存しました。", "", rowid)


def insert(conn: sqlite3.Connection, table: str, values: dict[str, Any], *, user: str = "") -> WriteResult:
    """行を足す。「自動: 登録した人」「自動: 直した人」の列にはログインした人を入れる。"""
    auto = _auto_columns(conn, table)
    conv, problem = convert_row(conn, table, {k: v for k, v in values.items() if k not in auto})
    if problem:
        return WriteResult(False, problem, "bad_value")
    conv = {k: v for k, v in conv.items() if v is not None}
    if user:
        conv.update({k: user for k in auto})
    try:
        if conv:
            rid = _write(conn, f"INSERT INTO {q(table)} ({', '.join(q(k) for k in conv)}) "
                               f"VALUES ({', '.join('?' for _ in conv)})", list(conv.values()))
        else:
            rid = _write(conn, f"INSERT INTO {q(table)} DEFAULT VALUES", [])
    except sqlite3.Error as exc:
        return WriteResult(False, f"足せませんでした: {exc}", "failed")
    return WriteResult(True, "行を足しました。", "", int(rid or 0))


def delete(conn: sqlite3.Connection, table: str, rowid: int, seen: dict[str, Any]) -> WriteResult:
    now = _current(conn, table, rowid)
    if now is None:
        return WriteResult(False, MSG_GONE, "gone")
    for name, v in seen.items():
        if name in now and now[name] != ("" if v is None else str(v)):
            return WriteResult(False, MSG_CONFLICT, "conflict", rowid)
    try:
        _write(conn, f"DELETE FROM {q(table)} WHERE rowid = ?", [rowid])
    except sqlite3.Error as exc:
        return WriteResult(False, f"消せませんでした: {exc}", "failed", rowid)
    return WriteResult(True, "削除しました。", "", rowid)


# ------------------------------------------------------------------
# ファイルの列のプレビュー
# ------------------------------------------------------------------
def file_target(value: str, base) -> Optional["Path"]:
    """セルの値 → ファイルの場所。絶対パスはそのまま、相対パスは 参照ファイルの起点 から。"""
    from pathlib import Path
    text = str(value or "").strip().strip('"')
    if not text:
        return None
    p = Path(text)
    if not p.is_absolute() and not re.match(r"^[A-Za-z]:[\\/]|^\\\\", text):
        p = Path(base) / p
    return p


def resolve(conn: sqlite3.Connection, table: str, column: str, value: str, base, *,
            need: str) -> tuple[str, Any, str]:
    """押したマスの行き先 → (種類 "url"/"file", URL か Path, だめなら理由)。

    **その表のその列に入っている値だけ**(どこのファイルでも開ける口にしない)。
    プレビューは「入力: ファイル」の列か、クリックが 現物を開く / プレビュー の列。
    現物を開く は、クリックが 現物を開く の列だけ。"""
    if not table_exists(conn, table) or column not in column_names(conn, table):
        return "", None, "その表・列がありません。"
    s = by_name(conn, table).get(column)
    if s is None:
        return "", None, "その表・列がありません。"
    if need == CLICK_OPEN and s.click != CLICK_OPEN:
        return "", None, f"「{column}」は現物を開く列ではありません(かんたん設定のクリックを「現物を開く」に)。"
    if need == CLICK_PREVIEW and s.input != "file" and s.click not in OPENS:
        return "", None, (f"「{column}」は開く列ではありません"
                          f"(かんたん設定のクリックを「プレビュー」か、入力を「ファイル」に)。")
    hit = conn.execute(f"SELECT 1 FROM {q(table)} WHERE CAST({q(column)} AS TEXT) = ? LIMIT 1", (value,)).fetchone()
    if hit is None:
        return "", None, "その値は表に入っていません(保存してから開いてください)。"
    if s.click in OPENS:
        kind, target = click_target(s, value)
        if not kind:
            return "", None, f"この値は開けません(URL でもファイルの場所でもない): {value}"
        if kind == "url":
            return "url", target, ""
        return "file", file_target(target, base), ""
    return "file", file_target(value, base), ""
