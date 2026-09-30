"""ログインとアクセスレベル(VBA `Login` フォームの移植)

【VBA の判定順(`LOGINEXE_Botton_Click`)】
    1. 本体ファイルのフォルダに届くか / 本体があるか
    2. DEVELOPERFLAG(固定の ID / PC 名)なら無条件で通す
    3. ID テーブルに入力 ID の行が無い     → 「IDが登録されていません。…」
    4. 入力 ID ≠ この PC のログイン ID    → 「使用中のIDはPC使用者と一致しません」
    5. ID テーブルにこの PC の行が無い     → 「PCが登録されていません。…」
    6. ID と PC の両方が一致する行が無い   → 「IDと使用PCの登録で一致するものがありません。」
    7. その行の PASS と入力(小文字・半角)が違う → 「入力パスワード「x」が違います。」
    Level は一致した行の Level。行が無い開発者は 5、それ以外は 1 で
    「登録がないので最低権限で仮設定されます。」

【文字の比べ方】
Access の `=` は大文字小文字を区別しない(ID・PC)。パスワードは VBA が
`StrConv(LCase(x), vbNarrow)` で両方をそろえてから比べる。

【セッション】
VBA は Excel を閉じるまで有効。Web 版は**プロセスに 1 つ**(1 台の PC を
1 人が使う前提。タブを増やしても同じ人)。設定「無操作で再ログイン(分)」が
0 より大きければ、その時間だけ触っていないと再ログインを求める。
"""
from __future__ import annotations

import os
import socket
import sqlite3
import threading
import time
from dataclasses import dataclass
from typing import Optional

from . import access_log, config, user_settings
from .logging_utils import get_logger
from .settings_tables import field_dic, read_table
from .vba_compat import to_narrow, val_int

log = get_logger("auth")

# VBA `DEVELOPERFLAG`(Option Compare Binary = 大文字小文字を区別)
DEVELOPER_IDS = ("Satoshi-Harada", "satoshi")
DEVELOPER_PCS = ("NLM-NGY-221790", "PCH370")

MSG_NO_ID = "IDが登録されていません。\n管理者に問い合わせて登録してからご使用ください。"
MSG_ID_MISMATCH = "使用中のIDはPC使用者と一致しません"
MSG_NO_PC = "PCが登録されていません。\n管理者に問い合わせて登録してからご使用ください。"
MSG_NO_PAIR = "IDと使用PCの登録で一致するものがありません。"
MSG_BAD_PASS = "入力パスワード「{pw}」が違います。"
MSG_TEMP_LEVEL = "登録がないので最低権限で仮設定されます。"
MSG_NEED_LOGIN = "ログインしてください。"
MSG_NO_LEVEL = "使用中の権限レベルは{lv}です。権限がありません。"


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
    message: str = ""          # 断りの文言(VBA の Msg.Box)
    note: str = ""             # 通ったが伝えること(仮設定など)
    user_id: str = ""
    level: int = 0
    developer: bool = False


def _same(a: str, b: str) -> bool:
    """Access の `=`(大文字小文字を区別しない)。"""
    return (a or "").casefold() == (b or "").casefold()


def authenticate(conn: sqlite3.Connection, inp_id: str, inp_pass: str, *,
                 use_id: Optional[str] = None, use_pc: Optional[str] = None) -> LoginResult:
    use_id = network_id() if use_id is None else use_id
    use_pc = pc_name() if use_pc is None else use_pc
    inp_pass_n = to_narrow((inp_pass or "").lower())

    cols, rows = read_table(conn, config.ID_TABLE)
    fj = field_dic(cols)
    ci, cp, cpw, clv = fj.get("ID", 0), fj.get("PC", 0), fj.get("PASS", 0), fj.get("Level", 0)
    ck_id = [r for r in rows if ci and _same(r[ci - 1], inp_id)]
    ck_pc = [r for r in rows if cp and _same(r[cp - 1], use_pc)]
    temp = [r for r in rows if ci and cp and _same(r[ci - 1], inp_id) and _same(r[cp - 1], use_pc)]

    dev = is_developer(use_id, use_pc)
    ng = True
    message = ""
    if dev:
        ng = False
    elif not ck_id:
        message = MSG_NO_ID
    elif inp_id != use_id:
        message = MSG_ID_MISMATCH
    elif not ck_pc:
        message = MSG_NO_PC
    elif not temp:
        message = MSG_NO_PAIR
    else:
        tgt = to_narrow((temp[0][cpw - 1] if cpw else "").lower())
        if inp_pass_n != tgt:
            message = MSG_BAD_PASS.format(pw=inp_pass_n)
        else:
            ng = False
    if ng:
        access_log.write("認証失敗")
        return LoginResult(False, message=message, user_id=inp_id)

    access_log.write("認証成功")
    note = ""
    if temp:
        level = val_int(temp[0][clv - 1]) if clv else 0
    elif dev:
        level = config.DEVELOP_LV
    else:
        level = 1
        note = MSG_TEMP_LEVEL
    user_settings.save(config.KEY_LAST_USER, inp_id)
    return LoginResult(True, note=note, user_id=inp_id, level=level, developer=dev)


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
            log.info("無操作のため再ログインが必要です: %s", _session.user_id)
            _session = None
        return _session


def start(result: LoginResult) -> Session:
    global _session
    now = time.time()
    with _lock:
        _session = Session(result.user_id, result.level, result.developer, now, now)
        return _session


def end() -> None:
    global _session
    with _lock:
        _session = None


def level() -> int:
    s = current()
    return s.level if s else 0


# ------------------------------------------------------------------
# レベル判定(VBA `ボタンクリック制御`)
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
    return "", MSG_NO_LEVEL.format(lv=lv)
