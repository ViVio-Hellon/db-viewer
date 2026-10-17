"""設定「ログ」に出す値(記録 = `dbviewer/trace.py` の 1 行 1 出来事)

画面は並べるだけ。出来事の呼び名・画面の名前・時刻の見せ方はここで決める。

なぜなぜ分析の下書き(`why_draft`)もここで作る ── 記録番号の前後から
「いつ・誰が・どこで・何をして・何が返ったか・その前に何があったか」を
事実として並べ、「なぜ」の欄は空けておく(考えるのは人)。
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Optional

from .. import config, trace, user_settings, why_sheet

# 出来事の種類 → 画面の呼び名
EV_LABEL = {
    "screen": "画面を開いた",
    "request": "操作",
    "error.server": "思わぬエラー(サーバ)",
    "error.client": "画面の中のエラー",
    "login.ok": "ログイン",
    "login.ng": "ログインできなかった",
    "cell.save": "一覧で直した",
    "record.save": "レコードを保存",
    "master.save": "マスタを保存",
    "master.delete": "マスタを削除",
    "master.add_column": "列を追加",
    "settings.save": "設定を保存",
    "settings.reset_mode": "起動モードを初期化",
    "db.open": "接続先を開いた",
    "accdb.convert": "Access を変換",
    "table.bring": "表を持ってきた",
    "table.refresh": "表を入れ替えた",
    "export": "ファイルに保存",
    "print": "印刷",
    "report": "帳票",
    "job.done": "長い処理が終わった",
    "job.failed": "長い処理が失敗",
    "log.moved": "ログの出力先を変えた",
    "log.warning": "警告(コードの中)",
    "log.error": "エラー(コードの中)",
    "why.save": "なぜなぜを残した",
    "log.export": "エラー一覧を書き出した",
    "list.row_save": "一覧で直した",
    "list.row_add": "一覧で行を足した",
    "list.row_delete": "一覧で行を消した",
    "list.add_column": "列を追加",
    "browse.add_column": "列を追加",
    "easy.columns": "列の設定を保存",
    "easy.start": "ログインを使い始めた",
    "easy.retire_vba": "VBA 版の設定を消した",
    "file.preview": "ファイルをプレビュー",
    "file.open": "ファイルを現物で開いた",
    "app.start": "起動",
    "app.stop": "終了",
}

# URL の頭 → 画面の名前(長いものから当てる)
_SCREENS = (
    ("/api/list/cell", "一覧(その場で直す)"),
    ("/api/list", "一覧"),
    ("/list", "一覧"),
    ("/api/record", "レコード"),
    ("/record", "レコード"),
    ("/api/master", "マスタ管理"),
    ("/master", "マスタ管理"),
    ("/api/browse", "テーブル閲覧"),
    ("/api/export", "出力"),
    ("/browse", "テーブル閲覧"),
    ("/api/report", "帳票"),
    ("/api/print", "印刷"),
    ("/report", "帳票"),
    ("/api/settings", "設定"),
    ("/api/fs", "設定(参照)"),
    ("/api/log", "設定(記録)"),
    ("/settings", "設定"),
    ("/api/login", "ログイン"),
    ("/api/logout", "ログアウト"),
    ("/login", "ログイン"),
    ("/api/shutdown", "終了"),
    ("/pending", "未処理"),
)

DAYS_CHOICES = (1, 7, 30, 90)
DEFAULT_DAYS = 7
MAX_KEEP_DAYS = 3650


def screen_of(path: str) -> str:
    p = (path or "").split("?", 1)[0]
    if p in ("", "/"):
        return "はじめ" if p == "/" else ""
    for head, name in _SCREENS:
        if p == head or p.startswith(head + "/") or p.startswith(head + "?"):
            return name
    return p


def _time(text: str) -> str:
    try:
        t = datetime.fromisoformat(str(text))
    except ValueError:
        return str(text or "")
    return t.strftime("%m/%d %H:%M:%S")


def row(rec: dict[str, Any]) -> dict[str, Any]:
    """一覧の 1 行。元の記録(`raw`)もそのまま付ける(中身を見る・コピーする)。"""
    ev = str(rec.get("ev", ""))
    label = EV_LABEL.get(ev, ev)
    st = rec.get("st")
    if ev == "request" and isinstance(st, int):
        label = "断られた" if 400 <= st < 500 else "エラー" if st >= 500 else "操作"
    msg = str(rec.get("msg", "") or "")
    data = rec.get("data")
    if not msg and isinstance(data, dict) and data.get("reason"):
        msg = f"理由: {data.get('reason')}"
    if not msg and ev == "request":
        # 書く操作が通った: 何を呼んだか(と、かかった時間)
        ms = rec.get("ms")
        msg = f"{rec.get('method', '')} {rec.get('path', '')}".strip() + (f"({ms} ms)" if ms is not None else "")
    return {
        "t": str(rec.get("t", "")),
        "time": _time(rec.get("t", "")),
        "lv": rec.get("lv", "INFO"),
        "ev": ev,
        "label": label,
        "rid": rec.get("rid", "") or "",
        "user": rec.get("user", "") or "",
        "pc": rec.get("pc", "") or "",
        "screen": screen_of(str(rec.get("path", "") or "")),
        "path": rec.get("path", "") or "",
        "method": rec.get("method", "") or "",
        "st": st,
        "ms": rec.get("ms"),
        "msg": msg,
        "raw": rec,
    }


def keep_days() -> int:
    try:
        return max(0, int(user_settings.get(config.KEY_LOG_KEEP_DAYS, config.DEFAULT_LOG_KEEP_DAYS)))
    except (TypeError, ValueError):
        return config.DEFAULT_LOG_KEEP_DAYS


# 見るもの(参照: vba-daily-report の「ログ」): エラーだけ / エラー・断り・警告 / 操作も全部
ONLY_CHOICES = ("errors", "problems", "all")


def _pick(days: int, only: str, q: str, pc: str, limit: int) -> list[dict[str, Any]]:
    only = only if only in ONLY_CHOICES else "problems"
    recs = trace.read(days, only_problems=(only != "all"), q=q, limit=max(limit, 2000) if pc or only == "errors" else limit)
    if only == "errors":
        recs = [r for r in recs if why_sheet.is_error(r)]
    if pc:
        recs = [r for r in recs if r.get("pc") == pc]
    return recs[:limit]


def events(days: int = DEFAULT_DAYS, *, only: str = "problems", q: str = "", limit: int = 300,
           pc: str = "") -> dict[str, Any]:
    only = only if only in ONLY_CHOICES else "problems"
    found = trace.files(days)
    rows = [row(r) for r in _pick(days, only, q, pc, limit)]
    sheets = why_sheet.all_sheets()
    for r in rows:
        sh = sheets.get(r["rid"]) if r["rid"] else None
        r["sheet"] = why_sheet.STATUS_LABELS.get(sh.get("status", ""), "") if sh else ""
        r["sheet_status"] = sh.get("status", "") if sh else ""
        r["is_error"] = r["lv"] == "ERROR"
    return {
        "dir": str(config.LOG_DIR),
        "days": days,
        "only": only,
        "q": q,
        "pc": pc,
        "files": len(found),
        "pcs": sorted({trace.pc_of(p) for p in found}),
        "rows": rows,
        "limit": limit,
        "open_errors": why_sheet.open_errors(),
        "open_days": why_sheet.OPEN_DAYS,
    }


def export(days: int, *, only: str, q: str, pc: str) -> dict[str, Any]:
    """いまの絞り込みのまま CSV に書く(ログの出力先の 書き出し へ)。"""
    rows = [row(r) for r in _pick(days, only, q, pc, 20000)]
    path = why_sheet.export_csv(rows)
    return {"path": str(path), "rows": len(rows)}


def chain(rid: str) -> dict[str, Any]:
    got = trace.chain(rid)
    if not got.get("found"):
        return {"rid": rid, "found": False, "rows": [],
                "message": f"記録番号 {rid} は見つかりません(置き場所: {config.LOG_DIR}、"
                           f"直近 30 日)。置き場所を変える前の記録は、前の置き場所にあります。"}
    rows = [row(r) for r in got["events"]]
    for r in rows:
        r["center"] = r["rid"] == rid
    return {"rid": rid, "found": True, "rows": rows, "draft": why_draft(rid, rows),
            "sheet": why_sheet.get(rid), "statuses": [[k, v] for k, v in why_sheet.STATUS_LABELS.items()],
            "why_count": why_sheet.WHY_COUNT}


def _line(r: dict[str, Any]) -> str:
    bits = [r["time"], r["label"]]
    if r["screen"]:
        bits.append(f"[{r['screen']}]")
    if r["st"] not in (None, "", 200):
        bits.append(f"状態 {r['st']}")
    if r["msg"]:
        bits.append(r["msg"])
    return " ".join(str(b) for b in bits)


def why_draft(rid: str, rows: list[dict[str, Any]]) -> str:
    """なぜなぜ分析の下書き(事実だけ。なぜの欄は空ける)。クリップボードに写して使う。"""
    center: Optional[dict[str, Any]] = next((r for r in rows if r.get("center")), None)
    if center is None:
        return ""
    raw = center["raw"]
    # 同じ記録番号の行がいくつかある(業務の記録 + 要求の結果)。結果の状態はそちらから
    done = next((r["raw"] for r in rows if r.get("center") and r["ev"] == "request"), raw)
    before = [r for r in rows if r["t"] < center["t"]][-8:]
    after = [r for r in rows if r is not center and r["t"] >= center["t"]][:5]
    exc = raw.get("exc") or {}
    data = raw.get("data")
    lines = [
        f"■ なぜなぜ分析(記録番号 {rid})",
        "",
        "【起きたこと(事実)】",
        f"  いつ   : {raw.get('t', '')}",
        f"  誰が   : {raw.get('user') or '(ログイン前)'}(Level {raw.get('lvl', '-')})/ PC {raw.get('pc', '')}",
        f"  どこで : {center['screen'] or '-'}  {raw.get('method', '')} {raw.get('path', '')}",
        f"  何が   : {center['label']}  {center['msg']}",
    ]
    if done.get("st") is not None:
        lines.append(f"  状態   : {done.get('st')}(かかった時間 {done.get('ms', '-')} ms)")
    if exc:
        lines.append(f"  例外   : {exc.get('type', '')}: {exc.get('msg', '')}")
        tb = [t for t in (exc.get("tb") or []) if 'File "' in t]
        if tb:
            lines.append(f"  場所   : {tb[-1].strip().splitlines()[0]}")
    if data not in (None, {}, ""):
        lines.append("  中身   : " + json.dumps(data, ensure_ascii=False)[:600])
    lines.append(f"  版     : {raw.get('ver', '')}")
    lines += ["", "【その前に起きていたこと】"]
    lines += [f"  {_line(r)}" for r in before] or ["  (同じ画面の記録はありません)"]
    if after:
        lines += ["", "【その後】"]
        lines += [f"  {_line(r)}" for r in after]
    lines += ["", "【なぜ?】(事実から一段ずつ。推測には「?」を付ける)",
              "  なぜ1: ", "  なぜ2: ", "  なぜ3: ", "  なぜ4: ", "  なぜ5: ",
              "", "【真因】", "  ", "", "【対策】(誰が・いつまでに)", "  ", "",
              "【歯止め】(同じことが起きないように)", "  "]
    return "\n".join(lines)
