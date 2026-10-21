"""設定画面に出す値(判断はここで済ませ、画面は並べるだけ)

python-web-tools の `presenters/settings.py` の形(Check / Section /
良し悪しの言葉 / タブのバッジ)を流用し、中身を DBビューアのものにした。

面(タブ):
    status   … いまの状態(接続先の DB が読めるか・設定テーブルの整合)
    source   … 接続先(見る DB ファイル 1 つ。0.19.0)
    paths    … 参照・出力・帳票(参照ファイルの起点・出力先・帳票原紙の置き場所)
    behavior … 動作(Access.log・無操作の再ログイン)
    access   … Access 取り込み(.accdb → .sqlite3。Phase 1)
    log      … 記録(置き場所・残す日数・最近の記録・記録番号の前後 = なぜなぜ分析)
    terminal … この端末(PC 名・ログの場所・版)
"""
from __future__ import annotations

import os
import socket
from contextlib import closing
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import re

from .. import (app_config, config, jobs, logging_utils, settings_tables, shared_db, source_db, table_browse,
               trace, user_settings)
from ..logging_utils import get_logger

log = get_logger("presenters.settings")

OK = "ok"
WARN = "warn"
NG = "ng"
INFO = "info"

LEVEL_LABEL = {OK: "問題なし", WARN: "要確認", NG: "足りません", INFO: "情報"}

TABS: tuple[tuple[str, str], ...] = (
    ("status", "いまの状態"),
    ("easy", "かんたん設定"),
    ("source", "接続先"),
    ("paths", "参照・出力"),
    ("behavior", "動作"),
    ("access", "Access 取り込み"),
    ("log", "ログ"),
    ("dist", "配布設定"),
    ("terminal", "この端末"),
)
TAB_KEYS = frozenset(k for k, _ in TABS)
DEFAULT_TAB = "status"

_RANK = {INFO: 0, OK: 1, WARN: 2, NG: 3}


@dataclass
class Check:
    label: str
    value: str
    level: str = INFO
    detail: str = ""


@dataclass
class Section:
    title: str
    checks: list[Check] = field(default_factory=list)
    mark: str = "・"
    action: tuple[str, str] = ("", "")
    label_override: str = ""

    @property
    def level(self) -> str:
        worst = INFO
        for c in self.checks:
            if _RANK[c.level] > _RANK[worst]:
                worst = c.level
        return worst

    @property
    def badge_tone(self) -> str:
        return self.level

    def label(self) -> str:
        return self.label_override or LEVEL_LABEL[self.level]


@dataclass
class SettingsViewModel:
    sections: list[Section]
    db_path_text: str            # 設定に入れた文字(相対も可)
    db_path: str                 # 実際に開く場所(決めていなければ "")
    export_dir: str
    export_dir_real: str
    export_dir_state: dict[str, str]
    export_format: str
    export_encoding: str
    can_open: bool
    ref_dir: str
    ref_dir_real: str
    ref_dir_state: dict[str, str]
    ref_uses: list[dict[str, str]]
    report: dict[str, Any]
    access_log: bool
    relogin_minutes: int
    accdb_source: str
    accdb_target: str
    log: dict[str, Any]
    terminal: dict[str, str]

    @property
    def level(self) -> str:
        worst = INFO
        for s in self.sections:
            if _RANK[s.level] > _RANK[worst]:
                worst = s.level
        return worst


# ------------------------------------------------------------------
def build(conn: Optional[sqlite3.Connection] = None) -> SettingsViewModel:
    db_text = shared_db.configured()
    export_dir = str(user_settings.get(config.KEY_EXPORT_DIR, "") or "")
    ref_dir = str(user_settings.get(config.KEY_REF_DIR, "") or "")

    db_path = shared_db.db_path()
    sections = [_db_section(db_path if db_text else None), _setup_section(db_path), _terminal_section()]
    return SettingsViewModel(
        sections=sections,
        db_path_text=db_text,
        db_path=str(db_path) if db_text else "",
        export_dir=export_dir,
        export_dir_real=str(table_browse.export_dir()),
        export_dir_state=_folder_state(table_browse.export_dir(), create_later=True),
        export_format=_choice(config.KEY_EXPORT_FORMAT, table_browse.FORMATS, table_browse.DEFAULT_FORMAT),
        export_encoding=_choice(config.KEY_EXPORT_ENCODING, table_browse.ENCODINGS, table_browse.DEFAULT_ENCODING),
        can_open=os.name == "nt",
        ref_dir=ref_dir,
        ref_dir_real=str(_ref_base(ref_dir)),
        ref_dir_state=_folder_state(_ref_base(ref_dir)),
        ref_uses=_ref_uses(db_path),
        report=_report_info(db_path),
        access_log=bool(user_settings.get(config.KEY_ACCESS_LOG, True)),
        relogin_minutes=int(user_settings.get(config.KEY_RELOGIN_MIN, 0) or 0),
        accdb_source=str(user_settings.get(config.KEY_ACCDB_SOURCE, "") or ""),
        accdb_target=str(user_settings.get(config.KEY_ACCDB_TARGET, "") or ""),
        log=log_state(),
        terminal=_terminal_values(),
    )


def _choice(key: str, allowed, default: str) -> str:
    v = str(user_settings.get(key, "") or "")
    return v if v in allowed else default


def _ref_base(ref_dir: str) -> Path:
    """参照形式の相対パスの起点(空なら DB ファイルのあるフォルダ)。"""
    server = shared_db.server_dir()
    return config.resolve_dir(ref_dir, server) if ref_dir.strip() else server


def _folder_state(path: Path, *, create_later: bool = False) -> dict[str, str]:
    """フォルダがあるか・書けるか。画面に一言で出す。"""
    try:
        if path.is_dir():
            return ({"level": OK, "text": "あります(書けます)"} if os.access(path, os.W_OK)
                    else {"level": WARN, "text": "ありますが、書き込めません"})
        if create_later:
            return {"level": INFO, "text": "まだ無い(最初の保存で作ります)"}
        return {"level": WARN, "text": "見つかりません"}
    except OSError as exc:
        return {"level": WARN, "text": f"確かめられません({exc})"}


_REF_KINDS = ("PDF", "ICON", "TXT", "XLS", "ZIP", "CSV", "DOCX")


def _ref_uses(db_path: Path) -> list[dict[str, str]]:
    """メインテーブルの `_設定`「参照形式」でファイルを開く列(PDF・ICON など)。"""
    try:
        conn = shared_db.connect(db_path)
    except Exception:                                  # noqa: BLE001 - 無ければ空
        return []
    try:
        general = settings_tables.load_general(conn)
        if not general.main_table:
            return []
        conv = settings_tables.load_conv(conn, general.conv_table)
    except Exception:                                  # noqa: BLE001
        return []
    finally:
        conn.close()
    out = []
    for r in conv.rows:
        kind = r.dsp_type.split("/")[0].upper() if r.dsp_type else ""
        if kind in _REF_KINDS:
            parts = r.dsp_type.split("/")
            where = ("相対: " + parts[2]) if len(parts) >= 3 and parts[2] else (
                "絶対: " + parts[1] if len(parts) >= 2 and parts[1] else "アプリのフォルダ")
            out.append({"item": r.item, "kind": kind, "where": where})
    return out


def _report_info(db_path: Path) -> dict[str, Any]:
    """全般設定「帳票」の読み方。Excel の原紙は使わない。"""
    from .. import reports
    try:
        conn = shared_db.connect(db_path)
        try:
            text = settings_tables.load_general(conn).report_str
        finally:
            conn.close()
    except Exception:                                  # noqa: BLE001
        text = ""
    if not text:
        return {"setting": "", "proc": "", "level": INFO,
                "text": "全般設定「帳票」が空なので、一覧に「帳票」ボタンは出ません(「印刷」はいつでも使えます)。"}
    try:
        spec = reports.parse(text)
        reports.builder_for(spec.proc)
    except reports.ReportError as exc:
        return {"setting": text, "proc": "", "level": WARN, "text": str(exc)}
    template = "/".join(p for p in (spec.folder, spec.file, spec.sheet) if p)
    return {"setting": text, "proc": spec.proc, "level": OK,
            "text": f"帳票 {spec.proc} を印刷用の画面で出します。"
                    + (f"原紙({template})は使いません。" if template else "")}


FIX_SOURCE = ("接続先を直す", "source")


def _db_section(db_path: Optional[Path]) -> Section:
    section = Section(title="業務DB(接続先)", mark="Ｄ", action=FIX_SOURCE)
    if db_path is None:
        section.checks.append(Check("DB ファイル", "(決めていません)", NG, shared_db.MSG_NOT_SET))
        return section
    if not db_path.is_file():
        section.checks.append(Check(
            "DB ファイル", str(db_path), NG,
            "アクセス許可がないか、ファイルが存在しないため参照できません。"
            "接続先の DB ファイルのパスを確かめてください。"
            "Access(.accdb)からまだ変換していないなら「Access 取り込み」で変換します。"))
        return section
    try:
        st = db_path.stat()
        with closing(shared_db.connect(db_path, read_only=True)) as conn:
            names = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        section.checks.append(Check("DB ファイル", str(db_path), OK,
                                    f"{st.st_size:,} バイト / 表 {len(names)}"))
        _required_tables_check(section, names)
    except Exception as exc:                          # noqa: BLE001 - 理由を画面に出す
        section.checks.append(Check("DB ファイル", str(db_path), NG, f"開けません: {exc}"))
    return section


def _required_tables_check(section: Section, names: list[str]) -> None:
    """設定の表(全般設定・ID)の有無。0.8.0 から無くてよい(表は一覧で選ぶ・ID が無ければ誰でも直せる)。"""
    wanted = (config.SET_TABLE, config.ID_TABLE)
    missing = [t for t in wanted if t not in names]
    if missing:
        section.checks.append(Check("設定の表", "無くても使えます", INFO,
                                    "無い表: " + "、".join(missing) + "(どの表も一覧で選んで見て直せます)"))
    else:
        section.checks.append(Check("設定の表", "そろっています", OK, "、".join(wanted)))


FIX_EASY = ("かんたん設定で直す", "easy")


def _setup_section(db_path: Path) -> Section:
    """VBA 起動時の整合性検査(CheckColumns / MasterFileCheck / formatCheck)と同じ判定。"""
    from .. import settings_tables

    section = Section(title="一覧の表と列の設定", mark="設", action=FIX_EASY)
    if not db_path.is_file():
        section.checks.append(Check("検査", "DB ファイルが無いので確かめられません", INFO))
        return section
    try:
        with closing(shared_db.connect(db_path, read_only=True)) as conn:
            setup = settings_tables.load_setup(conn)
    except settings_tables.SetupError as exc:
        section.checks.append(Check("表示する表", "ありません", NG, str(exc)))
        return section
    except Exception as exc:                          # noqa: BLE001
        section.checks.append(Check("検査", "読めません", NG, str(exc)))
        return section
    g = setup.general
    section.checks.append(Check("一覧の表", g.main_table, OK,
                                "一覧の上で選んだ表(この PC で覚える)" if setup.auto_main
                                else "全般設定の「メインテーブル名」"))
    section.checks.append(Check(
        "列の設定", "かんたん設定(表示・入力・印刷)",
        INFO if setup.auto_conv else OK, f"本体 {len(setup.main_columns)} 列"))
    section.checks.append(_rights_check())
    for note in setup.notices:
        if "自動" in note and setup.auto_main and note.startswith("表示する表"):
            continue
        section.checks.append(Check("お知らせ", "", WARN, note))
    if setup.has_general:
        section.checks.append(Check("初期表示", g.init_view or "(空 = 行数制限なし)", INFO))
        section.checks.append(Check("初期フィルタ", g.init_filter or "(なし)", INFO))
        section.checks.append(Check("帳票", g.report_str or "(なし)", INFO))
        section.checks.append(Check("テーブル閲覧", g.browse_str or "(空 = 全部の表)", INFO))
    if setup.column_problems:
        # VBA 版の決まり(マスタの 2 列目 = 表名)。Web 版は合っていなくても動く(選ぶ窓は 2 列目を使う)
        section.checks.append(Check("CheckColumns(VBA 版の決まり)", "合っていない表があります", INFO,
                                    "\n".join(setup.column_problems)))
    if not setup.auto_conv and not setup.format_ok:
        section.checks.append(Check("formatCheck(_設定 の 11 列)", "要確認", WARN,
                                    "ﾃﾞｰﾀﾃｰﾌﾞﾙのﾌｫｰﾏｯﾄに問題があります。最新の書式になっているか確認してください。"))
    return section


def _open_errors() -> int:
    from .. import why_sheet
    try:
        return why_sheet.open_errors()
    except Exception:                                  # noqa: BLE001 - 数えられなくても画面は出す
        return 0


def log_state() -> dict[str, Any]:
    """記録の置き場所・残す日数・今日の問題の数。"""
    text = str(user_settings.get(config.KEY_LOG_DIR, "") or "").strip()
    wanted = logging_utils.resolve_log_dir(text)
    actual = config.LOG_DIR
    state = _folder_state(actual, create_later=True)
    problem = ""
    if wanted != actual:
        problem = f"設定の出力先({wanted})に書けないため、この PC の既定の場所に書いています(5 分ごとに試し直します)。"
        state = {"level": WARN, "text": "既定の場所に書いています"}
    try:
        keep = max(0, int(user_settings.get(config.KEY_LOG_KEEP_DAYS, config.DEFAULT_LOG_KEEP_DAYS)))
    except (TypeError, ValueError):
        keep = config.DEFAULT_LOG_KEEP_DAYS
    today = trace.read(1, only_problems=True, limit=2000)
    return {
        "dir": text,
        "dir_real": str(actual),
        "dir_default": str(config.DEFAULT_LOG_DIR),
        "dir_state": state,
        "problem": problem,
        "keep_days": keep,
        "pc": trace.pc_name(),
        "file": trace.file_for().name,
        "errors_today": sum(1 for r in today if r.get("lv") == "ERROR"),
        "warns_today": sum(1 for r in today if r.get("lv") == "WARN"),
        "open_errors": _open_errors(),
        "open_days": 2,
        "can_open": os.name == "nt",
    }


def _terminal_values() -> dict[str, str]:
    return {
        "pc_name": socket.gethostname(),
        "user": os.environ.get("USERNAME") or os.environ.get("USER") or "",
        "version": app_config.version_label(),
        "app_root": str(app_config.APP_ROOT),
        "local_root": str(app_config.local_root()),
        "log_dir": str(config.LOG_DIR),
        "config_path": str(config.USER_CONFIG_PATH),
    }


def _terminal_section() -> Section:
    t = _terminal_values()
    section = Section(title="この端末", mark="端")
    section.checks.append(Check("PC 名", t["pc_name"], INFO))
    section.checks.append(Check("ログイン ID(OS)", t["user"] or "(不明)", INFO))
    section.checks.append(Check("版", t["version"], INFO, t["app_root"]))
    section.checks.append(Check("ローカル領域", t["local_root"], INFO, "ログ: " + t["log_dir"]))
    problem = app_config.version_problem()
    if problem:
        section.checks.append(Check("config/app.json", "要確認", WARN, problem))
    if app_config.load_error():
        section.checks.append(Check("config/app.json", "既定値で動作", WARN, app_config.load_error()))
    return section


# ------------------------------------------------------------------
def tab_badges(view: SettingsViewModel) -> dict[str, dict[str, str]]:
    """開いていない面の問題を隠さない。"""
    out: dict[str, dict[str, str]] = {}
    lv = view.level
    if lv in (WARN, NG):
        out["status"] = {"level": lv, "text": LEVEL_LABEL[lv]}
        out["source"] = {"level": lv, "text": LEVEL_LABEL[lv]}
    # まだ対策済にしていないエラー(この 2 日)。開かなくても気づけるように
    from .. import why_sheet
    open_errors = why_sheet.open_errors()
    if view.log.get("problem"):
        out["log"] = {"level": WARN, "text": "出力先"}
    elif open_errors:
        out["log"] = {"level": WARN, "text": f"エラー{open_errors}件"}
    return out


def to_dict(view: SettingsViewModel) -> dict[str, Any]:
    return {
        "level": view.level,
        "level_label": LEVEL_LABEL,
        "sections": [{
            "title": s.title, "mark": s.mark, "level": s.level,
            "badge_tone": s.badge_tone, "label": s.label(),
            "action": {"label": s.action[0], "tab": s.action[1]} if s.action[0] else None,
            "checks": [{"label": c.label, "value": c.value, "level": c.level,
                        "detail": c.detail} for c in s.checks],
        } for s in view.sections],
        "db_path_text": view.db_path_text,
        "db_path": view.db_path,
        "export_dir": view.export_dir,
        "export_dir_real": view.export_dir_real,
        "export_dir_state": view.export_dir_state,
        "export_format": view.export_format,
        "export_encoding": view.export_encoding,
        "encodings": table_browse.ENCODINGS,
        "can_open": view.can_open,
        "ref_dir": view.ref_dir,
        "ref_dir_real": view.ref_dir_real,
        "ref_dir_state": view.ref_dir_state,
        "ref_uses": view.ref_uses,
        "report": view.report,
        "access_log": view.access_log,
        "relogin_minutes": view.relogin_minutes,
        "accdb_source": view.accdb_source,
        "accdb_target": view.accdb_target,
        "log": view.log,
        "terminal": view.terminal,
        "tab_badges": tab_badges(view),
    }


# ------------------------------------------------------------------
@dataclass
class SaveResult:
    ok: bool
    message: str = ""
    reason: str = ""
    field: str = ""


REFUSE_BAD_INPUT = "bad_input"

# 本文の鍵 → 利用者設定の鍵。**ここに無いものは保存しない**
_TEXT_KEYS = {
    "export_dir": config.KEY_EXPORT_DIR,
    "ref_dir": config.KEY_REF_DIR,
    "accdb_source": config.KEY_ACCDB_SOURCE,
}




def _rights_check() -> "Check":
    """直せる人: 設定ファイルの「アクセス権限」(0.22.0)。"""
    from .. import access_rights, auth
    s = auth.current()
    me = f"この PC は {s.label}" if s else ""
    o = access_rights.overview()
    if not o["found"]:
        return Check("直せる人", "設定ファイルのアクセス権限を読めません", WARN, f"{o['path']}: {o['problem']} {me}")
    if not o["rows"]:
        return Check("直せる人", "誰でも(アクセス権限の表に行が無い)", INFO,
                     "かんたん設定 → 権限 で行を足すと 編集可 / 閲覧のみ が効きます")
    return Check("直せる人", f"{access_rights.EDIT} の行の人・PC({len(o['edit_rows'])} 行)", OK,
                 f"{o['path']} {me}")


def save(body: dict[str, Any]) -> SaveResult:
    """本文に入っている項目だけを保存する。"""
    message = "設定を保存しました。"
    if "db_path" in body:
        result = _save_db_path(str(body["db_path"] or ""))
        if not result.ok:
            return result
        message = result.message
    for key, setting in _TEXT_KEYS.items():
        if key in body:
            user_settings.save(setting, str(body[key] or "").strip())
    if "export_format" in body:
        fmt = str(body["export_format"] or "")
        if fmt not in table_browse.FORMATS:
            return SaveResult(False, "形式は csv / tsv / xlsx のどれかにしてください。", REFUSE_BAD_INPUT, "export_format")
        user_settings.save(config.KEY_EXPORT_FORMAT, fmt)
    if "export_encoding" in body:
        enc = str(body["export_encoding"] or "")
        if enc not in table_browse.ENCODINGS:
            return SaveResult(False, "文字コードの指定が正しくありません。", REFUSE_BAD_INPUT, "export_encoding")
        user_settings.save(config.KEY_EXPORT_ENCODING, enc)
    if "access_log" in body:
        user_settings.save(config.KEY_ACCESS_LOG, bool(body["access_log"]))
    if "log_keep_days" in body:
        try:
            keep = int(body["log_keep_days"])
        except (TypeError, ValueError):
            keep = -1
        if not 0 <= keep <= MAX_LOG_KEEP_DAYS:
            return SaveResult(False, f"残す日数は 0〜{MAX_LOG_KEEP_DAYS} の整数で入れてください(0 = 消さない)。",
                              REFUSE_BAD_INPUT, "log_keep_days")
        user_settings.save(config.KEY_LOG_KEEP_DAYS, keep)
    if "log_dir" in body:
        result = _save_log_dir(str(body["log_dir"] or "").strip())
        if not result.ok:
            return result
    if "relogin_minutes" in body:
        try:
            minutes = max(0, int(body["relogin_minutes"] or 0))
        except (TypeError, ValueError):
            return SaveResult(False, "無操作の分数は整数で入れてください。", REFUSE_BAD_INPUT,
                              "relogin_minutes")
        user_settings.save(config.KEY_RELOGIN_MIN, minutes)
    return SaveResult(True, message)


MAX_LOG_KEEP_DAYS = 3650


def _save_db_path(text: str) -> SaveResult:
    """見る DB ファイル(フルパス)を決める。**開ける .sqlite3 のときだけ**保存する。空にすると「決めていない」。"""
    from .. import db_files
    text = text.strip().strip('"')
    if not text:
        user_settings.save(config.KEY_DB_PATH, "")
        return SaveResult(True, "DB ファイルを空にしました(一覧には何も出ません)。")
    path = config.resolve_dir(text)
    if path.is_dir():
        return SaveResult(False, f"フォルダではなく、DB ファイルを選んでください: {path}", REFUSE_BAD_INPUT, "db_path")
    if path.suffix.lower() in (".accdb", ".mdb"):
        return SaveResult(False, "Access のファイルは直接は開けません。「Access 取り込み」で .sqlite3 にしてから選んでください。",
                          REFUSE_BAD_INPUT, "db_path")
    if path.suffix.lower() not in source_db.SUFFIXES:
        return SaveResult(False, f"開けるのは {' / '.join(source_db.SUFFIXES)} のファイルです: {path.name}",
                          REFUSE_BAD_INPUT, "db_path")
    if not path.is_file():
        return SaveResult(False, f"アクセス許可がないか、ファイルが存在しないため参照できません。\n{path}",
                          REFUSE_BAD_INPUT, "db_path")
    info = db_files.inspect_sqlite(path)
    if info["kind"] != db_files.KIND_OPEN:
        return SaveResult(False, f"{path.name} は開けません: {info['note']}", REFUSE_BAD_INPUT, "db_path")
    # 0.22.0: 接続先は設定ファイルの一覧から選ぶ。一覧に無い DB を足すのは 編集可 の人だけ
    from .. import auth, settings_file
    try:
        if settings_file.find_db(text) is None:
            if not auth.ensure().can_edit:
                return SaveResult(False, "接続先に無い DB です。接続先を足すのは 編集可 の人だけです"
                                         "(接続先の一覧から選んでください)。", "level", "db_path")
            settings_file.add_db(text)
    except (sqlite3.Error, OSError) as exc:
        return SaveResult(False, f"設定ファイルを開けないので、接続先に足せません: {exc}", REFUSE_BAD_INPUT, "db_path")
    user_settings.save(config.KEY_DB_PATH, text)
    return SaveResult(True, f"{path.name} を開きます({info['note']})。")


def _save_log_dir(text: str) -> SaveResult:
    """記録の置き場所を変える。**書けるのを確かめてから**(書けない場所は保存しない)。

    移る前の置き場所に「どこへ移したか」を 1 行残す ── 前の記録を見ている人が、
    続きの場所を知れるように。
    """
    target = logging_utils.resolve_log_dir(text)
    before = config.LOG_DIR
    if target == before:
        user_settings.save(config.KEY_LOG_DIR, text)
        return SaveResult(True)
    why = logging_utils.probe_writable(target)
    if why:
        return SaveResult(False, f"記録の置き場所に書き込めません: {target}({why})", REFUSE_BAD_INPUT, "log_dir")
    trace.event("log.moved", f"記録の置き場所を {target} に移します", data={"from": str(before), "to": str(target)})
    why = logging_utils.set_log_dir(target)
    if why:
        return SaveResult(False, f"記録の置き場所に書き込めません: {target}({why})", REFUSE_BAD_INPUT, "log_dir")
    user_settings.save(config.KEY_LOG_DIR, text)
    trace.event("log.moved", f"記録の置き場所を {before} から移しました", data={"from": str(before), "to": str(target)})
    return SaveResult(True)


# ------------------------------------------------------------------
# 長時間処理(Access 取り込み)。形は python-web-tools と同じ
# ------------------------------------------------------------------
JOB_STATE_LABEL = {
    jobs.STATE_RUNNING: "実行中",
    jobs.STATE_DONE: "完了",
    jobs.STATE_FAILED: "失敗",
    jobs.STATE_INTERRUPTED: "中断",
}
JOB_STATE_KIND = {
    jobs.STATE_RUNNING: "run",
    jobs.STATE_DONE: "ok",
    jobs.STATE_FAILED: "ng",
    jobs.STATE_INTERRUPTED: "warn",
}
LANE_LIMIT = 6
_STEP_TAIL = re.compile(r"\s*を?[^\sを]{0,8}?(?:中|しています)[.。…]*\s*$")


def step_title(message: str) -> str:
    trimmed = _STEP_TAIL.sub("", message).strip()
    return trimmed or message.strip()


def step_dict(step: jobs.Step, *, no: int = 1, total: int = 1) -> dict[str, Any]:
    state = "run" if step.running else ("done" if step.ok else "ng")
    return {"no": no, "total": total, "label": step_title(step.label), "state": state,
            "state_label": {"run": "実行中", "done": "完了", "ng": "失敗"}[state],
            "elapsed_sec": round(step.elapsed_sec, 1)}


def lane_steps(steps: list[jobs.Step]) -> list[dict[str, Any]]:
    total = len(steps)
    if total < 2:
        return []
    order = [i for i, s in enumerate(steps) if not s.running and not s.ok]
    order += [i for i, s in enumerate(steps) if s.running]
    order += list(range(total - 1, -1, -1))
    keep: list[int] = []
    for index in order:
        if index in keep:
            continue
        keep.append(index)
        if len(keep) >= LANE_LIMIT:
            break
    keep.sort()
    return [step_dict(steps[i], no=i + 1, total=total) for i in keep]


def job_dict(job: Optional[jobs.Job]) -> Optional[dict[str, Any]]:
    if job is None:
        return None
    return {
        "id": job.id, "kind": job.kind, "label": job.label, "state": job.state,
        "state_label": JOB_STATE_LABEL.get(job.state, job.state),
        "state_kind": JOB_STATE_KIND.get(job.state, "run"),
        "pct": job.pct, "message": job.message, "ok": job.ok,
        "summary": job.summary, "error": job.error,
        "elapsed_sec": round(job.elapsed_sec, 1), "running": job.is_running,
        "steps": lane_steps(job.steps), "steps_total": len(job.steps),
    }


def jobs_dict(registry: Optional[jobs.JobRegistry] = None) -> dict[str, Any]:
    registry = registry or jobs.get_registry()
    running = registry.running()
    return {"running": [job_dict(running)] if running else [],
            "recent": [job_dict(job) for job in registry.recent()],
            "busy": running is not None}


# ------------------------------------------------------------------
# Access 取り込み
# ------------------------------------------------------------------
REFUSE_NO_SOURCE = "no_source"
REFUSE_BUSY = "busy"


def default_target(src: Path) -> Path:
    """変換した .sqlite3 の既定の置き場所: いまの接続先フォルダに、Access と同じ名前で。"""
    return shared_db.server_dir() / (src.stem + config.DB_SUFFIX)


def start_accdb_import(source: str = "", target: str = "") -> tuple[Optional[jobs.Job], SaveResult]:
    """`.accdb` を `.sqlite3` に変換して、指定の場所に保存する(長時間処理)。

    保存先に同じ名前があれば `.bak` に退けてから差し替える。保存先を空にすると、
    いまの接続先フォルダに Access と同じ名前で置く。
    """
    from ..accdb_bridge import importer

    text = (source or user_settings.get(config.KEY_ACCDB_SOURCE, "") or "").strip()
    if not text:
        return None, SaveResult(False, "元の .accdb を指定してください。", REFUSE_NO_SOURCE, "accdb_source")
    if source:
        user_settings.save(config.KEY_ACCDB_SOURCE, text)
    src = config.resolve_dir(text)
    if not src.is_file():
        return None, SaveResult(False, f"アクセス許可がないか、ファイルが存在しないため参照できません。\n{src}",
                                REFUSE_BAD_INPUT, "accdb_source")
    if src.suffix.lower() not in (".accdb", ".mdb"):
        return None, SaveResult(False, "元は Access のファイル(.accdb / .mdb)を指定してください。",
                                REFUSE_BAD_INPUT, "accdb_source")
    out_text = (target or "").strip()
    dest = config.resolve_dir(out_text) if out_text else default_target(src)
    if dest.is_dir():                                # フォルダだけ選んだ → Access と同じ名前で
        dest = dest / (src.stem + config.DB_SUFFIX)
    if dest.suffix.lower() not in source_db.SUFFIXES:
        dest = dest.with_suffix(config.DB_SUFFIX)
    if not dest.parent.is_dir():
        return None, SaveResult(False, f"保存先のフォルダがありません: {dest.parent}", REFUSE_BAD_INPUT, "accdb_target")
    user_settings.save(config.KEY_ACCDB_TARGET, str(dest))

    def work(progress):
        return importer.import_accdb(src, dest, progress=progress)

    try:
        job = jobs.get_registry().start("accdb_import", "Access → .sqlite3 変換", work)
    except jobs.JobBusy as exc:
        return None, SaveResult(False, f"{exc.running.label}を実行中です。終わってからもう一度押してください。",
                                REFUSE_BUSY)
    return job, SaveResult(True, f"変換を始めました: {src.name} → {dest}")


def open_db(path_text: str) -> SaveResult:
    """その .sqlite3 を接続先(DB ファイル)にする。"""
    return _save_db_path(path_text)

