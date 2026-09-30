"""モード ── DBビューアはモードを1つしか持たない

python-web-tools(梱包資材総合ツール)の基盤は「現場」「資材」の2モードを
別ポートで動かす作りで、起動制御・ロックファイル・ポート選びが
モード名を鍵にしている。DBビューアにモードの区別は無い(VBA 版にも無い。
誰が何をできるかは `ID` テーブルの Level が決める)が、**基盤のファイルを
書き換えずに複製する**ために、モードを1つだけ持つ形でこの表を残す。

基盤の改修を後から取り込むときの差分を小さくするのが目的で、
モードを増やす予定は無い。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Mode:
    key: str
    label: str
    detail: str
    color: str
    mark: str


MAIN = "main"

ALL: tuple[Mode, ...] = (
    Mode(key=MAIN, label="標準",
         detail="設定テーブルで決まる業務DBを閲覧・登録する",
         color="#0e6b84", mark="DB"),
)

KEYS: tuple[str, ...] = tuple(m.key for m in ALL)
DEFAULT = MAIN

# 旧名は無い。基盤(`app_config._canonicalize_modes` など)が参照するので空で残す
LEGACY_NAMES: dict[str, str] = {}


def get(key: str) -> Mode:
    for mode in ALL:
        if mode.key == key:
            return mode
    raise ValueError(f"未知のモード: {key!r} (使えるのは {', '.join(KEYS)})")


def label(key: str) -> str:
    return get(key).label


def normalize(key: str) -> str:
    """空や未知の名前は既定に倒す。モードが1つしか無いので迷う余地が無い。"""
    text = (key or "").strip()
    text = LEGACY_NAMES.get(text, text)
    return text if text in KEYS else DEFAULT
