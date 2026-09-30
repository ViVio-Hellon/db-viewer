"""帳票の格子(Grid)を印刷用 HTML と xlsx のシートにする"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from .. import printing, xlsx_append
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


def to_sheet(grid: Grid, sheet_name: str) -> xlsx_append.SheetSpec:
    rows = []
    for row in grid.rows:
        cells = []
        for j, c in enumerate(row.cells):
            border = xlsx_append.BORDER_NONE
            if j < 13:
                if row.border_all and row.bottom_double:
                    border = xlsx_append.BORDER_ALL_DOUBLE
                elif row.border_all:
                    border = xlsx_append.BORDER_ALL
                elif row.bottom_double:
                    border = xlsx_append.BORDER_DOUBLE_ONLY
                elif row.bottom_thin:
                    border = xlsx_append.BORDER_BOTTOM
            cells.append(xlsx_append.SheetCell(c.value, c.number, border))
        rows.append(cells)
    return xlsx_append.SheetSpec(name=sheet_name, rows=rows, font_name=FONT_NAME, font_size=FONT_SIZE,
                                 col_widths=column_widths(grid), paper_size=9, orientation=ORIENTATION)


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


def table_html(title: str, columns: list[str], rows: list[list[str]]) -> str:
    """VBA MF3 の PrintForm の代わり: 表をそのまま A4 横に印刷する。"""
    setup = printing.PageSetup(paper="A4", orientation=printing.LANDSCAPE, margin_mm=printing.SAFE_MARGIN_MM,
                               extra_css="table.form { font-size: 8pt; } table.form td, table.form th { white-space: nowrap; }")
    report = printing.Report(title=title, setup=setup)
    report.add_sheet(f"<h3 style='margin:0 0 4px'>{printing.escape(title)}</h3>" + printing.table(rows, header=columns))
    return printing.render_html(report)
