"""表を画面の「グリッド」で見せるための下ごしらえ(一覧・閲覧で共通)

VBA の一覧は ListView の限界(1 ページ数十行・フィルタは決めた列だけ・並べ替えは
列クリックの 1 段だけ)に合わせた作りだった。Web 版は**全行をまとめて画面へ渡し**、
絞り込み・並べ替え・列の表示や並びを画面の中で即座に行う。

ここで決めること:
- 列の種類(数値 / 日付 / 文字)。並べ替えと範囲の絞り込みに使う
- 利用者ごとの列の見せ方(表示する列・並び・幅)の保存。**この PC の設定**
  (`user_settings`)に、システムと表ごとに持つ
"""
from __future__ import annotations

import re
from typing import Any, Iterable, Optional

from . import user_settings

KIND_NUM = "num"
KIND_DATE = "date"
KIND_TEXT = "text"

_NUM = re.compile(r"^-?(?:\d+|\d+\.\d+|\.\d+)$")
_DATE = re.compile(r"^\d{4}[/-]\d{1,2}[/-]\d{1,2}(?:[ T]\d{1,2}:\d{2}(?::\d{2})?)?$")
# 種類を決めるのに見る行数(全部見なくても分かる。多い表で遅くしない)
SAMPLE_ROWS = 2000


def _is_num(text: str) -> bool:
    s = text.strip().replace(",", "")
    # 先頭 0 の桁("007")は番号なので文字のまま
    if len(s) > 1 and s[0] == "0" and s[1] != ".":
        return False
    return bool(_NUM.match(s))


def column_kind(values: Iterable[str]) -> str:
    """空欄を除いて、全部が数値なら num、全部が日付なら date、ほかは text。"""
    seen = 0
    num = date = True
    for v in values:
        s = (v or "").strip()
        if not s:
            continue
        seen += 1
        num = num and _is_num(s)
        date = date and bool(_DATE.match(s))
        if not num and not date:
            return KIND_TEXT
    if not seen:
        return KIND_TEXT
    return KIND_NUM if num else KIND_DATE if date else KIND_TEXT


def columns_meta(columns: list[str], rows: list[list[str]]) -> list[dict[str, Any]]:
    sample = rows[:SAMPLE_ROWS]
    return [{"name": c, "kind": column_kind(r[j] if j < len(r) else "" for r in sample)}
            for j, c in enumerate(columns)]


# ------------------------------------------------------------------
# 列の見せ方(表示する列・並び・幅)
# ------------------------------------------------------------------
def view_key(*parts: object) -> str:
    return "grid_view::" + "::".join(str(p) for p in parts)


def _clean(raw: Any, columns: list[str]) -> dict[str, Any]:
    """保存してあった見せ方を、いまの列に合わせる。

    - 消えた列は落とす。新しく増えた列は末尾に足して見せる(気づけるように)
    - 幅は 40〜800px の整数に丸める
    """
    raw = raw if isinstance(raw, dict) else {}
    have = set(columns)
    order = [c for c in dict.fromkeys(str(x) for x in raw.get("order") or []) if c in have]
    order += [c for c in columns if c not in order]
    hidden = [c for c in dict.fromkeys(str(x) for x in raw.get("hidden") or []) if c in have]
    if len(hidden) >= len(columns):          # 全部隠すと何も見えない
        hidden = []
    widths: dict[str, int] = {}
    for k, v in (raw.get("widths") or {}).items():
        if str(k) in have:
            try:
                widths[str(k)] = max(40, min(800, int(float(v))))
            except (TypeError, ValueError):
                continue
    return {"order": order, "hidden": hidden, "widths": widths}


def load_view(key: str, columns: list[str]) -> dict[str, Any]:
    return _clean(user_settings.get(key, None), columns)


def save_view(key: str, columns: list[str], raw: Any) -> dict[str, Any]:
    view = _clean(raw, columns)
    user_settings.save(key, view)
    return view


def reset_view(key: str, columns: list[str]) -> dict[str, Any]:
    user_settings.delete(key)
    return _clean(None, columns)


def pick(columns: list[str], rows: list[list[str]], *, keys: Optional[list[str]] = None,
         key_index: int = -1, names: Optional[list[str]] = None,
         keep_order: bool = False) -> tuple[list[str], list[list[str]]]:
    """画面で絞り込んだ行を取り出す。

    `keys` は画面に並んでいる行のキー(管理番号)を上から順に。無ければ全行。
    `keep_order` なら行の順は元(`rows`)のまま、`keys` は「どの行か」だけに使う。
    `names` は列名(この順)。無ければ全列。
    """
    if keys is not None and key_index >= 0 and keep_order:
        wanted = {str(k) for k in keys}
        rows = [r for r in rows if str(r[key_index]) in wanted]
    elif keys is not None and key_index >= 0:
        by_key: dict[str, list[list[str]]] = {}
        for r in rows:
            by_key.setdefault(str(r[key_index]), []).append(r)
        out: list[list[str]] = []
        used: set[str] = set()
        for k in keys:
            k = str(k)
            if k in by_key and k not in used:
                used.add(k)
                out.extend(by_key[k])
        rows = out
    if names:
        idx = [columns.index(n) for n in names if n in columns]
        columns = [columns[i] for i in idx]
        rows = [[r[i] if i < len(r) else "" for i in idx] for r in rows]
    return columns, rows
