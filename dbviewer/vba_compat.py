"""VBA の組み込み関数の挙動を再現する小さな道具

判定・変換の結果を VBA と同じにするための関数。**ここにあるものは
VBA の癖をそのまま写している**(直したくなっても直さない)。
"""
from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime, timedelta
from typing import Optional

# VBA `IsDate` が真になる代表的な書き方。Windows の日本語ロケールで
# `CDate` が解釈できるもの(yyyy/m/d, yyyy-m-d, m/d, yyyy年m月d日 など)。
# 時刻つき(hh:mm[:ss])も受ける。
_DATE_PATTERNS = (
    re.compile(r"^\s*(\d{4})[/\-.](\d{1,2})[/\-.](\d{1,2})(?:\s+(\d{1,2}):(\d{1,2})(?::(\d{1,2}))?)?\s*$"),
    re.compile(r"^\s*(\d{4})年(\d{1,2})月(\d{1,2})日(?:\s+(\d{1,2}):(\d{1,2})(?::(\d{1,2}))?)?\s*$"),
)
_MD_PATTERN = re.compile(r"^\s*(\d{1,2})[/\-](\d{1,2})\s*$")
_YM_PATTERN = re.compile(r"^\s*(\d{4})[/\-](\d{1,2})\s*$")
_TIME_PATTERN = re.compile(r"^\s*(\d{1,2}):(\d{1,2})(?::(\d{1,2}))?\s*$")


def _build(y: int, m: int, d: int, hh: int = 0, mm: int = 0, ss: int = 0) -> Optional[datetime]:
    try:
        return datetime(y, m, d, hh, mm, ss)
    except ValueError:
        return None


def cdate(text: object) -> Optional[datetime]:
    """VBA `CDate` 相当。解釈できなければ `None`(VBA は型エラー)。

    VBA の `IsDate("1-2-3")` は真(2001/02/03)、`IsDate("0-1-3-1")` は偽。
    その挙動は `GetSQLFilterStr` の分岐に効くので、ここで同じにする。
    """
    if isinstance(text, datetime):
        return text
    if isinstance(text, date):
        return datetime(text.year, text.month, text.day)
    if text is None:
        return None
    s = str(text).strip()
    if not s:
        return None
    for pat in _DATE_PATTERNS:
        m = pat.match(s)
        if m:
            g = [int(x) if x else 0 for x in m.groups()]
            return _build(*g)
    m = _MD_PATTERN.match(s)
    if m:
        # 「1-2」「3/4」→ 今年の月日(VBA と同じ)。「1-2-3」は下で年月日
        return _build(datetime.now().year, int(m.group(1)), int(m.group(2)))
    m = re.match(r"^\s*(\d{1,2})[/\-](\d{1,2})[/\-](\d{1,2})\s*$", s)
    if m:
        # VBA: 1-2-3 → 2001/02/03(2桁年は 30 以上なら 19xx)
        y, mo, d = (int(x) for x in m.groups())
        y = 2000 + y if y < 30 else 1900 + y
        return _build(y, mo, d)
    m = _YM_PATTERN.match(s)
    if m:
        return _build(int(m.group(1)), int(m.group(2)), 1)
    m = _TIME_PATTERN.match(s)
    if m:
        hh, mm, ss = (int(x) if x else 0 for x in m.groups())
        if hh < 24 and mm < 60 and ss < 60:
            return datetime(1899, 12, 30, hh, mm, ss)
    return None


def is_date(text: object) -> bool:
    """VBA `IsDate`。"""
    return cdate(text) is not None


_NUMERIC = re.compile(r"^\s*[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?\s*$")


def is_numeric(text: object) -> bool:
    """VBA `IsNumeric`。全角数字も `StrConv` 前なので偽(VBA と同じ)。"""
    if text is None:
        return False
    if isinstance(text, (int, float)):
        return True
    s = str(text)
    if not s.strip():
        return False
    # VBA はカンマ区切り(1,000)も数値とみなす
    return bool(_NUMERIC.match(s.replace(",", "")))


def val(text: object) -> float:
    """VBA `Val`: 先頭の数値部分だけを読む。読めなければ 0。"""
    if text is None:
        return 0.0
    if isinstance(text, (int, float)):
        return float(text)
    s = str(text).strip().replace(" ", "")
    m = re.match(r"^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?", s)
    if not m:
        return 0.0
    try:
        return float(m.group(0))
    except ValueError:
        return 0.0


def val_int(text: object) -> int:
    """`CLng(Val(x))` 相当(VBA の丸めは銀行家丸めだが、ここでは切り捨てで十分)。"""
    return int(val(text))


def to_narrow(text: str) -> str:
    """VBA `StrConv(x, vbNarrow)`: 全角の英数記号・カナを半角に。"""
    return unicodedata.normalize("NFKC", text) if text else text


def cv(text: str) -> str:
    """VBA `cv`: 大文字・半角に変換。"""
    return to_narrow((text or "").upper())


def format_date(value: datetime, fmt: str) -> str:
    """VBA `Format(date, fmt)` のうち使っている書式だけ。"""
    if fmt == "YYYY/MM/DD hh:mm:ss":
        return value.strftime("%Y/%m/%d %H:%M:%S")
    if fmt == "YYYY/MM/DD":
        return value.strftime("%Y/%m/%d")
    if fmt == "YYYYMMDD":
        return value.strftime("%Y%m%d")
    if fmt == "YYYY":
        return value.strftime("%Y")
    if fmt == "YYYYMM":
        return value.strftime("%Y%m")
    if fmt == "yyyy-mm-dd":
        return value.strftime("%Y-%m-%d")
    if fmt == "YYYYMMDD_HHMMSS":
        return value.strftime("%Y%m%d_%H%M%S")
    raise ValueError(f"未対応の書式: {fmt}")


def format_thousands(value: object) -> str:
    """VBA `Format(x, '#,##0')`。数値でなければそのまま。"""
    if value is None:
        return ""
    try:
        num = float(value)
    except (TypeError, ValueError):
        return str(value)
    return f"{int(round(num)):,}"


def format_000(n: int) -> str:
    """VBA `Format(i, "000")`。"""
    return f"{int(n):03d}"


def date_add_month(value: datetime, months: int) -> datetime:
    """VBA `DateAdd("m", n, d)`。月末はその月の末日に丸める。"""
    y = value.year + (value.month - 1 + months) // 12
    m = (value.month - 1 + months) % 12 + 1
    last = (datetime(y + (m == 12), (m % 12) + 1, 1) - timedelta(days=1)).day
    return value.replace(year=y, month=m, day=min(value.day, last))


def normalize_date_text(text: object) -> str:
    """文字列の日付を 'yyyy/mm/dd hh:mm:ss' / 'yyyy/mm/dd' にそろえる(比較用)。

    Access は TEXT 列と日付リテラルを比べるとき、TEXT を日付に変換してから
    比べる。SQLite にはそれが無いので、比較の前に両辺をこの形にそろえる
    (`shared_db` が SQL 関数 `VBADATE` として登録する)。
    日付と読めない文字列はそのまま返す。
    """
    d = cdate(text)
    if d is None:
        return "" if text is None else str(text)
    if d.hour == 0 and d.minute == 0 and d.second == 0:
        return d.strftime("%Y/%m/%d")
    return d.strftime("%Y/%m/%d %H:%M:%S")


def now_text() -> str:
    """VBA `Format(Now, "YYYY/MM/DD hh:mm:ss")`。"""
    return datetime.now().strftime("%Y/%m/%d %H:%M:%S")


def clean(text: object) -> str:
    """Excel `Application.Clean` + `Trim`: 制御文字を除き、前後の空白を落とす。"""
    if text is None:
        return ""
    s = "".join(ch for ch in str(text) if ord(ch) >= 32 or ch in "\t")
    return s.strip(" ")


def vba_sort_key(text: object) -> tuple:
    """VBA `QuickSort` の `<` 比較(Option Compare Binary = 文字コード順)。"""
    return (str(text) if text is not None else "",)
