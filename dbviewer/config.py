"""業務の定数と、この端末の設定の置き場所

VBA 版の標準モジュール【A0】UserSetting にあった定数群を移す。

【VBA との対応】
- `progNo` と `プログラム番号チェック` の Select Case → `PROFILES`(システムプロファイル)
- `GetSetting(SysName, "起動設定", "モード")` → `user_settings`(JSON)
- `SrvPath` / `MasterPath` → `server_dir()` / `db_path()`
- `ServerFolderPath` の UNC は VBA の値をそのまま既定にしてある。実際に見る
  先は設定タブ(接続先)で変えられる

【`app_config.py` との違い】
`app_config.py` はアプリという入れ物の値(ID・ポート・ローカル領域)。
ここは業務の値(テーブル名・列名・プロファイル・パスの決め方)。
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from . import app_config

BASE_DIR = Path(__file__).resolve().parent.parent

# ログの置き場所(利用者ごとのローカル領域)
LOG_DIR = Path(os.environ.get("DBVIEWER_LOG_DIR", str(app_config.local_dir("logs"))))

# 利用者設定(VBA のレジストリの代替)。**リポジトリの中には置かない**
USER_CONFIG_PATH = Path(os.environ.get(
    "DBVIEWER_CONFIG_PATH", str(app_config.local_dir("data") / "user_config.json")))

# ------------------------------------------------------------------
# 固定テーブル名・列名(VBA `Public Const ...` と同名)
# ------------------------------------------------------------------
SET_TABLE = "全般設定"          # setTable
KEY_ITEM = "管理番号"           # KeyItem
FLT_TABLE = "フィルタ"          # fltTable
TYPE_TABLE = "タイプ"           # TypeTable
ID_TABLE = "ID"                 # IDTable
ITEM_NAME = "項目名"            # ItemName
PRESET = "設定内容"             # preset
FILTER_ITEM = "フィルタ"        # FilterItem
FILTER_TYPE = "タイプ"          # FilterType
REG_CON = "登録条件"            # RegCon
IND_TYPE = "表示"               # IndType
ADD_IND = "追加表示"            # AddInd
INP_TYPE = "入力形式"           # InpType
DSP_TYPE = "参照形式"           # dspType
MS_JOIN = "マスタ結合"          # MsJoin
HIS_REC = "履歴"                # hisRec
SORT_FLD = "ソート"             # sortfld
CHAR_COLOR_SET = "文字色設定"   # CharColorSet
SETTING_SUFFIX = "_設定"        # ConvTable の addStr
HISTORY_PREFIX = "H_"           # 履歴テーブルの接頭辞

# 全般設定 の 項目名
GS_MAIN_TABLE = "メインテーブル名"
GS_INIT_VIEW = "初期表示"
GS_INIT_FILTER = "初期フィルタ"
GS_REPORT = "帳票"
GS_TABLE_BROWSE = "テーブル閲覧"

# 開発用検証レベル(VBA `developLv`)
DEVELOP_LV = 5

# 業務 DB の拡張子(Access → SQLite 変換後)
DB_SUFFIX = ".sqlite3"
# VBA と同じ場所に同じ形で残すログ(TSV)
ACCESS_LOG_NAME = "Access.log"

# 「表を持ってくる」で足した表の記録(本体 .sqlite3 の中)。python-web-tools と同じ名前。
# VBA には無い表なので、整合性検査・マスタ管理の一覧には出さない(`settings_tables.table_names`)
BROUGHT_REGISTRY = "ツールで足した表"


# ------------------------------------------------------------------
# システムプロファイル(VBA progNo と `プログラム番号チェック`)
# ------------------------------------------------------------------
@dataclass(frozen=True)
class Profile:
    prog_no: int
    sys_name: str            # SysName(レジストリ登録名)
    accdb_file: str          # ACCDBFILE(本体ファイル)
    server_dir: str          # ServerFolderPath(オンライン時のフォルダ)
    note: str = ""

    @property
    def db_file(self) -> str:
        """SQLite に変換したあとの本体ファイル名。拡張子だけ変える。"""
        return Path(self.accdb_file).stem + DB_SUFFIX


PROFILES: tuple[Profile, ...] = (
    Profile(1, "ISO文書台帳管理システム", "ISO文書台帳管理.accdb",
            r"\\nlmsrvngy03\各課共有\0109_仕上課\04 ISO\ISO管理台帳"),
    Profile(2, "化学物質管理システム", "00_マスタ.accdb",
            r"\\Nlmfangyshrd\DocuWorks\0000_共通\003_化学物質管理"),
    Profile(3, "仮払出納帳システム", "仮払出納帳.accdb",
            r"\\nlmfangyshrd\各課共有\0102_事務課\総務／環境伝票など\仮払出納帳"),
    Profile(4, "作業標準管理システム", "作業標準管理システム.accdb",
            r"\\nlmfangyshrd\各課共有\0130_日軽稲沢\梱包課\AIM\【■】_参照用ファイル\作業標準DB"),
    Profile(5, "技革保管リスト", "技革保管リスト.accdb",
            r"\\nlmsrvngy03\各課共有\0110_技術室\003_技革-板ｻﾝﾌﾟﾙ"),
    Profile(6, "4M管理システム", "4M管理.accdb",
            r"\\nlmsrvngy03\各課共有\0109_仕上課", "4M置き場(修正必要)"),
    Profile(7, "仕上課人事DB", "仕上課人事データベース.accdb",
            r"\\nlmsrvngy03\各課共有\0109_仕上課\データベース", "4M置き場(修正必要)"),
)

# VBA の `progNo = 4`。ここだけが既定の出どころ
DEFAULT_PROG_NO = 4


def profile(prog_no: Optional[int] = None) -> Profile:
    """プロファイルを引く。無い番号は VBA と同じく設定ミスとして断る。"""
    no = DEFAULT_PROG_NO if prog_no is None else int(prog_no)
    for p in PROFILES:
        if p.prog_no == no:
            return p
    raise ValueError("プログラム番号設定ミスです。再度番号を設定し直してください。")


# ------------------------------------------------------------------
# 利用者設定の鍵(`user_settings`)
# ------------------------------------------------------------------
KEY_PROG_NO = "prog_no"              # 使うプロファイル
KEY_ONLINE = "online"                # VBA 起動設定/モード(オンライン=True)
KEY_SERVER_DIR = "server_dir"        # オンライン時のフォルダ(空ならプロファイルの既定)
KEY_LOCAL_DIR = "local_dir"          # オフライン時のフォルダ(空ならアプリのフォルダ)
KEY_DB_FILE = "db_file"              # 本体ファイル名(空ならプロファイルの既定 .sqlite3)
KEY_REPORT_DIR = "report_dir"        # (使わない)以前の「帳票原紙の置き場所」。帳票は Excel 原紙を使わなくなった
KEY_EXPORT_FORMAT = "export_format"  # 出力の既定の形式(csv / tsv / xlsx)
KEY_EXPORT_ENCODING = "export_encoding"  # csv / tsv の文字コード(cp932 / utf-8-sig)
KEY_EXPORT_DIR = "export_dir"        # 出力先(空ならローカル領域 work)
KEY_REF_DIR = "ref_dir"              # 参照ファイル(PDF 等)の相対パスの起点(空なら本体と同じフォルダ)
KEY_ACCESS_LOG = "access_log"        # Access.log を書くか(既定 True)
KEY_RELOGIN_MIN = "relogin_minutes"  # 無操作で再ログイン(0 = しない)
KEY_ACCDB_SOURCE = "accdb_source"    # Access 取り込みの元 .accdb
KEY_ACCDB_TARGET = "accdb_target"    # Access 取り込みの保存先 .sqlite3(空ならいまの接続先フォルダに同じ名前で)
KEY_LAST_USER = "last_user"          # 前回のログイン ID(入力欄の初期値)


def is_relative_setting(text: str) -> bool:
    """`\\\\server\\...` や `C:\\...` でなければ相対(アプリのフォルダ基準)。"""
    t = (text or "").strip()
    if not t:
        return False
    if t.startswith(("\\\\", "//")):
        return False
    if len(t) >= 2 and t[1] == ":":
        return False
    return not Path(t).is_absolute()


def resolve_dir(text: str, base: Optional[Path] = None) -> Path:
    """設定の文字列を実際のフォルダに直す。相対はアプリのフォルダ基準。"""
    t = (text or "").strip()
    root = base if base is not None else BASE_DIR
    if not t:
        return root
    if is_relative_setting(t):
        return (root / t).resolve()
    return Path(t)


def ensure_dirs() -> None:
    """ログと設定の置き場所を作る(既にあれば何もしない)。"""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    USER_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
