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
from typing import Any

from . import config
from .logging_utils import get_logger

log = get_logger("user_settings")

# 表示用。VBA `GetSetting(..., "存在なし")` に相当する「未設定」の言葉
UNSET_LABEL = "未設定"


def load_all() -> dict[str, Any]:
    """設定ファイル全体を読む。壊れていても例外にせず空扱いにする。"""
    path = config.USER_CONFIG_PATH
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.warning("設定ファイルを読めませんでした(既定値で続行): %s", exc)
        return {}
    return data if isinstance(data, dict) else {}


def get(key: str, default: Any = None) -> Any:
    """VBA `GetSetting` 相当。"""
    return load_all().get(key, default)


# ログに値を出さない鍵。伏せるだけで、保存はふつうに行う
HIDDEN_IN_LOG = ("admin_password", "password")


def save(key: str, value: Any) -> bool:
    """VBA `SaveSetting` 相当。書き込めなければ False を返す。"""
    data = load_all()
    data[key] = value
    try:
        config.USER_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        config.USER_CONFIG_PATH.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
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
    data = load_all()
    if key not in data:
        return True
    del data[key]
    try:
        config.USER_CONFIG_PATH.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        log.warning("設定ファイルに書けませんでした: %s", exc)
        return False
    log.info("設定を消しました: %s", key)
    return True
