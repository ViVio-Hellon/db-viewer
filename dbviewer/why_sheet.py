"""なぜなぜシート(0.11.0) ── エラーの記録番号ごとに、人が書くなぜ 1〜5・対策・状態を残す

ViVio-Hellon/vba-daily-report-python-migration(v4.1.0「エラーを後から追う」)にそろえた:

    記録から言えること … 起きたこと・直接の原因・そのときの状態・それまでの経過(自動。log_view の下書き)
    人が書くこと       … なぜ 1〜5・対策・状態(未着手 / 対策中 / 対策済)・書いた人

【どこに残すか】ログの置き場所に `なぜなぜ_<PC名>.jsonl`。1 行 = 1 回の保存。同じ記録番号は**いちばん新しい
保存**が効く(書き直しても前の行は消さない = 誰がいつ書き換えたかも残る)。PC ごとにファイルを分けるのは、
共有フォルダで同じファイルを取り合わないため。読むときはどの PC のぶんも読む。

【CSV】いまの絞り込みのまま、ログの置き場所の `書き出し\\エラー一覧_日時.csv` へ(Shift_JIS。Excel で開ける)。
ダウンロードはしない。
"""
from __future__ import annotations

import csv
import json
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional

from . import config, trace

PREFIX = f"{config.TOOL_LOG_NAME}_なぜなぜ_"     # 0.33.0 からツール名で始める
LEGACY_PREFIX = "なぜなぜ_"                     # 0.32.0 まで(この PC の既定の場所にあるものだけ読む)
SUFFIX = ".jsonl"
EXPORT_DIR = "書き出し"
WHY_COUNT = 5
TEXT_MAX = 400
STATUS_OPEN, STATUS_DOING, STATUS_DONE = "open", "doing", "done"
STATUS_LABELS = {STATUS_OPEN: "未着手", STATUS_DOING: "対策中", STATUS_DONE: "対策済"}
# 見出しの「エラー N 件」で数える日数(探しに来た人が見たいのは最近のもの)
OPEN_DAYS = 2

_lock = threading.Lock()


def file_for(pc: Optional[str] = None, folder: Optional[Path] = None) -> Path:
    return (folder or config.LOG_DIR) / f"{PREFIX}{pc or trace.pc_name()}{SUFFIX}"


def _files() -> list[Path]:
    out = []
    for folder in trace.read_dirs():
        try:
            out += [p for p in folder.iterdir()
                    if p.name.startswith((PREFIX, LEGACY_PREFIX)) and p.name.endswith(SUFFIX)]
        except OSError:
            continue
    return out


def _clip(v: Any) -> str:
    return str(v or "").strip()[:TEXT_MAX]


def save(rid: str, body: dict[str, Any], *, by: str = "") -> dict[str, Any]:
    """なぜなぜを 1 回保存する。戻り値は残した中身。"""
    whys = body.get("whys") if isinstance(body.get("whys"), list) else []
    status = body.get("status") if body.get("status") in STATUS_LABELS else STATUS_OPEN
    rec = {"t": datetime.now().isoformat(timespec="seconds"), "rid": str(rid)[:16], "status": status,
           "whys": [_clip(w) for w in (whys + [""] * WHY_COUNT)[:WHY_COUNT]],
           "measure": _clip(body.get("measure")), "by": _clip(body.get("by")) or by, "pc": trace.pc_name()}
    line = json.dumps(rec, ensure_ascii=False)
    path = file_for()
    with _lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8", newline="\n") as f:
            f.write(line + "\n")
    trace.event("why.save", f"なぜなぜを残した({STATUS_LABELS[status]})", data={"rid": rec["rid"], "by": rec["by"]})
    return rec


def all_sheets() -> dict[str, dict[str, Any]]:
    """記録番号 → いちばん新しいなぜなぜ。"""
    out: dict[str, dict[str, Any]] = {}
    for p in _files():
        try:
            with open(p, encoding="utf-8", errors="replace") as f:
                for line in f:
                    try:
                        rec = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(rec, dict) and rec.get("rid"):
                        old = out.get(rec["rid"])
                        if old is None or str(rec.get("t", "")) >= str(old.get("t", "")):
                            out[rec["rid"]] = rec
        except OSError:
            continue
    return out


def get(rid: str) -> Optional[dict[str, Any]]:
    return all_sheets().get(rid)


def is_error(rec: dict[str, Any]) -> bool:
    """なぜなぜの対象になる「エラー」(思わぬエラー・画面のエラー・記録に残った ERROR)。"""
    return rec.get("lv") == "ERROR"


def open_errors(days: int = OPEN_DAYS) -> int:
    """この `days` 日の、まだ「対策済」にしていないエラーの数(レールとタブの見出し)。"""
    sheets = all_sheets()
    n = 0
    seen = set()
    for r in trace.read(days, only_problems=True, limit=5000):
        rid = r.get("rid") or ""
        if not is_error(r) or (rid and rid in seen):
            continue
        seen.add(rid)
        if (sheets.get(rid) or {}).get("status") != STATUS_DONE:
            n += 1
    return n


_open_cache: dict[str, Any] = {"t": 0.0, "n": 0}


def open_errors_cached(seconds: float = 30.0) -> int:
    """画面を開くたびに数えないよう、30 秒は同じ数を返す。"""
    import time
    now = time.monotonic()
    if now - _open_cache["t"] > seconds:
        try:
            _open_cache["n"] = open_errors()
        except Exception:                               # noqa: BLE001 - 数えられなくても画面は出す
            _open_cache["n"] = 0
        _open_cache["t"] = now
    return int(_open_cache["n"])


def forget_cache() -> None:
    _open_cache["t"] = 0.0


# ------------------------------------------------------------------
# CSV に書き出す(ダウンロードはしない。ログの置き場所の 書き出し へ)
# ------------------------------------------------------------------
CSV_HEADER = ["時刻", "記録番号", "重さ", "出来事", "画面", "利用者", "PC", "何と出た", "状態コード", "例外",
              "例外の場所", *[f"なぜ{i}" for i in range(1, WHY_COUNT + 1)], "対策", "対策の状況", "書いた人"]


def _where(rec: dict[str, Any]) -> str:
    """例外の場所(このツールのコードのいちばん深いところ)。"""
    tb = ((rec.get("exc") or {}).get("tb")) or []
    for line in reversed(tb):
        m = re.search(r'File "([^"]+)", line (\d+), in (\S+)', str(line))
        if m and ("dbviewer" in m.group(1) or "app" in m.group(1)) and "site-packages" not in m.group(1):
            name = re.split(r"[\\/](?=dbviewer|app)[\\/]?", m.group(1))[-1]
            return f"{name}:{m.group(2)}({m.group(3)})"
    return ""


def csv_rows(rows: Iterable[dict[str, Any]]) -> list[list[str]]:
    """`log_view.row` の行(raw 付き)→ CSV の行。"""
    sheets = all_sheets()
    out = []
    for r in rows:
        raw = r.get("raw") or {}
        exc = raw.get("exc") or {}
        sh = sheets.get(r.get("rid") or "") or {}
        whys = (sh.get("whys") or []) + [""] * WHY_COUNT
        out.append([r.get("t", ""), r.get("rid", ""), r.get("lv", ""), r.get("label", ""), r.get("screen", ""),
                    r.get("user", ""), r.get("pc", ""), r.get("msg", ""), str(r.get("st") or ""),
                    f"{exc.get('type', '')}: {exc.get('msg', '')}" if exc else "", _where(raw),
                    *whys[:WHY_COUNT], sh.get("measure", ""),
                    STATUS_LABELS.get(sh.get("status", ""), "") if sh else "", sh.get("by", "")])
    return out


def export_csv(rows: list[dict[str, Any]], *, now: Optional[datetime] = None) -> Path:
    folder = config.LOG_DIR / EXPORT_DIR
    folder.mkdir(parents=True, exist_ok=True)
    stamp = (now or datetime.now()).strftime("%Y%m%d_%H%M%S")
    path = folder / f"エラー一覧_{stamp}.csv"
    n = 2
    while path.exists():
        path = folder / f"エラー一覧_{stamp}_{n}.csv"
        n += 1
    with open(path, "w", encoding="cp932", errors="replace", newline="") as f:
        w = csv.writer(f, lineterminator="\r\n")
        w.writerow(CSV_HEADER)
        w.writerows(csv_rows(rows))
    return path

