"""見えない文字の点検(0.42.0)

表の値に、目に見えないのに入っている文字があると、画面では同じに見えるのに一致しない
(ファイルを探す・絞り込む・選択肢と比べる で食い違う)。作業NO 0-1-2・10-12-2 で起きた。
表の全部の列を見て、そういう値を一覧にする。**直さない(見るだけ)**。

見るもの:
    前後の空白類   … 半角・全角の空白、改行、タブ、NBSP など(値の前後にあるもの)
    見えない文字   … NBSP・BOM・ゼロ幅スペースなど(どこにあっても)
    制御文字       … 改行・タブ以外の 0x00〜0x1F・0x7F(どこにあっても)
    先頭の '       … Excel で数式にしないために付けた ' が残ったもの(かもしれない)
"""
from __future__ import annotations

import sqlite3
from typing import Any

from .settings_tables import column_names, q, table_exists

MAX_FINDINGS = 500

# 見えない文字(名前)
INVISIBLE: dict[str, str] = {
    " ": "NBSP", "­": "ソフトハイフン", "​": "ゼロ幅スペース", "‌": "ゼロ幅非接合子",
    "‍": "ゼロ幅接合子", "⁠": "ワードジョイナー", "﻿": "BOM", " ": "狭いNBSP",
    " ": "行区切り", " ": "段落区切り",
}
SPACES: dict[str, str] = {" ": "半角空白", "　": "全角空白", "\t": "タブ", "\r": "CR", "\n": "改行"}


def _name(ch: str) -> str:
    if ch in INVISIBLE:
        return INVISIBLE[ch]
    if ch in SPACES:
        return SPACES[ch]
    if ord(ch) < 32 or ord(ch) == 0x7F:
        return f"制御文字 U+{ord(ch):04X}"
    if ch.isspace():
        return f"空白 U+{ord(ch):04X}"
    return ""


def _odd(ch: str) -> bool:
    return ch in INVISIBLE or (ord(ch) < 32 and ch not in "\t\r\n") or ord(ch) == 0x7F


def inspect_value(text: str) -> list[str]:
    """値のおかしな所(種類)。無ければ空。"""
    kinds: list[str] = []
    stripped = text.strip()
    if stripped != text:
        lead, trail = text[: len(text) - len(text.lstrip())], text[len(text.rstrip()):]
        names = sorted({_name(c) or f"U+{ord(c):04X}" for c in lead + trail})
        kinds.append("前後に空白類(" + "・".join(names) + ")")
    odd = sorted({_name(c) for c in text if _odd(c)})
    if odd:
        kinds.append("見えない文字(" + "・".join(odd) + ")")
    if "\n" in stripped or "\r" in stripped:
        kinds.append("途中に改行")
    if stripped.startswith("'"):
        kinds.append("先頭に '(Excel の名残かも)")
    return kinds


def show(text: str) -> str:
    """値を、見えない文字・前後の空白を印にして見せる(例: ⟨BOM⟩10-12-2⟨NBSP⟩)。"""
    out = []
    n = len(text)
    lead = n - len(text.lstrip())
    trail = len(text.rstrip())
    for i, ch in enumerate(text):
        name = _name(ch)
        if _odd(ch) or (name and (i < lead or i >= trail)) or ch in "\r\n":
            out.append(f"⟨{name or f'U+{ord(ch):04X}'}⟩")
        else:
            out.append(ch)
    return "".join(out)


def check_table(conn: sqlite3.Connection, table: str) -> dict[str, Any]:
    """表の全部の文字の値を点検する。"""
    if not table_exists(conn, table):
        raise ValueError(f"表 [{table}] がありません。")
    cols = column_names(conn, table)
    findings: list[dict[str, Any]] = []
    by_col: dict[str, int] = {}
    by_kind: dict[str, int] = {}
    total = 0
    sel = ", ".join(q(c) for c in cols)
    for row in conn.execute(f"SELECT rowid, {sel} FROM {q(table)}"):
        rowid, values = row[0], row[1:]
        for col, v in zip(cols, values):
            if not isinstance(v, str) or not v:
                continue
            kinds = inspect_value(v)
            if not kinds:
                continue
            total += 1
            by_col[col] = by_col.get(col, 0) + 1
            for k in kinds:
                key = k.split("(")[0]
                by_kind[key] = by_kind.get(key, 0) + 1
            if len(findings) < MAX_FINDINGS:
                findings.append({"rowid": rowid, "head": "" if values[0] is None else str(values[0]),
                                 "column": col, "value": v, "shown": show(v), "kinds": kinds})
    return {"table": table, "head": cols[0] if cols else "", "total": total, "findings": findings,
            "truncated": total > len(findings), "by_column": by_col, "by_kind": by_kind,
            "rows": conn.execute(f"SELECT COUNT(*) FROM {q(table)}").fetchone()[0]}


# ------------------------------------------------------------------
# 取り除く(0.43.0)。書く前に DB の控えを取る。1 つの取引で書く(途中で失敗したら何も変えない)
# ------------------------------------------------------------------
def clean(text: str, *, apostrophe: bool = False) -> str:
    """前後の空白類・見えない文字を取り、途中の見えない文字・制御文字(改行・タブ以外)も取る。
    **途中の改行は残す**(わざと入れていることがある)。`apostrophe` なら先頭の ' も取る。"""
    t = "".join(ch for ch in text if not _odd(ch))
    t = t.strip()
    if apostrophe and t.startswith("'"):
        t = t[1:].strip()
    return t


def plan(conn: sqlite3.Connection, table: str, *, apostrophe: bool = False) -> list[tuple[int, str, str, str]]:
    """直す所: (rowid, 列, いまの値, 直した値)。"""
    cols = column_names(conn, table)
    sel = ", ".join(q(c) for c in cols)
    out = []
    for row in conn.execute(f"SELECT rowid, {sel} FROM {q(table)}"):
        for col, v in zip(cols, row[1:]):
            if isinstance(v, str) and v:
                new = clean(v, apostrophe=apostrophe)
                if new != v:
                    out.append((row[0], col, v, new))
    return out


def clean_table(conn: sqlite3.Connection, table: str, *, apostrophe: bool = False, backup) -> dict[str, Any]:
    """取り除く。`backup()` は控えのファイルの場所を返す(取れなければ空 → 書かない)。"""
    if not table_exists(conn, table):
        raise ValueError(f"表 [{table}] がありません。")
    todo = plan(conn, table, apostrophe=apostrophe)
    if not todo:
        return {"ok": True, "changed": 0, "backup": "", "message": "取り除くものはありませんでした。"}
    saved = backup()
    if not saved:
        return {"ok": False, "changed": 0, "backup": "",
                "message": "DB の控えを取れなかったので、書き換えていません。"}
    # SAVEPOINT: 取引の外でも中でも、途中で失敗したらここで書いたぶんだけ戻せる
    try:
        conn.execute("SAVEPOINT char_clean")
        for rowid, col, old, new in todo:
            conn.execute(f"UPDATE {q(table)} SET {q(col)} = ? WHERE rowid = ? AND {q(col)} = ?",
                         (None if new == "" else new, rowid, old))
        conn.execute("RELEASE char_clean")
        conn.commit()
    except sqlite3.Error as exc:
        conn.execute("ROLLBACK TO char_clean")
        conn.execute("RELEASE char_clean")
        return {"ok": False, "changed": 0, "backup": saved,
                "message": f"書き換えられませんでした(何も変えていません): {exc}"}
    cols = sorted({c for _r, c, _o, _n in todo})
    return {"ok": True, "changed": len(todo), "backup": saved,
            "message": f"[{table}] の {len(todo)} 個の値から見えない文字・前後の空白を取り除きました"
                       f"(列: {'・'.join(cols)})。書く前の控え: {saved}"}
