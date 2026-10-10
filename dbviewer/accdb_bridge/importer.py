"""Access のダンプ(TSV)を読んで sqlite3 を作る

【2つの入口】
- `import_accdb(accdb, target)` … cscript で `bridge.vbs` を動かして TSV を作り、sqlite3 にする
- `import_tsv_dir(directory, target)` … できあがった TSV フォルダから sqlite3 にする
  (試験用の基準データ `tests/fixtures/realdata` もこの形。Windows 以外でも通る)

【型の対応(ADO DataTypeEnum → SQLite)】
    2,3,16,17,20 (整数)            → INTEGER
    4,5,6,14,131 (浮動小数・通貨)  → REAL   ※通貨は小数 4 桁の実数として持つ
    7,133,134,135 (日付時刻)       → TEXT   'yyyy/mm/dd hh:mm:ss'(時刻 0 なら 'yyyy/mm/dd')
    11 (Yes/No)                    → INTEGER  -1 / 0(Access と同じ)
    130,200,201,202,203 (文字)     → TEXT
    128,204,205 (バイナリ/OLE)     → 取り込まない(NULL)。警告に残す
    その他                          → TEXT

【`管理番号` の索引】
VBA `adoSQL` は `管理番号` で行を特定して UPDATE / DELETE する。値が一意な表には
UNIQUE 索引を張り、重複がある表には普通の索引を張って警告に残す(黙って直さない)。

【差し替えの手順】
`<target>.tmp` に書き切ってから、いまの `<target>` を `<target>.bak` に退け、
`.tmp` を `<target>` に改名する。書きかけの本体を他の端末に読ませない。
"""
from __future__ import annotations

import csv
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional

from ..logging_utils import get_logger

log = get_logger("accdb_bridge")

BRIDGE_VBS = Path(__file__).resolve().parent / "bridge.vbs"
SCHEMA_FILE = "_schema.tsv"
NULL_MARK = "\\N"

# ADO の型番号 → SQLite の宣言型
_INTEGER = {2, 3, 16, 17, 20}
_REAL = {4, 5, 6, 14, 131}
_DATE = {7, 133, 134, 135}
_BOOL = {11}
_BINARY = {128, 204, 205}
_TEXT = {8, 129, 130, 200, 201, 202, 203}

Progress = Callable[[int, str], None]


def sqlite_type(ado_type: int) -> str:
    if ado_type in _INTEGER or ado_type in _BOOL:
        return "INTEGER"
    if ado_type in _REAL:
        return "REAL"
    return "TEXT"


@dataclass
class Column:
    name: str
    ado_type: int = 202
    size: int = 0

    @property
    def sqlite_type(self) -> str:
        return sqlite_type(self.ado_type)

    @property
    def is_binary(self) -> bool:
        return self.ado_type in _BINARY


@dataclass
class Table:
    name: str
    columns: list[Column]
    rows: list[list[Optional[str]]]


@dataclass
class ImportResult:
    target: Path
    tables: list[tuple[str, int]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    elapsed_sec: float = 0.0
    backup: Optional[Path] = None

    ok: bool = True

    def summary(self) -> str:
        parts = [f"{len(self.tables)} 表 / {sum(n for _, n in self.tables):,} 行"]
        if self.warnings:
            parts.append(f"警告 {len(self.warnings)} 件")
        return " / ".join(parts)


class BridgeError(RuntimeError):
    """cscript / bridge.vbs が動かなかった。文言はそのまま画面に出す。"""


# ------------------------------------------------------------------
# 1. Access → TSV(cscript)
# ------------------------------------------------------------------
def cscript_path() -> str:
    """`cscript.exe` の場所。Windows 以外では見つからない。"""
    found = shutil.which("cscript") or shutil.which("cscript.exe")
    if found:
        return found
    windir = os.environ.get("SystemRoot") or os.environ.get("WINDIR")
    if windir:
        cand = Path(windir) / "System32" / "cscript.exe"
        if cand.is_file():
            return str(cand)
    raise BridgeError("cscript.exe が見つかりません。Access の取り込みは Windows でだけ動きます。")


def dump_accdb(accdb: Path, out_dir: Path, *, timeout_sec: float = 600.0) -> Path:
    """bridge.vbs を動かして TSV を作る。戻り値は TSV のフォルダ。"""
    if not accdb.is_file():
        raise BridgeError(f"アクセス許可がないか、ファイルが存在しないため参照できません。\n{accdb}")
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [cscript_path(), "//nologo", str(BRIDGE_VBS), "dump", str(accdb), str(out_dir)]
    log.info("Access をダンプします: %s", " ".join(cmd))
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout_sec,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        raise BridgeError(f"Access の読み取りが {timeout_sec:.0f} 秒で終わりませんでした。") from None
    if proc.returncode != 0:
        err = _decode(proc.stderr) or _decode(proc.stdout)
        raise BridgeError(
            "Access を読めませんでした(cscript の終了コード "
            f"{proc.returncode})。\n{err}\n"
            "Microsoft ACE OLE DB 12.0(Access Database Engine)が入っているか、"
            "ファイルが開ける場所にあるかを確かめてください。")
    return out_dir


def _decode(data: bytes) -> str:
    for enc in ("cp932", "utf-8"):
        try:
            return data.decode(enc).strip()
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace").strip()


# ------------------------------------------------------------------
# 2. TSV → 表
# ------------------------------------------------------------------
_UNESCAPE = {"t": "\t", "n": "\n", "r": "\r", "\\": "\\", "N": None}


def unescape(cell: str) -> Optional[str]:
    """bridge.vbs の書き方(\\t \\n \\r \\\\ \\N)を戻す。"""
    if cell == NULL_MARK:
        return None
    if "\\" not in cell:
        return cell
    out = []
    i = 0
    n = len(cell)
    while i < n:
        ch = cell[i]
        if ch == "\\" and i + 1 < n and cell[i + 1] in ("t", "n", "r", "\\"):
            out.append(_UNESCAPE[cell[i + 1]])
            i += 2
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        return raw.decode("utf-16")
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:].decode("utf-8")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp932")


def _lines(path: Path) -> list[str]:
    """行に切る。**`str.splitlines()` は使わない** ── あちらは NEL(\x85) や
    U+2028 でも切るので、値の中にそういう文字があると行数が狂う
    (実データで起きた)。行の区切りは CRLF / LF だけ。"""
    text = _read_text(path).replace("\r\n", "\n")
    if text.endswith("\n"):
        text = text[:-1]
    return text.split("\n") if text else []


def read_schema(directory: Path) -> dict[str, list[Column]]:
    """`_schema.tsv`(表・列・ADO 型・大きさ)。無ければ空。"""
    path = directory / SCHEMA_FILE
    if not path.is_file():
        return {}
    out: dict[str, list[Column]] = {}
    for line in _lines(path):
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        table, column = parts[0], parts[1]
        try:
            ado = int(parts[2])
        except ValueError:
            ado = 202
        size = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
        out.setdefault(table, []).append(Column(column, ado, size))
    return out


def read_tsv_dir(directory: Path, *, escaped: Optional[bool] = None) -> list[Table]:
    """フォルダの TSV を全部読む。

    `escaped` を省くと `_schema.tsv` がある(= bridge.vbs の出力)ときだけ
    エスケープを戻す。試験用の素の TSV(UTF-8、エスケープ無し)も読める。
    """
    schema = read_schema(directory)
    if escaped is None:
        escaped = bool(schema)
    tables: list[Table] = []
    for path in sorted(directory.glob("*.tsv")):
        if path.name == SCHEMA_FILE:
            continue
        name = path.stem
        lines = _lines(path)
        if not lines:
            continue
        if escaped:
            header = [unescape(h) or "" for h in lines[0].split("\t")]
            rows = [[unescape(c) for c in line.split("\t")] for line in lines[1:]]
        else:
            reader = csv.reader(lines, delimiter="\t", quoting=csv.QUOTE_NONE)
            header = next(reader)
            rows = [list(r) for r in reader]
        typed = {c.name: c for c in schema.get(name, [])}
        columns = [typed.get(h, Column(h)) for h in header]
        # 列数がずれた行は後ろを切る／足す(黙って落とさず警告にする)
        width = len(columns)
        fixed = []
        for r in rows:
            if len(r) < width:
                r = r + [None] * (width - len(r))
            elif len(r) > width:
                r = r[:width]
            fixed.append(r)
        tables.append(Table(name, columns, fixed))
    return tables


# ------------------------------------------------------------------
# 3. 表 → sqlite3
# ------------------------------------------------------------------
def quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _convert(value: Optional[str], column: Column) -> object:
    if value is None:
        return None
    t = column.sqlite_type
    if t == "INTEGER":
        s = value.strip()
        if s == "":
            return None
        try:
            return int(s)
        except ValueError:
            try:
                f = float(s)
            except ValueError:
                return value
            # **切り捨てない。** 整数の列に小数が来たら(地域の設定・型の食い違い)小数のまま入れる。
            # 以前は int(float(s)) で 2502.5 → 2502 と黙って書いていた(python-web-tools と同じ失敗)
            return int(f) if f.is_integer() else f
    if t == "REAL":
        s = value.strip()
        if s == "":
            return None
        try:
            return float(s)
        except ValueError:
            return value
    return value


def write_sqlite(tables: Iterable[Table], target: Path, *,
                 key_item: str = "管理番号",
                 progress: Optional[Progress] = None) -> ImportResult:
    started = time.monotonic()
    result = ImportResult(target=target)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    if tmp.exists():
        tmp.unlink()
    tables = list(tables)
    total = max(1, len(tables))
    conn = sqlite3.connect(str(tmp))
    try:
        conn.execute("PRAGMA journal_mode=DELETE")
        conn.execute("PRAGMA synchronous=FULL")
        for i, table in enumerate(tables):
            if progress:
                progress(int(i * 90 / total), f"{table.name} を書き込み中")
            cols = [c for c in table.columns if not c.is_binary]
            for c in table.columns:
                if c.is_binary:
                    result.warnings.append(f"{table.name}.{c.name}: バイナリ/OLE 列は取り込みません")
            names = [c.name for c in cols]
            dupes = {n for n in names if names.count(n) > 1}
            if dupes:
                result.warnings.append(f"{table.name}: 同名の列があります {sorted(dupes)}")
            decl = ", ".join(f"{quote(c.name)} {c.sqlite_type}" for c in cols)
            conn.execute(f"DROP TABLE IF EXISTS {quote(table.name)}")
            conn.execute(f"CREATE TABLE {quote(table.name)} ({decl})")
            idx = [table.columns.index(c) for c in cols]
            values = [tuple(_convert(row[j], cols[k]) for k, j in enumerate(idx))
                      for row in table.rows]
            marks = ", ".join("?" for _ in cols)
            conn.executemany(
                f"INSERT INTO {quote(table.name)} ({', '.join(quote(n) for n in names)}) VALUES ({marks})",
                values)
            result.tables.append((table.name, len(values)))
            _index_key(conn, table.name, names, key_item, result)
        conn.commit()
    finally:
        conn.close()
    if progress:
        progress(95, "本体ファイルを差し替え中")
    result.backup = _swap_in(tmp, target)
    result.elapsed_sec = time.monotonic() - started
    log.info("sqlite3 を作りました: %s (%s, %.1f秒)", target, result.summary(), result.elapsed_sec)
    for w in result.warnings:
        log.warning("取り込みの警告: %s", w)
    return result


def _index_key(conn: sqlite3.Connection, table: str, names: list[str],
               key_item: str, result: ImportResult) -> None:
    if key_item not in names:
        return
    dup = conn.execute(
        f"SELECT {quote(key_item)}, COUNT(*) FROM {quote(table)} "
        f"GROUP BY {quote(key_item)} HAVING COUNT(*) > 1").fetchall()
    idx_name = quote(f"ix_{table}_{key_item}")
    if dup:
        conn.execute(f"CREATE INDEX {idx_name} ON {quote(table)} ({quote(key_item)})")
        sample = ", ".join(str(r[0]) for r in dup[:5])
        result.warnings.append(
            f"{table}: {key_item} が重複しています({len(dup)} 種: {sample})。"
            "VBA と同じく、この行の修正・削除は同じ番号の行すべてに効きます")
    else:
        conn.execute(f"CREATE UNIQUE INDEX {idx_name} ON {quote(table)} ({quote(key_item)})")


def _swap_in(tmp: Path, target: Path) -> Optional[Path]:
    backup = None
    if target.exists():
        backup = target.with_name(target.name + ".bak")
        if backup.exists():
            backup.unlink()
        os.replace(target, backup)
    os.replace(tmp, target)
    return backup


# ------------------------------------------------------------------
# 入口
# ------------------------------------------------------------------
def import_tsv_dir(directory: Path, target: Path, *,
                   progress: Optional[Progress] = None) -> ImportResult:
    if progress:
        progress(5, "TSV を読み込み中")
    tables = read_tsv_dir(directory)
    if not tables:
        raise BridgeError(f"TSV が見つかりません: {directory}")
    return write_sqlite(tables, target, progress=progress)


def import_accdb(accdb: Path, target: Path, *,
                 progress: Optional[Progress] = None,
                 work_dir: Optional[Path] = None) -> ImportResult:
    """Access → sqlite3。`work_dir` を省くと一時フォルダに TSV を作って消す。"""
    if progress:
        progress(2, "Access を読み取り中(cscript)")
    if work_dir is None:
        with tempfile.TemporaryDirectory(prefix="dbviewer_accdb_") as tmp:
            out = dump_accdb(accdb, Path(tmp) / "dump")
            return import_tsv_dir(out, target, progress=progress)
    out = dump_accdb(accdb, work_dir / "dump")
    return import_tsv_dir(out, target, progress=progress)


def list_tables(accdb: Path) -> list[str]:
    """cscript で表の名前だけを取る。"""
    proc = subprocess.run([cscript_path(), "//nologo", str(BRIDGE_VBS), "tables", str(accdb)],
                          capture_output=True, timeout=120,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if proc.returncode != 0:
        raise BridgeError(_decode(proc.stderr) or "Access の表の一覧を取れませんでした。")
    return [ln for ln in _decode(proc.stdout).splitlines() if ln.strip()]


if __name__ == "__main__":                       # python -m dbviewer.accdb_bridge.importer <src> <target>
    src, dst = Path(sys.argv[1]), Path(sys.argv[2])
    res = (import_tsv_dir if src.is_dir() else import_accdb)(src, dst, progress=lambda p, m: print(p, m))
    print(res.summary())
    for w in res.warnings:
        print("!", w)
