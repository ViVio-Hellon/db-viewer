"""利用者ごとの設定の永続化 (VBA `GetSetting`/`SaveSetting` の代替)

VBA版は Windows レジストリに保存していた。

    GetSetting(SysName, "起動設定", "モード", "存在なし")
    SaveSetting SysName, "起動設定", "モード", "オフライン"

Python版はレジストリに依存しないよう、JSONファイル(`config.USER_CONFIG_PATH`)
に保存する。読み書きのたびにファイルを開くので、外部から書き換えても
次回の読み取りで反映される。鍵の一覧は `config.KEY_*`。
"""
from __future__ import annotations

import json
import os
import shutil
import threading
import time
from typing import Any

from . import config
from .logging_utils import get_logger

log = get_logger("user_settings")

# 表示用。VBA `GetSetting(..., "存在なし")` に相当する「未設定」の言葉
UNSET_LABEL = "未設定"


_lock = threading.RLock()                 # このプロセスの中で読み書きを 1 つずつ(0.46.3)
READ_TRIES = 5


def _backup_path():
    return config.USER_CONFIG_PATH.with_name(config.USER_CONFIG_PATH.name + ".bak")


def _read(path) -> tuple[dict[str, Any], bool]:
    """(中身, 読めたか)。書いている途中で空・半端に見えたら、少し待って読み直す。"""
    last: Exception | None = None
    for i in range(READ_TRIES):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return (data if isinstance(data, dict) else {}), True
        except FileNotFoundError:
            return {}, True
        except (OSError, ValueError) as exc:
            last = exc
            time.sleep(0.05 * (i + 1))
    log.warning("設定ファイルを読めませんでした: %s (%s)", path, last)
    return {}, False


def load_all() -> dict[str, Any]:
    """設定ファイル全体を読む。壊れていても例外にせず、控え(.bak)があればそれを使う。"""
    path = config.USER_CONFIG_PATH
    with _lock:
        if not path.exists():
            return {}
        data, ok = _read(path)
        if ok:
            return data
        bak, ok_bak = _read(_backup_path())
        if ok_bak and bak:
            log.warning("設定ファイルが壊れていたので、控えの内容で続けます: %s", _backup_path())
            return bak
        log.warning("設定ファイルを読めませんでした(既定値で続行)")
        return {}


def _write(data: dict[str, Any]) -> None:
    """**途中の姿を見せない書き方**(0.46.3)。隣に書いてから差し替える。前の中身は .bak に残す。

    以前はファイルを直接書き直していたので、書いている最中に別の処理が読むと空に見え、
    「空 + 1 項目」で書き戻して**ほかの設定がぜんぶ消えた**(見る DB・設定ファイル・ログの場所など)。"""
    path = config.USER_CONFIG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    for i in range(10):                       # Windows でほかが開いていると差し替えが一瞬断られる
        try:
            os.replace(tmp, path)
            break
        except PermissionError:
            if i == 9:
                raise
            time.sleep(0.05 * (i + 1))
    try:                                      # 書き終えた中身を控えに(壊れたらこれで続ける)
        shutil.copyfile(path, _backup_path())
    except OSError:
        pass


def get(key: str, default: Any = None) -> Any:
    """VBA `GetSetting` 相当。"""
    return load_all().get(key, default)


# ログに値を出さない鍵。伏せるだけで、保存はふつうに行う
HIDDEN_IN_LOG = ("admin_password", "password")


def save(key: str, value: Any) -> bool:
    """VBA `SaveSetting` 相当。書き込めなければ False を返す。"""
    with _lock:
        data = load_all()
        data[key] = value
        try:
            _write(data)
        except OSError as exc:
            log.warning("設定ファイルに書けませんでした: %s", exc)
            return False
    # **値を出さない鍵がある。** 管理者パスワードは撹拌済みだが、
    # ログに残す理由が無い(残せば持ち出せる)
    log.info("設定を保存しました: %s=%s", key,
             "(伏せます)" if key in HIDDEN_IN_LOG else value)
    return True


def delete(key: str) -> bool:
    """VBA `DeleteSetting` 相当。無ければ何もしない(True)。"""
    with _lock:
        data = load_all()
        if key not in data:
            return True
        del data[key]
        try:
            _write(data)
        except OSError as exc:
            log.warning("設定ファイルに書けませんでした: %s", exc)
            return False
    log.info("設定を消しました: %s", key)
    return True
