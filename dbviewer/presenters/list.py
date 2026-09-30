"""一覧画面に出す値(VBA MF1 の一覧ページ)。判断はここで済ませ、画面は並べるだけ。"""
from __future__ import annotations

import sqlite3
from typing import Any, Optional

from .. import filters as flt, records
from ..settings_tables import SystemSetup

# 列の寄せ方(VBA `LvWidthFit`: 幅が狭ければ右、広ければ左、間は中央)
_ALIGN_RIGHT_MAX = 2
_ALIGN_LEFT_MIN = 8


def parse_filter_values(raw: dict[str, Any]) -> dict[int, str]:
    """`{"1": "値", ...}` → `{1: "値"}`。番号でないものは捨てる。"""
    out: dict[int, str] = {}
    for k, v in (raw or {}).items():
        try:
            out[int(k)] = "" if v is None else str(v)
        except (TypeError, ValueError):
            continue
    return out


def build(conn: sqlite3.Connection, setup: SystemSetup, *, values: dict[int, str],
          page_no: int = 1, per_page: int = records.DEFAULT_PAGE_ROWS,
          page_flag: bool = False, event_no: int = 0, reset: bool = False) -> dict[str, Any]:
    states = flt.states_from_rows(setup.filters, values)
    page = records.list_view(conn, setup, states, page_no=page_no, per_page=per_page,
                             page_flag=page_flag, event_no=event_no, reset=reset)
    widths = flt.width_array([f.width for f in states if f.item != ""], 100.0)
    active = [f for f in states if f.item != ""]
    filter_views = []
    for f, w in zip(active, widths):
        cands = page.candidates.get(f.no)
        filter_views.append({
            "no": f.no, "item": f.item, "type": f.ftype, "kind": f.kind,
            "label": f.label, "tip": f.tip,
            "value": "" if reset else f.value,
            # None = 作り直さない(画面はいまの候補を残す)
            "candidates": cands,
            "width_pct": round(w, 2),
        })
    return {
        "columns": page.columns,
        "align": column_align(page.columns, page.page_rows),
        "rows": page.page_rows,
        "page": {"no": page.page_no, "count": page.page_count, "rows": page.row_count,
                 "per_page": page.per_page, "label": f"{page.page_no} / {page.page_count}"},
        "filters": filter_views,
        "sql": page.vba_sql,
        "where": page.where_vba,
        "key_item_col": page.fj.get("管理番号", 0),
        "mode": "選択",
    }


def column_align(columns: list[str], rows: list[list[str]]) -> list[str]:
    out = []
    for j, _ in enumerate(columns):
        longest = max((len(r[j]) for r in rows if j < len(r)), default=0)
        if longest <= _ALIGN_RIGHT_MAX:
            out.append("right")
        elif longest >= _ALIGN_LEFT_MIN:
            out.append("left")
        else:
            out.append("center")
    return out


def summary_view(conn: sqlite3.Connection, setup: SystemSetup, item: str) -> dict[str, Any]:
    view = records.summary(conn, setup, item)
    return {"columns": view.columns, "rows": view.rows, "sql": view.vba_sql,
            "align": column_align(view.columns, view.rows), "mode": "集計", "item": item,
            "page": {"no": 1, "count": 1, "rows": len(view.rows), "per_page": len(view.rows),
                     "label": "1 / 1"}}
