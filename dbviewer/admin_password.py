"""管理者パスワード(0.17.0)── アクセス権限・設定ファイルを直すときの鍵

作りは all-tools・python-web-tools の `admin_password` と同じ:

- 既定は ほかのツールと同じ `nisk`(`DBVIEWER_ADMIN_PASSWORD` で変えられる)。
- 変えたものは**平文で持たない。** PBKDF2(SHA-256・20万回・塩16バイト)で撹拌して
  `pbkdf2$<回数>$<塩>$<結果>` の形でこの PC の設定に入れる。変えるには**いまのパスワード**が要る。4文字以上
- 忘れたら この PC の設定の `admin_password` を消すと既定に戻る
- 鍵はこのプロセスの中だけ。**30 分さわらなければ掛かる**(使えば延びる)
- 続けて 5 回まちがえたら 30 秒は受け付けない(当てずっぽうを遅くする)

アクセス制御の本体ではなく、**うっかり書き換えないための守り**(各ツールと同じ位置づけ)。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Optional

from . import config, user_settings

DEFAULT_PASSWORD = os.environ.get("DBVIEWER_ADMIN_PASSWORD", "nisk")

SCHEME = "pbkdf2"
ITERATIONS = 200_000
SALT_BYTES = 16
MIN_LENGTH = 4
IDLE_LOCK_SEC = 30 * 60
MAX_MISSES = 5
COOL_DOWN_SEC = 30.0


@dataclass
class Result:
    ok: bool = True
    message: str = ""
    reason: str = ""


def _encode(password: str, salt: bytes, iterations: int = ITERATIONS) -> str:
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return "$".join([SCHEME, str(iterations), base64.b64encode(salt).decode("ascii"),
                     base64.b64encode(digest).decode("ascii")])


def _matches(password: str, stored: str) -> bool:
    try:
        scheme, iterations, salt, _digest = stored.split("$")
        if scheme != SCHEME:
            return False
        expected = _encode(password, base64.b64decode(salt), int(iterations))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(expected, stored)


def _stored() -> Optional[str]:
    value = user_settings.get(config.KEY_ADMIN_PASSWORD)
    return value if isinstance(value, str) and value.strip() else None


def is_custom() -> bool:
    return _stored() is not None


def verify(password: str) -> bool:
    stored = _stored()
    if stored is not None:
        return _matches(str(password), stored)
    return hmac.compare_digest(str(password).encode("utf-8"), DEFAULT_PASSWORD.encode("utf-8"))


def change(current: str, new: str, confirm: str) -> Result:
    if not verify(current):
        return Result(False, "いまのパスワードが違います。", "wrong_password")
    new = str(new)
    if len(new) < MIN_LENGTH:
        return Result(False, f"新しいパスワードは {MIN_LENGTH} 文字以上にしてください。", "too_short")
    if new != str(confirm):
        return Result(False, "確認のために入れたものと一致しません。", "mismatch")
    if new == str(current):
        return Result(False, "いまと同じパスワードです。", "same")
    user_settings.save(config.KEY_ADMIN_PASSWORD, _encode(new, secrets.token_bytes(SALT_BYTES)))
    return Result(True, "管理者パスワードを変えました(この PC だけ)。")


class Session:
    """鍵の状態(このプロセスの中だけ)。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._until = 0.0
        self._misses = 0
        self._wait_until = 0.0

    def unlock(self, password: str) -> Result:
        with self._lock:
            now = time.monotonic()
            if now < self._wait_until:
                return Result(False, f"続けてまちがえたので、{int(self._wait_until - now) + 1} 秒待ってください。",
                              "cool_down")
        ok = verify(password)
        with self._lock:
            if ok:
                self._until, self._misses = time.monotonic() + IDLE_LOCK_SEC, 0
                return Result(True, "鍵を開けました(30 分さわらなければ掛かります)。")
            self._until = 0.0
            self._misses += 1
            if self._misses >= MAX_MISSES:
                self._misses, self._wait_until = 0, time.monotonic() + COOL_DOWN_SEC
            return Result(False, "パスワードが違います。", "bad_password")

    def lock(self) -> None:
        with self._lock:
            self._until = 0.0

    def is_open(self) -> bool:
        """開いているか。使えば延びる。"""
        with self._lock:
            if time.monotonic() >= self._until:
                return False
            self._until = time.monotonic() + IDLE_LOCK_SEC
            return True

    def peek(self) -> bool:
        with self._lock:
            return time.monotonic() < self._until


session = Session()
