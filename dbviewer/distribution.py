"""配布設定(0.30.0)── 1 台で決めた設定を、配った先の PC でそのまま使う

作りは python-web-tools の `packaging_tool/distribution.py` と同じ。

【なぜ要るのか】
接続先・設定ファイルの場所・参照/出力/記録の置き場所・管理者パスワードは PC ごとの
`user_config.json`(ローカル領域)に入る。配った先で 1 台ずつ設定画面を開いて打ち直すのは手間で、
打ち間違えると**別の DB・別の設定ファイルを見る** PC ができる。

【流れ】
    1. 1 台で起動して設定し、設定 →「配布設定」で書き出す(管理者パスワード)
    2. ツールのフォルダの直下に `配布設定\\` ができ、配るものが全部そこに入る
    3. `scripts\\make_dist.bat` で配布用フォルダを作って配る(`配布設定\\` も入る)
    4. 配った先は起動したとき `配布設定\\` を見つけて読み込む

【`配布設定\\` の中身】

    配布設定\\
      設定.json            設定ファイルの場所・開く DB・参照/出力/記録の置き場所・管理者パスワード(撹拌した値)…
      設定ファイル.json    設定ファイルの中身(接続先・かんたん設定・アクセス権限)。選んだときだけ
      はじめに読む.txt     何が入っているか・配った先で何が起きるか

設定ファイルを共有フォルダに置いていれば「設定ファイルの場所」だけで足りる(みんな同じファイルを見る)。
**この PC の中に置いているときは、配った先からは見えない**ので、中身(`設定ファイル.json`)を入れる。
配った先は、その PC の設定ファイルがまだ無ければ、その中身で作る。

【読み込むときの決まり】**その PC で変えたものは上書きしない。**

    設定             … 無い項目・空の項目は埋める。その PC で入れた値はそのまま
    設定ファイルの中身 … その PC の設定ファイルが無ければ作る。あれば触らない

**新しい配布設定が置かれたら**(前に読んだものと中身が違えば)、前の配布のまま変えていない項目
(と、前の配布のまま変えていないこの PC の中の設定ファイル)は新しい値に入れ替える。
揃えたいときは、設定 →「配布設定」の「読み込み直す」(管理者パスワード)で上書きする。
共有フォルダの設定ファイルは、読み込み直しでも上書きしない(ほかの PC も使っているため)。

【パスワード】書き出す・消す・読み込み直すには管理者パスワードが要る。起動時の読み込みには要らない
(`配布設定\\` を置いたのは、フォルダを配った管理者本人だから)。
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from . import admin_password, app_config, config, settings_file, user_settings
from .logging_utils import get_logger

log = get_logger("distribution")

# `配布設定\\` の置き場所(ツールのフォルダの直下)
DIR = Path(os.environ.get("DBVIEWER_DISTRIBUTION_DIR", str(config.BASE_DIR / "配布設定")))
SETTINGS_NAME = "設定.json"
CONTENTS_NAME = "設定ファイル.json"
README_NAME = "はじめに読む.txt"

FORMAT = 1

# この PC が最後に読み込んだとき
KEY_APPLIED = "distribution_applied"

# 入れられるもの: (鍵, 画面の名前, 既定で入れるか)。鍵は `user_config.json` の鍵そのもの
ITEMS: tuple[tuple[str, str, bool], ...] = (
    (config.KEY_SETTINGS_FILE, "設定ファイルの場所(権限・かんたん設定・接続先)", True),
    (config.KEY_DB_PATH, "開く DB", True),
    (config.KEY_REF_DIR, "参照ファイルの起点", True),
    (config.KEY_EXPORT_DIR, "出力先", True),
    (config.KEY_EXPORT_FORMAT, "出力の形式", True),
    (config.KEY_EXPORT_ENCODING, "出力の文字コード", True),
    (config.KEY_LOG_DIR, "記録(ログ)の置き場所", True),
    (config.KEY_LOG_KEEP_DAYS, "記録を残す日数", True),
    (config.KEY_ACCESS_LOG, "Access.log を書く", True),
    (config.KEY_RELOGIN_MIN, "無操作で再ログイン(分)", True),
    (config.KEY_ACCDB_SOURCE, "Access 取り込みの元(.accdb)", False),
    (config.KEY_ACCDB_TARGET, "Access 取り込みの保存先", False),
    (config.KEY_ADMIN_PASSWORD, "管理者パスワード", True),
)
ITEM_KEYS = frozenset(k for k, _, _ in ITEMS)
ITEM_LABELS = {k: label for k, label, _ in ITEMS}
# 場所の項目(この PC の中を指していたら、書き出すときに知らせる)
PATH_KEYS = frozenset({config.KEY_SETTINGS_FILE, config.KEY_DB_PATH, config.KEY_REF_DIR, config.KEY_EXPORT_DIR,
                       config.KEY_LOG_DIR, config.KEY_ACCDB_SOURCE, config.KEY_ACCDB_TARGET})

CONTENTS = "settings_contents"
CONTENTS_LABEL = "設定ファイルの中身(接続先・かんたん設定・アクセス権限)"

# 設定ファイルの表と列(中身を書くときは、この列だけ受け取る)
TABLES: dict[str, tuple[str, ...]] = {
    settings_file.T_DBS: ("管理番号", "名前", "パス", "備考"),
    settings_file.T_EASY: ("DB", "表", "設定", "更新者", "更新日時"),
    settings_file.T_RIGHTS: ("管理番号", "ログインID", "PC名", "権限", "有効", "備考"),
}

REFUSE_NEED_PASSWORD = "need_password"
REFUSE_BAD_INPUT = "bad_input"
REFUSE_FAILED = "failed"


@dataclass
class Result:
    ok: bool = True
    message: str = ""
    reason: str = ""
    applied: list[str] = field(default_factory=list)
    kept: list[str] = field(default_factory=list)      # その PC で入れた値なので読まなかったもの


def settings_path(base: Optional[Path] = None) -> Path:
    return (base or DIR) / SETTINGS_NAME


def contents_path(base: Optional[Path] = None) -> Path:
    return (base or DIR) / CONTENTS_NAME


def _now() -> str:
    return datetime.now().strftime("%Y/%m/%d %H:%M:%S")


def _digest(data: Any) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


# ------------------------------------------------------------------
# 設定ファイルの中身(JSON にして配る。.sqlite3 は配布フォルダに入れない決まりなので)
# ------------------------------------------------------------------
def dump_contents(p: Optional[Path] = None) -> Optional[dict[str, list[dict]]]:
    """設定ファイルの 3 つの表の行。ファイルが無ければ None。"""
    p = p or settings_file.path()
    if not p.is_file():
        return None
    out: dict[str, list[dict]] = {}
    with closing(sqlite3.connect(f"file:{settings_file._uri(p)}?mode=ro", uri=True,
                                 timeout=settings_file.TIMEOUT_SEC)) as conn:
        have = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table, cols in TABLES.items():
            if table not in have:
                out[table] = []
                continue
            names = [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')]
            use = [c for c in cols if c in names]
            sql = 'SELECT ' + ", ".join(f'"{c}"' for c in use) + f' FROM "{table}" ORDER BY rowid'
            out[table] = [dict(zip(use, row)) for row in conn.execute(sql)]
    return out


def _clean_contents(data: Any) -> Optional[dict[str, list[dict]]]:
    """読んだ中身を、知っている表・列だけにする。形が違えば None。"""
    if not isinstance(data, dict):
        return None
    out: dict[str, list[dict]] = {}
    for table, cols in TABLES.items():
        rows = data.get(table) or []
        if not isinstance(rows, list):
            return None
        out[table] = [{c: r.get(c) for c in cols if c in r} for r in rows if isinstance(r, dict)]
    return out


def write_contents(target: Path, contents: dict[str, list[dict]]) -> None:
    """中身から設定ファイルを作る(あれば置き換える)。隣に作ってから差し替えるので、途中で失敗しても前のものは残る。"""
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".配布から")
    if tmp.exists():
        tmp.unlink()
    with closing(sqlite3.connect(str(tmp))) as conn:
        for sql in settings_file._DDL:
            conn.execute(sql)
        for table, rows in contents.items():
            for row in rows:
                cols = [c for c in TABLES[table] if c in row]
                if not cols:
                    continue
                conn.execute(f'INSERT INTO "{table}" (' + ", ".join(f'"{c}"' for c in cols) + ") VALUES ("
                             + ", ".join("?" for _ in cols) + ")", [row[c] for c in cols])
        conn.commit()
    _replace(tmp, target)
    settings_file._ready.discard(str(target))


def _replace(src: Path, target: Path, *, tries: int = 5) -> None:
    """`os.replace`。Windows で一時的に掴まれていたら待ってやり直す。"""
    for attempt in range(1, tries + 1):
        try:
            os.replace(src, target)
            return
        except PermissionError:
            if attempt == tries:
                raise
            time.sleep(0.2 * attempt)


def _replace_file(target: Path, data: bytes) -> None:
    tmp = target.with_name(target.name + ".書き出し中")
    try:
        tmp.write_bytes(data)
        _replace(tmp, target)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def is_here(p: Path) -> bool:
    """この PC の中だけの場所か(ローカル領域・この人のフォルダ)。共有フォルダ(\\\\PC名\\…)は違う。"""
    text = str(p)
    if text.startswith(("\\\\", "//")):
        return False
    roots = [app_config.local_root()]
    home = os.environ.get("USERPROFILE") or str(Path.home())
    if home:
        roots.append(Path(home))
    for root in roots:
        try:
            Path(p).resolve().relative_to(Path(root).resolve())
            return True
        except (ValueError, OSError):
            continue
    return False


# ------------------------------------------------------------------
# 読む
# ------------------------------------------------------------------
@dataclass
class Bundle:
    settings: dict[str, Any]
    contents: Optional[dict[str, list[dict]]] = None
    created_at: str = ""
    created_on: str = ""

    def fingerprint(self) -> str:
        return _digest({"settings": self.settings, "contents": self.contents})


def read(base: Optional[Path] = None) -> Optional[Bundle]:
    """置いてある配布設定。無い・読めない・形が違うなら None。"""
    base = base or DIR
    if not base.is_dir():
        return None
    settings: dict[str, Any] = {}
    meta: dict[str, Any] = {}
    path = settings_path(base)
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as exc:
            log.warning("配布設定を読めませんでした: %s", exc)
            return None
        if not isinstance(data, dict) or data.get("format") != FORMAT:
            log.warning("配布設定の形が違うため読みません: %s", path)
            return None
        raw = data.get("settings") or {}
        settings = {k: v for k, v in raw.items() if k in ITEM_KEYS}       # 知らない鍵は捨てる
        meta = data
    contents = None
    cpath = contents_path(base)
    if cpath.exists():
        try:
            contents = _clean_contents(json.loads(cpath.read_text(encoding="utf-8-sig")).get("tables"))
        except (OSError, ValueError, AttributeError) as exc:
            log.warning("配布設定の設定ファイルの中身を読めませんでした: %s", exc)
    if not settings and contents is None:
        return None
    return Bundle(settings=settings, contents=contents, created_at=str(meta.get("created_at", "")),
                  created_on=str(meta.get("created_on", "")))


def show(key: str, value: Any) -> str:
    """画面・はじめに読む.txt に出す形。**管理者パスワードの値は出さない。**"""
    if key == config.KEY_ADMIN_PASSWORD:
        return "(設定済み)"
    if isinstance(value, bool):
        return "する" if value else "しない"
    text = "" if value is None else str(value)
    return text if text.strip() else "(既定)"


def _count(contents: Optional[dict[str, list[dict]]]) -> str:
    if not contents:
        return ""
    return " / ".join(f"{t} {len(contents.get(t, []))} 行" for t in TABLES)


def summary() -> dict[str, Any]:
    """設定画面に出す、配布設定のいまと、この PC のいまの値。"""
    bundle = read()
    current = user_settings.load_all()
    applied = user_settings.get(KEY_APPLIED)
    sf = settings_file.path()
    sf_here = is_here(sf)
    items = []
    for key, label, default in ITEMS:
        have = key in current and not _blank(current.get(key))
        value = current.get(key) if have else None
        items.append({"key": key, "label": label, "default": default and have, "set": have,
                      "value": show(key, value) if have else "(既定のまま。入れなくても配った先も既定)",
                      "here": bool(have and key in PATH_KEYS and is_here(Path(str(value))))})
    out: dict[str, Any] = {
        "exists": bundle is not None,
        "path": str(DIR),
        "items": items,
        "contents": {"key": CONTENTS, "label": CONTENTS_LABEL, "default": sf_here and sf.is_file(),
                     "file": str(sf), "here": sf_here, "exists": sf.is_file()},
        "applied_at": applied.get("at", "") if isinstance(applied, dict) else "",
        "bundle": [],
        "created_at": "",
        "created_on": "",
    }
    if bundle is None:
        return out
    rows = [{"label": ITEM_LABELS[k], "value": show(k, v)} for k, v in bundle.settings.items()]
    if bundle.contents is not None:
        rows.append({"label": CONTENTS_LABEL, "value": f"{CONTENTS_NAME}({_count(bundle.contents)})"})
    out.update(bundle=rows, created_at=bundle.created_at, created_on=bundle.created_on)
    return out


# ------------------------------------------------------------------
# 書き出す(配る側)
# ------------------------------------------------------------------
def _authorized(password: str) -> bool:
    """鍵が開いていれば(アクセス権限で開けたまま)そのまま。でなければパスワードで開ける。"""
    if admin_password.session.is_open():
        return True
    return bool(password) and admin_password.session.unlock(str(password)).ok


def export(password: str, items: list[str], with_contents: bool) -> Result:
    """**この PC のいまの設定**を `配布設定\\` に書き出す(前の中身は置き換える)。

    この PC で一度も変えていない項目は入れない ── 配った先も同じ既定で動くので要らない
    (空で上書きしないためにも入れない)。"""
    if not _authorized(password):
        return Result(False, "配布設定を書き出すには管理者パスワードが要ります。", REFUSE_NEED_PASSWORD)
    unknown = [k for k in items if k not in ITEM_KEYS]
    if unknown:
        return Result(False, f"知らない項目です: {', '.join(unknown)}", REFUSE_BAD_INPUT)
    if not items and not with_contents:
        return Result(False, "入れる項目を 1 つ以上選んでください。", REFUSE_BAD_INPUT)

    current = user_settings.load_all()
    settings = {k: current[k] for k in items if k in current and not _blank(current[k])}
    defaults = [ITEM_LABELS[k] for k in items if k not in settings]
    contents = None
    if with_contents:
        try:
            contents = dump_contents()
        except sqlite3.Error as exc:
            return Result(False, f"設定ファイルを読めませんでした: {settings_file.path()}({exc})", REFUSE_FAILED)
        if contents is None:
            return Result(False, f"設定ファイルがまだありません: {settings_file.path()}", REFUSE_BAD_INPUT)
    if not settings and contents is None:
        return Result(False, "書き出せる設定が無いので、フォルダは作っていません("
                      + "・".join(defaults) + " はこの PC で変えていないため既定のままです)。", REFUSE_BAD_INPUT)

    # **フォルダの中へ直接書き、ファイルごとに置き換える**(python-web-tools と同じ。Windows で作った直後の
    # フォルダの付け替えが失敗して、前のものだけ消えることがあったため)。設定.json は最後に書く
    meta = {"format": FORMAT, "created_at": _now(), "created_on": platform.node(), "settings": settings}
    try:
        DIR.mkdir(parents=True, exist_ok=True)
        if contents is not None:
            _replace_file(contents_path(), json.dumps({"format": FORMAT, "tables": contents}, ensure_ascii=False,
                                                      indent=2).encode("utf-8"))
        elif contents_path().exists():
            contents_path().unlink()                               # 今回は配らない
        _replace_file(DIR / README_NAME, _readme(meta, contents).encode("utf-8-sig"))
        _replace_file(settings_path(), json.dumps(meta, ensure_ascii=False, indent=2).encode("utf-8"))
    except OSError as exc:
        log.warning("配布設定を書き出せませんでした: %s", exc)
        return Result(False, f"配布設定を書き出せませんでした: {DIR}({exc})。フォルダに書く権限があるか、"
                             "開いているファイルが無いか確かめてください。", REFUSE_FAILED)
    written = read()                                               # 書いたつもりで無い、を黙って通さない
    if written is None:
        return Result(False, f"配布設定を書いたはずの {DIR} から読み直せませんでした。", REFUSE_FAILED)
    # 書き出した PC は、いまの値がそのまま配布設定の値
    _mark_applied(written, settings, _digest(contents) if contents is not None else None)

    names = [ITEM_LABELS[k] for k in settings] + ([CONTENTS_LABEL] if contents is not None else [])
    message = (f"配布設定を書き出しました({len(names)} 項目): {DIR}。配るときは scripts\\make_dist.bat で"
               "配布用フォルダを作ってください(このフォルダも入ります)。")
    here = [ITEM_LABELS[k] for k in settings if k in PATH_KEYS and is_here(Path(str(settings[k])))]
    if here:
        message += " この PC の中を指している場所があります(配った先に同じ場所が無いと使えません): " + "・".join(here) + "。"
    if defaults:
        message += " 既定のままなので入れていないもの(配った先も既定で動きます): " + "・".join(defaults) + "。"
    log.info("配布設定を書き出しました: %s", ", ".join(names))
    return Result(True, message, applied=names)


def _readme(meta: dict[str, Any], contents: Optional[dict]) -> str:
    lines = ["DBビューア 配布設定", "", f"作成: {meta['created_at']}({meta['created_on']})", "",
             "このフォルダに入っているもの"]
    for key, value in meta["settings"].items():
        lines.append(f"  {ITEM_LABELS[key]}: {show(key, value)}")
    if contents is not None:
        lines.append(f"  {CONTENTS_LABEL}: {CONTENTS_NAME}({_count(contents)})")
    lines += [
        "",
        "配った先で起きること",
        "  起動したときにこのフォルダを読み込みます。",
        "  無い項目・空の項目は埋めます。書き出し直したものなら、前の配布のまま変えていない",
        "  項目も新しい値に入れ替えます。その PC で変えた設定は上書きしません。",
        "  設定ファイルの中身は、その PC の設定ファイルがまだ無ければ、それで作ります。",
        "  揃えたいときは、設定 →「配布設定」→「配布設定を読み込み直す」(管理者パスワード)。",
        "",
    ]
    return "\n".join(lines)


def remove(password: str) -> Result:
    if not _authorized(password):
        return Result(False, "配布設定を消すには管理者パスワードが要ります。", REFUSE_NEED_PASSWORD)
    try:
        if DIR.exists():
            shutil.rmtree(DIR)
    except OSError as exc:
        return Result(False, f"消せませんでした: {exc}", REFUSE_FAILED)
    log.info("配布設定を消しました")
    return Result(True, "配布設定を消しました。この PC の設定はそのままです。")


# ------------------------------------------------------------------
# 読み込む(配られた側)
# ------------------------------------------------------------------
def _applied_record() -> dict[str, Any]:
    raw = user_settings.get(KEY_APPLIED)
    if not isinstance(raw, dict):
        return {}
    return {"at": str(raw.get("at", "")), "fingerprint": str(raw.get("fingerprint", "")),
            "values": dict(raw.get("values") or {}), "contents": str(raw.get("contents", ""))}


def _mark_applied(bundle: Bundle, values: dict, contents_digest: Optional[str], *, touched: bool = True) -> None:
    """読み込んだ(書き出した)ことを覚える。次に**違う**配布設定が来たとき、この PC の値がここと同じなら
    「配布のまま変えていない」とみなして入れ替える。"""
    prev = _applied_record()
    user_settings.save(KEY_APPLIED, {
        "at": _now() if touched else prev.get("at", ""),
        "fingerprint": bundle.fingerprint(),
        "values": {**prev.get("values", {}), **values},
        "contents": contents_digest if contents_digest is not None else prev.get("contents", ""),
    })


def _blank(value: Any) -> bool:
    """空の値(入っていないのと同じ)。`False`・0 は「しない」という値なので空ではない。"""
    return value is None or (isinstance(value, str) and not value.strip())


def _apply(bundle: Bundle, *, overwrite: bool) -> Result:
    result = Result()
    current = user_settings.load_all()
    record = _applied_record()
    renewed = bool(record.get("fingerprint")) and record["fingerprint"] != bundle.fingerprint()
    given: dict[str, Any] = {}
    for key, value in bundle.settings.items():
        have = current.get(key)
        if overwrite or key not in current or _blank(have):
            take = True
        elif have == value:
            given[key] = value                                     # もう同じ値
            continue
        elif renewed and key in record["values"] and have == record["values"][key]:
            take = True                                            # 前の配布のまま変えていない
        else:
            take = False
        if take and user_settings.save(key, value):
            result.applied.append(ITEM_LABELS[key])
            given[key] = value
        elif not take:
            result.kept.append(ITEM_LABELS[key])                   # この PC で入れた値
    contents_digest = None
    if bundle.contents is not None:
        target = settings_file.path()                              # 上で場所を入れたなら、その場所
        want = _digest(bundle.contents)
        try:
            have_digest = _digest(dump_contents(target)) if target.is_file() else ""
        except sqlite3.Error:
            have_digest = "?"
        if have_digest == want:
            contents_digest = want                                 # もう同じ中身
        elif not target.is_file() or (is_here(target) and (
                overwrite or (renewed and have_digest and have_digest == record.get("contents")))):
            try:
                write_contents(target, bundle.contents)
                result.applied.append(CONTENTS_LABEL)
                contents_digest = want
                log.info("配布設定の中身で設定ファイルを作りました: %s", target)
            except (OSError, sqlite3.Error) as exc:
                log.warning("配布設定の中身で設定ファイルを作れませんでした: %s(%s)", target, exc)
        else:
            result.kept.append(CONTENTS_LABEL + ("" if is_here(target) else "(共有フォルダのものは上書きしません)"))
    _mark_applied(bundle, given, contents_digest, touched=bool(result.applied))
    return result


def apply_on_start() -> Result:
    """起動時に呼ぶ。`配布設定\\` があれば、**その PC に無いものだけ**読む。"""
    bundle = read()
    if bundle is None:
        return Result(True, "")
    result = _apply(bundle, overwrite=False)
    if result.applied:
        log.info("配布設定を読み込みました: %s(この PC の値なので読まなかったもの: %s)",
                 ", ".join(result.applied), ", ".join(result.kept) or "なし")
        result.message = "配布設定を読み込みました"
    return result


def reapply(password: str) -> Result:
    """設定画面から。**すでにあるものも上書きして**読み込み直す。"""
    if not _authorized(password):
        return Result(False, "配布設定を読み込み直すには管理者パスワードが要ります。", REFUSE_NEED_PASSWORD)
    bundle = read()
    if bundle is None:
        return Result(False, "配布設定が置かれていません。", REFUSE_BAD_INPUT)
    result = _apply(bundle, overwrite=True)
    result.message = f"配布設定を読み込みました({len(result.applied)} 項目)"
    if result.kept:
        result.message += "。読まなかったもの: " + "・".join(result.kept)
    return result
