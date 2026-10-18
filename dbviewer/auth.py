"""誰が使っているか と、編集可 / 閲覧のみ(0.16.0)

【0.16.0 から】VBA 版から続いていた「ID の表 + パスワード + Level 1〜5」をやめた。
Windows の**ログインID**と**PC名**で、梱包資材マスタの `アクセス権限` 表を引き、
**編集可**(`dbviewer:edit` の行がある)か **閲覧のみ** かを決める(`access_rights`)。パスワードは要らない。
画面を開けば自動で入る(ログインの画面は無い。起動モードが未設定のときだけ先にそれを決める)。

中の決まり(どの操作に何が要るか)は Level の数のまま持つ: 編集可 = 5、閲覧のみ = 1。
閲覧のみは 新規・修正・削除・列の追加・設定の保存 ができない(ダブルクリックは参照で開く)。

【セッション】プロセスに 1 つ(1 台の PC を 1 人が使う前提)。権限は `access_rights.RECHECK_SEC` ごとに
読み直す(取り消しが効くように。読み直しは裏で、画面は待たせない)。設定「無操作で権限を読み直す(分)」が
0 より大きければ、その時間触っていないと次の操作で読み直す。
"""
from __future__ import annotations

import os
import socket
import threading
import time
from dataclasses import dataclass
from typing import Optional

from . import access_log, access_rights, config, user_settings
from .logging_utils import get_logger

log = get_logger("auth")

# VBA `DEVELOPERFLAG`(Option Compare Binary = 大文字小文字を区別)
DEVELOPER_IDS = ("Satoshi-Harada", "satoshi")
DEVELOPER_PCS = ("NLM-NGY-221790", "PCH370")

EDIT_LEVEL = 5
VIEW_LEVEL = 1
MSG_NEED_LOGIN = "起動モード(オンライン / オフライン)を先に決めてください。"
MSG_NO_LEVEL = ("閲覧のみの権限です。直すには、アクセス権限マスタ(梱包資材マスタの「アクセス権限」)に、"
                "この ID・PC で dbviewer:edit の行を足してもらってください。")


def network_id() -> str:
    """VBA `NetWorkID`(WScript.Network.UserName)。"""
    return os.environ.get("USERNAME") or os.environ.get("USER") or ""


def pc_name() -> str:
    """VBA `UsePCName`(WScript.Network.ComputerName)。"""
    return os.environ.get("COMPUTERNAME") or socket.gethostname()


def is_developer(use_id: str, use_pc: str) -> bool:
    return use_id in DEVELOPER_IDS or use_pc in DEVELOPER_PCS


@dataclass
class LoginResult:
    ok: bool
    message: str = ""
    note: str = ""
    user_id: str = ""
    level: int = 0
    developer: bool = False
    rights: Optional[access_rights.Decision] = None


def rights_result() -> LoginResult:
    """この PC のログイン ID・PC 名で、アクセス権限マスタを引く(パスワードは無い)。"""
    use_id, use_pc = network_id(), pc_name()
    d = access_rights.evaluate(use_id, use_pc)
    dev = is_developer(use_id, use_pc)
    edit = d.edit or dev
    access_log.write("認証成功")
    user_settings.save(config.KEY_LAST_USER, use_id)
    return LoginResult(True, note=d.reason + ("(開発者)" if dev and not d.edit else ""), user_id=use_id,
                       level=EDIT_LEVEL if edit else VIEW_LEVEL, developer=dev, rights=d)


# ------------------------------------------------------------------
# セッション(プロセスに 1 つ)
# ------------------------------------------------------------------
@dataclass
class Session:
    user_id: str
    level: int
    developer: bool
    logged_in_at: float
    last_seen: float
    rights: Optional[access_rights.Decision] = None
    checked_at: float = 0.0

    @property
    def can_edit(self) -> bool:
        return self.level >= EDIT_LEVEL

    @property
    def label(self) -> str:
        return access_rights.LABEL_EDIT if self.can_edit else access_rights.LABEL_VIEW

    def touch(self) -> None:
        self.last_seen = time.time()

    def expired(self) -> bool:
        minutes = int(user_settings.get(config.KEY_RELOGIN_MIN, 0) or 0)
        if minutes <= 0:
            return False
        return time.time() - self.last_seen > minutes * 60


_lock = threading.Lock()
_session: Optional[Session] = None


def current() -> Optional[Session]:
    """いまのログイン。無操作で切れていれば消して `None`。"""
    global _session
    with _lock:
        if _session is not None and _session.expired():
            log.info("無操作だったので権限を読み直します: %s", _session.user_id)
            _session = None
        return _session


def start(result: LoginResult) -> Session:
    global _session
    now = time.time()
    with _lock:
        _session = Session(result.user_id, result.level, result.developer, now, now, result.rights, now)
        return _session


def sign_in() -> Session:
    """アクセス権限マスタを引いて、セッションを始める(画面を開けば自動で)。"""
    result = rights_result()
    s = start(result)
    log.info("権限: %s / %s → %s(%s)", s.user_id, pc_name(), s.label, result.rights.source if result.rights else "")
    return s


_refreshing = threading.Event()


def ensure() -> Session:
    """いまのセッション。無ければ始める。古ければ裏で読み直す(取り消しが効くように)。"""
    s = current()
    if s is None:
        return sign_in()
    if time.time() - s.checked_at > access_rights.RECHECK_SEC and not _refreshing.is_set():
        _refreshing.set()
        s.checked_at = time.time()

        def again() -> None:
            try:
                result = rights_result()
                with _lock:
                    if _session is s:
                        if s.level != result.level:
                            log.info("権限が変わりました: %s → %s", s.label,
                                     access_rights.LABEL_EDIT if result.level >= EDIT_LEVEL else access_rights.LABEL_VIEW)
                        s.level, s.rights, s.checked_at = result.level, result.rights, time.time()
            except Exception:                            # noqa: BLE001 - 読み直せなければ今のまま
                log.warning("権限を読み直せませんでした", exc_info=True)
            finally:
                _refreshing.clear()

        threading.Thread(target=again, daemon=True, name="rights-recheck").start()
    return s


def end() -> None:
    global _session
    with _lock:
        _session = None


def level() -> int:
    s = current()
    return s.level if s else 0


def can_edit() -> bool:
    return level() >= EDIT_LEVEL


# ------------------------------------------------------------------
# レベル判定(VBA `ボタンクリック制御`。編集可 = 5 / 閲覧のみ = 1)
# ------------------------------------------------------------------
def required_level(mode: str) -> int:
    if mode in ("新規", "削除"):
        return 3
    if mode == "修正":
        return 2
    return 0


def check_mode(mode: str, lv: int, click_type: str = "ButtonClick") -> tuple[str, str]:
    """(通したモード, 断りの文言)。ダブルクリックで修正に足りなければ参照に落とす。"""
    need = required_level(mode)
    if lv >= need:
        return mode, ""
    if mode == "修正" and click_type == "DblClick":
        return "参照", ""
    return "", MSG_NO_LEVEL
