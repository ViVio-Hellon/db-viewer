"""表を持ってくる(Access で作った表を、いまの本体 .sqlite3 へ足す・最新にする)

python-web-tools `packaging_tool/table_bring.py`(VER3.5.8, d478462)の移植。
画面・手順・守っていることは元のまま。DBビューアに合わせて変えたところは
末尾の【元との違い】にまとめた。

【なぜ要るのか】
Access で表を作り直すたびに「Access → sqlite3 を丸ごと変換 → 差し替え」
(設定 → Access 取り込み)をすると、Web 版で登録した行(一覧の新規・修正、
H_ 履歴、マスタ管理で直した設定)のうち Access に無いものが消える。
そこで、選んだ表だけを足す/入れ替える。ほかの表・行・索引には触らない。

【読むのはいつも .sqlite3】
- `.sqlite3` / `.db` を選んだら、そのまま読む
- `.accdb` / `.mdb` を選んだら、Access 取り込みと同じ経路
  (cscript.exe → bridge.vbs → ADODB → ACE OLE DB → TSV → .sqlite3)で
  作業フォルダに .sqlite3 を作ってから、それを読む(`_convert`)。
  pyodbc / access_parser は使わない(配布先 PC に入れられないため)

【守っていること】
- **無い表しか作らない**(「持ってくる」)。同じ名前の表があれば断る
- もうある表は「入れ替える」(列が同じか Access で列を足しただけ)か
  「作り直す」(列が消えた・名前が変わった。今の表は `表_作り直す前_日時` で残す)
- Web 版がふだん書き込む表(メインテーブル・H_ 履歴)、ログインに使う ID、
  控えの表は入れ替えない(`_tool_owned`)
- 1つの表は **作る+中身を入れる を1回で確定** する。途中で落ちたら何も残らない
- 書く前に本体を手元の `backup` へ写す
- 何をしたかは画面と取り込み診断(`import_diag`)に出す
"""
from __future__ import annotations

import re
import shutil
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from . import app_config, config, import_diag, source_db
from .logging_utils import get_logger

log = get_logger("table_bring")

REFUSE_NO_FILE = "no_file"
REFUSE_NO_DEST = "no_dest"
REFUSE_SAME_FILE = "same_file"
REFUSE_NOTHING = "nothing"
REFUSE_EXISTS = "exists"
REFUSE_WRITE_FAILED = "write_failed"
REFUSE_CONVERT = "convert_failed"

# もうある表にすること
ACTION_REFRESH = "refresh"        # 中身を入れ替える(Access にだけある列は足す)
ACTION_REBUILD = "rebuild"        # 作り直す(列が消えた・名前が変わった)

# 足した表の記録(本体の中)
REGISTRY = config.BROUGHT_REGISTRY

# Access のファイル。これを選んだら bridge で .sqlite3 にしてから読む
ACCESS_SUFFIXES = (".accdb", ".mdb")
READ_ENGINE = "cscript → ADODB → ACE OLE DB"


@dataclass
class Candidate:
    """持ってくるファイルの中の表1つ。"""

    name: str
    rows: int = 0
    columns: list[str] = field(default_factory=list)
    exists: bool = False          # 本体にもう同じ名前の表がある
    suspect: int = 0              # 文字化けの疑いがある行(置き換え文字 U+FFFD を含む)
    current_rows: int = 0         # 本体のいまの行数
    refresh_why: str = ""         # 入れ替えられない理由(入れ替えられるなら空)
    keeps: str = ""               # 入れ替えても残すもの(DBビューアでは使わない)
    added_columns: list[str] = field(default_factory=list)
    removed_columns: list[str] = field(default_factory=list)
    import_loses: list[str] = field(default_factory=list)  # 消える列のうち、設定で使っている列

    @property
    def action(self) -> str:
        if not self.exists:
            return ""
        return ACTION_REBUILD if self.removed_columns else ACTION_REFRESH

    @property
    def can_bring(self) -> bool:
        return not self.exists

    @property
    def can_refresh(self) -> bool:
        return self.exists and not self.refresh_why


@dataclass
class Plan:
    """中を見た結果。持ってこられる表と、もうある表。"""

    source: str = ""
    dest: str = ""
    ok: bool = False
    message: str = ""
    candidates: list[Candidate] = field(default_factory=list)
    dest_note: str = ""           # 書き先のファイル
    converted: str = ""           # Access を変換して読んだとき、その旨
    converter: str = ""           # 変換に使う経路

    @property
    def new_tables(self) -> list[Candidate]:
        return [c for c in self.candidates if c.can_bring]

    @property
    def refreshable(self) -> list[Candidate]:
        return [c for c in self.candidates if c.can_refresh]


@dataclass
class BringResult:
    ok: bool
    message: str
    reason: str = ""
    brought: list[tuple[str, int]] = field(default_factory=list)
    backup: str = ""
    refreshed: list[tuple[str, int, int]] = field(default_factory=list)
    kept_old: list[tuple[str, str]] = field(default_factory=list)


# Access が自分のために持っている表。**持ってこない。**
_ACCESS_INTERNAL = re.compile(r"^(MSys|f_[0-9A-Fa-f]{32}_)")


def is_access_internal(name: str) -> bool:
    return bool(_ACCESS_INTERNAL.match(name))


_ASCII_LOWER = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")


def _fold(name: str) -> str:
    """表の名前の比べ方。sqlite3 と同じく英字の大文字・小文字だけを区別しない。"""
    return name.translate(_ASCII_LOWER)


def _by_fold(names) -> dict[str, str]:
    return {_fold(n): n for n in names}


class ConvertError(Exception):
    """Access を .sqlite3 にできなかった。文は画面にそのまま出す。"""


def is_access(path: Path) -> bool:
    return Path(path).suffix.lower() in ACCESS_SUFFIXES


# 同じ Access を何度も変換しない(中を見る → 持ってくる で2回呼ばれる)。
# 鍵はファイルの場所と姿(大きさ・更新時刻)。変わっていたら変換し直す
_converted_cache: dict[str, tuple[tuple[int, int], Path, str]] = {}


def _convert(src: Path) -> tuple[Path, str]:
    """Access を .sqlite3 にする。返すのは (作ったファイル, 読み取り方式)。

    元は変換ツール(accdb_converter: pyodbc / access_parser)を別プロセスで
    呼んでいた。DBビューアは配布先 PC に Python の追加部品を入れられないので、
    Access 取り込みと同じ bridge(cscript → ADODB → ACE)で変換する。
    """
    stat = src.stat()
    stamp = (stat.st_size, stat.st_mtime_ns)
    cached = _converted_cache.get(str(src))
    if cached and cached[0] == stamp and cached[1].is_file():
        return cached[1], cached[2]

    from .accdb_bridge import importer
    work = app_config.local_dir("work")
    work.mkdir(parents=True, exist_ok=True)
    out = work / f"表を持ってくる_{src.stem}_{datetime.now():%Y%m%d_%H%M%S}.sqlite3"
    try:
        importer.import_accdb(src, out)
    except importer.BridgeError as exc:
        out.unlink(missing_ok=True)
        raise ConvertError(str(exc)) from None
    except Exception as exc:                          # noqa: BLE001 - 理由を画面に出す
        out.unlink(missing_ok=True)
        raise ConvertError(f"Access を変換できませんでした。{exc}") from None
    if cached:
        Path(cached[1]).unlink(missing_ok=True)
    _converted_cache[str(src)] = (stamp, out, READ_ENGINE)
    log.info("Access を変換しました: %s → %s", src, out)
    return out, READ_ENGINE


def _readable(src: Path) -> tuple[Path, str]:
    """読む実体(いつも .sqlite3)。Access なら変換したもの、sqlite3 ならそのもの。"""
    if is_access(src):
        return _convert(src)
    return src, ""


def _suspect_rows(path: Path, table: str, columns: list[str]) -> int:
    """文字化けの疑いがある行の数。**置き換え文字(U+FFFD)を含む行**を数える。"""
    if not columns:
        return 0
    cond = " OR ".join(f"instr(CAST({source_db.quote_identifier(c)} AS TEXT),"
                       " char(65533)) > 0" for c in columns)
    try:
        rows = source_db.read_query(
            path, f"SELECT COUNT(*) AS n FROM {source_db.quote_identifier(table)}"
                  f" WHERE {cond}")
    except (source_db.SourceError, sqlite3.Error):
        return 0
    return int(rows[0]["n"]) if rows else 0


# ドラッグ&ドロップで受け取れるファイル
UPLOAD_SUFFIXES = ACCESS_SUFFIXES + source_db.SUFFIXES
UPLOAD_LIMIT_BYTES = 1024 * 1024 * 1024


def save_upload(filename: str, stream: Any) -> tuple[Optional[Path], str]:
    """ドロップされたファイルを手元の作業フォルダへ置く。(置いた場所, 断る理由)。

    名前はファイル名の部分だけを使う(フォルダを含む名前で外へ書かせない)。
    """
    name = Path(str(filename).replace("\\", "/")).name
    if Path(name).suffix.lower() not in UPLOAD_SUFFIXES:
        return None, ("Access(.accdb / .mdb)か sqlite3(.sqlite3 / .db)の"
                      "ファイルを落としてください。")
    folder = app_config.local_dir("work") / "表を持ってくる"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / name
    written = 0
    try:
        with open(target, "wb") as fh:
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > UPLOAD_LIMIT_BYTES:
                    raise ValueError("大きすぎます")
                fh.write(chunk)
    except (OSError, ValueError) as exc:
        target.unlink(missing_ok=True)
        return None, f"ファイルを受け取れませんでした({exc})"
    _converted_cache.pop(str(target), None)
    log.info("持ってくるファイルを受け取りました: %s (%s バイト)", target, written)
    return target, ""


def _dest() -> Optional[Path]:
    """書き先 = いまの接続先の本体(.sqlite3)。"""
    from . import shared_db
    path = shared_db.db_path()
    return path if path.is_file() else None


def _dest_missing_why() -> str:
    from . import shared_db
    return (f"書き先の本体が見つかりません: {shared_db.db_path()}。"
            "設定 → 接続先 を確かめるか、先に「Access から取り込む」で本体を作ってください。")


def _same(a: Path, b: Path) -> bool:
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return str(a) == str(b)


def plan(source_path: str) -> Plan:
    """持ってくるファイルの中を見る。**書かない。**"""
    out = Plan(source=str(source_path or "").strip(), converter=READ_ENGINE)
    if not out.source:
        out.message = "持ってくるファイルを選んでください。"
        return out
    src = Path(out.source)
    if not src.is_file():
        out.message = f"ファイルが見つかりません: {src}"
        return out
    dest = _dest()
    if dest is None:
        out.message = _dest_missing_why()
        return out
    out.dest = str(dest)
    out.dest_note = f"書き先: {dest}"
    if _same(src, dest):
        out.message = ("選んだのは、いま使っている本体そのものです。"
                       "Access(.accdb)か、Access から変換した別のファイルを選んでください。")
        return out
    try:
        readable, engine = _readable(src)
    except ConvertError as exc:
        out.message = str(exc)
        return out
    if is_access(src):
        out.converted = f"Access を .sqlite3 に変換して読みました(読み取り: {engine})"
    try:
        names = source_db.list_tables(readable)
        existing = _by_fold(source_db.list_tables(dest))
        counts = source_db.table_counts(readable)
    except (source_db.SourceError, sqlite3.Error) as exc:
        out.message = f"ファイルを読めません: {exc}"
        return out
    internal = [n for n in names if is_access_internal(n)]
    names = [n for n in names if not is_access_internal(n) and n != REGISTRY]
    dest_counts = source_db.table_counts(dest)
    for name in names:
        columns = source_db.columns(readable, name)
        here = existing.get(_fold(name))
        found = Candidate(
            name=name, rows=max(counts.get(name, 0), 0), columns=columns,
            exists=here is not None, suspect=_suspect_rows(readable, name, columns))
        if here is not None:
            found.current_rows = max(dest_counts.get(here, 0), 0)
            _compare_columns(found, source_db.columns(dest, here), here, dest)
        out.candidates.append(found)
    out.ok = True
    new = len(out.new_tables)
    refresh = len([c for c in out.refreshable if c.action == ACTION_REFRESH])
    rebuild = len([c for c in out.refreshable if c.action == ACTION_REBUILD])
    out.message = (f"{len(names)}表のうち、本体に無い表が {new} 個"
                   f"、中身を Access の最新に入れ替えられる表が {refresh} 個")
    if rebuild:
        out.message += f"、列が変わったので作り直す表が {rebuild} 個"
    out.message += "あります。"
    if internal:
        out.message += f"(Access の内部の表 {len(internal)} 個は出していません)"
    suspects = [c for c in out.candidates if c.suspect]
    if suspects:
        out.message += (" ⚠ 文字化けの疑いがある行があります: "
                        + "、".join(f"{c.name} {c.suspect}行" for c in suspects)
                        + "。持ってくる前に Access の中身と見比べてください。")
    return out


def _create_sql(src: Path, table: str) -> tuple[str, list[tuple[str, str]]]:
    """元の表の `CREATE TABLE` と、その表の索引(名前, `CREATE INDEX`)。"""
    rows = source_db.read_query(
        src, "SELECT type, name, sql FROM sqlite_master WHERE tbl_name = ? COLLATE NOCASE"
             " AND sql IS NOT NULL", [table])
    create = next((r["sql"] for r in rows if r["type"] == "table"), "")
    indexes = [(r["name"], r["sql"]) for r in rows if r["type"] == "index"]
    if not create:
        raise source_db.SourceError(f"{table} の定義を読めません")
    return create, indexes


def backup_copy(dest: Path, why: str = "表を持ってくる前") -> Path:
    """書く前に本体を手元へ写す。戻したいときの頼り。"""
    folder = app_config.local_dir("backup")
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{dest.stem}_{why}_{datetime.now():%Y%m%d_%H%M%S}{dest.suffix}"
    shutil.copyfile(dest, target)
    return target


def bring(source_path: str, tables: list[str]) -> BringResult:
    """選んだ表を本体へ作り、中身を写す。**無い表だけ。**"""
    src = Path(str(source_path or "").strip())
    if not src.is_file():
        return BringResult(False, f"ファイルが見つかりません: {src}", REFUSE_NO_FILE)
    dest = _dest()
    if dest is None:
        return BringResult(False, _dest_missing_why(), REFUSE_NO_DEST)
    if _same(src, dest):
        return BringResult(False, "選んだのは、いま使っている本体そのものです。", REFUSE_SAME_FILE)
    wanted = [t for t in dict.fromkeys(str(t) for t in tables) if t]
    if not wanted:
        return BringResult(False, "持ってくる表を選んでください。", REFUSE_NOTHING)

    original = src
    try:
        src, _engine = _readable(src)
    except ConvertError as exc:
        return BringResult(False, str(exc), REFUSE_CONVERT)

    internal = [t for t in wanted if is_access_internal(t) or t == REGISTRY]
    if internal:
        return BringResult(False, f"Access の内部の表・控えの表は持ってきません: {', '.join(internal)}",
                           REFUSE_NOTHING)
    available = set(source_db.list_tables(src))
    missing = [t for t in wanted if t not in available]
    if missing:
        return BringResult(False, f"選んだファイルに無い表です: {', '.join(missing)}",
                           REFUSE_NOTHING)
    existing = _by_fold(source_db.list_tables(dest))
    already = [t for t in wanted if _fold(t) in existing]
    if already:
        # **上書きも合流もしない。** 今ある表には Web 版で書いた行が入っているかもしれない
        return BringResult(False, f"本体にもうある表は持ってきません: "
                                  f"{', '.join(already)}", REFUSE_EXISTS)

    try:
        backup = backup_copy(dest)
    except OSError as exc:
        return BringResult(False, f"書く前の控えを取れませんでした({exc})。"
                                  "何も変えていません。", REFUSE_WRITE_FAILED)

    import_diag.write("=" * 70)
    import_diag.write(f"■ 表を持ってくる  {original} → {dest}")
    if original != src:
        import_diag.write(f"  Access を変換して読みました: {src}")
    import_diag.write(f"  書く前の控え: {backup}")
    brought: list[tuple[str, int]] = []
    failed: list[str] = []
    q = source_db.quote_identifier
    with source_db.connect(dest) as dst:
        for table in wanted:
            try:
                create, indexes = _create_sql(src, table)
                rows = source_db.read_table(src, table)
                skipped_idx: list[str] = []
                with dst.transaction() as tx:
                    if tx.query("SELECT 1 FROM sqlite_master WHERE type = 'table'"
                                " AND name = ? COLLATE NOCASE", [table]):
                        raise source_db.SourceError("ほかの端末が先に作りました")
                    tx.execute(create)
                    for row in rows:
                        tx.insert(table, row)
                    tx.execute(
                        f"CREATE TABLE IF NOT EXISTS {q(REGISTRY)}"
                        " (表 TEXT PRIMARY KEY, 足した日時 TEXT, 元のファイル TEXT)")
                    tx.execute(
                        f"INSERT OR REPLACE INTO {q(REGISTRY)} (表, 足した日時, 元のファイル)"
                        " VALUES (?, ?, ?)",
                        [table, datetime.now().strftime("%Y/%m/%d %H:%M:%S"), str(original)])
                    # 索引の名前はファイルの中で1つしか使えない。同じ名前があれば作らない
                    for name, sql in indexes:
                        if tx.query("SELECT 1 FROM sqlite_master WHERE name = ?", [name]):
                            skipped_idx.append(name)
                            continue
                        tx.execute(sql)
                brought.append((table, len(rows)))
                made = len(indexes) - len(skipped_idx)
                import_diag.write(f"  持ってきた: {table} ({len(rows):,}行"
                                  + (f"・索引{made}" if made else "") + ")")
                if skipped_idx:
                    import_diag.write(f"    同じ名前があるので作らなかった索引: "
                                      f"{', '.join(skipped_idx)}")
                log.info("表を持ってきました: %s %s行 (%s → %s)", table, len(rows), src, dest)
            except (source_db.SourceError, sqlite3.Error) as exc:
                failed.append(f"{table}({exc})")
                import_diag.write(f"  ✕ 持ってこられませんでした: {table} ── {exc}")
                log.warning("表を持ってこられませんでした: %s: %s", table, exc)

    done = "、".join(f"{t}({n:,}行)" for t, n in brought)
    if failed and not brought:
        return BringResult(False, "持ってこられませんでした: " + "、".join(failed)
                           + "。本体は変えていません。",
                           REFUSE_WRITE_FAILED, backup=str(backup))
    message = (f"本体に {done} を足しました。今ある表には触っていません。"
               "一覧の表の選び欄に「足」の印で出ていて、そのまま直せます。"
               f"(書いたファイル: {dest})")
    if failed:
        message += " ただし次は持ってこられませんでした: " + "、".join(failed)
    return BringResult(not failed, message, "" if not failed else REFUSE_WRITE_FAILED,
                       brought=brought, backup=str(backup))


def brought_tables(conn: sqlite3.Connection) -> set[str]:
    """「表を持ってくる」で足した表の名前(一覧の表の選び欄の「足」の印)。"""
    try:
        rows = conn.execute(f"SELECT 表 FROM {source_db.quote_identifier(REGISTRY)}").fetchall()
    except sqlite3.Error:
        return set()
    return {str(r[0]) for r in rows}


# ------------------------------------------------------------------
# もうある表の中身を、Access の最新に入れ替える
# ------------------------------------------------------------------
def _main_table(dest: Path) -> str:
    try:
        rows = source_db.read_query(
            dest, f"SELECT {source_db.quote_identifier(config.PRESET)} AS v"
                  f" FROM {source_db.quote_identifier(config.SET_TABLE)}"
                  f" WHERE {source_db.quote_identifier(config.ITEM_NAME)} = ?",
            [config.GS_MAIN_TABLE])
    except (source_db.SourceError, sqlite3.Error):
        return ""
    return str(rows[0]["v"] or "") if rows else ""


def _tool_owned(dest: Path) -> dict[str, str]:
    """**Web 版がふだん書き込む表。** Access から入れ替えない。

    これらは本体(.sqlite3)のほうが新しい ── 一覧の新規・修正・削除と履歴が
    書き足されていて、Access には届いていない。Access の中身で入れ替えると消える。
    """
    owned: dict[str, str] = {}
    main = _main_table(dest)
    if main:
        owned[main] = ("Web 版の一覧で登録・修正する表です(メインテーブル)。"
                       "Access の中身で入れ替えると Web 版で登録した行が消えます")
    try:
        names = source_db.list_tables(dest)
    except (source_db.SourceError, sqlite3.Error):
        names = []
    for name in names:
        if name.startswith(config.HISTORY_PREFIX):
            owned[name] = "Web 版が書き込む履歴の表です"
    owned[config.ID_TABLE] = "マスタ管理で直す表です(書き間違えると誰も入れなくなるため)"
    owned[REGISTRY] = "このツールの控えの表です"
    return owned


def _used_columns(dest: Path, table: str) -> list[str]:
    """設定で使っている列。消えると起動時の整合性検査で止まる。

    - その表に `<表>_設定` があれば、その 項目名(メインテーブルの列の決まり)
    - `フィルタ` の列(メインテーブルのとき)
    """
    q = source_db.quote_identifier
    used: list[str] = []
    try:
        names = set(source_db.list_tables(dest))
        conv = table + config.SETTING_SUFFIX
        if conv in names:
            used += [str(r["v"] or "") for r in source_db.read_query(
                dest, f"SELECT {q(config.ITEM_NAME)} AS v FROM {q(conv)}")]
        if table == _main_table(dest) and config.FLT_TABLE in names:
            used += [str(r["v"] or "") for r in source_db.read_query(
                dest, f"SELECT {q(config.FILTER_ITEM)} AS v FROM {q(config.FLT_TABLE)}")]
    except (source_db.SourceError, sqlite3.Error):
        return used
    return [c for c in dict.fromkeys(used) if c]


def _compare_columns(found: Candidate, have: list[str], table: str, dest: Path) -> None:
    """Access の列と本体の列を比べて、足す列・消える列・できない理由を入れる。"""
    found.added_columns = [c for c in found.columns if c not in have]
    found.removed_columns = [c for c in have if c not in found.columns]
    reads = _used_columns(dest, table)
    found.import_loses = [c for c in found.removed_columns if c in reads]
    found.refresh_why = refresh_why(table, found, dest)


def refresh_why(table: str, found: Candidate, dest: Path) -> str:
    """その表を入れ替え(作り直し)られない理由。できるなら空。"""
    owned = _tool_owned(dest).get(table)
    if owned:
        return owned + "。Access の中身では入れ替えません"
    if found.suspect:
        return (f"文字化けの疑いがある行が {found.suspect}行 あります。"
                "Access の中身を確かめてから読み直してください")
    if not found.columns:
        return "Access の表に列がありません"
    return ""


def refresh(source_path: str, tables: list[str]) -> BringResult:
    """もうある表を Access(変換したもの)の最新にする。表ごとに2通り。

    - **入れ替える**(列が同じか、Access で列を足しただけ): Access にだけある
      列を本体の表に足してから、中身を入れ替える
    - **作り直す**(本体にだけある列がある): いまの表を `表_作り直す前_日時` という
      名前で残し、Access の定義で作り直して中身を写す
    """
    src = Path(str(source_path or "").strip())
    if not src.is_file():
        return BringResult(False, f"ファイルが見つかりません: {src}", REFUSE_NO_FILE)
    dest = _dest()
    if dest is None:
        return BringResult(False, _dest_missing_why(), REFUSE_NO_DEST)
    if _same(src, dest):
        return BringResult(False, "選んだのは、いま使っている本体そのものです。", REFUSE_SAME_FILE)
    wanted = [t for t in dict.fromkeys(str(t) for t in tables) if t]
    if not wanted:
        return BringResult(False, "入れ替える表を選んでください。", REFUSE_NOTHING)
    original = src
    try:
        src, _engine = _readable(src)
    except ConvertError as exc:
        return BringResult(False, str(exc), REFUSE_CONVERT)

    available = _by_fold(source_db.list_tables(src))
    existing = _by_fold(source_db.list_tables(dest))
    refusals = []
    actions: dict[str, str] = {}
    named = []
    for table in wanted:
        if _fold(table) not in available:
            refusals.append(f"{table}(選んだファイルに無い)")
        elif _fold(table) not in existing:
            refusals.append(f"{table}(本体に無い。「持ってくる」を使ってください)")
        else:
            table = existing[_fold(table)]
            named.append(table)
            columns = source_db.columns(src, table)
            found = Candidate(name=table, columns=columns, exists=True,
                              suspect=_suspect_rows(src, table, columns))
            _compare_columns(found, source_db.columns(dest, table), table, dest)
            if found.refresh_why:
                refusals.append(f"{table}({found.refresh_why})")
            actions[table] = found.action
    if refusals:
        return BringResult(False, "入れ替えられない表があります: " + "、".join(refusals)
                           + "。何も変えていません。", REFUSE_NOTHING)
    wanted = list(dict.fromkeys(named))

    try:
        backup = backup_copy(dest, "中身を入れ替える前")
    except OSError as exc:
        return BringResult(False, f"書く前の控えを取れませんでした({exc})。"
                                  "何も変えていません。", REFUSE_WRITE_FAILED)
    import_diag.write("=" * 70)
    import_diag.write(f"■ 表を Access の最新にする  {original} → {dest}")
    import_diag.write(f"  書く前の控え: {backup}")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    done: list[tuple[str, int, int]] = []
    kept_old: list[tuple[str, str]] = []
    failed: list[str] = []
    notes: list[str] = []
    with source_db.connect(dest) as dst:
        for table in wanted:
            rebuild = actions[table] == ACTION_REBUILD
            verb = "作り直した" if rebuild else "入れ替えた"
            try:
                if rebuild:
                    before, after, note, old = _rebuild_one(dst, src, table, stamp)
                    kept_old.append((table, old))
                else:
                    before, after, note = _refresh_one(dst, src, table)
                done.append((table, before, after))
                if note:
                    notes.append(f"{table}: {note}")
                import_diag.write(f"  {verb}: {table} {before:,}行 → {after:,}行"
                                  + (f"({note})" if note else ""))
                log.info("表を%s: %s %s→%s行 (%s)", verb, table, before, after, src)
            except (source_db.SourceError, sqlite3.Error) as exc:
                failed.append(f"{table}({exc})")
                import_diag.write(f"  ✕ {'作り直せ' if rebuild else '入れ替えられ'}"
                                  f"ませんでした: {table} ── {exc}")
                log.warning("表を Access の最新にできませんでした: %s: %s", table, exc)

    if failed and not done:
        return BringResult(False, "Access の最新にできませんでした: " + "、".join(failed)
                           + "。本体は変えていません。",
                           REFUSE_WRITE_FAILED, backup=str(backup))
    rebuilt = {t for t, _o in kept_old}
    parts = [f"{t}({b:,}行 → {a:,}行{'・作り直し' if t in rebuilt else ''})"
             for t, b, a in done]
    message = "Access の最新にしました: " + "、".join(parts) + f"。(書いたファイル: {dest})"
    if kept_old:
        message += (" 作り直す前の表は "
                    + "、".join(f"{old}" for _t, old in kept_old)
                    + " という名前で残しています(要らなければ消してください)。")
    if notes:
        message += " " + "。".join(notes) + "。"
    if failed:
        message += " ただし次はできませんでした: " + "、".join(failed)
    return BringResult(not failed, message, "" if not failed else REFUSE_WRITE_FAILED,
                       backup=str(backup), refreshed=done, kept_old=kept_old)


def _column_types(path: Path, table: str) -> dict[str, str]:
    """列の宣言した型(`PRAGMA table_info` の type)。型の無い列は空。"""
    try:
        with source_db._connect(Path(path), read_only=True) as conn:
            with source_db.identifiers_as_utf8(conn):
                rows = conn.execute(
                    f"PRAGMA table_info({source_db.quote_identifier(table)})").fetchall()
        return {r["name"]: str(r["type"] or "") for r in rows}
    except (source_db.SourceError, sqlite3.Error):
        return {}


def _refresh_one(dst: "source_db.SourceConnection", src: Path,
                 table: str) -> tuple[int, int, str]:
    """1つの表を入れ替える。(前の行数, 後の行数, 添える一言)。"""
    q = source_db.quote_identifier
    have = source_db.columns(dst.path, table)
    columns = source_db.columns(src, table)
    added = [c for c in columns if c not in have]
    kinds = _column_types(src, table)
    rows = source_db.read_table(src, table)
    notes = []
    with dst.transaction() as tx:
        before = int(tx.query(f"SELECT COUNT(*) AS n FROM {q(table)}")[0]["n"])
        for column in added:
            kind = kinds.get(column, "")
            tx.execute(f"ALTER TABLE {q(table)} ADD COLUMN {q(column)}"
                       + (f" {kind}" if kind else ""))
        if added:
            notes.append(f"列 {'・'.join(added)} を足しました")
        tx.execute(f"DELETE FROM {q(table)}")
        for row in rows:
            tx.insert(table, {c: row.get(c) for c in columns})
        after = int(tx.query(f"SELECT COUNT(*) AS n FROM {q(table)}")[0]["n"])
    return before, after, "、".join(notes)


def _rebuild_one(dst: "source_db.SourceConnection", src: Path, table: str,
                 stamp: str) -> tuple[int, int, str, str]:
    """1つの表を Access の定義で作り直す。(前の行数, 後の行数, 添える一言, 残した名前)。

    いまの表は `表_作り直す前_日時` という名前に変えて残す(中身もそのまま)。
    その表の索引は外す ── 索引の名前はファイルの中で1つしか使えないため。
    """
    q = source_db.quote_identifier
    create, indexes = _create_sql(src, table)
    columns = source_db.columns(src, table)
    rows = source_db.read_table(src, table)
    old = f"{table}_作り直す前_{stamp}"
    notes = []
    with dst.transaction() as tx:
        if tx.query("SELECT 1 FROM sqlite_master WHERE name = ?", [old]):
            raise source_db.SourceError(f"{old} という表がもうあります")
        before = int(tx.query(f"SELECT COUNT(*) AS n FROM {q(table)}")[0]["n"])
        old_indexes = [r["name"] for r in tx.query(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = ?"
            " AND sql IS NOT NULL", [table])]
        tx.execute(f"ALTER TABLE {q(table)} RENAME TO {q(old)}")
        for name in old_indexes:
            tx.execute(f"DROP INDEX {q(name)}")
        tx.execute(create)
        made = tx.query("SELECT name FROM sqlite_master WHERE type = 'table'"
                        " AND name = ? COLLATE NOCASE", [table])[0]["name"]
        if made != table:
            # Access では大文字・小文字だけ違う名前だった。本体での名前に戻す
            tx.execute(f"ALTER TABLE {q(made)} RENAME TO {q(old + '_作りかけ')}")
            tx.execute(f"ALTER TABLE {q(old + '_作りかけ')} RENAME TO {q(table)}")
        for row in rows:
            tx.insert(table, {c: row.get(c) for c in columns})
        skipped = []
        for name, sql in indexes:
            if tx.query("SELECT 1 FROM sqlite_master WHERE name = ?", [name]):
                skipped.append(name)
                continue
            tx.execute(sql)
        if skipped:
            notes.append(f"同じ名前があるので作らなかった索引: {'・'.join(skipped)}")
        gone = [n for n in old_indexes if n not in {name for name, _sql in indexes}]
        if gone:
            notes.append(f"Access に無い索引 {'・'.join(gone)} は付けていません")
        after = int(tx.query(f"SELECT COUNT(*) AS n FROM {q(table)}")[0]["n"])
    return before, after, "、".join(notes), old


def plan_dict(p: Plan) -> dict[str, Any]:
    return {
        "source": p.source, "dest": p.dest, "dest_note": p.dest_note,
        "ok": p.ok, "message": p.message,
        "converted": p.converted, "converter": p.converter,
        "warn": any(c.suspect for c in p.new_tables),
        "access": is_access(Path(p.source)) if p.source else False,
        "tables": [{"name": c.name, "rows": c.rows, "columns": c.columns,
                    "exists": c.exists, "suspect": c.suspect,
                    "current_rows": c.current_rows, "refresh_why": c.refresh_why,
                    "can_refresh": c.can_refresh, "keeps": c.keeps,
                    "action": c.action, "added_columns": c.added_columns,
                    "removed_columns": c.removed_columns,
                    "import_loses": c.import_loses} for c in p.candidates],
    }


# 【元(python-web-tools)との違い】
# - 書き先: 梱包資材マスタ(`sync_sources.find_material_db`)→ いまの接続先の本体
#   (`shared_db.db_path()`)
# - Access の変換: 変換ツール accdb_converter(pyodbc / access_parser)を別プロセスで
#   → 同梱の bridge(cscript → ADODB → ACE)。変換ツールの場所の設定は無くした
# - 入れ替えない表: 発注・受払・パレット実績など → メインテーブル・H_ 履歴・ID・控えの表
# - PalletMaster の在庫の行を残す処理(KEEPS)は梱包資材マスタ専用なので持ってこない
# - 「取り込みで読む列」→「設定で使っている列」(`<表>_設定` の項目名・フィルタの列)
# - 書いたあとの手元の取り込み直し(`_follow`)は無い(DBビューアは本体を直接読む)
