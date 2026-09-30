"""入力形式(_設定 の「入力形式」)の解釈(VBA `setModifyedForm` / `TextDblClickEvents` の分岐)

    ""  or  "/n"          … 自由入力(n は表示行数)          ラベル Ivory White、入力可
    "$フォーム/引数,..."   … 専用フォーム(CoinInputFm など)   Rococo White、直接入力不可
    "#VIEW#"              … 表示だけ                          Ivory、入力不可
    "#NEWUSER#" "#NEWPC#" "#NEWDATE#" "#UPUSER#" "#USEPC#" "#UPDATE#"
                          … 自動(登録者・PC・日時)            Rococo、入力不可
    "calender[@過去|@未来][/連動先/関数/引数]"
                          … カレンダー                        Oyster White
    "数値変換"            … 自由入力 + Enter で数値化         Ivory、入力可
    "数値N/最小/最大/刻み" … 数値入力フォーム(N=1 スピン, 2 スクロールバー, 3 テンキー)  Oyster
    "sマスタ名" / "mマスタ名" … マスタから 1 つ / 複数        Champagne White
    "フィールド"          … 一覧の列名から選ぶ([フィルタ_設定] 用)  Champagne
    "フォルダ"            … フォルダのファイル名から選ぶ      Champagne
    それ以外              … ルール適用外(VBA は赤ラベル + SYSTEM ERROR)

VBA は 1 文字目と `cv()`(大文字・半角)で分岐する。順番も同じにしてある。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .vba_compat import cv

AUTO_TAGS = ("#NEWUSER#", "#NEWDATE#", "#UPUSER#", "#UPDATE#", "#NEWPC#", "#USEPC#")
VIEW_TAG = "#VIEW#"

MSG_RULE_ERROR = "ルール適用外です。マスタファイルを設定し直してください。"
SYSTEM_ERROR_VALUE = "SYSTEM ERROR"

# VBA のラベル色(RGB)。画面は CSS の class で塗る。ここは名前の出どころ
COLOR_FREE = "ivory"        # RGB(255,251,237)
COLOR_FORM = "rococo"       # RGB(238,231,227)
COLOR_CALENDER = "oyster"   # RGB(246,230,222)
COLOR_MASTER = "champagne"  # RGB(254,239,212)
COLOR_ERROR = "error"       # vbRed


@dataclass
class InputSpec:
    raw: str
    kind: str                     # free / form / view / auto / calender / numconv / num / master / field / folder / error
    color: str
    locked: bool                  # VBA `objITB.Locked`(直接入力できない)
    enabled: bool                 # VBA `objITB.Enabled`
    lines: int = 1                # "/n" の表示行数
    form_name: str = ""           # $ の後のフォーム名
    form_args: list[str] = field(default_factory=list)
    auto_tag: str = ""            # #NEWUSER# など(cv 済み)
    date_limit: str = ""          # 過去 / 未来
    link_field: str = ""          # calender/連動先
    link_func: str = ""           # calender/…/関数
    link_args: list[str] = field(default_factory=list)
    num_mode: int = 0             # 数値N の N
    num_min: float = 0.0
    num_max: float = 0.0
    num_step: float = 1.0
    master: str = ""              # s/m の後のマスタ名
    multi: bool = False           # m なら複数選択
    message: str = ""             # ルール適用外の文言

    @property
    def is_auto(self) -> bool:
        return self.kind in ("auto", "view")


def parse(text: Optional[str]) -> InputSpec:
    inp = (text or "")
    ini = inp[:1]
    cvv = cv(inp)
    # 行数("/n")
    lines = 1
    if "/" in inp:
        tail = inp.split("/")[1] if inp.count("/") >= 1 else ""
        if tail.isdigit():
            lines = max(1, int(tail))

    if inp == "" or ini == "/":
        return InputSpec(inp, "free", COLOR_FREE, locked=False, enabled=True, lines=lines)
    if ini == "$":
        parts = inp.split("/")
        name = parts[0][1:]
        args = parts[1].split(",") if len(parts) > 1 else []
        return InputSpec(inp, "form", COLOR_FORM, locked=True, enabled=False,
                         form_name=name, form_args=args)
    if cvv == VIEW_TAG:
        return InputSpec(inp, "view", COLOR_FREE, locked=True, enabled=False, auto_tag=cvv)
    if ini == "#":
        return InputSpec(inp, "auto", COLOR_FORM, locked=True, enabled=False, auto_tag=cvv)
    if cvv.startswith("CALENDER"):
        parts = inp.split("/")
        head = parts[0]
        limit = head.split("@")[1] if "@" in head else ""
        spec = InputSpec(inp, "calender", COLOR_CALENDER, locked=True, enabled=True,
                         date_limit=limit)
        if len(parts) > 3:
            spec.link_field = parts[1]
            spec.link_func = parts[2]
            spec.link_args = parts[3].split(",") if "," in parts[3] else [parts[3]]
        return spec
    if cvv == "数値変換":
        return InputSpec(inp, "numconv", COLOR_FREE, locked=False, enabled=True)
    if cvv.startswith("数値"):
        parts = inp.split("/")
        spec = InputSpec(inp, "num", COLOR_CALENDER, locked=True, enabled=True)
        try:
            spec.num_mode = int(parts[0][-1:]) if parts[0][-1:].isdigit() else 0
            spec.num_min = float(parts[1]) if len(parts) > 1 else 0.0
            spec.num_max = float(parts[2]) if len(parts) > 2 else 0.0
            spec.num_step = float(parts[3]) if len(parts) > 3 else 1.0
        except ValueError:
            pass
        return spec
    if cv(ini) in ("M", "S"):
        return InputSpec(inp, "master", COLOR_MASTER, locked=True, enabled=True,
                         master=inp[1:], multi=(ini == "m"))
    if inp == "フィールド":
        return InputSpec(inp, "field", COLOR_MASTER, locked=True, enabled=True)
    if inp == "フォルダ":
        return InputSpec(inp, "folder", COLOR_MASTER, locked=True, enabled=True)
    return InputSpec(inp, "error", COLOR_ERROR, locked=True, enabled=True, message=MSG_RULE_ERROR)


def auto_value(spec: InputSpec, mode: str, *, user: str, pc: str, now: str) -> Optional[str]:
    """VBA `GetArrDataFromCtrl`: 自動項目に入れる値。対象外なら `None`。"""
    new = mode == "新規"
    mod = mode == "修正"
    tag = spec.auto_tag
    if (tag == "#NEWUSER#" and new) or (tag == "#UPUSER#" and mod):
        return user
    if (tag == "#NEWPC#" and new) or (tag == "#USEPC#" and mod):
        return pc
    if (tag == "#NEWDATE#" and new) or (tag == "#UPDATE#" and mod):
        return now
    return None
