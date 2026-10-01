"""帳票の登録簿(VBA `Application.Run(reportArr(3), ...)` の代わり)

全般設定「帳票」は `フォルダ/原紙ファイル/原紙シート名/プロシージャ名[/引数]`。
Web 版が使うのは [3] プロシージャ名(半角英数字)と [4] 以降の引数だけ。
ここに無い名前は VBA と同じ文言で断る。

【Excel の原紙は使わない】
VBA は原紙ブック(出力原紙.xlsx)のシートを複製して書き込み、印刷プレビューを
出していた。Web 版は帳票を**印刷用の画面**(A4・余白 6mm)で出し、原紙は読まない
([0]〜[2] は書いてあっても無視する)。Excel が無い PC でも同じ帳票が出る。
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
