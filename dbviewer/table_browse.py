"""テーブル閲覧(VBA `MF3`)と出力(VBA `ExportData` / `ExportFile` / `SaveArrayAsWorkbook`)

【閲覧】全般設定「テーブル閲覧」に `/` 区切りで書かれた表を、全行 1 ページで見せる。
【出力】VBA は保存ダイアログで xlsx / txt(タブ) / csv を選ばせ、既定のファイル名は
`Format(Now, "YYYYMMDD_HHMMSS")`。Web はブラウザから OS の保存ダイアログを出せないので、
設定タブ「出力先」のフォルダに書いて場所を知らせる。

txt / csv の行の形は VBA の `ExportFile` そのまま: **各セルのあとに区切りを付ける**
(行末にも 1 つ残る)、行末は CRLF、文字コードは ANSI(cp932)。
xlsx は 1 シートに配列をそのまま置く。Excel は文字列でも数値に見えるものを数値として
持つので、数値に読める値だけ数値にする(日付は文字列のまま)。
"""
from __future__ import annotations

import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from . import config
from .settings_tables import read_table, table_exists
from .vba_compat import format_date

sys.path.insert(0, str(config.BASE_DIR / "vendor"))
import xlsx_writer  # noqa: E402  (標準ライブラリだけの writer)

MSG_ASK = "ListViewに表示されているデータを出力しますか？"
MSG_DONE = "出力しました。"
MSG_CANCEL = "キャンセルしました。"
MSG_EMPTY_ASK = "データが空です。強制的に作成しますか？"
EXTENSIONS = ("xlsx", "txt", "csv")


def browse_tables(browse_str: str) -> list[str]:
    """VBA `MF3.リスト更新`: `/` 区切り。空なら無し。"""
    if browse_str == "":
        return []
    return browse_str.split("/") if "/" in browse_str else [browse_str]


@dataclass
class TableView:
    table: str
    columns: list[str]
    rows: list[list[str]]


def view(conn: sqlite3.Connection, table: str) -> TableView:
    if not table_exists(conn, table):
        raise ValueError(f"テーブル [{table}] がありません。")
    cols, rows = read_table(conn, table)
    return TableView(table, cols, rows)


def default_name() -> str:
    return format_date(datetime.now(), "YYYYMMDD_HHMMSS")


def _number(value: str):
    """Excel が数値として持つもの(先頭 0 の桁や "-" 入りは文字列のまま)。"""
    s = value.strip()
    if s == "" or (len(s) > 1 and s[0] == "0" and s[1] != "."):
        return value
    try:
        n = float(s)
    except ValueError:
        return value
    return int(n) if n.is_integer() and "." not in s and "e" not in s.lower() else n


def export(table_data: list[list[str]], directory: Path, name: str, ext: str) -> Path:
    """`table_data` は 1 行目が列名(VBA の `GetTableArr` / `TableData` と同じ形)。"""
    ext = ext.lower().lstrip(".")
    if ext not in EXTENSIONS:
        raise ValueError("拡張子は xlsx / txt / csv のどれかにしてください。")
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name or default_name()}.{ext}"
    if ext == "xlsx":
        header = table_data[0] if table_data else []
        rows = [[_number(v) if isinstance(v, str) else v for v in r] for r in table_data[1:]]
        xlsx_writer.write_xlsx([{"name": "Sheet1", "columns": header, "rows": rows}], str(path))
        return path
    spl = "\t" if ext == "txt" else ","
    with open(path, "w", encoding="cp932", errors="replace", newline="") as f:
        for row in table_data:
            f.write("".join(str(v) + spl for v in row) + "\r\n")
    return path
