"""帳票の格子(Grid)と、画面の表を、印刷用 HTML にする(Excel は使わない)"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

from .. import printing
from ..vba_compat import format_date
from .sagyo_hyojun import FONT_NAME, FONT_SIZE, ORIENTATION, PAPER, Grid, Row

# VBA: ColumnWidth 10.25 のあと AutoFit。文字数から近い幅を出す(上限あり)
MIN_WIDTH = 10.25
MAX_WIDTH = 60.0


def column_widths(grid: Grid) -> list[float]:
    w = max(13, grid.width)
    out = [MIN_WIDTH] * w
    for row in grid.rows:
        for j, c in enumerate(row.cells):
            length = sum(2 if ord(ch) > 0x7F else 1.1 for ch in c.value)
            out[j] = max(out[j], min(MAX_WIDTH, length + 2))
    return out


def default_sheet_name(now: datetime | None = None) -> str:
    """VBA `Format(Date, "yyyy-mm-dd")`。"""
    return format_date(now or datetime.now(), "yyyy-mm-dd")


_CSS = """
table.rep { border-collapse: collapse; font-family: "メイリオ", Meiryo, sans-serif; font-size: %(size)spt; }
table.rep td { padding: 1px 4px; white-space: nowrap; vertical-align: middle; line-height: 1.6; }
table.rep td.n { text-align: right; }
table.rep tr.all td { border: 1px solid #000; }
table.rep tr.dbl td { border-bottom: 3px double #000; }
table.rep tr.thin td { border-bottom: 1px solid #000; }
"""


# Excel の列幅(文字数)→ mm。メイリオ 10pt の "0" がおよそ 1.9mm
MM_PER_WIDTH = 1.9


def fit_zoom(widths: list[float], setup: printing.PageSetup) -> float:
    """表の幅が印刷できる幅を超えるときの縮小率(1.0 なら等倍)。

    VBA は Excel の既定(A4 縦、縮小なし)で印刷し、収まらない列は次の紙に
    分かれて出ていた。HTML では列を紙に分けられないので、横をページ幅に
    収める(意図した差異として docs/04 に記載)。xlsx 側は原紙どおり。
    """
    total = sum(widths) * MM_PER_WIDTH
    printable = setup.paper_size_mm()[0] - 2 * setup.effective_margin_mm
    return 1.0 if total <= printable else round(printable / total, 3)


def to_html(grid: Grid, title: str) -> str:
    setup = printing.PageSetup(paper=PAPER, orientation=ORIENTATION,
                               margin_mm=printing.SAFE_MARGIN_MM, extra_css=_CSS % {"size": FONT_SIZE})
    report = printing.Report(title=title, setup=setup)
    widths = column_widths(grid)
    zoom = fit_zoom(widths, setup)
    parts = [f'<table class="rep" style="zoom:{zoom}">' if zoom < 1 else '<table class="rep">']
    parts.append("<colgroup>" + "".join(f'<col style="width:{w * MM_PER_WIDTH:.1f}mm">' for w in widths) + "</colgroup>")
    for row in grid.rows:
        cls = " ".join(k for k, on in (("all", row.border_all), ("dbl", row.bottom_double), ("thin", row.bottom_thin)) if on)
        parts.append(f'<tr class="{cls}">')
        cells = list(row.cells) + [type(row.cells[0])() for _ in range(max(0, 13 - len(row.cells)))] if row.cells else []
        for c in cells[:14]:
            parts.append(f'<td class="{"n" if c.number else ""}">{printing.escape(c.value)}</td>')
        parts.append("</tr>")
    parts.append("</table>")
    report.add_sheet("".join(parts))
    return printing.render_html(report)


def _text_mm(text: str, pt: float) -> float:
    """文字列の幅のあたり(mm)。全角は 1 字 = 字の大きさ、半角は その 0.55 倍。"""
    em = pt * 0.3528
    w = 0.0
    for ch in str(text):
        c = ord(ch)
        w += em * (0.5 if 0xFF61 <= c <= 0xFF9F else 1.0 if c > 0x2E80 else 0.55)
    return w


def view_html(title: str, columns: list[str], rows: list[list[str]], *, note: str = "",
              numeric: Optional[list[bool]] = None) -> str:
    """画面に出している表(絞り込み・並べ替え・列の設定どおり)を A4 横で印刷する。

    列の幅は中身から決め、紙の幅(余白 6mm の内側)に収まらなければ縮める。
    見出しは各ページの頭に繰り返す。
    """
    pt = 8.0
    setup = printing.PageSetup(paper="A4", orientation=printing.LANDSCAPE, margin_mm=printing.SAFE_MARGIN_MM,
                               extra_css=_VIEW_CSS % {"pt": pt})
    report = printing.Report(title=title, setup=setup)
    widths = []
    for j, c in enumerate(columns):
        w = _text_mm(c, pt)
        for r in rows[:500]:
            w = max(w, _text_mm(r[j] if j < len(r) else "", pt))
        widths.append(min(max(w + 3.0, 8.0), 80.0))
    # 幅は見積もりなので 3% 余裕を持つ(罫線・余白のぶん)
    printable = (setup.paper_size_mm()[0] - 2 * setup.effective_margin_mm) * 0.97
    total = sum(widths)
    zoom = 1.0 if total <= printable else round(printable / total, 3)
    numeric = numeric or [False] * len(columns)
    parts = [f'<div class="vh"><b>{printing.escape(title)}</b><span>{printing.escape(note)}</span></div>',
             '<table class="vw"' + (f' style="zoom:{zoom}"' if zoom < 1 else "") + ">",
             "<colgroup>" + "".join(f'<col style="width:{w:.1f}mm">' for w in widths) + "</colgroup>",
             "<thead><tr>" + "".join(f"<th>{printing.escape(c)}</th>" for c in columns) + "</tr></thead><tbody>"]
    for r in rows:
        parts.append("<tr>" + "".join(
            f'<td class="{"n" if j < len(numeric) and numeric[j] else ""}">{printing.escape(r[j] if j < len(r) else "")}</td>'
            for j in range(len(columns))) + "</tr>")
    parts.append("</tbody></table>")
    report.add_sheet("".join(parts))
    return printing.render_html(report)


_VIEW_CSS = """
.vh { display:flex; justify-content:space-between; align-items:baseline; margin:0 0 3mm; font-size:10pt; }
.vh span { font-size:8pt; color:#444; }
table.vw { border-collapse:collapse; table-layout:fixed; font-family:"Meiryo UI","Yu Gothic UI",sans-serif; font-size:%(pt)spt; }
table.vw th, table.vw td { border:0.6pt solid #555; padding:0.6mm 1mm; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
table.vw th { background:#e8eef1; text-align:left; }
table.vw td.n { text-align:right; font-variant-numeric:tabular-nums; }
table.vw thead { display:table-header-group; }
table.vw tr { break-inside:avoid; }
"""


def table_html(title: str, columns: list[str], rows: list[list[str]]) -> str:
    """VBA MF3 の PrintForm の代わり: 表をそのまま A4 横に印刷する。"""
    setup = printing.PageSetup(paper="A4", orientation=printing.LANDSCAPE, margin_mm=printing.SAFE_MARGIN_MM,
                               extra_css="table.form { font-size: 8pt; } table.form td, table.form th { white-space: nowrap; }")
    report = printing.Report(title=title, setup=setup)
    report.add_sheet(f"<h3 style='margin:0 0 4px'>{printing.escape(title)}</h3>" + printing.table(rows, header=columns))
    return printing.render_html(report)
