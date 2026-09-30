"""画面の外枠に渡す値

左のレールの並びと、テンプレートが必要とする共通の値をここで作る。
並びは VBA 版の操作の順(MF1 一覧 → 登録/修正 → MF2 マスタ管理 →
MF3 テーブル閲覧/出力 → 設定)。番号は装飾ではなく順序が実在するから振る。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from flask import current_app, request

from dbviewer import app_config, idle_exit, modes, user_settings


@dataclass
class NavItem:
    key: str
    label: str
    url: str
    note: str = ""
    badge: str = ""
    badge_kind: str = "todo"
    ready: bool = True


# 画面の並び。左から作業する順
NAV: tuple[tuple[str, str, str, str], ...] = (
    ("list", "一覧", "/list", "絞り込んで探す・登録する"),
    ("master", "マスタ管理", "/master", "設定表とマスタ表を直す"),
    ("browse", "閲覧・出力", "/browse", "表を見る・xlsx/csv に出す"),
    ("settings", "設定", "/settings", "接続先・パス・この端末"),
)

# もう作った画面。ここに挙がっていないものは「準備中」ページを出す
READY_SCREENS = frozenset({"settings"})

# 準備中の画面の説明。key: (作る予定, 何ができるようになるか, いま使う画面)
PENDING_SCREENS: dict[str, tuple[str, str, str]] = {
    "list": ("Phase 3", "一覧・フィルタ・集計・ページ送りと、レコードの登録/修正/削除", "VBA 版"),
    "master": ("Phase 5", "設定表(全般設定・_設定・フィルタ・タイプ・ID)とマスタ表の編集", "VBA 版"),
    "browse": ("Phase 5", "テーブル閲覧と xlsx/txt/csv 出力", "VBA 版"),
}


def nav_items(mode: str = modes.DEFAULT,
              badges: Optional[dict[str, tuple[str, str]]] = None) -> list[NavItem]:
    badges = badges or {}
    items = []
    for key, label, url, note in NAV:
        ready = key in READY_SCREENS
        badge, kind = badges.get(key, ("", "todo"))
        if not ready and not badge:
            badge, kind = "準備中", "todo"
        items.append(NavItem(key=key, label=label, url=url, note=note,
                             badge=badge, badge_kind=kind, ready=ready))
    return items


def pending_items(mode: str = modes.DEFAULT) -> list[NavItem]:
    return [item for item in nav_items(mode) if not item.ready]


_FAVICON = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
            '<rect width="32" height="32" rx="6" fill="{color}"/>'
            '<text x="16" y="21" font-size="13" font-family="sans-serif" font-weight="bold"'
            ' text-anchor="middle" fill="#ffffff">{mark}</text></svg>')


def favicon(mode: str = modes.DEFAULT) -> str:
    item = modes.get(modes.normalize(mode))
    return _FAVICON.format(color=item.color, mark=item.mark)


FALLBACK_URL = "/settings"


def home_url(mode: str = modes.DEFAULT) -> str:
    """`/` を開いたときに入る画面。レールの先頭で、**実際に登録されている**もの。"""
    registered = {rule.rule for rule in current_app.url_map.iter_rules()}
    for item in nav_items(mode):
        if item.ready and item.url in registered:
            return item.url
    return FALLBACK_URL


def ribbon_values() -> dict[str, str]:
    """帯に常時出す値。ログイン(Phase 4)でユーザーと Level が入る。"""
    from dbviewer import shared_db

    try:
        prof = shared_db.current_profile()
        sys_name = prof.sys_name
    except ValueError:
        sys_name = "(プロファイル未設定)"
    online = user_settings.get(user_settings_key_online(), True)
    return {
        "system": sys_name,
        "user": "",
        "level": "",
        "mode": "オンライン" if online else "オフライン",
    }


def user_settings_key_online() -> str:
    from dbviewer import config
    return config.KEY_ONLINE


def shell_context(active: str, *,
                  badges: Optional[dict[str, tuple[str, str]]] = None) -> dict[str, Any]:
    """`base.html` が必要とする値一式。"""
    config = current_app.config
    mode = config["MODE"]
    return {
        "active": active,
        "nav": nav_items(mode, badges),
        "mode": mode,
        "display_name": config["DISPLAY_NAME"],
        "version_label": app_config.version_label(),
        "favicon": favicon(mode),
        "token": config["TOKEN"],
        # このタブの番号。自動終了の見張りが画面ごとに数えるためのもの
        "screen_id": request.headers.get("X-Screen-Id", "") or _new_screen_id(),
        "ribbon": ribbon_values(),
        "health_poll_ms": app_config.health_poll_seconds() * 1000,
        "log_poll_ms": max(500, app_config.job_poll_ms() * 3),
        "alive_poll_ms": idle_exit.HEARTBEAT_MS,
    }


def _new_screen_id() -> str:
    import uuid
    return uuid.uuid4().hex
