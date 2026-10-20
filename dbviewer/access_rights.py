"""アクセス権限 ── この端末が 編集可 か 閲覧のみ か(0.16.0。0.22.0 から設定ファイルの表)

Windows の**ログインID**と**PC名**を条件に、**設定ファイル**(`settings_file.py`。かんたん設定・接続先と同じ
ファイル)の `アクセス権限` 表を引く。パスワードは要らない(ログインIDとPC名は Windows が持っている事実で、
利用者が画面から変えられない)。

    管理番号  ログインID  PC名             権限      有効  備考
    --------  ----------  ---------------  --------  ----  ----
    1         yamada      (空)             編集可    1     山田はどの PC でも編集可
    2         (空)        NLM-NGY-240638   編集可    1     この PC は誰でも編集可
    3         suzuki      NLM-NGY-240638   閲覧のみ  1     書いておくだけ(編集可の行が無ければ閲覧のみ)

* 権限が `編集可`(all-tools と同じ書き方の `dbviewer:edit` も)の行が当てはまれば 編集可。無ければ 閲覧のみ
* 空欄は「問わない」。**ログインIDもPC名も空の行は誰にも効かない**(全員への許可になり、権限を設ける意味が消える)
* 大文字小文字・全角半角は区別しない(Windows の ID と PC 名がそうなので)
* `有効` は空欄なら有効、`0` / `false` / `×` / `無効` などなら無効
* **表に行が 1 つも無ければ、誰でも 編集可**(作ったばかりの設定ファイル。行を足すと効き始める)

【直すとき(0.17.0)】設定 → かんたん設定 → 権限 で、**管理者パスワード**(既定 nisk。`admin_password`)で
鍵を開けたときだけ、この表の行を足す・直す・消す。書く前に設定ファイルの控えを取り、ほかの PC が先に変えて
いたら書かない。

【届かないとき】設定ファイルを共有フォルダに置いたとき。届かないたびに 閲覧のみ に落とすと仕事が止まるので、
**前回読めた結果をこの PC に覚えておき**、届かないときはそれで決める(all-tools と同じ)。
一度も読めていなければ 閲覧のみ。
"""
from __future__ import annotations

import json
import sqlite3
import time
import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

from . import app_config
from .logging_utils import get_logger

log = get_logger("access_rights")

TABLE = "アクセス権限"
EDIT = "編集可"
VIEW = "閲覧のみ"
EDIT_WORDS = {"編集可", "dbviewer:edit"}          # all-tools の書き方も読む
COL_NO, COL_LOGIN, COL_PC, COL_PERMISSION, COL_ENABLED, COL_NOTE = (
    "管理番号", "ログインID", "PC名", "権限", "有効", "備考")
_OFF = {"0", "false", "no", "off", "×", "x", "無効", "いいえ", "n"}
RECHECK_SEC = 300.0

SOURCE_UNSET, SOURCE_MASTER, SOURCE_CACHE, SOURCE_UNREACHABLE = "unset", "master", "cache", "unreachable"
LABEL_EDIT, LABEL_VIEW = "編集可", "閲覧のみ"


@dataclass
class Decision:
    edit: bool
    source: str                         # unset | master | cache | unreachable
    reason: str
    login_id: str = ""
    pc: str = ""
    path: str = ""
    matched: list[dict[str, Any]] = field(default_factory=list)   # この端末に当てはまった dbviewer の行
    checked_at: float = 0.0

    @property
    def label(self) -> str:
        return LABEL_EDIT if self.edit else LABEL_VIEW

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["label"] = self.label
        return d


def _norm(text: Any) -> str:
    return unicodedata.normalize("NFKC", "" if text is None else str(text)).strip().casefold()


def enabled(value: Any) -> bool:
    return _norm(value) not in _OFF


def applies(row: dict[str, Any], login_id: str, pc: str) -> bool:
    """その行がこの人・この端末に当てはまるか。空欄は問わない。両方空の行は誰にも効かない。"""
    rid, rpc = _norm(row.get(COL_LOGIN)), _norm(row.get(COL_PC))
    if not rid and not rpc:
        return False
    return (not rid or rid == _norm(login_id)) and (not rpc or rpc == _norm(pc))


def decide(rows: list[dict[str, Any]], login_id: str, pc: str) -> tuple[bool, list[dict[str, Any]]]:
    """(編集可か, 当てはまった 編集可 の行)。"""
    hit = [r for r in rows if is_edit(r.get(COL_PERMISSION)) and enabled(r.get(COL_ENABLED))
           and applies(r, login_id, pc)]
    return bool(hit), hit


def is_edit(permission: Any) -> bool:
    return _norm(permission) in {_norm(w) for w in EDIT_WORDS}


def edit_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """表の中の 編集可 の行(設定の画面で「誰が編集可か」を見せる)。"""
    return [r for r in rows if is_edit(r.get(COL_PERMISSION))]


# ------------------------------------------------------------------
# どのファイルか: 設定ファイル(無ければ作る)
# ------------------------------------------------------------------
def master_path() -> Path:
    from . import settings_file
    try:
        return settings_file.ensure()
    except (sqlite3.Error, OSError) as exc:              # 届かない: 読むときに前回の結果で決める
        log.warning("設定ファイルを開けません: %s", exc)
        return settings_file.path()


def read_rows(path: Path) -> list[dict[str, Any]]:
    """`アクセス権限` の行(読み取り専用で開く)。表が無い・読めないときは例外。"""
    if not path.is_file():
        raise FileNotFoundError(f"見つかりません: {path}")
    uri = "file:" + str(path).replace("?", "%3F").replace("#", "%23") + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=3)
    try:
        conn.row_factory = sqlite3.Row
        have = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (TABLE,)).fetchone()
        if not have:
            raise LookupError(f"{path.name} に「{TABLE}」の表がありません")
        return [dict(r) for r in conn.execute(f'SELECT * FROM "{TABLE}"')]
    finally:
        conn.close()


# ------------------------------------------------------------------
# 前回読めた結果(届かないとき用)
# ------------------------------------------------------------------
def _cache_file() -> Path:
    return app_config.local_dir("runtime") / "access_rights.json"


def _save_cache(d: Decision) -> None:
    try:
        path = _cache_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(d.to_dict(), ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def _load_cache(path: str, login_id: str, pc: str) -> Optional[dict[str, Any]]:
    try:
        d = json.loads(_cache_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(d, dict) or d.get("path") != path or _norm(d.get("login_id")) != _norm(login_id) \
            or _norm(d.get("pc")) != _norm(pc):
        return None
    return d


# ------------------------------------------------------------------
def evaluate(login_id: str, pc: str) -> Decision:
    """この人・この端末は 編集可 か 閲覧のみ か。"""
    now = time.time()
    path = master_path()
    try:
        rows = read_rows(path)
    except Exception as exc:                             # noqa: BLE001 - 届かない・壊れている
        cached = _load_cache(str(path), login_id, pc)
        if cached is not None:
            when = time.strftime("%m/%d %H:%M", time.localtime(float(cached.get("checked_at") or 0)))
            log.warning("設定ファイルのアクセス権限を読めないので前回の結果(%s)を使います: %s", when, exc)
            return Decision(bool(cached.get("edit")), SOURCE_CACHE,
                            f"設定ファイルに届かないので、前回({when})読めた結果を使っています: {exc}",
                            login_id, pc, str(path), list(cached.get("matched") or []), now)
        log.warning("設定ファイルのアクセス権限を読めません(前回の結果も無いので閲覧のみ): %s", exc)
        return Decision(False, SOURCE_UNREACHABLE, f"設定ファイルのアクセス権限を読めないので、閲覧のみにしています: {exc}",
                        login_id, pc, str(path), [], now)
    if not rows:
        return Decision(True, SOURCE_UNSET, "アクセス権限の表にまだ行が無いので、誰でも編集できます"
                        "(設定 → かんたん設定 → 権限 で行を足すと効き始めます)。", login_id, pc, str(path), [], now)
    edit, hit = decide(rows, login_id, pc)
    reason = (f"アクセス権限の {len(hit)} 行が当てはまりました({EDIT})。" if edit else
              f"アクセス権限に、この ID・PC で {EDIT} の行がありません。")
    d = Decision(edit, SOURCE_MASTER, reason, login_id, pc, str(path),
                 [{k: r.get(k) for k in (COL_NO, COL_LOGIN, COL_PC, COL_PERMISSION, COL_ENABLED, COL_NOTE)}
                  for r in hit], now)
    _save_cache(d)
    return d


def overview() -> dict[str, Any]:
    """設定の画面: どのファイルを読んでいるか・誰が編集可か。"""
    path = master_path()
    out: dict[str, Any] = {"path": str(path), "found": False, "edit_rows": [], "rows": 0, "problem": "",
                           "permission": EDIT, "table": TABLE}
    try:
        rows = read_rows(path)
    except Exception as exc:                             # noqa: BLE001
        out["problem"] = str(exc)
        return out
    out["found"] = True
    out["rows"] = len(rows)
    out["edit_rows"] = [{k: r.get(k) for k in (COL_NO, COL_LOGIN, COL_PC, COL_ENABLED, COL_NOTE)}
                        for r in edit_rows(rows)]
    return out


# ------------------------------------------------------------------
# アクセス権限を直す(0.17.0。管理者パスワードで鍵を開けたときだけ。鍵は呼ぶ側が確かめる)
# ------------------------------------------------------------------
COLUMNS = (COL_NO, COL_LOGIN, COL_PC, COL_PERMISSION, COL_ENABLED, COL_NOTE)
MSG_CHANGED = "ほかの PC が先にこの行を変えていました。いまの中身を出したので、もう一度直してください。"


@dataclass
class WriteResult:
    ok: bool
    message: str
    reason: str = ""
    no: int = 0
    backup: str = ""


def all_rows(path: Path) -> list[dict[str, Any]]:
    """直す画面に出す行(管理番号の順)。"""
    rows = read_rows(path)
    out = [{k: r.get(k) for k in COLUMNS} for r in rows]
    return sorted(out, key=lambda r: int(r.get(COL_NO) or 0))


def _clean(row: dict[str, Any]) -> tuple[dict[str, Any], str]:
    login = str(row.get(COL_LOGIN) or "").strip()
    pc = str(row.get(COL_PC) or "").strip()
    perm = str(row.get(COL_PERMISSION) or "").strip()
    note = str(row.get(COL_NOTE) or "").strip()
    on = row.get(COL_ENABLED)
    enabled_value = 0 if on in (False, 0, "0") or (isinstance(on, str) and not enabled(on)) else 1
    if not perm:
        return {}, f"権限を選んでください({EDIT} / {VIEW})。"
    if not login and not pc:
        return {}, "ログインID か PC名 のどちらかは入れてください(両方空の行は誰にも効きません)。"
    for name, v in ((COL_LOGIN, login), (COL_PC, pc), (COL_PERMISSION, perm), (COL_NOTE, note)):
        if len(v) > 200 or any(ord(ch) < 32 for ch in v):
            return {}, f"「{name}」が長すぎるか、使えない文字があります。"
    return {COL_LOGIN: login, COL_PC: pc, COL_PERMISSION: perm, COL_ENABLED: enabled_value, COL_NOTE: note}, ""


def _same_as_seen(now: dict[str, Any], seen: Optional[dict[str, Any]]) -> bool:
    if seen is None:
        return True
    norm = lambda v: "" if v is None else str(v)
    return all(norm(now.get(k)) == norm(seen.get(k)) for k in COLUMNS if k in seen)


_backed_up: set[str] = set()


def _backup(path: Path) -> str:
    """書く前に控え(鍵を開けてから最初の 1 回。sqlite の backup で、書きかけを写さない)。"""
    key = str(path)
    if key in _backed_up:
        return ""
    folder = app_config.local_dir("backup")
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / f"{path.stem}_アクセス権限の前_{time.strftime('%Y%m%d_%H%M%S')}{path.suffix}"
    src = sqlite3.connect("file:" + str(path).replace("?", "%3F").replace("#", "%23") + "?mode=ro", uri=True, timeout=5)
    try:
        dst = sqlite3.connect(dest)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    _backed_up.add(key)
    return str(dest)


def forget_backups() -> None:
    """鍵を掛けたら、次に開けたときにまた控えを取る。"""
    _backed_up.clear()


def _write(path: Path, work) -> WriteResult:
    try:
        backup = _backup(path)
    except (OSError, sqlite3.Error) as exc:
        return WriteResult(False, f"控えを取れなかったので書きません: {exc}", "backup_failed")
    try:
        conn = sqlite3.connect(path, timeout=5)
    except sqlite3.Error as exc:
        return WriteResult(False, f"設定ファイルを開けません: {exc}", "failed")
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN IMMEDIATE")
        try:
            result = work(conn)
            if result.ok:
                conn.execute("COMMIT")
            else:
                conn.execute("ROLLBACK")
        except sqlite3.Error as exc:
            conn.execute("ROLLBACK")
            return WriteResult(False, f"書けませんでした: {exc}", "failed")
    except sqlite3.Error as exc:
        return WriteResult(False, f"書けませんでした(ほかの PC が使っているかもしれません): {exc}", "busy")
    finally:
        conn.close()
    result.backup = backup
    return result


def _current(conn: sqlite3.Connection, no: int) -> Optional[dict[str, Any]]:
    r = conn.execute(f'SELECT * FROM "{TABLE}" WHERE "{COL_NO}" = ?', (no,)).fetchone()
    return {k: r[k] for k in r.keys()} if r else None


def save_row(path: Path, row: dict[str, Any], seen: Optional[dict[str, Any]] = None) -> WriteResult:
    """管理番号が無ければ足す、あれば直す(見ていた値と違えば書かない)。"""
    values, problem = _clean(row)
    if problem:
        return WriteResult(False, problem, "bad_input")
    raw_no = row.get(COL_NO)
    no = int(raw_no) if str(raw_no or "").strip().isdigit() else 0

    def work(conn: sqlite3.Connection) -> WriteResult:
        if no:
            now = _current(conn, no)
            if now is None:
                return WriteResult(False, "その行はもうありません(ほかの PC が消しました)。", "gone", no)
            if not _same_as_seen(now, seen):
                return WriteResult(False, MSG_CHANGED, "conflict", no)
            conn.execute(f'UPDATE "{TABLE}" SET ' + ", ".join(f'"{k}" = ?' for k in values) + f' WHERE "{COL_NO}" = ?',
                         [*values.values(), no])
            return WriteResult(True, f"{no} 番の行を直しました。", "", no)
        cur = conn.execute(f'INSERT INTO "{TABLE}" (' + ", ".join(f'"{k}"' for k in values) + ") VALUES ("
                           + ", ".join("?" for _ in values) + ")", list(values.values()))
        return WriteResult(True, f"{cur.lastrowid} 番の行を足しました。", "", int(cur.lastrowid or 0))

    return _write(path, work)


def delete_row(path: Path, no: int, seen: Optional[dict[str, Any]] = None) -> WriteResult:
    def work(conn: sqlite3.Connection) -> WriteResult:
        now = _current(conn, no)
        if now is None:
            return WriteResult(False, "その行はもうありません(ほかの PC が消しました)。", "gone", no)
        if not _same_as_seen(now, seen):
            return WriteResult(False, MSG_CHANGED, "conflict", no)
        conn.execute(f'DELETE FROM "{TABLE}" WHERE "{COL_NO}" = ?', (no,))
        return WriteResult(True, f"{no} 番の行を消しました。", "", no)

    return _write(path, work)


def permissions_in_use(path: Path) -> list[str]:
    """権限の欄の候補(編集可・閲覧のみ と、この表で使われているほかの値)。"""
    try:
        rows = read_rows(path)
    except Exception:                                    # noqa: BLE001
        return [EDIT, VIEW]
    seen = {str(r.get(COL_PERMISSION) or "").strip() for r in rows} - {"", EDIT, VIEW}
    return [EDIT, VIEW, *sorted(seen)]
