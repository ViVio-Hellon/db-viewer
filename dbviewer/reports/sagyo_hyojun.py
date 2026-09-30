"""帳票 REPORT(作業標準管理システムの一覧帳票。VBA 【B1】Report `REPORT` の移植)

VBA の手順(そのまま):
    1. 一覧 SQL の結果(1 行目が列名)から 13 列を A〜M に写す
       VBA は列番号 sj = (2,3,4,5,6,7,8,9,12,20,21,26,27) で選んでいたが、それは
       27 列以上あった旧レイアウトの番号(不明点 #18)。Web 版は出力例の見出しと
       同じ **列名** で選ぶ(判断事項で確定)
    2. 原紙「作業標準」のシートを複製し、シート名を yyyy-mm-dd(重複なら _n)にする
    3. 下から上へ見て、作業NO の "-" より前が変わるところに空行を入れる
    4. Total 行(最終行+2)に Ⅰ〜Ⅳ(3〜6 列目)の合計と GrandTotal(その合計)
    5. 3〜6 列目を上から見て、空欄の行に「小計」とその列の小計を書く。
       各データ行は A:M を細罫線、小計の前の行は下を二重太線、小計行は下を細罫線
    6. 全セル メイリオ 10、列幅 10.25 → 自動調整

【出力例との照合】
`tests/fixtures/realdata/expected/帳票出力例_2024-03-28.tsv` が VBA の実出力。
Ⅰ〜Ⅳ・小計・Total・GrandTotal・空行の位置が一致することを試験で確かめる。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..vba_compat import val

# 出力例の見出し = 出す列(この順)
COLUMNS = ("作業名", "作業NO", "Ⅰ", "Ⅱ", "Ⅲ", "Ⅳ", "RA", "PC化", "危険度分類ランク",
           "廃止", "文書番号", "更新者", "更新日")
SUM_COLS = (3, 4, 5, 6)          # 1 始まり(Ⅰ〜Ⅳ)
WIDTH_COLS = 13                  # A:M
FONT_NAME = "メイリオ"
FONT_SIZE = 10
PAPER = "A4"
ORIENTATION = "portrait"


@dataclass
class Cell:
    value: str = ""
    number: bool = False          # Excel が数値として持つ(VBA は文字列を入れても数値になる)


@dataclass
class Row:
    cells: list[Cell]
    kind: str = "data"            # header / data / subtotal / total / blank
    border_all: bool = False      # A:M 細罫線
    bottom_double: bool = False   # 下を二重太線
    bottom_thin: bool = False     # 下を細罫線


@dataclass
class Grid:
    rows: list[Row] = field(default_factory=list)
    sheet_name: str = ""
    grand_total: float = 0.0

    @property
    def width(self) -> int:
        return max((len(r.cells) for r in self.rows), default=0)

    def text_rows(self) -> list[list[str]]:
        w = max(WIDTH_COLS, self.width)
        return [[c.value for c in r.cells] + [""] * (w - len(r.cells)) for r in self.rows]


def _num_text(x: float) -> str:
    return str(int(x)) if float(x).is_integer() else str(x)


def prefix(work_no: str) -> str:
    """VBA `Split(WS.Range("B" & i), "-")(0)`。"""
    return str(work_no).split("-")[0]


def select_columns(table: list[list[str]]) -> list[list[str]]:
    """一覧の結果(1 行目が列名)から 13 列を名前で選ぶ。無い列は空。"""
    if not table:
        return [list(COLUMNS)]
    header = table[0]
    idx = [header.index(c) if c in header else -1 for c in COLUMNS]
    out = [list(COLUMNS)]
    for r in table[1:]:
        out.append([r[i] if i >= 0 and i < len(r) else "" for i in idx])
    return out


def build(table: list[list[str]], *, sheet_name: str = "") -> Grid:
    """`table` は一覧 SQL の結果(1 行目が列名)。"""
    sheet = select_columns(table)
    # 1〜2. 見出し + データ
    rows: list[Row] = [Row([Cell(v) for v in sheet[0]], kind="header")]
    for r in sheet[1:]:
        cells = [Cell(v, number=(j + 1 in SUM_COLS and _is_number(v))) for j, v in enumerate(r)]
        rows.append(Row(cells, kind="data"))

    # 3. 作業NO の頭が変わったら空行(下から上へ、行 3 以上)
    i = len(rows)                               # VBA の MaxCelR(1 始まり)
    while i >= 3:
        if prefix(rows[i - 1].cells[1].value) != prefix(rows[i - 2].cells[1].value):
            rows.insert(i - 1, Row([Cell("") for _ in range(WIDTH_COLS)], kind="blank"))
        i -= 1
    last_row = len(rows)                        # VBA の LastRow = UR(WS, 1)
    # 行 LastRow+1(空)と LastRow+2(Total)
    rows.append(Row([Cell("") for _ in range(WIDTH_COLS)], kind="blank"))
    total = Row([Cell("") for _ in range(WIDTH_COLS + 1)], kind="total")
    rows.append(total)

    # 4. Total(1〜LastRow の合計。空行にはまだ何も無い)
    total.cells[0] = Cell("Total")
    grand = 0.0
    for a in SUM_COLS:
        s = sum(val(rows[k].cells[a - 1].value) for k in range(0, last_row) if rows[k].kind == "data")
        total.cells[a - 1] = Cell(_num_text(s), number=True)
        grand += s
    total.cells[12] = Cell("GrandTotal")
    total.cells[13] = Cell(_num_text(grand), number=True)

    # 5. 小計(1〜LastRow+1 を上から。空欄の行に書く)
    for a in SUM_COLS:
        sub = 0.0
        for k in range(0, last_row + 1):
            row = rows[k]
            if row.cells[a - 1].value != "":
                sub += val(row.cells[a - 1].value)
                row.border_all = True
            else:
                row.cells[0] = Cell("小計")
                row.cells[a - 1] = Cell(_num_text(sub), number=True)
                row.kind = "subtotal"
                if k >= 1:
                    rows[k - 1].bottom_double = True
                row.bottom_thin = True
                sub = 0.0
    return Grid(rows=rows, sheet_name=sheet_name, grand_total=grand)


def _is_number(text: str) -> bool:
    try:
        float(str(text).strip())
        return str(text).strip() != ""
    except ValueError:
        return False
