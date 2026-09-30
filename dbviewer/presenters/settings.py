"""設定画面に出す値(判断はここで済ませ、画面は並べるだけ)

python-web-tools の `presenters/settings.py` の形(Check / Section /
良し悪しの言葉 / タブのバッジ)を流用し、中身を DBビューアのものにした。

面(タブ):
    status   … いまの状態(接続先の DB が読めるか・設定テーブルの整合)
    source   … 接続先(プロファイル・オンライン/オフライン・フォルダ・本体ファイル)
    paths    … 参照・出力・帳票(参照ファイルの起点・出力先・帳票原紙の置き場所)
    behavior … 動作(Access.log・無操作の再ログイン)
    access   … Access 取り込み(.accdb → .sqlite3。Phase 1)
    terminal … この端末(PC 名・ログの場所・版)
"""
from __future__ import annotations

import os
import socket
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import re

from .. import app_config, config, jobs, shared_db, user_settings
from ..logging_utils import get_logger

log = get_logger("presenters.settings")

OK = "ok"
WARN = "warn"
NG = "ng"
INFO = "info"

LEVEL_LABEL = {OK: "問題なし", WARN: "要確認", NG: "足りません", INFO: "情報"}

TABS: tuple[tuple[str, str], ...] = (
    ("status", "いまの状態"),
    ("source", "接続先"),
    ("paths", "参照・出力・帳票"),
    ("behavior", "動作"),
    ("access", "Access 取り込み"),
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
    prog_no: int
    profiles: list[dict[str, Any]]
    online: bool
    server_dir: str
    server_dir_default: str
    server_dir_real: str
    local_dir: str
    local_dir_real: str
    db_file: str
    db_file_default: str
    db_path: str
    report_dir: str
    report_dir_real: str
    export_dir: str
    export_dir_real: str
    ref_dir: str
    ref_dir_real: str
    access_log: bool
    relogin_minutes: int
    accdb_source: str
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
    prog_no = int(user_settings.get(config.KEY_PROG_NO, config.DEFAULT_PROG_NO))
    try:
        prof = config.profile(prog_no)
    except ValueError:
        prof = config.profile(config.DEFAULT_PROG_NO)
        prog_no = prof.prog_no
    online = bool(user_settings.get(config.KEY_ONLINE, True))
    server_dir = str(user_settings.get(config.KEY_SERVER_DIR, "") or "")
    local_dir = str(user_settings.get(config.KEY_LOCAL_DIR, "") or "")
    db_file = str(user_settings.get(config.KEY_DB_FILE, "") or "")
    report_dir = str(user_settings.get(config.KEY_REPORT_DIR, "") or "")
    export_dir = str(user_settings.get(config.KEY_EXPORT_DIR, "") or "")
    ref_dir = str(user_settings.get(config.KEY_REF_DIR, "") or "")

    db_path = shared_db.db_path()
    sections = [_db_section(db_path, prof, online), _terminal_section()]
    return SettingsViewModel(
        sections=sections,
        prog_no=prog_no,
        profiles=[{"prog_no": p.prog_no, "sys_name": p.sys_name,
                   "db_file": p.db_file, "server_dir": p.server_dir}
                  for p in config.PROFILES],
        online=online,
        server_dir=server_dir,
        server_dir_default=prof.server_dir,
        server_dir_real=_real(server_dir or prof.server_dir),
        local_dir=local_dir,
        local_dir_real=_real(local_dir),
        db_file=db_file,
        db_file_default=prof.db_file,
        db_path=str(db_path),
        report_dir=report_dir,
        report_dir_real=_real(report_dir, db_path.parent),
        export_dir=export_dir,
        export_dir_real=_real(export_dir, app_config.local_dir("work")),
        ref_dir=ref_dir,
        ref_dir_real=_real(ref_dir, db_path.parent),
        access_log=bool(user_settings.get(config.KEY_ACCESS_LOG, True)),
        relogin_minutes=int(user_settings.get(config.KEY_RELOGIN_MIN, 0) or 0),
        accdb_source=str(user_settings.get(config.KEY_ACCDB_SOURCE, "") or ""),
        terminal=_terminal_values(),
    )


def _real(text: str, base: Optional[Path] = None) -> str:
    """相対や空のときだけ、実際に見に行く先を出す。同じなら空。"""
    t = (text or "").strip()
    if not t:
        return str(base) if base is not None else ""
    if config.is_relative_setting(t):
        return str(config.resolve_dir(t, base))
    return ""


FIX_SOURCE = ("接続先を直す", "source")


def _db_section(db_path: Path, prof: config.Profile, online: bool) -> Section:
    section = Section(title="業務DB(接続先)", mark="Ｄ", action=FIX_SOURCE)
    section.checks.append(Check("システム", f"{prof.prog_no}: {prof.sys_name}", INFO))
    section.checks.append(Check("接続モード", "オンライン(共有フォルダ)" if online else "オフライン(手元のフォルダ)", INFO))
    if not db_path.is_file():
        section.checks.append(Check(
            "本体ファイル", str(db_path), NG,
            "アクセス許可がないか、ファイルが存在しないため参照できません。"
            "接続先のフォルダと本体ファイル名を確かめてください。"
            "Access(.accdb)からまだ変換していないなら「Access 取り込み」で変換します。"))
        return section
    try:
        st = db_path.stat()
        with shared_db.connect(db_path, read_only=True) as conn:
            names = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        section.checks.append(Check("本体ファイル", str(db_path), OK,
                                    f"{st.st_size:,} バイト / 表 {len(names)}"))
        _required_tables_check(section, names)
    except Exception as exc:                          # noqa: BLE001 - 理由を画面に出す
        section.checks.append(Check("本体ファイル", str(db_path), NG, f"開けません: {exc}"))
    return section


def _required_tables_check(section: Section, names: list[str]) -> None:
    """VBA `MasterFileCheck` と同じ必須テーブルの有無。"""
    required = (config.SET_TABLE, config.FLT_TABLE, config.TYPE_TABLE, config.ID_TABLE)
    missing = [t for t in required if t not in names]
    if missing:
        section.checks.append(Check("必須テーブル", "足りません", NG,
                                    "無い表: " + "、".join(missing)))
    else:
        section.checks.append(Check("必須テーブル", "そろっています", OK,
                                    "、".join(required)))


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
        "prog_no": view.prog_no,
        "profiles": view.profiles,
        "online": view.online,
        "server_dir": view.server_dir,
        "server_dir_default": view.server_dir_default,
        "server_dir_real": view.server_dir_real,
        "local_dir": view.local_dir,
        "local_dir_real": view.local_dir_real,
        "db_file": view.db_file,
        "db_file_default": view.db_file_default,
        "db_path": view.db_path,
        "report_dir": view.report_dir,
        "report_dir_real": view.report_dir_real,
        "export_dir": view.export_dir,
        "export_dir_real": view.export_dir_real,
        "ref_dir": view.ref_dir,
        "ref_dir_real": view.ref_dir_real,
        "access_log": view.access_log,
        "relogin_minutes": view.relogin_minutes,
        "accdb_source": view.accdb_source,
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
    "server_dir": config.KEY_SERVER_DIR,
    "local_dir": config.KEY_LOCAL_DIR,
    "db_file": config.KEY_DB_FILE,
    "report_dir": config.KEY_REPORT_DIR,
    "export_dir": config.KEY_EXPORT_DIR,
    "ref_dir": config.KEY_REF_DIR,
    "accdb_source": config.KEY_ACCDB_SOURCE,
}


def save(body: dict[str, Any]) -> SaveResult:
    """本文に入っている項目だけを保存する。"""
    if "prog_no" in body:
        try:
            prof = config.profile(int(body["prog_no"]))
        except (TypeError, ValueError):
            return SaveResult(False, "プログラム番号設定ミスです。再度番号を設定し直してください。",
                              REFUSE_BAD_INPUT, "prog_no")
        user_settings.save(config.KEY_PROG_NO, prof.prog_no)
    if "online" in body:
        user_settings.save(config.KEY_ONLINE, bool(body["online"]))
    for key, setting in _TEXT_KEYS.items():
        if key in body:
            text = str(body[key] or "").strip()
            if key == "db_file" and text and ("\\" in text or "/" in text):
                return SaveResult(False, "本体ファイルはファイル名だけを入れてください(フォルダは接続先で)。",
                                  REFUSE_BAD_INPUT, key)
            user_settings.save(setting, text)
    if "access_log" in body:
        user_settings.save(config.KEY_ACCESS_LOG, bool(body["access_log"]))
    if "relogin_minutes" in body:
        try:
            minutes = max(0, int(body["relogin_minutes"] or 0))
        except (TypeError, ValueError):
            return SaveResult(False, "無操作の分数は整数で入れてください。", REFUSE_BAD_INPUT,
                              "relogin_minutes")
        user_settings.save(config.KEY_RELOGIN_MIN, minutes)
    return SaveResult(True, "設定を保存しました。")


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


def start_accdb_import(source: str = "") -> tuple[Optional[jobs.Job], SaveResult]:
    """`.accdb` → いまの接続先の `.sqlite3`。走らせたら `Job` を返す。"""
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
    target = shared_db.db_path()

    def work(progress):
        return importer.import_accdb(src, target, progress=progress)

    try:
        job = jobs.get_registry().start("accdb_import", "Access 取り込み", work)
    except jobs.JobBusy as exc:
        return None, SaveResult(False, f"{exc.running.label}を実行中です。終わってからもう一度押してください。",
                                REFUSE_BUSY)
    return job, SaveResult(True, f"取り込みを始めました: {src.name} → {target}")
