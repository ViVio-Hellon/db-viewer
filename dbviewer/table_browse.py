"""テーブル閲覧(VBA `MF3`)と出力

【閲覧】全般設定「テーブル閲覧」に `/` 区切りで書かれた表を見せる。
【出力】画面に出している表(絞り込み・並べ替え・列の設定どおり)をファイルに書く。
ブラウザからは保存ダイアログを出さない(ダウンロードはしない)。設定「出力先」の
フォルダに書いて、場所を知らせる。

形式は CSV が基本。Excel を前提にしない:
- csv … カンマ区切り。値にカンマ・改行・" があれば "…" で囲む(ふつうの CSV)
- tsv … タブ区切り(.txt)。表計算ソフト・メモ帳どちらでも開ける
- xlsx … 標準ライブラリだけで書く(Excel が無くても作れる)
文字コードは CP932(Shift_JIS、日本語版 Excel がそのまま開ける)か UTF-8(BOM 付き)。

VBA の `ExportFile` は「各セルのあとに区切り」(行末にも 1 つ残る)形だった。
Web 版はふつうの CSV/TSV にした(意図した差異、docs/06)。
"""
from __future__ import annotations

import csv
import re
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

MSG_DONE = "出力しました。"
MSG_EMPTY = "出す行がありません。絞り込みを見直してください。"
FORMATS = {"csv": ("csv", ","), "tsv": ("txt", "\t"), "xlsx": ("xlsx", "")}
ENCODINGS = {"cp932": "Shift_JIS(CP932)", "utf-8-sig": "UTF-8(BOM 付き)"}
DEFAULT_FORMAT = "csv"
DEFAULT_ENCODING = "cp932"


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


def export_dir() -> Path:
    """出力先。設定「出力先」、空ならこの PC のローカル領域(work/出力)。"""
    from . import app_config, user_settings
    text = str(user_settings.get(config.KEY_EXPORT_DIR, "") or "").strip()
    default = app_config.local_dir("work") / "出力"
    return config.resolve_dir(text, default) if text else default


def default_name(prefix: str = "") -> str:
    stamp = format_date(datetime.now(), "YYYYMMDD_HHMMSS")
    return f"{prefix}_{stamp}" if prefix else stamp


def _safe_name(name: str) -> str:
    """ファイル名に使えない字を _ に。フォルダを含めさせない。"""
    bad = '\\/:*?"<>|'
    out = "".join("_" if ch in bad or ord(ch) < 32 else ch for ch in str(name)).strip(" .")
    return out[:120] or default_name()


def _unique(path: Path) -> Path:
    if not path.exists():
        return path
    n = 2
    while True:
        cand = path.with_name(f"{path.stem}_{n}{path.suffix}")
        if not cand.exists():
            return cand
        n += 1


def _number(value: str):
    """xlsx で数値として持つもの(先頭 0 の桁や "-" 入りは文字列のまま)。"""
    s = value.strip()
    if s == "" or (len(s) > 1 and s[0] == "0" and s[1] != "."):
        return value
    try:
        n = float(s)
    except ValueError:
        return value
    # Excel が数として持てるのは有効 15 桁まで。それより長い数字(番号など)を数にすると、
    # 開いたときに下の桁が 0 に化ける(12345678901234567 → 12345678901234500)。文字のまま出す
    if len(re.sub(r"[^0-9]", "", s.split("e")[0].split("E")[0]).lstrip("0")) > 15:
        return value
    return int(n) if n.is_integer() and "." not in s and "e" not in s.lower() else n


def export(columns: list[str], rows: list[list[str]], directory: Path, name: str,
           fmt: str = DEFAULT_FORMAT, encoding: str = DEFAULT_ENCODING) -> Path:
    """表を書く。同じ名前があれば `_2` を付ける(上書きしない)。戻り値は書いた場所。"""
    fmt = (fmt or DEFAULT_FORMAT).lower()
    if fmt == "txt":
        fmt = "tsv"
    if fmt not in FORMATS:
        raise ValueError("形式は csv / tsv / xlsx のどれかにしてください。")
    if encoding not in ENCODINGS:
        encoding = DEFAULT_ENCODING
    ext, sep = FORMATS[fmt]
    directory.mkdir(parents=True, exist_ok=True)
    path = _unique(directory / f"{_safe_name(name)}.{ext}")
    if fmt == "xlsx":
        data = [[_number(v) if isinstance(v, str) else v for v in r] for r in rows]
        xlsx_writer.write_xlsx([{"name": "Sheet1", "columns": list(columns), "rows": data}], str(path))
        return path
    with open(path, "w", encoding=encoding, errors="replace", newline="") as f:
        w = csv.writer(f, delimiter=sep, lineterminator="\r\n", quoting=csv.QUOTE_MINIMAL)
        w.writerow(columns)
        w.writerows(rows)
    return path
