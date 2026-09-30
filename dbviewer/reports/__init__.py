"""帳票の登録簿(VBA `Application.Run(reportArr(3), ...)` の代わり)

全般設定「帳票」は `フォルダ/原紙ファイル/原紙シート名/プロシージャ名[/引数]`。
    [0] フォルダ … 空ならアプリのフォルダ(Web 版は設定「帳票原紙の置き場所」、
                    それも空なら本体と同じフォルダ)
    [1] 原紙ファイル(出力原紙.xlsx)
    [2] 複製する原紙シート
    [3] プロシージャ名(半角英数字)。ここに無い名前は VBA と同じ文言で断る
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from .. import config
from . import others, sagyo_hyojun
from .sagyo_hyojun import Grid

Builder = Callable[[list[list[str]], list[str]], Grid]

REGISTRY: dict[str, Builder] = {
    "REPORT": lambda table, args: sagyo_hyojun.build(table),
    "SUITOUREPORT": others.suitou,
    "DAITYOREPORT": others.daityo,
    "GIKAKUREPORT": others.gikaku,
}

MSG_BAD_SETTING = "正しくデータが設定されていません。"
MSG_NO_FILE = "アクセス許可がないか、ファイルが存在しないため参照できません。"


class ReportError(RuntimeError):
    pass


@dataclass
class ReportSpec:
    folder: str
    file: str
    sheet: str
    proc: str
    args: list[str] = field(default_factory=list)

    def path(self, default_dir: Path) -> Path:
        base = Path(self.folder) if self.folder else default_dir
        return base / self.file


def parse(report_str: str) -> ReportSpec:
    if "/" not in report_str:
        raise ReportError(MSG_BAD_SETTING)
    parts = report_str.split("/")
    if len(parts) < 4:
        raise ReportError(MSG_BAD_SETTING)
    return ReportSpec(parts[0], parts[1], parts[2], parts[3], parts)


def builder_for(proc: str) -> Builder:
    if proc not in REGISTRY:
        raise ReportError(
            "対象となるプログラムが存在しません。プログラム名を確認してください。\n"
            f"指定プログラム : {proc}\n候補プログラム : {','.join(REGISTRY)}")
    return REGISTRY[proc]


def build(spec: ReportSpec, table: list[list[str]]) -> Grid:
    return builder_for(spec.proc)(table, spec.args)


def xlsx_append_report(template: Path, grid: Grid, sheet_name: str, *, template_sheet: str = "") -> str:
    """原紙 xlsx にシートを足す(VBA `ActSh.Copy After:= ... BK.Save`)。戻り値は付いた名前。"""
    from .. import xlsx_append
    from . import render

    spec = render.to_sheet(grid, sheet_name)
    return xlsx_append.append_sheet(template, spec, template_sheet_name=template_sheet or None)
