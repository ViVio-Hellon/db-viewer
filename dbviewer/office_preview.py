"""Excel・Word をアプリの窓の中で見る(0.13.0)── 標準ライブラリだけで読む

ライン PC の Python には部品を足せないので、`zipfile` と `xml.etree` だけで .xlsx / .docx の中身を読み、
HTML の表・文にして見せる(書式・図・グラフは出さない。**中身を確かめる**ためのプレビュー)。

    .xlsx / .xlsm … シートごとの表(値。日付の書式なら日付にする)。1 シート 2,000 行 × 100 列まで。
                    **シートのタブで 1 枚ずつ**見る(0.15.0)。隠しシートは出さない(数だけ知らせる)
    .docx         … 段落(見出しは太字)と表。図は出さない
    .xls / .doc   … 古い形式は Python の標準では読めない → その PC の Excel・Word に PDF にしてもらう
                    (`office_bridge`。PDF にできないときは .xlsx / .docx にしてもらい、ここで表・文にする)

壊れたファイル・暗号のかかったファイルは `PreviewError`(理由を窓に出す)。
"""
from __future__ import annotations

import io
import re
import zipfile
from datetime import datetime, timedelta
from html import escape
from pathlib import Path
from xml.etree import ElementTree as ET

MAX_ROWS = 2000
MAX_COLS = 100
MAX_SHEETS = 30
MAX_PARAS = 5000

_S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_PR = "{http://schemas.openxmlformats.org/package/2006/relationships}"
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

# Excel の組み込みの日付・時刻の書式番号
_DATE_FMT_IDS = set(range(14, 23)) | {27, 30, 36, 45, 46, 47, 50, 57}
OLD_FORMATS = {".xls": "Excel 97-2003", ".doc": "Word 97-2003"}


class PreviewError(Exception):
    """プレビューできない(理由は文で)。"""


def _open(path: Path, data: bytes | None = None) -> zipfile.ZipFile:
    try:
        return zipfile.ZipFile(io.BytesIO(data) if data is not None else path)
    except zipfile.BadZipFile:
        raise PreviewError("ファイルが壊れているか、パスワードがかかっていて読めません。「現物を開く」で見てください。")
    except OSError as exc:
        raise PreviewError(f"ファイルを読めません: {exc}")


def _xml(z: zipfile.ZipFile, name: str):
    try:
        return ET.fromstring(z.read(name))
    except KeyError:
        return None
    except ET.ParseError:
        raise PreviewError(f"ファイルの中身({name})を読めません。")


# ------------------------------------------------------------------
# Excel
# ------------------------------------------------------------------
def _col_index(ref: str) -> int:
    """"C12" → 2(0 始まり)。"""
    n = 0
    for ch in re.match(r"[A-Z]*", ref).group(0):
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def _col_name(i: int) -> str:
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def _date_styles(z: zipfile.ZipFile) -> set[int]:
    """日付・時刻の書式になっているセルの書式番号(cellXfs の何番目か)。"""
    root = _xml(z, "xl/styles.xml")
    if root is None:
        return set()
    custom = set()
    fmts = root.find(f"{_S}numFmts")
    if fmts is not None:
        for f in fmts:
            code = re.sub(r'"[^"]*"|\[[^\]]*\]|\\.', "", f.get("formatCode", "")).lower()
            if re.search(r"[ymdhs]", code) and not re.fullmatch(r"[#0.,%e+\- ]*", code):
                custom.add(int(f.get("numFmtId", "0")))
    out = set()
    xfs = root.find(f"{_S}cellXfs")
    for i, xf in enumerate(xfs if xfs is not None else []):
        fid = int(xf.get("numFmtId", "0"))
        if fid in _DATE_FMT_IDS or fid in custom:
            out.add(i)
    return out


def _excel_date(v: str, date1904: bool) -> str:
    try:
        n = float(v)
    except ValueError:
        return v
    base = datetime(1904, 1, 1) if date1904 else datetime(1899, 12, 30)
    d = base + timedelta(days=n)
    if n == int(n):
        return d.strftime("%Y/%m/%d")
    if n < 1:
        return d.strftime("%H:%M:%S").removesuffix(":00") if d.second == 0 else d.strftime("%H:%M:%S")
    return d.strftime("%Y/%m/%d %H:%M")


def _number(v: str) -> str:
    """"120.5" はそのまま、"0.30000000000000004" は 15 桁に丸めて見せる(Excel の見え方に近づける)。"""
    try:
        f = float(v)
    except ValueError:
        return v
    if f == int(f) and abs(f) < 1e15:
        return str(int(f))
    return f"{f:.15g}"


def read_xlsx(path: Path, data: bytes | None = None) -> list[dict]:
    """[{name, rows: [[値...]], cols, truncated}]。"""
    with _open(path, data) as z:
        wb = _xml(z, "xl/workbook.xml")
        if wb is None:
            raise PreviewError("Excel のブックとして読めません。")
        pr = wb.find(f"{_S}workbookPr")
        date1904 = pr is not None and pr.get("date1904") in ("1", "true")
        rels = _xml(z, "xl/_rels/workbook.xml.rels")
        target = {r.get("Id"): r.get("Target", "") for r in (rels if rels is not None else [])}
        shared = []
        ss = _xml(z, "xl/sharedStrings.xml")
        for si in (ss if ss is not None else []):
            shared.append("".join(t.text or "" for t in si.iter(f"{_S}t")))
        dates = _date_styles(z)
        sheets = []
        listed = wb.find(f"{_S}sheets")
        for sh in (list(listed) if listed is not None else [])[:MAX_SHEETS]:
            name = sh.get("name", "")
            t = target.get(sh.get(f"{_R}id"), "")
            part = t.lstrip("/") if t.startswith("/") else "xl/" + t
            root = _xml(z, part)
            rows: list[list[str]] = []
            width = 0
            truncated = False
            if root is not None:
                for row in root.iter(f"{_S}row"):
                    r = int(row.get("r", len(rows) + 1)) - 1
                    if r >= MAX_ROWS:
                        truncated = True
                        break
                    while len(rows) <= r:
                        rows.append([])
                    cells = rows[r]
                    for c in row.findall(f"{_S}c"):
                        ci = _col_index(c.get("r", "")) if c.get("r") else len(cells)
                        if ci >= MAX_COLS:
                            truncated = True
                            continue
                        kind = c.get("t", "n")
                        v = c.find(f"{_S}v")
                        text = v.text if v is not None and v.text is not None else ""
                        if kind == "s":
                            text = shared[int(text)] if text.isdigit() and int(text) < len(shared) else ""
                        elif kind == "inlineStr":
                            text = "".join(x.text or "" for x in c.iter(f"{_S}t"))
                        elif kind == "b":
                            text = "TRUE" if text == "1" else "FALSE"
                        elif kind == "n" and text:
                            text = _excel_date(text, date1904) if int(c.get("s", "0")) in dates else _number(text)
                        while len(cells) <= ci:
                            cells.append("")
                        cells[ci] = text
                    width = max(width, len(cells))
            while rows and not any(rows[-1]):
                rows.pop()
            sheets.append({"name": name, "rows": rows, "cols": width, "truncated": truncated,
                           "hidden": sh.get("state") in ("hidden", "veryHidden")})
        return sheets


def xlsx_html(path: Path, data: bytes | None = None, *, banner: str = "") -> str:
    """`data` があればその中身(Excel が .xls から作った .xlsx)。見出しの名前は `path` のまま。
    シートのタブで 1 枚ずつ見せる(0.15.0)。"""
    sheets = read_xlsx(path, data)
    shown = [sh for sh in sheets if not sh["hidden"]] or sheets
    tabs, bodies = [], []
    for i, sh in enumerate(shown):
        head = "".join(f"<th>{_col_name(c)}</th>" for c in range(sh["cols"]))
        body = []
        for r, row in enumerate(sh["rows"]):
            cells = "".join(f"<td>{escape(v)}</td>" for v in row + [""] * (sh["cols"] - len(row)))
            body.append(f"<tr><th>{r + 1}</th>{cells}</tr>")
        note = (f"<p class='note'>大きいので {MAX_ROWS:,} 行 × {MAX_COLS} 列までを出しています。全部は「現物を開く」で。</p>"
                if sh["truncated"] else "")
        size = f"{len(sh['rows']):,} 行" if sh["rows"] else "空"
        tabs.append(sheet_tab(i, sh["name"], size))
        table = (f"<div class='wrap'><table><thead><tr><th></th>{head}</tr></thead>"
                 f"<tbody>{''.join(body)}</tbody></table></div>" if sh["rows"] else "<p class='note'>(空のシート)</p>")
        bodies.append(f"<section class='sheet' data-s='{i}'{'' if i == 0 else ' hidden'}>{note}{table}</section>")
    return _page(path, "Excel", _banner(banner) + sheet_bar(tabs, len(sheets) - len(shown)) + "".join(bodies),
                 script=TAB_SCRIPT)


def pdf_sheets_html(path: Path, sheets: list[dict], src, *, hidden: int = 0) -> str:
    """Excel がシートごとに作った PDF を、シートのタブで 1 枚ずつ(0.15.0)。`src(i)` はそのシートの PDF の場所。
    sheets: [{name, ok, note}]。PDF にできなかったシート(空など)はタブに印を付け、押すと理由を出す。"""
    tabs, bodies = [], []
    for i, sh in enumerate(sheets):
        tabs.append(sheet_tab(i, sh["name"], "" if sh.get("ok") else (sh.get("note") or "出せない"),
                              src=src(i) if sh.get("ok") else ""))
        if not sh.get("ok"):
            bodies.append(f"<section class='sheet' data-s='{i}' hidden><p class='note'>"
                          f"{escape(sh['name'])}: {escape(sh.get('note') or 'PDF にできませんでした')}</p></section>")
    frame = "<iframe id='pdf' title='シートの PDF'></iframe>"
    return _page(path, "Excel", sheet_bar(tabs, hidden) + "".join(bodies) + frame, script=TAB_SCRIPT, full=True)


def sheet_tab(i: int, name: str, sub: str, *, src: str = "") -> str:
    data_src = f" data-src='{escape(src, quote=True)}'" if src else ""
    small = f"<small>{escape(sub)}</small>" if sub else ""
    return (f"<button type='button' class='tab' role='tab' data-s='{i}'{data_src} "
            f"aria-selected='{'true' if i == 0 else 'false'}'>{escape(name)}{small}</button>")


def sheet_bar(tabs: list[str], hidden: int) -> str:
    more = f"<span class='note'>隠しシート {hidden} 枚は出していません</span>" if hidden else ""
    return f"<nav class='tabs' role='tablist' aria-label='シート'>{''.join(tabs)}{more}</nav>"


# シートのタブ: 押したシートだけを出す。#s2 のように覚える(開き直しても同じシート)
TAB_SCRIPT = r"""
(function () {
  var tabs = Array.prototype.slice.call(document.querySelectorAll('.tab'));
  var frame = document.getElementById('pdf');
  function pick(i) {
    tabs.forEach(function (t) { t.setAttribute('aria-selected', String(t.dataset.s === String(i))); });
    Array.prototype.forEach.call(document.querySelectorAll('section.sheet'), function (s) {
      s.hidden = s.dataset.s !== String(i);
    });
    var tab = tabs.filter(function (t) { return t.dataset.s === String(i); })[0];
    if (frame) {
      var src = tab && tab.dataset.src;
      frame.hidden = !src;
      if (src && frame.getAttribute('src') !== src) frame.setAttribute('src', src);
    }
    if (tab) tab.scrollIntoView({ block: 'nearest', inline: 'nearest' });
    try { history.replaceState(null, '', '#s' + i); } catch (e) { /* 覚えられないだけ */ }
  }
  tabs.forEach(function (t) { t.addEventListener('click', function () { pick(t.dataset.s); }); });
  document.addEventListener('keydown', function (e) {
    if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return;
    var cur = tabs.findIndex(function (t) { return t.getAttribute('aria-selected') === 'true'; });
    var next = Math.max(0, Math.min(tabs.length - 1, cur + (e.key === 'ArrowRight' ? 1 : -1)));
    if (next !== cur) pick(tabs[next].dataset.s);
  });
  var m = /^#s(\d+)$/.exec(location.hash);
  pick(m && tabs.some(function (t) { return t.dataset.s === m[1]; }) ? m[1] : (tabs[0] ? tabs[0].dataset.s : 0));
})();
"""


# ------------------------------------------------------------------
# Word
# ------------------------------------------------------------------
def _run_text(el) -> str:
    out = []
    for node in el.iter():
        if node.tag == f"{_W}t":
            out.append(node.text or "")
        elif node.tag == f"{_W}tab":
            out.append("\t")
        elif node.tag in (f"{_W}br", f"{_W}cr"):
            out.append("\n")
    return "".join(out)


def _para_html(p) -> str:
    style = p.find(f"{_W}pPr/{_W}pStyle")
    name = (style.get(f"{_W}val", "") if style is not None else "").lower()
    text = _run_text(p)
    if not text.strip():
        return "<p class='blank'></p>"
    body = escape(text).replace("\n", "<br>")
    m = re.match(r"(heading|見出し)\s*(\d)", name)
    if m or name == "title":
        level = min(int(m.group(2)) if m else 1, 4) + 1
        return f"<h{level}>{body}</h{level}>"
    bold = all((r.find(f"{_W}rPr/{_W}b") is not None) for r in p.findall(f"{_W}r") if _run_text(r).strip())
    return f"<p><b>{body}</b></p>" if bold and p.findall(f"{_W}r") else f"<p>{body}</p>"


def docx_html(path: Path, data: bytes | None = None, *, banner: str = "") -> str:
    with _open(path, data) as z:
        root = _xml(z, "word/document.xml")
    if root is None:
        raise PreviewError("Word の文書として読めません。")
    body = root.find(f"{_W}body")
    out = []
    count = 0
    for el in (body if body is not None else []):
        count += 1
        if count > MAX_PARAS:
            out.append(f"<p class='note'>長いので {MAX_PARAS:,} 段落までを出しています。全部は「現物を開く」で。</p>")
            break
        if el.tag == f"{_W}p":
            out.append(_para_html(el))
        elif el.tag == f"{_W}tbl":
            rows = []
            for tr in el.findall(f"{_W}tr"):
                cells = "".join("<td>" + "<br>".join(escape(_run_text(p)) for p in tc.findall(f"{_W}p")) + "</td>"
                                for tc in tr.findall(f"{_W}tc"))
                rows.append(f"<tr>{cells}</tr>")
            out.append(f"<div class='wrap'><table class='doc'>{''.join(rows)}</table></div>")
    has_pics = any(True for _ in root.iter(f"{_W}drawing"))
    note = "<p class='note'>図・写真は出していません(「現物を開く」で見られます)。</p>" if has_pics else ""
    return _page(path, "Word", _banner(banner) + note + "<article>" + "".join(out) + "</article>")


# ------------------------------------------------------------------
def _banner(text: str) -> str:
    return f"<p class='note'>{escape(text)}</p>" if text else ""


def _page(path: Path, kind: str, inner: str, *, script: str = "", full: bool = False) -> str:
    return f"""<!doctype html><html lang="ja"><meta charset="utf-8"><title>{escape(path.name)}</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
:root {{ --bg:#f6f8f9; --ink:#1b2730; --muted:#5d6d78; --line:#d5dde2; --head:#e9eff2; --accent:#0e6b84; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#11191f; --ink:#e2eaef; --muted:#9aabb5; --line:#2b3942;
  --head:#1a252c; --accent:#5cc0d6; color-scheme: dark; }} }}
body {{ margin:0; padding:16px 20px 40px; background:var(--bg); color:var(--ink);
  font:14px/1.6 "BIZ UDPGothic","Yu Gothic UI","Meiryo UI",system-ui,sans-serif; }}
header {{ display:flex; gap:10px; align-items:baseline; flex-wrap:wrap; margin-bottom:10px; }}
header b {{ font-size:16px; overflow-wrap:anywhere; }}
header small {{ color:var(--muted); }}
.kind {{ font-size:11px; padding:1px 8px; border:1px solid var(--accent); color:var(--accent); border-radius:99px; }}
nav {{ display:flex; gap:6px; flex-wrap:wrap; margin:0 0 12px; }}
nav a {{ color:var(--accent); text-decoration:none; padding:2px 10px; border:1px solid var(--line); border-radius:6px; }}
nav.tabs {{ flex-wrap:nowrap; overflow-x:auto; gap:4px; border-bottom:1px solid var(--line); margin:0 0 12px; padding-bottom:0;
  align-items:flex-end; }}
nav.tabs .note {{ flex:none; margin-left:8px; align-self:center; }}
.tab {{ flex:none; font:inherit; color:var(--muted); background:transparent; border:1px solid transparent; border-bottom:0;
  border-radius:6px 6px 0 0; padding:4px 12px; cursor:pointer; display:flex; gap:6px; align-items:baseline; }}
.tab small {{ font-size:11px; color:var(--muted); }}
.tab:hover {{ color:var(--ink); background:var(--head); }}
.tab[aria-selected="true"] {{ color:var(--ink); background:var(--bg); border-color:var(--line); font-weight:700;
  margin-bottom:-1px; box-shadow:inset 0 2px 0 var(--accent); }}
body.full {{ height:100vh; box-sizing:border-box; display:flex; flex-direction:column; padding-bottom:12px; }}
body.full iframe {{ flex:1; width:100%; border:1px solid var(--line); border-radius:6px; background:#fff; }}
h2 {{ font-size:15px; margin:18px 0 6px; }}
.wrap {{ overflow:auto; max-height:75vh; width:fit-content; max-width:100%; border:1px solid var(--line); border-radius:6px; }}
table {{ border-collapse:collapse; font-size:13px; }}
th, td {{ border:1px solid var(--line); padding:3px 8px; white-space:pre-wrap; vertical-align:top; }}
thead th, tbody th {{ background:var(--head); color:var(--muted); font-weight:600; position:sticky; }}
thead th {{ top:0; }} tbody th {{ left:0; text-align:right; }}
table.doc td {{ min-width:4em; }}
article {{ max-width:52em; }}
article p {{ margin:0 0 .6em; white-space:pre-wrap; }} article p.blank {{ margin:0 0 .6em; min-height:.6em; }}
.note {{ color:var(--muted); font-size:12px; }}
</style>
<body class="{'full' if full else ''}">
<header><span class="kind">{kind}</span><b>{escape(path.name)}</b><small>{escape(str(path.parent))}</small>
<small>{'プレビュー(Excel が作った PDF。シートのタブで切り替え)' if full else 'プレビュー(中身だけ。書式・図は出しません)'}</small></header>
{inner}
{f'<script>{script}</script>' if script else ''}
</body></html>"""
