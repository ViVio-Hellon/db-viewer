"""設定ファイル(0.22.0)── 権限・かんたん設定・接続先 を 1 つの .sqlite3 に

設定 → かんたん設定 の「設定ファイル」でファイル名(フルパス)を決める。**無ければ作る。**
決めていなければ、この PC のデータフォルダの `DBビューア設定.sqlite3`(これも無ければ作る)。
作ったときは、この PC で開いていた DB を接続先に、梱包資材マスタ(0.21.0 までの権限)の dbviewer:edit の行を
アクセス権限に(権限 = 編集可)写しておく(上げたとたんに 誰でも 編集可 / 接続先が空 にならないように)。
共有フォルダのファイルにすれば、同じファイルを決めた PC どうしで 権限・かんたん設定・接続先 が同じになる。

中の表(無ければ作る。ほかの表があっても触らない):

    接続先        … 管理番号・名前・パス・備考。設定 → 接続先 のコンボボックスと、上の帯の DB のメニュー
    かんたん設定  … DB・表・設定(JSON)・更新者・更新日時。DB ごと・表ごと・列名ごとの 表示・入力・印刷・クリック
    アクセス権限  … 管理番号・ログインID・PC名・権限・有効・備考。権限 = 編集可 / 閲覧のみ(access_rights.py)

どの DB を見ているか(選んでいる接続先)は PC ごと(`user_settings` の db_path)。設定ファイルには入れない
(共有したとき、ほかの PC の画面が勝手に替わらないように)。
"""
from __future__ import annotations

import os
import sqlite3
import threading
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from . import config, logging_utils, user_settings
from .logging_utils import get_logger

log = get_logger("settings_file")

DEFAULT_NAME = "DBビューア設定.sqlite3"
SUFFIXES = (".sqlite3", ".db")
TIMEOUT_SEC = 10

T_DBS = "接続先"
T_EASY = "かんたん設定"
T_RIGHTS = "アクセス権限"

_DDL = (
    f'CREATE TABLE IF NOT EXISTS "{T_DBS}" ("管理番号" INTEGER PRIMARY KEY AUTOINCREMENT, '
    '"名前" TEXT, "パス" TEXT NOT NULL, "備考" TEXT)',
    f'CREATE TABLE IF NOT EXISTS "{T_EASY}" ("DB" TEXT NOT NULL, "表" TEXT NOT NULL, "設定" TEXT, '
    '"更新者" TEXT, "更新日時" TEXT, PRIMARY KEY ("DB", "表"))',
    f'CREATE TABLE IF NOT EXISTS "{T_RIGHTS}" ("管理番号" INTEGER PRIMARY KEY AUTOINCREMENT, '
    '"ログインID" TEXT, "PC名" TEXT, "権限" TEXT, "有効" INTEGER DEFAULT 1, "備考" TEXT)',
)

_lock = threading.Lock()
_ready: set[str] = set()                 # このプロセスで表をそろえたファイル


# ------------------------------------------------------------------
# どのファイルか
# ------------------------------------------------------------------
def default_path() -> Path:
    return config.USER_CONFIG_PATH.parent / DEFAULT_NAME


def configured() -> str:
    """設定に入れた文字(空なら既定の場所)。0.21.0 の「保存先フォルダ」は、そのフォルダの既定の名前に移す。"""
    text = str(user_settings.get(config.KEY_SETTINGS_FILE, "") or "").strip()
    if not text:
        folder = str(user_settings.get(config.KEY_EASY_DIR, "") or "").strip()
        if folder:
            text = str(config.resolve_dir(folder) / DEFAULT_NAME)
            user_settings.save(config.KEY_SETTINGS_FILE, text)
            user_settings.delete(config.KEY_EASY_DIR)
    return text


def path() -> Path:
    text = configured()
    return config.resolve_dir(text) if text else default_path()


def target_of(text: str) -> Path:
    """入れた文字 → ファイル。フォルダだけなら既定の名前を付ける。拡張子が無ければ .sqlite3。"""
    p = config.resolve_dir(text.strip().strip('"'))
    if p.is_dir():
        return p / DEFAULT_NAME
    if p.suffix.lower() not in SUFFIXES:
        p = p.with_name(p.name + ".sqlite3")
    return p


def check_target(text: str) -> tuple[Optional[Path], str]:
    """設定に入れる前に確かめる → (ファイル, だめなら理由)。空は既定の場所。"""
    text = (text or "").strip().strip('"')
    if not text:
        return default_path(), ""
    p = target_of(text)
    if not p.parent.is_dir():
        return None, f"フォルダが見つかりません: {p.parent}(共有フォルダならつながっているか確かめてください)"
    if p.exists():
        try:
            with closing(sqlite3.connect(f"file:{_uri(p)}?mode=ro", uri=True, timeout=3)) as c:
                c.execute("SELECT name FROM sqlite_master LIMIT 1").fetchall()
        except sqlite3.Error as exc:
            return None, f"{p.name} は .sqlite3 として読めません: {exc}"
    why = logging_utils.probe_writable(p.parent)
    if why:
        return None, f"このフォルダに書き込めません: {p.parent}({why})"
    return p, ""


def _uri(p: Path) -> str:
    return str(p).replace("?", "%3F").replace("#", "%23")


# ------------------------------------------------------------------
# 開く(無ければ作る)
# ------------------------------------------------------------------
def ensure(p: Optional[Path] = None) -> Path:
    """ファイルと 3 つの表をそろえる。作ったときは、この PC の今までの接続先を入れておく。"""
    p = p or path()
    key = str(p)
    if key in _ready and p.is_file():
        return p
    with _lock:
        fresh = not p.exists()
        if fresh:
            p.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(str(p), timeout=TIMEOUT_SEC)) as conn:
            for sql in _DDL:
                conn.execute(sql)
            conn.commit()
            if fresh:
                _seed_dbs(conn)
                _seed_rights(conn)
        if fresh:
            log.info("設定ファイルを作りました: %s", p)
        _ready.add(key)
    return p


def _seed_dbs(conn: sqlite3.Connection) -> None:
    """作ったばかりのファイルに、この PC で開いていた DB(0.20.0 の「最近開いた DB」)を入れる。"""
    texts = []
    now = str(user_settings.get(config.KEY_DB_PATH, "") or "").strip()
    if now:
        texts.append(now)
    recent = user_settings.get(config.KEY_DB_RECENT, [])
    if isinstance(recent, list):
        texts += [str(t).strip() for t in recent if isinstance(t, str) and t.strip()]
    seen: list[str] = []
    for t in texts:
        if any(same_file(t, s) for s in seen):
            continue
        seen.append(t)
        conn.execute(f'INSERT INTO "{T_DBS}" ("名前", "パス", "備考") VALUES (?, ?, ?)',
                     (Path(t.replace("\\", "/")).name, t, "この PC で開いていた DB"))
    conn.commit()


OLD_MASTER_NAME = "梱包資材マスタ.sqlite3"


def _old_master() -> Optional[Path]:
    """0.21.0 まで権限を読んでいた梱包資材マスタ(設定の場所 → 見ている DB と同じフォルダ)。"""
    text = str(user_settings.get(config.KEY_ACCESS_DB, "") or "").strip().strip('"')
    if text:
        return Path(text)
    now = str(user_settings.get(config.KEY_DB_PATH, "") or "").strip()
    if now:
        cand = config.resolve_dir(now).parent / OLD_MASTER_NAME
        return cand if cand.is_file() else None
    return None


def _seed_rights(conn: sqlite3.Connection) -> None:
    """作ったばかりのファイルに、梱包資材マスタの dbviewer:edit の行を 編集可 として写す
    (上げたとたんに誰でも 編集可 にならないように)。読めなければ何もしない。"""
    old = _old_master()
    if old is None or not old.is_file():
        return
    try:
        with closing(sqlite3.connect(f"file:{_uri(old)}?mode=ro", uri=True, timeout=3)) as src:
            src.row_factory = sqlite3.Row
            rows = [dict(r) for r in src.execute(f'SELECT * FROM "{T_RIGHTS}"')]
    except sqlite3.Error as exc:
        log.info("梱包資材マスタのアクセス権限を写せません(読めない): %s", exc)
        return
    took = 0
    for r in rows:
        if str(r.get("権限") or "").strip().casefold() != "dbviewer:edit":
            continue
        conn.execute(f'INSERT INTO "{T_RIGHTS}" ("ログインID", "PC名", "権限", "有効", "備考") VALUES (?, ?, ?, ?, ?)',
                     (r.get("ログインID") or "", r.get("PC名") or "", "編集可", r.get("有効", 1),
                      f"梱包資材マスタから(元の管理番号 {r.get('管理番号', '')})"))
        took += 1
    conn.commit()
    if took:
        log.info("梱包資材マスタの dbviewer:edit の行を %d 行写しました: %s", took, old)


def connect(p: Optional[Path] = None) -> sqlite3.Connection:
    p = ensure(p)
    conn = sqlite3.connect(str(p), timeout=TIMEOUT_SEC)
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout={TIMEOUT_SEC * 1000}")
    return conn


def info() -> dict[str, Any]:
    """画面に出す: どのファイルか・あるか・行の数。"""
    p = path()
    out: dict[str, Any] = {"path": str(p), "configured": configured(), "default": str(default_path()),
                           "exists": p.is_file(), "problem": "", "counts": {}}
    try:
        with closing(connect(p)) as conn:
            for t in (T_DBS, T_EASY, T_RIGHTS):
                out["counts"][t] = conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
        out["exists"] = True
    except (sqlite3.Error, OSError) as exc:
        out["problem"] = str(exc)
    return out


# ------------------------------------------------------------------
# 接続先
# ------------------------------------------------------------------
def same_file(a: str, b: str) -> bool:
    """同じファイルを指しているか(Windows は大文字・小文字と / \\ の違いを見ない)。"""
    return db_key(config.resolve_dir(a)) == db_key(config.resolve_dir(b))


def db_key(p: Path) -> str:
    return os.path.normcase(os.path.normpath(str(p)))


def dbs() -> list[dict[str, Any]]:
    """接続先(管理番号の順)。"""
    with closing(connect()) as conn:
        rows = conn.execute(f'SELECT "管理番号", "名前", "パス", "備考" FROM "{T_DBS}" ORDER BY "管理番号"').fetchall()
    return [dict(r) for r in rows if str(r["パス"] or "").strip()]


def find_db(text: str) -> Optional[dict[str, Any]]:
    return next((d for d in dbs() if same_file(d["パス"], text)), None)


def add_db(text: str, note: str = "") -> dict[str, Any]:
    """接続先に足す(もうあればそれを返す)。"""
    text = text.strip()
    have = find_db(text)
    if have:
        return have
    name = Path(text.replace("\\", "/")).name
    with closing(connect()) as conn:
        cur = conn.execute(f'INSERT INTO "{T_DBS}" ("名前", "パス", "備考") VALUES (?, ?, ?)', (name, text, note))
        conn.commit()
        no = int(cur.lastrowid or 0)
    log.info("接続先を足しました: %s", text)
    return {"管理番号": no, "名前": name, "パス": text, "備考": note}


def remove_db(text: str) -> bool:
    """接続先から外す(ファイルは消さない)。"""
    have = find_db(text)
    if not have:
        return False
    with closing(connect()) as conn:
        conn.execute(f'DELETE FROM "{T_DBS}" WHERE "管理番号" = ?', (have["管理番号"],))
        conn.commit()
    log.info("接続先から外しました: %s", text)
    return True


# ------------------------------------------------------------------
# 接続先の表を直接直す(0.32.0。設定 → 接続先 の「接続先の登録」)
# ------------------------------------------------------------------
DB_COLUMNS = ("管理番号", "名前", "パス", "備考")
MSG_DB_CHANGED = "ほかの PC がこの行を直しました。読み直してからもう一度してください。"


def db_rows() -> list[dict[str, Any]]:
    """接続先の表の全部の行(パスが空の行も。直す画面に出す)。"""
    with closing(connect()) as conn:
        rows = conn.execute(f'SELECT "管理番号", "名前", "パス", "備考" FROM "{T_DBS}" ORDER BY "管理番号"').fetchall()
    return [{k: r[k] for k in DB_COLUMNS} for r in rows]


def _db_clean(row: dict[str, Any]) -> tuple[dict[str, Any], str]:
    name = str(row.get("名前") or "").strip()
    text = str(row.get("パス") or "").strip().strip('"')
    note = str(row.get("備考") or "").strip()
    if not text:
        return {}, "パスを入れてください(DB ファイルのフルパス)。"
    for label, v, limit in (("名前", name, 200), ("パス", text, 1000), ("備考", note, 500)):
        if len(v) > limit or any(ord(ch) < 32 for ch in v):
            return {}, f"「{label}」が長すぎるか、使えない文字があります。"
    if not name:
        name = Path(text.replace("\\", "/")).name
    return {"名前": name, "パス": text, "備考": note}, ""


def _same_seen(now: dict[str, Any], seen: Optional[dict[str, Any]]) -> bool:
    if seen is None:
        return True
    norm = lambda v: "" if v is None else str(v)
    return all(norm(now.get(k)) == norm(seen.get(k)) for k in DB_COLUMNS if k in seen)


def save_db_row(row: dict[str, Any], seen: Optional[dict[str, Any]] = None) -> tuple[bool, str, int]:
    """管理番号が無ければ足す、あれば直す → (できたか, 知らせ, 管理番号)。同じファイルの行は 2 つ作らない。"""
    values, problem = _db_clean(row)
    if problem:
        return False, problem, 0
    raw = row.get("管理番号")
    no = int(raw) if str(raw or "").strip().isdigit() else 0
    with closing(connect()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            for other in conn.execute(f'SELECT "管理番号", "パス" FROM "{T_DBS}"').fetchall():
                if other["管理番号"] != no and str(other["パス"] or "").strip() and same_file(other["パス"], values["パス"]):
                    conn.execute("ROLLBACK")
                    return False, f"同じファイルはもう {other['管理番号']} 番にあります。", 0
            if no:
                now = conn.execute(f'SELECT * FROM "{T_DBS}" WHERE "管理番号" = ?', (no,)).fetchone()
                if now is None:
                    conn.execute("ROLLBACK")
                    return False, "その行はもうありません(ほかの PC が消しました)。", no
                if not _same_seen(dict(now), seen):
                    conn.execute("ROLLBACK")
                    return False, MSG_DB_CHANGED, no
                conn.execute(f'UPDATE "{T_DBS}" SET "名前" = ?, "パス" = ?, "備考" = ? WHERE "管理番号" = ?',
                             (values["名前"], values["パス"], values["備考"], no))
                msg = f"{no} 番を直しました。"
            else:
                cur = conn.execute(f'INSERT INTO "{T_DBS}" ("名前", "パス", "備考") VALUES (?, ?, ?)',
                                   (values["名前"], values["パス"], values["備考"]))
                no = int(cur.lastrowid or 0)
                msg = f"{no} 番に足しました。"
            conn.execute("COMMIT")
        except sqlite3.Error:
            conn.execute("ROLLBACK")
            raise
    log.info("接続先の表: %s %s", msg, values["パス"])
    return True, msg, no


def delete_db_row(no: int, seen: Optional[dict[str, Any]] = None) -> tuple[bool, str, Optional[dict[str, Any]]]:
    """1 行消す(ファイルは消さない)→ (できたか, 知らせ, 消した行)。"""
    with closing(connect()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        now = conn.execute(f'SELECT * FROM "{T_DBS}" WHERE "管理番号" = ?', (no,)).fetchone()
        if now is None:
            conn.execute("ROLLBACK")
            return False, "その行はもうありません(ほかの PC が消しました)。", None
        if not _same_seen(dict(now), seen):
            conn.execute("ROLLBACK")
            return False, MSG_DB_CHANGED, None
        conn.execute(f'DELETE FROM "{T_DBS}" WHERE "管理番号" = ?', (no,))
        conn.execute("COMMIT")
    log.info("接続先の表: %s 番を消しました: %s", no, now["パス"])
    return True, f"{no} 番を消しました(ファイルは消していません)。", dict(now)


# ------------------------------------------------------------------
# かんたん設定(DB ごと・表ごとに JSON 1 つ)
# ------------------------------------------------------------------
def easy_read(db: Path, table: str) -> Optional[str]:
    """保存してある設定(JSON の文字)。無ければ None。届かなければ例外。"""
    with closing(connect()) as conn:
        row = conn.execute(f'SELECT "設定" FROM "{T_EASY}" WHERE "DB" = ? AND "表" = ?',
                           (db_key(db), table)).fetchone()
    return row[0] if row else None


def easy_write(db: Path, table: str, text: str, user: str = "") -> None:
    with closing(connect()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(f'INSERT OR REPLACE INTO "{T_EASY}" VALUES (?, ?, ?, ?, ?)',
                         (db_key(db), table, text, user, datetime.now().strftime("%Y/%m/%d %H:%M:%S")))
            conn.execute("COMMIT")
        except sqlite3.Error:
            conn.execute("ROLLBACK")
            raise


def legacy_easy_file(db: Path) -> Path:
    """0.21.0 の保存先(フォルダの <DB 名>_かんたん設定.sqlite3)。設定ファイルと同じフォルダを見る。"""
    return path().parent / f"{db.stem}_かんたん設定.sqlite3"
