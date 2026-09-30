"""既存の xlsx(帳票原紙)にシートを 1 枚足す(標準ライブラリだけ)

VBA は原紙ブック(出力原紙.xlsx)を開き、一時シートを末尾に複製してから値と罫線を
置き、**原紙ブックそのものを保存**していた(原紙にシートが溜まっていく)。
同じ動きにするため、zip の中の XML を直して書き戻す:

    xl/worksheets/sheetN.xml   … 新しいシート(値・列幅・ページ設定)
    xl/workbook.xml            … <sheets> に 1 行
    xl/_rels/workbook.xml.rels … 関係を 1 行
    [Content_Types].xml        … Override を 1 行
    docProps/app.xml           … シート名の一覧(あれば)
    xl/styles.xml              … フォント・罫線・セル書式を足す

セルの書式は「フォント(メイリオ 10)+ 罫線の形」の組み合わせで足す。
数値に読める値は数値セル、それ以外は文字列(inlineStr)。
"""
from __future__ import annotations

import os
import re
import shutil
import zipfile
from dataclasses import dataclass, field
from html import escape
from pathlib import Path
from typing import Optional

_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_NS_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_REL_WS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"
_CT_WS = "application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"

# 罫線の形(この順で styles.xml に足す)
BORDER_NONE = "none"
BORDER_ALL = "all"                  # 四辺 細線
BORDER_ALL_DOUBLE = "all_double"    # 三辺 細線 + 下 二重線
BORDER_BOTTOM = "bottom"            # 下だけ 細線
BORDER_DOUBLE_ONLY = "double_only"  # 下だけ 二重線
_BORDER_XML = {
    BORDER_NONE: "<border><left/><right/><top/><bottom/><diagonal/></border>",
    BORDER_ALL: ('<border><left style="thin"><color indexed="64"/></left><right style="thin"><color indexed="64"/></right>'
                 '<top style="thin"><color indexed="64"/></top><bottom style="thin"><color indexed="64"/></bottom><diagonal/></border>'),
    BORDER_ALL_DOUBLE: ('<border><left style="thin"><color indexed="64"/></left><right style="thin"><color indexed="64"/></right>'
                        '<top style="thin"><color indexed="64"/></top><bottom style="double"><color indexed="64"/></bottom><diagonal/></border>'),
    BORDER_BOTTOM: '<border><left/><right/><top/><bottom style="thin"><color indexed="64"/></bottom><diagonal/></border>',
    BORDER_DOUBLE_ONLY: '<border><left/><right/><top/><bottom style="double"><color indexed="64"/></bottom><diagonal/></border>',
}


@dataclass
class SheetCell:
    value: str = ""
    number: bool = False
    border: str = BORDER_NONE


@dataclass
class SheetSpec:
    name: str
    rows: list[list[SheetCell]]
    font_name: str = "メイリオ"
    font_size: float = 10
    col_widths: list[float] = field(default_factory=list)
    paper_size: int = 9              # A4
    orientation: str = "portrait"


def col_letter(index: int) -> str:
    """1 → A, 27 → AA。"""
    out = ""
    n = index
    while n > 0:
        n, r = divmod(n - 1, 26)
        out = chr(65 + r) + out
    return out


def unique_sheet_name(existing: list[str], base: str) -> str:
    """VBA: 名前かぶりがあったら `_1`, `_2` …。"""
    if base not in existing:
        return base
    j = 1
    while f"{base}_{j}" in existing:
        j += 1
    return f"{base}_{j}"


def sheet_names(path: Path) -> list[str]:
    with zipfile.ZipFile(path) as z:
        wb = z.read("xl/workbook.xml").decode("utf-8")
    return [_unescape(m) for m in re.findall(r'<sheet [^>]*?name="([^"]*)"', wb)]


def _unescape(text: str) -> str:
    return (text.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
            .replace("&apos;", "'").replace("&amp;", "&"))


def _bump_count(xml: str, tag: str, add: int) -> str:
    m = re.search(rf'<{tag} count="(\d+)"', xml)
    if not m:
        return xml
    return xml[:m.start(1)] + str(int(m.group(1)) + add) + xml[m.end(1):]


def _add_styles(styles: str, font_name: str, font_size: float) -> tuple[str, dict[str, int]]:
    """フォント 1 つと罫線 5 つ、セル書式 5 つを足す。戻り値は罫線の形 → cellXfs の番号。"""
    fonts_count = int(re.search(r'<fonts count="(\d+)"', styles).group(1))
    font_xml = (f'<font><sz val="{font_size:g}"/><color theme="1"/><name val="{escape(font_name)}"/>'
                f'<family val="2"/><charset val="128"/></font>')
    styles = styles.replace("</fonts>", font_xml + "</fonts>", 1)
    styles = _bump_count(styles, "fonts", 1)
    font_id = fonts_count

    borders_count = int(re.search(r'<borders count="(\d+)"', styles).group(1))
    border_ids = {}
    add = ""
    for i, (key, xml) in enumerate(_BORDER_XML.items()):
        border_ids[key] = borders_count + i
        add += xml
    styles = styles.replace("</borders>", add + "</borders>", 1)
    styles = _bump_count(styles, "borders", len(_BORDER_XML))

    xfs_count = int(re.search(r'<cellXfs count="(\d+)"', styles).group(1))
    xf_ids = {}
    add = ""
    for i, key in enumerate(_BORDER_XML):
        xf_ids[key] = xfs_count + i
        add += (f'<xf numFmtId="0" fontId="{font_id}" fillId="0" borderId="{border_ids[key]}" xfId="0" '
                f'applyFont="1" applyBorder="1"><alignment vertical="center"/></xf>')
    styles = styles.replace("</cellXfs>", add + "</cellXfs>", 1)
    styles = _bump_count(styles, "cellXfs", len(_BORDER_XML))
    return styles, xf_ids


def _sheet_xml(spec: SheetSpec, xf_ids: dict[str, int], template_sheet: Optional[str]) -> str:
    width = max((len(r) for r in spec.rows), default=1)
    cols = ""
    if spec.col_widths:
        cols = "<cols>" + "".join(
            f'<col min="{i}" max="{i}" width="{w:.4f}" customWidth="1"/>'
            for i, w in enumerate(spec.col_widths, start=1)) + "</cols>"
    rows_xml = []
    for r, row in enumerate(spec.rows, start=1):
        cells = []
        for c, cell in enumerate(row, start=1):
            ref = f"{col_letter(c)}{r}"
            s = xf_ids[cell.border]
            if cell.value == "" and cell.border == BORDER_NONE:
                continue
            if cell.number:
                cells.append(f'<c r="{ref}" s="{s}"><v>{escape(cell.value)}</v></c>')
            elif cell.value == "":
                cells.append(f'<c r="{ref}" s="{s}"/>')
            else:
                cells.append(f'<c r="{ref}" s="{s}" t="inlineStr"><is><t xml:space="preserve">{escape(cell.value)}</t></is></c>')
        rows_xml.append(f'<row r="{r}">' + "".join(cells) + "</row>")
    dim = f"A1:{col_letter(width)}{max(1, len(spec.rows))}"
    margins = '<pageMargins left="0.7" right="0.7" top="0.75" bottom="0.75" header="0.3" footer="0.3"/>'
    setup = f'<pageSetup paperSize="{spec.paper_size}" orientation="{spec.orientation}"/>'
    if template_sheet:
        m = re.search(r"<pageMargins[^>]*/>", template_sheet)
        if m:
            margins = m.group(0)
        m = re.search(r"<pageSetup[^>]*/>", template_sheet)
        if m:
            setup = re.sub(r'\s+r:id="[^"]*"', "", m.group(0))
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            f'<worksheet xmlns="{_NS}" xmlns:r="{_NS_R}">'
            f'<dimension ref="{dim}"/><sheetViews><sheetView workbookViewId="0"/></sheetViews>'
            '<sheetFormatPr defaultRowHeight="16.5"/>' + cols +
            "<sheetData>" + "".join(rows_xml) + "</sheetData>" + margins + setup + "</worksheet>")


def append_sheet(path: Path, spec: SheetSpec, *, template_sheet_name: Optional[str] = None) -> str:
    """`path` の xlsx にシートを足して上書きする。戻り値は付いたシート名。"""
    tmp = path.with_name(path.name + ".tmp")
    with zipfile.ZipFile(path) as zin:
        names = zin.namelist()
        workbook = zin.read("xl/workbook.xml").decode("utf-8")
        rels = zin.read("xl/_rels/workbook.xml.rels").decode("utf-8")
        types = zin.read("[Content_Types].xml").decode("utf-8")
        styles = zin.read("xl/styles.xml").decode("utf-8")
        app = zin.read("docProps/app.xml").decode("utf-8") if "docProps/app.xml" in names else None

        existing = [_unescape(m) for m in re.findall(r'<sheet [^>]*?name="([^"]*)"', workbook)]
        sheet_name = unique_sheet_name(existing, spec.name)
        sheet_ids = [int(x) for x in re.findall(r'sheetId="(\d+)"', workbook)]
        sheet_id = max(sheet_ids or [0]) + 1
        n = 1
        while f"xl/worksheets/sheet{n}.xml" in names:
            n += 1
        rid_nums = [int(x) for x in re.findall(r'Id="rId(\d+)"', rels)]
        rid = f"rId{max(rid_nums or [0]) + 1}"

        template_sheet = None
        if template_sheet_name:
            for m in re.finditer(r'<sheet [^>]*?name="([^"]*)"[^>]*?r:id="([^"]*)"', workbook):
                if _unescape(m.group(1)) == template_sheet_name:
                    t = re.search(rf'<Relationship Id="{m.group(2)}"[^>]*Target="([^"]*)"', rels)
                    if t and ("xl/" + t.group(1)) in names:
                        template_sheet = zin.read("xl/" + t.group(1)).decode("utf-8")
                    break

        styles, xf_ids = _add_styles(styles, spec.font_name, spec.font_size)
        sheet_xml = _sheet_xml(spec, xf_ids, template_sheet)
        workbook = workbook.replace(
            "</sheets>", f'<sheet name="{escape(sheet_name)}" sheetId="{sheet_id}" r:id="{rid}"/></sheets>', 1)
        rels = rels.replace(
            "</Relationships>",
            f'<Relationship Id="{rid}" Type="{_REL_WS}" Target="worksheets/sheet{n}.xml"/></Relationships>', 1)
        types = types.replace(
            "</Types>", f'<Override PartName="/xl/worksheets/sheet{n}.xml" ContentType="{_CT_WS}"/></Types>', 1)
        if app:
            app = _patch_app(app, sheet_name)

        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                if item.filename in ("xl/workbook.xml", "xl/_rels/workbook.xml.rels", "[Content_Types].xml",
                                     "xl/styles.xml", "docProps/app.xml"):
                    continue
                zout.writestr(item, zin.read(item.filename))
            zout.writestr("xl/workbook.xml", workbook)
            zout.writestr("xl/_rels/workbook.xml.rels", rels)
            zout.writestr("[Content_Types].xml", types)
            zout.writestr("xl/styles.xml", styles)
            if app:
                zout.writestr("docProps/app.xml", app)
            zout.writestr(f"xl/worksheets/sheet{n}.xml", sheet_xml)
    os.replace(tmp, path)
    return sheet_name


def _patch_app(app: str, sheet_name: str) -> str:
    """docProps/app.xml のシート一覧に足す(無ければ触らない)。"""
    m = re.search(r'<TitlesOfParts><vt:vector size="(\d+)" baseType="lpstr">', app)
    if not m:
        return app
    size = int(m.group(1)) + 1
    app = app[:m.start(1)] + str(size) + app[m.end(1):]
    app = app.replace("</vt:vector></TitlesOfParts>",
                      f"<vt:lpstr>{escape(sheet_name)}</vt:lpstr></vt:vector></TitlesOfParts>", 1)
    m2 = re.search(r"<vt:lpstr>ワークシート</vt:lpstr></vt:variant><vt:variant><vt:i4>(\d+)</vt:i4>", app)
    if m2:
        app = app[:m2.start(1)] + str(int(m2.group(1)) + 1) + app[m2.end(1):]
    return app


def copy_template(template: Path, target: Path) -> None:
    shutil.copyfile(template, target)
