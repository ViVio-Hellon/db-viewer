"""補助入力の選択肢(VBA `SF` フォームと `TextDblClickEvents` のデータ側)

- マスタ … `SELECT * FROM [マスタ名]` の全列・全行(画面は表で見せ、押した行の
            マスタ名と同じ列の値を返す。無ければ 2 列目 = 管理番号の次)
- マスタ連動 … `マスタ名/結合列/表示列`。結合列の値を入れ、表示列を連動欄に出す
- フィールド … いまの一覧の列名
- フォルダ … 参照形式 `拡張子/絶対パス/相対パス` のフォルダにあるファイル名(拡張子なし)
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from . import config
from .settings_tables import field_dic, read_table, table_exists


@dataclass
class MasterChoices:
    name: str
    columns: list[str]
    rows: list[list[str]]
    value_col: int                 # 返す列(1 始まり)。VBA: マスタ名と同じ列、無ければ 2
    link_col: int = 0              # 連動表示の列(マスタ連動のとき)
    multi: bool = False
    message: str = ""


def master(conn: sqlite3.Connection, name: str, *, multi: bool = False,
           parent: str = "MF1") -> MasterChoices:
    if not table_exists(conn, name):
        return MasterChoices(name, [], [], 0, message=f"マスタ [{name}] がありません。")
    cols, rows = read_table(conn, name)
    fj = field_dic(cols)
    j = fj.get(name, 0)
    if parent == "MF2":
        j = 2
    if j == 0:
        j = 2 if len(cols) >= 2 else 1
    return MasterChoices(name, cols, rows, j, multi=multi)


def master_link(conn: sqlite3.Connection, ms_join: str, *, multi: bool = False) -> MasterChoices:
    """`マスタ名/結合列/表示列`。"""
    parts = ms_join.split("/")
    name = parts[0]
    if not table_exists(conn, name):
        return MasterChoices(name, [], [], 0, message=f"マスタ [{name}] がありません。")
    cols, rows = read_table(conn, name)
    fj = field_dic(cols)
    j = fj.get(parts[1], 0) if len(parts) > 1 else 0
    k = fj.get(parts[2], 0) if len(parts) > 2 else 0
    if j == 0:
        j = 2 if len(cols) >= 2 else 1
    return MasterChoices(name, cols, rows, j, link_col=k, multi=multi)


def link_display(conn: sqlite3.Connection, ms_join: str, value: str) -> str:
    """VBA `SET_BEF` の USERJOIN: 結合列 = 値 の行の表示列(先頭に空白 1 つ)。"""
    if "/" not in ms_join or value == "":
        return ""
    parts = ms_join.split("/")
    if len(parts) < 3 or not table_exists(conn, parts[0]):
        return ""
    cols, rows = read_table(conn, parts[0])
    fj = field_dic(cols)
    j, k = fj.get(parts[1], 0), fj.get(parts[2], 0)
    if not j or not k:
        return ""
    for r in rows:
        if r[j - 1] == value:
            return " " + r[k - 1]
    return ""


def folder_files(directory: Path, ext: str) -> list[str]:
    """VBA `FileNameIndex(path, "*" & ext)` → 拡張子を除いた名前(`Split(name, ".")(0)`)。"""
    try:
        names = sorted(p.name for p in directory.iterdir() if p.is_file() and p.name.lower().endswith(ext.lower()))
    except OSError:
        return []
    return [n.split(".")[0] for n in names]


@dataclass
class TagInfo:
    """VBA `GetArrFromTag`: `種類/絶対パス/相対パス` → (種類, 実際のフォルダ)。"""
    kind: str
    directory: Path
    raw: str = ""
    extra: list[str] = field(default_factory=list)


def tag_info(tag: str, *, base_dir: Path, server_dir: Path) -> TagInfo:
    if "/" in tag:
        parts = tag.split("/")
        abs_dir = parts[1] if parts[1] != "" else str(base_dir)
        if len(parts) >= 3:
            rel = parts[2]
            abs_dir = str(server_dir) if rel == "" else str(server_dir / rel)
        return TagInfo(parts[0], Path(abs_dir), tag, parts[3:])
    return TagInfo(tag, base_dir, tag)
