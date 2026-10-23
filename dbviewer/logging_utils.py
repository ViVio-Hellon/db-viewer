"""ログ出力 (VBA `DebugLog`/`InitializeDebugLog` 相当)

VBA版はFileSystemObjectで手動追記し、さらに端末のライン名を
レジストリから推測してサブフォルダ分けする、という凝った作りだったが、
Python版ではファイル配置の複雑さの割に価値が薄いため、標準の
`logging` モジュールに置き換える。

- 出力先: 置き場所の中のツール名のフォルダ(config.LOG_DIR = `<置き場所>\\dbviewer`。0.33.0)に
  `dbviewer_<PC名>_YYYYMMDD.log`(PC 名を入れるのは、
  設定で共有フォルダを記録の置き場所にしたとき、PC ごとにファイルを分けるため)
- 置き場所は設定「ログ」で変えられる(`set_log_dir` / `apply_settings`)
- 操作の記録(なぜなぜ分析用)は別のファイル(`trace.py`)
- コンソール(Debug.Printのイミディエイト相当)にも同時出力
"""
from __future__ import annotations

import logging
import os
from datetime import date
from pathlib import Path

from . import config

_configured = False


def log_path_for(day: date) -> "Path":
    """その日のログファイル。**名前の作り方はここ1か所**。"""
    from .trace import pc_name
    return config.LOG_DIR / f"{config.TOOL_LOG_NAME}_{pc_name()}_{day:%Y%m%d}.log"


class DailyFileHandler(logging.FileHandler):
    """日付が変わったら、その日のファイルへ書き換える。

    **開いたままの端末のため。** 書き出し先を起動時に1度だけ決めると、
    夜勤をまたいだ端末は翌日ぶんを前日のファイルに書き続ける ── 13日の
    不具合を追うときに、13日のログが12日のファイルに入っている。

    `TimedRotatingFileHandler` を使わないのは、あちらが「いまのファイルを
    日付付きの名前へ退ける」作りで、こちらの名前の付け方
    (`dbviewer_YYYYMMDD.log` にそのまま書く)と噛み合わないため。
    日付を見て開き直すだけで足りる。
    """

    def __init__(self, day: date, **kwargs) -> None:
        self._day = day
        super().__init__(log_path_for(day), **kwargs)

    def reopen(self) -> None:
        """置き場所が変わった(設定「ログ」)。次の 1 行から新しい場所へ。"""
        self.acquire()
        try:
            self.close()
            self._day = date.today()
            self.baseFilename = str(log_path_for(self._day))
            self.stream = None
        finally:
            self.release()

    def emit(self, record: logging.LogRecord) -> None:
        # **書く直前に見る。** 日付が変わったことは、次の1行で気づく
        today = date.today()
        if today != self._day:
            self._day = today
            self.close()
            self.baseFilename = str(log_path_for(today))
            self.stream = None                    # 次の emit で開き直す
        super().emit(record)


def configure_logging() -> None:
    """アプリ起動時に一度だけ呼ぶ。二重呼び出しは無害(冪等)。"""
    global _configured
    if _configured:
        return

    config.ensure_dirs()

    root = logging.getLogger("dbviewer")
    root.setLevel(logging.DEBUG)

    formatter = logging.Formatter("%(asctime)s | %(name)s | %(levelname)s | %(message)s",
                                   datefmt="%Y/%m/%d %H:%M:%S")

    file_handler = DailyFileHandler(date.today(), encoding="utf-8")
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    # コンソールが無いときは付けない。
    # **`pythonw.exe` では `sys.stderr` が `None`** になる(Start.vbs は
    # コンソールを出さないために pythonw を使う)。`StreamHandler()` は
    # そのとき `stream = None` を抱え、1行出すたびに `None.write` で
    # 例外を起こす。`logging` が握りつぶすので表には出ないが、
    # 選定アルゴリズムは実データ1件で数千行のDEBUGを出すため、
    # 誰にも見えない例外をその回数ぶん払うことになる。
    if _has_console():
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        root.addHandler(console_handler)

    _configured = True


def probe_writable(folder: "Path") -> str:
    """書けるか試す。書ければ ""、だめなら理由。"""
    try:
        folder.mkdir(parents=True, exist_ok=True)
        probe = folder / f".dbviewer_probe_{os.getpid()}"
        probe.write_text("x", encoding="utf-8")
        probe.unlink()
        return ""
    except OSError as exc:
        return str(exc)


def set_log_dir(folder: "Path") -> str:
    """記録の置き場所を変える(動いたまま)。書けなければ変えずに理由を返す。"""
    why = probe_writable(folder)
    if why:
        return why
    config.LOG_DIR = folder
    for handler in logging.getLogger("dbviewer").handlers:
        if isinstance(handler, DailyFileHandler):
            handler.reopen()
    return ""


def resolve_log_dir(text: str) -> "Path":
    """設定の文字 → 書くフォルダ(その中のツール名のフォルダ。0.33.0)。空なら既定(この PC のローカル領域)、
    相対はアプリのフォルダ基準。"""
    text = (text or "").strip()
    return config.tool_log_dir(config.resolve_dir(text)) if text else config.DEFAULT_LOG_DIR


def apply_settings() -> dict:
    """利用者設定の「記録の置き場所」を当てる。だめなら既定の場所のまま、理由を返す。

    戻り値: {"dir": 実際の置き場所, "requested": 設定の文字, "problem": 理由(無ければ "")}
    """
    from . import user_settings
    text = str(user_settings.get(config.KEY_LOG_DIR, "") or "").strip()
    target = resolve_log_dir(text)
    problem = set_log_dir(target)
    if problem and target != config.DEFAULT_LOG_DIR:
        set_log_dir(config.DEFAULT_LOG_DIR)
        logging.getLogger("dbviewer").warning("記録の置き場所に書けないので既定の場所にします: %s (%s)",
                                              target, problem)
    return {"dir": str(config.LOG_DIR), "requested": text, "problem": problem}


def _has_console() -> bool:
    """標準エラー出力に書けるか。

    `pythonw.exe` は `sys.stderr` が `None`。リダイレクト先が閉じている
    場合に備えて `write` を持つかどうかまで見る。
    """
    import sys
    return getattr(sys, "stderr", None) is not None and hasattr(sys.stderr, "write")


def get_logger(name: str = "dbviewer") -> logging.Logger:
    """モジュール別ロガーを取得する(未初期化なら自動で初期化する)。"""
    configure_logging()
    return logging.getLogger(f"dbviewer.{name}" if name != "dbviewer" else name)


def silence_console() -> None:
    """コンソールへのログ出力だけを止める(ファイルへの出力は残す)。

    テストや診断スクリプトのように、**そのプログラム自身の出力が主役**で
    あって業務ログが主役ではない場面のためのもの。
    選定アルゴリズムはDEBUGを大量に出すため(実データ41通りで19万文字)、
    止めないと肝心の結果が流れて読めない。

    `get_logger()` はインポート時に呼ばれることがあり、その時点で既に
    コンソール用ハンドラが付いている。あとから `configure_logging()` が
    走る場合もあるので、既存のハンドラを外すだけでなく、
    以降の追加も弾くように `addHandler` を包む。

    ファイルへの出力は残すので、調査に必要な情報は失われない。
    出力先は `DBVIEWER_LOG_DIR` で変えられる。
    """
    logger = logging.getLogger("dbviewer")

    def _is_console(handler: logging.Handler) -> bool:
        # FileHandler は StreamHandler の派生なので先に除外する
        return (isinstance(handler, logging.StreamHandler)
                and not isinstance(handler, logging.FileHandler))

    for handler in list(logger.handlers):
        if _is_console(handler):
            logger.removeHandler(handler)

    if getattr(logger, "_console_silenced", False):
        return                                  # 二重に包まない(冪等)

    original_add = logger.addHandler

    def add_handler(handler: logging.Handler) -> None:
        if _is_console(handler):
            return
        original_add(handler)

    logger.addHandler = add_handler             # type: ignore[method-assign]
    logger._console_silenced = True             # type: ignore[attr-defined]
