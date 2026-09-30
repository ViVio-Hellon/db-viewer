"""ほかのシステムの帳票(VBA SUITOUREPORT / DAITYOREPORT / GIKAKUREPORT)

判断事項 D4: プラグインとして移植するが、実データが無いので **未検証**。
VBA のセル配置をそのまま格子(Grid)に写している。書式(塗り・数値書式・結合)は
HTML/xlsx では最小限。
"""
from __future__ import annotations

from ..vba_compat import cdate, format_date, val
from .sagyo_hyojun import Cell, Grid, Row


def _grid_from_matrix(cells: dict[tuple[int, int], Cell], *, borders: set[int] = frozenset()) -> Grid:
    max_r = max((r for r, _ in cells), default=1)
    max_c = max((c for _, c in cells), default=1)
    rows = []
    for r in range(1, max_r + 1):
        row = Row([cells.get((r, c), Cell("")) for c in range(1, max_c + 1)], kind="data")
        row.border_all = r in borders
        rows.append(row)
    return Grid(rows=rows)


def _fj(header: list[str], name: str) -> int:
    return header.index(name) + 1 if name in header else 0


def mode_value(values: list[str]) -> str:
    """VBA `ModeValue`(最頻値。同数なら並べ替え後の先のもの)。"""
    if not values:
        return ""
    vals = sorted(values)
    best, best_n = vals[0], 0
    cnt = 1
    for i in range(len(vals) - 1):
        if vals[i] == vals[i + 1]:
            cnt += 1
        else:
            if cnt > best_n:
                best_n, best = cnt, vals[i]
            cnt = 1
    if cnt > best_n:
        best = vals[-1]
    return best


def suitou(table: list[list[str]], args: list[str]) -> Grid:
    """仮払出納帳(SUITOUREPORT)。列は 日付/使用内容/差/残高/担当者/整理番号、金種増減。"""
    header = table[0] if table else []
    fj = [_fj(header, n) for n in ("日付", "使用内容", "差", "残高", "担当者", "整理番号")]
    sj = [0, 1, 4, 10, 13, 17, 19]
    cells: dict[tuple[int, int], Cell] = {}
    months, yms = [], []
    n = 0
    data = table[1:]
    for i, r in enumerate(data, start=1):
        n += 1
        for j in range(1, 7):
            v = r[fj[j - 1] - 1] if fj[j - 1] else ""
            cells[(i + 6, sj[j])] = Cell(v, number=j in (3, 4) and _num(v))
        d = cdate(r[fj[0] - 1] if fj[0] else "")
        if d is not None:
            months.append(str(d.month))
            yms.append(format_date(d, "YYYY")[2:] + f"{d.month:02d}01")
    cells[(3, 1)] = Cell(mode_value(months))
    cells[(4, 10)] = Cell(mode_value(yms))
    k = 6 + n + 1
    cells[(k, sj[1])] = Cell("合計")
    cells[(k, sj[3])] = Cell(_txt(sum(val(r[fj[2] - 1]) for r in data if fj[2])), number=True)
    cells[(k, sj[4])] = Cell(data[-1][fj[3] - 1] if data and fj[3] else "", number=True)
    k += 1
    coins = [10000, 5000, 1000, 500, 100, 50, 10, 5, 1]
    cells[(1 + k, 1)] = Cell("金種")
    for i, c in enumerate(coins, start=1):
        cells[(1 + i + k, 1)] = Cell(str(c), number=True)
    cells[(11 + k, 1)] = Cell("合計金額")
    kj = _fj(header, "金種増減")
    sums = [0.0] * 10
    for r in table[1:]:
        s = r[kj - 1] if kj else ""
        if "," in s:
            parts = s.split(",")
            for j in range(1, 10):
                sums[j] += val(parts[j]) if j < len(parts) else 0
    total = 0.0
    for i in range(1, 12):
        if i not in (1, 11):
            cells[(i + k, 4)] = Cell(_txt(sums[i - 1]), number=True)
    for i in range(1, 10):
        t = sums[i] * coins[i - 1]
        total += t
        cells[(k + i + 1, 10)] = Cell(_txt(t), number=True)
    cells[(1 + k, 4)] = Cell("枚数")
    cells[(11 + k, 4)] = Cell("-")
    cells[(1 + k, 10)] = Cell("金額")
    cells[(11 + k, 10)] = Cell(_txt(total), number=True)
    return _grid_from_matrix(cells, borders=set(range(6, 6 + len(data) + 2)))


def daityo(table: list[list[str]], args: list[str]) -> Grid:
    """ISO 文書台帳(DAITYOREPORT)。R1〜R30 まで。"""
    rev_index = int(val(args[4])) if len(args) > 4 else 10
    header = table[0] if table else []
    names = ["文書番号", "最新版", "規定名称", "使用フラグ", "制定日"] + [f"R{i}" for i in range(1, 31)]
    fj = [_fj(header, n) for n in names]
    cells: dict[tuple[int, int], Cell] = {}
    for j, name in enumerate(names, start=1):
        cells[(2, j)] = Cell(name)
    for i, r in enumerate(table[1:], start=1):
        for j in range(1, 36):
            cells[(i + 2, j)] = Cell(r[fj[j - 1] - 1] if fj[j - 1] and fj[j - 1] - 1 < len(r) else "")
    g = _grid_from_matrix(cells)
    # 印刷範囲は 5 + RevCntIndex 列まで
    width = 5 + rev_index
    for row in g.rows:
        del row.cells[width:]
    return g


def gikaku(table: list[list[str]], args: list[str]) -> Grid:
    """技革保管リスト(GIKAKUREPORT)。VBA は中身が空(列番号を引くだけ)。"""
    return Grid(rows=[])


def _num(text: str) -> bool:
    try:
        float(str(text).replace(",", ""))
        return str(text).strip() != ""
    except ValueError:
        return False


def _txt(x: float) -> str:
    return str(int(x)) if float(x).is_integer() else str(x)
