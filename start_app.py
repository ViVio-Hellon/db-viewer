#!/usr/bin/env python3
"""Python側の起動開始点 (基盤仕様書 2.5)

アプリ本体を読む前に実行環境を整え、多重起動を判定し、サーバを立てて
ブラウザを開く。**業務機能はここに書かない。**

    Start.vbs (通常) / start.bat (診断)
        └─ start_app.py            ← ここ
             ├─ 実行環境の確認      (Python版数・必須パッケージ・書込権限)
             ├─ launch_guard        (多重起動の判定・ポート選び)
             ├─ server              (waitress + Flask)
             └─ ブラウザを開く

使い方:

    python start_app.py                  起動する
    python start_app.py --no-browser     ブラウザを開かない(検証用)
    python start_app.py --check          環境の確認だけして終わる(診断用)
"""
from __future__ import annotations

import argparse
import os
import sys
import threading
import time
import webbrowser
from pathlib import Path
from typing import Optional

APP_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(APP_ROOT))

# 起動の起点。**待機画面が出るまでの時間**をログに残すために持つ ──
# 「遅い」という感想を、測れる数字にしておく
_BOOT_AT = time.monotonic()

# 必要なPython。README が 3.9 以上を掲げているので、それに合わせる
MIN_PYTHON = (3, 9)

# `requirements.txt` に対応する import 名(pip の名前と違うものがある)
REQUIRED_PACKAGES = (("flask", "Flask"), ("waitress", "waitress"))

# ブラウザを開いたあと、待ち受けが始まるのを待つ上限(秒)
LISTEN_TIMEOUT_SEC = 15

# `--mode` の既定。**どのショートカットを押したかでは決めない**の意味。
# 開くモードは `アクセス権限` マスタが端末ごとに決める(基盤仕様書 2.1
# 「利用者向けの通常起動ファイルを1つに絞る」)
AUTO = "auto"


# デスクトップ版(`bridge.py`)は待ち受けないので waitress は要らない
BRIDGE_PACKAGES = (("flask", "Flask"),)

# もう一方の版(ブラウザ版 / デスクトップ版)が動いているので止まった
CODE_OTHER_RUNNING = "other_running"


class StartupError(RuntimeError):
    """利用者に見せる、次の行動が分かる形のエラー。`code` は外枠(exe)が見分けに使う。"""

    def __init__(self, message: str, hint: str = "", code: str = "") -> None:
        super().__init__(message)
        self.hint = hint
        self.code = code


# ------------------------------------------------------------------
# 1. 実行環境の確認
# ------------------------------------------------------------------
def check_python_version() -> None:
    if sys.version_info < MIN_PYTHON:
        raise StartupError(
            f"Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} 以上が必要です"
            f"(いまは {sys.version.split()[0]})",
            "https://www.python.org/downloads/ から新しいPythonを入れてください。")


def check_packages(packages=None) -> None:
    """必須パッケージの有無。**入れ方まで示す**(基盤仕様書 ステップ5)。

    既定(`None`)はブラウザ版の一覧。
    """
    import importlib.util

    if packages is None:
        packages = REQUIRED_PACKAGES
    missing = [pip_name for module, pip_name in packages
               if importlib.util.find_spec(module) is None]
    if missing:
        raise StartupError(
            f"必要なパッケージが入っていません: {', '.join(missing)}",
            f"コマンドプロンプトで次を実行してください:\n"
            f"    {console_python()} -m pip install -r requirements.txt")


def console_python() -> str:
    """`pip` を実行するときに使うPythonの名前。

    `Start.vbs` は画面を出さないために **pythonw.exe** で起動する。
    そのまま `sys.executable` を案内すると
    `pythonw.exe -m pip install ...` と出るが、pythonw には画面が無いので
    **実行しても何も表示されない**(成否すら分からない)。
    案内するときは必ずコンソール側の `python.exe` に読み替える。
    """
    # Windowsのパスを他のOSで扱うと `Path` が `\` を区切りとみなさない。
    # 案内文を組み立てるだけなので、両方の区切りで切っておく
    name = sys.executable.replace("\\", "/").rsplit("/", 1)[-1]
    if name.lower().startswith("pythonw"):
        return "python" + name[len("pythonw"):]
    return name


def should_abort(check) -> bool:
    """起動確認が取れなかったとき、起動そのものを中止するか。

    中止するのは **TCPでも繋がらないとき**だけ。
    TCPが通っているならサーバ自身は待ち受けており、応答を取れないのは
    こちら側の確認経路の都合(プロキシ・セキュリティ製品)であることが
    多い。ブラウザはローカルアドレスをプロキシから除外するのが普通なので、
    そのまま開けばつながる。

    以前はここを区別せず中止していたため、**動いているサーバごと**
    終わらせてしまい、現場では「起動できません」としか見えなかった。
    """
    return not check.ok and not check.tcp_ok


def describe_loopback() -> str:
    """自分自身への通信まわりの状態(診断用)。

    「サーバは起動しているのに画面が出ない」の原因はここに集まる。
    社内PCではプロキシ設定に 127.0.0.1 の除外が無いことがあり、
    **自分自身への通信までプロキシへ送られて失敗する**。
    実際にこれで起動できなかったので、確認できるようにしておく。
    """
    import launch_guard

    lines = ["", "--- 自分自身への通信 ---"]
    proxies = launch_guard.proxy_settings()
    if proxies:
        lines.append("プロキシ設定  : "
                     + ", ".join(f"{k}={v}" for k, v in sorted(proxies.items())))
        lines.append("  ※このアプリはプロキシを経由せずに 127.0.0.1 へ接続します。")
        lines.append("    ブラウザ側でも 127.0.0.1 / localhost が除外されているか"
                     "確認してください。")
    else:
        lines.append("プロキシ設定  : なし")
    return "\n".join(lines)


def check_writable() -> Path:
    """ローカル領域を作れるか(基盤仕様書 2.7)。"""
    from dbviewer import app_config

    try:
        root = app_config.ensure_local_dirs()
    except OSError as exc:
        raise StartupError(
            f"作業用フォルダを作れません: {exc}",
            "書き込みの権限があるか、ディスクの空きがあるか確認してください。") from None

    probe = root / "runtime" / ".write-test"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        raise StartupError(
            f"作業用フォルダに書き込めません: {root}",
            f"{exc}") from None
    return root


def check_config() -> None:
    """アプリ固有値が読めているか。読めなくても既定値で動くが、記録は残す。"""
    from dbviewer import app_config

    error = app_config.load_error()
    if error:
        log().warning("%s — 既定値で起動します", error)
    for problem in app_config.port_range_conflicts():
        log().warning("%s", problem)


def run_environment_checks(*, bridge: bool = False) -> Path:
    """順に確認する。落ちたところで理由が分かるように分けてある。"""
    check_python_version()
    check_packages(BRIDGE_PACKAGES if bridge else None)
    root = check_writable()
    check_config()
    return root


# ------------------------------------------------------------------
# ログ
# ------------------------------------------------------------------
_log = None


def log():
    """起動入口のログ(基盤仕様書 2.6 の `launcher.log` にあたる)。

    `dbviewer.logging_utils` は業務ログ(`app.log`)なので、
    「起動前に失敗したのか」を切り分けられるよう名前を分ける。
    """
    global _log
    if _log is None:
        from dbviewer.logging_utils import get_logger
        _log = get_logger("launcher")
    return _log


def log_environment(mode: str) -> None:
    """起動のたびに残す1枚(基盤仕様書 2.6)。"""
    from dbviewer import app_config

    log().info("=" * 60)
    log().info("起動: mode=%s pid=%s", mode, os.getpid())
    log().info("Python: %s (%s)", sys.version.split()[0], sys.executable)
    log().info("アプリ本体: %s", APP_ROOT)
    log().info("ローカル領域: %s", app_config.local_root())


# ------------------------------------------------------------------
# 2. 起動
# ------------------------------------------------------------------
def resolve_mode(requested: str) -> str:
    """開くモード。DBビューアは1つしか無い(`dbviewer/modes.py`)。"""
    from dbviewer import modes
    return modes.normalize(requested if requested != AUTO else "")


def start(mode: str, *, open_browser: bool = True) -> int:
    """戻り値はプロセスの終了コード。

    【順番が要点】
    待機画面より前に置くものを、**できるだけ減らしてあります**。
    以前は Flask とアプリ本体を組み立ててからブラウザを開いていたので、
    いちばん重い import を「待たせるために見せるもの」より先にやって
    いました ── 出るころには待つ理由がほぼ終わっています。

        ポートを決める → 待ち受け開始(待機画面だけ)→ ブラウザ
            → 本体を組み立てて差し替え → 重い初期化

    最初の3つは標準ライブラリと `app_config` だけで済みます。
    """
    import launch_guard
    import server as server_module
    from dbviewer import app_config

    mode = resolve_mode(mode)
    log_environment(mode)

    # --- 多重起動の判定 (基盤仕様書 2.4) ---
    #
    # **まずロックを取る。** 判定してから書くまでのあいだに始まった
    # 2つ目は「ロックなし」を見て一緒に立ち上がってしまう ── 待ち受けの
    # 確認だけで最大15秒あり、その日の最初なら取り込みも走るので、
    # 利用者が「反応が無い」ともう一度押す時間は十分にある。
    # `try_acquire` は作れたかどうかで決めるので、2つ目は必ず外れる。
    while not launch_guard.try_acquire(mode):
        guard = launch_guard.check_existing(mode)
        if not guard.should_start:
            log().info("既存のインスタンスに合流します: %s", guard.url)
            print(f"すでに起動しています。ブラウザを開きます: {guard.url}")
            if open_browser:
                webbrowser.open(guard.url)
            return 0
        # 掃除できた(死んでいた・古い版だった)ので取り直す。
        # それでも取れなければ、掃除した隙に別の1つ目が入っている
        log().info("多重起動の判定: %s", guard.reason)

    # ここから先の失敗は**必ずロックを手放してから**投げる。
    # 残すと、次の起動が「起動中のまま応答がない」を待つことになる
    try:
        # --- デスクトップ版が動いていれば止まる(同時に動かさない)---
        if launch_guard.hold_app_lock(launch_guard.KIND_BROWSER) is None:
            message, hint = launch_guard.other_running_message(launch_guard.KIND_BROWSER)
            raise StartupError(message, hint, CODE_OTHER_RUNNING)

        # --- ポート選び ---
        port = launch_guard.pick_port(mode)
        if port is None:
            candidates = app_config.port_candidates(mode)
            raise StartupError(
                f"使えるポートがありません(試した番号: {candidates})",
                "他のアプリが使っている可能性があります。"
                "config/app.json の port を変えるか、そのアプリを終了してください。")

        # --- 待ち受けを始める(この時点ではまだ待機画面だけ) ---
        srv = server_module.AppServer(mode, port)
        thread = server_module.run_in_background(srv)

        check = server_module.diagnose_listening(port, timeout=LISTEN_TIMEOUT_SEC)
        if should_abort(check):
            raise StartupError(
                "サーバを起動できませんでした",
                f"{check.hint}\n\nログ: {app_config.local_dir('logs')}")
    except BaseException:
        launch_guard.remove_lock(mode)
        raise
    if not check.ok:
        log().warning("起動確認の応答を取れませんでしたが、待ち受けは"
                      "できているので続行します:\n%s", check.hint)
        print("[注意] 起動の確認応答を取れませんでした。"
              "画面が出ない場合は次を確認してください:")
        print(check.hint)

    # 待ち受けが始まってからロックを書く。先に書くと、起動に失敗した
    # ロックが残って次回の判定を惑わせる
    launch_guard.write_lock(
        launch_guard.build_lock_info(mode, port, srv.token))

    try:
        # --- ブラウザを開く ---
        # **ここまでが最短**。待機画面はもう出せる状態で、本体の
        # 組み立ては次の行から始まる
        if open_browser:
            log().info("ブラウザを開きます: %s", srv.url)
            webbrowser.open(srv.url)
        else:
            print(f"起動しました: {srv.url}")
        log().info("待機画面まで %.2f秒", time.monotonic() - _BOOT_AT)

        # --- 本体を組み立てて差し替える ---
        try:
            srv.build()
        except Exception as exc:                  # noqa: BLE001 - 画面に出して継続
            log().exception("アプリを組み立てられませんでした")
            srv.boot.mark_error(f"アプリを組み立てられませんでした: {exc}")
            _hold_after_failure(srv, thread)
            return 1

        _initialize(srv)

        # --- 待ち受けが終わるまでここで止まる ---
        _hold_until_stopped(srv, thread)
        return 0
    finally:
        tidy_children()
        launch_guard.remove_lock(mode)
        launch_guard.release_app_lock()
        log().info("終了しました: mode=%s", mode)


# 本体を組み立てられなかったとき、画面からの問い合わせがこの秒数来なければ終わる。
# 待機画面は理由を出したあとも数秒ごとに問い合わせる(= 見ている)。タブを閉じれば途絶える
FAILED_IDLE_SEC = 90.0


def _hold_after_failure(srv, thread, *, watch_screen: bool = True) -> None:
    """本体を組み立てられなかったまま **Python を残さない**(0.11.1。all-tools で起きた)。

    待機画面は理由を出したまま残る(閉じると理由が読めないので)。以前はここで止まる道が無く、
    stop.bat でも止まらずに Python が残り、フォルダを消せなくなった。いまは:
      - stop.bat・画面の「終了する」… 待機画面が `/api/shutdown` に答える(`BootApp._shutdown`)
      - 画面を閉じた … 問い合わせが `FAILED_IDLE_SEC` 秒来なければ自分で終わる
    デスクトップ版(`watch_screen=False`)は窓を閉じると外枠が終わらせる。
    """
    while thread.is_alive() and not srv.stop_requested:
        thread.join(timeout=1.0)
        if watch_screen and srv.boot.idle_seconds() > FAILED_IDLE_SEC:
            log().warning("起動に失敗したまま画面が %.0f秒 来ないので終了します", FAILED_IDLE_SEC)
            srv.stop()
    _hold_until_stopped(srv, thread)


# 停止を頼んでから、受付の輪が終わるのを待つ上限(秒)。
# ここを過ぎたら**確実に落とす** ── 残ったプロセスは次回の起動で
# 「すでに起動しています」と判定され、入れ替えた新しい版が動かない
EXIT_WAIT_SEC = 6.0


def _hold_until_stopped(srv, thread) -> None:
    """待ち受けが終わるまで止まる。**終わらなくても必ず抜ける。**

    waitress の受付の輪は、つながったままの接続が1本でもあると
    回り続けます(`server.AppServer.stop` の説明)。そちらは直して
    ありますが、**ここでも受け止めておきます** ── 止まらないことの
    害が大きすぎるからです。残ったプロセスは、次の起動で
    「すでに起動しています」と判定され、入れ替えた新しい版が
    いつまでも動きません。
    """
    while thread.is_alive():
        thread.join(timeout=0.5)
        if not srv.stop_requested:
            continue
        # 停止を頼んである。ここからは上限つきで待つ
        thread.join(timeout=EXIT_WAIT_SEC)
        if thread.is_alive():
            log().warning("待ち受けが %.0f秒 で終わらないので、"
                          "プロセスを終了します", EXIT_WAIT_SEC)
            _hard_exit()
        return


def _hard_exit() -> None:
    """後始末をしてから、確実に落とす。

    `sys.exit()` は例外なので、受付の輪が回っている別スレッドには
    効きません。`os._exit()` は後始末をしないので、**残すべきものは
    先に書いてから**呼びます(ログは追記なので閉じなくても失われない)。
    """
    import logging

    tidy_children()
    logging.shutdown()
    os._exit(0)


def leave_app_folder() -> None:
    """プロセスの「いまいるフォルダ」を、アプリのフォルダの外(ローカル領域の work)へ移す(0.25.0)。

    Windows は、**どれかのプロセスがいまいるフォルダを消せない。** プレビューで起こす cscript・Excel、
    「現物を開く」で開いた Excel・Word、「フォルダを開く」のエクスプローラは、このプロセスのいまいるフォルダを
    引き継ぐ。アプリのフォルダのままだと、それらが残っているあいだ **新しい版にするためにフォルダごと消せない**
    (stop.bat を使うまで消えない、と言われた正体)。読み込む場所はどれも `__file__` から決めているので、
    移っても困らない(sys.path にはアプリのフォルダを絶対パスで入れておく。後から読み込む部品のため)。"""
    root = str(APP_ROOT)
    sys.path[:] = [root if p in ("", ".") else p for p in sys.path]
    if root not in sys.path:
        sys.path.insert(0, root)
    try:
        from dbviewer import app_config
        target = app_config.local_dir("work")
        target.mkdir(parents=True, exist_ok=True)
        os.chdir(target)
    except OSError as exc:
        log().warning("いまいるフォルダをアプリの外へ移せませんでした(続けます): %s", exc)


def tidy_children() -> None:
    """終わるときの片付け: プレビューで起こして残った隠れた Excel・Word を止める(0.25.0)。

    今までは次の起動と stop.bat でしか片付けておらず、「終了」のあとも残ってフォルダを消せないことがあった。
    止めるのは、このアプリが起こして記録した PID だけ(利用者が開いた Excel・Word には触らない)。"""
    try:
        from dbviewer.office_bridge import procs as office_procs
        office_procs.cleanup_leftovers(log())
    except Exception:                             # noqa: BLE001 - 片付けられなくても終わる
        log().warning("残った Excel・Word を確かめられませんでした", exc_info=True)


def start_bridge(mode: str, *, token: str = "", server_factory) -> int:
    """デスクトップ版の起動(`bridge.py` から)。**ポートを使わない。**

    窓・多重起動の防止・終了は外枠(Rust/Tauri)が持つ。ここでするのはブラウザ版と同じ
    「待機画面 → 本体を組み立てる → 初期化」だけで、中身(`_initialize`)は共有する
    (python-web-tools と同じ作り)。**ブラウザ版が動いていれば止まる。**
    """
    import secrets

    import launch_guard
    import server as server_module

    mode = resolve_mode(mode)
    leave_app_folder()
    log_environment(mode)
    if launch_guard.hold_app_lock(launch_guard.KIND_DESKTOP) is None:
        message, hint = launch_guard.other_running_message(launch_guard.KIND_DESKTOP)
        raise StartupError(message, hint, CODE_OTHER_RUNNING)
    try:
        srv = server_factory(mode, token or secrets.token_urlsafe(24))
        thread = server_module.run_in_background(srv)
        # stop.bat・状態表示が「デスクトップ版が動いている」と分かるように
        launch_guard.write_desktop_marker(mode)
        log().info("待機画面まで %.2f秒(デスクトップ版)", time.monotonic() - _BOOT_AT)
        try:
            srv.build()
        except Exception as exc:                  # noqa: BLE001 - 画面に出して継続
            log().exception("アプリを組み立てられませんでした")
            srv.boot.mark_error(f"アプリを組み立てられませんでした: {exc}")
            _hold_after_failure(srv, thread, watch_screen=False)
            return 1
        # 窓を閉じたら外枠が終わらせるので、心拍による自動終了は使わない
        _initialize(srv, watch_idle=False)
        _hold_until_stopped(srv, thread)
        log().info("終了しました: mode=%s(デスクトップ版)", mode)
        return 0
    finally:
        tidy_children()
        launch_guard.remove_desktop_marker()
        launch_guard.release_app_lock()


def _initialize(srv, *, watch_idle: bool = True) -> None:
    """重い初期化。サーバが立ってから行う。

    ここで失敗しても**サーバは落とさない**。落とすと利用者のブラウザには
    「接続できません」としか出ず、理由が伝わらない。画面に理由を出す。
    """
    from dbviewer import shared_db
    from dbviewer.office_bridge import procs as office_procs

    # 前にプレビューで起こした隠れた Excel・Word が残っていれば止める(アプリごと止められたとき)
    try:
        office_procs.cleanup_leftovers(log())
    except Exception:                             # noqa: BLE001 - 片付けられなくても起動は続ける
        log().warning("残った Excel・Word を確かめられませんでした", exc_info=True)

    try:
        srv.mark_stage("アプリを準備中")
        # 配布設定(ツール直下の `配布設定\`)があれば、**接続先を見るより先に**読む(0.30.0)
        # ── 設定ファイル・開く DB の場所が入っているので、読む前に見ると既定の場所を見る
        from dbviewer import distribution
        loaded = distribution.apply_on_start()
        if loaded.applied:
            log().info("配布設定を読み込みました: %s", ", ".join(loaded.applied))
        # 接続先の自己診断。**結果はログに残す。** 読めなくても起動は止めない
        # (設定画面の「いまの状態」が同じ内容を画面にも出す)
        info = shared_db.probe()
        if info["exists"]:
            log().info("業務DB: %s (%s バイト)", info["path"], f"{info['size']:,}")
        else:
            log().warning("業務DBが見つかりません: %s", info["path"])
    except Exception as exc:                      # noqa: BLE001 - 画面に出して継続
        log().exception("初期化に失敗しました")
        srv.mark_error(f"初期化に失敗しました: {exc}")
        # 失敗しても、画面が居なくなったら終わる見張りは付ける(付けないと Python が残る)
        if watch_idle:
            _watch_for_idle(srv)
        return

    # 画面が居なくなったら終わる(基盤仕様書 2.8)。デスクトップ版は窓が閉じたら終わる
    if watch_idle:
        _watch_for_idle(srv)
    srv.mark_ready(True)


def _watch_for_idle(srv) -> None:
    """画面が居なくなったら止める見張りを立てる。

    止め方は `/api/shutdown` と同じ道(`srv.stop`)を通します ──
    止め方が2つあると、片方だけ直した状態を作ってしまいます。
    処理中かどうかの判断も同じものを使います。
    """
    from dbviewer import idle_exit, jobs

    def busy() -> bool:
        return bool(jobs.get_registry().busy_labels())

    idle_exit.install(srv.stop, busy)


# ------------------------------------------------------------------
# 3. 失敗の伝え方
# ------------------------------------------------------------------
def report_failure(error: StartupError, *, open_browser: bool) -> None:
    """コンソールとブラウザの両方に出す。

    `Start.vbs`(コンソール非表示)で起動された場合、標準出力は誰にも
    見えない。基盤仕様書 2.2 の「起動できない場合は、確認すべきログの
    場所を表示します」を満たすため、HTMLを書いてブラウザで開く。
    """
    print(f"\n[エラー] {error}", file=sys.stderr)
    if error.hint:
        print(error.hint, file=sys.stderr)

    try:
        from dbviewer import app_config
        log_dir = str(app_config.local_dir("logs"))
    except Exception:                             # noqa: BLE001 - 失敗の報告で失敗しない
        log_dir = "(ローカル領域を特定できませんでした)"

    try:
        log().error("起動に失敗: %s / %s", error, error.hint)
    except Exception:                             # noqa: BLE001
        pass

    if not open_browser:
        return
    try:
        path = _write_error_page(str(error), error.hint, log_dir)
        webbrowser.open(path.as_uri())
    except Exception as exc:                      # noqa: BLE001
        print(f"(エラー画面を出せませんでした: {exc})", file=sys.stderr)


def _write_error_page(message: str, hint: str, log_dir: str) -> Path:
    """起動に失敗したことを伝えるHTMLを一時領域に書く。"""
    import html
    import tempfile

    try:
        from dbviewer import app_config
        target = app_config.local_dir("work") / "起動エラー.html"
        target.parent.mkdir(parents=True, exist_ok=True)
    except Exception:                             # noqa: BLE001
        target = Path(tempfile.gettempdir()) / "dbviewer_起動エラー.html"

    target.write_text(f"""<!doctype html>
<html lang="ja"><head><meta charset="utf-8">
<title>起動できませんでした</title>
<style>
 body{{margin:0;min-height:100vh;display:grid;place-items:center;
       background:#eef1f5;color:#101720;
       font-family:system-ui,"Yu Gothic UI","Meiryo UI",sans-serif;line-height:1.7}}
 .box{{width:min(560px,calc(100vw - 48px));background:#fff;border:1px solid #c9d2dc;
       border-radius:4px;padding:32px;box-shadow:0 6px 20px rgba(16,23,32,.08)}}
 h1{{margin:0 0 12px;font-size:19px;color:#b4232a}}
 .hint{{margin-top:16px;padding:14px;background:#fdeaea;border-left:4px solid #b4232a;
        border-radius:0 3px 3px 0;white-space:pre-wrap}}
 code{{font-family:ui-monospace,Consolas,monospace;font-size:13px;
       background:rgba(0,0,0,.06);padding:2px 5px;border-radius:2px;word-break:break-all}}
 dt{{color:#556171;font-size:13px;margin-top:12px}}
</style></head>
<body><main class="box">
<h1>起動できませんでした</h1>
<p>{html.escape(message)}</p>
{f'<div class="hint">{html.escape(hint)}</div>' if hint else ''}
<dt>ログの場所</dt>
<p><code>{html.escape(log_dir)}</code></p>
<dt>診断</dt>
<p>コンソールで詳しく見るには <code>start.bat</code> を実行してください。</p>
</main></body></html>
""", encoding="utf-8")
    return target


# ------------------------------------------------------------------
# CLI
# ------------------------------------------------------------------
def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="DBビューアを起動する")
    # 既定は auto ── **この端末に許されたモード**で開く。
    # 旧名(warehouse)も受ける。現場に配ったショートカットがまだ
    # そちらで書かれている。値の対応は `dbviewer/modes.py` が持つが、
    # ここは環境確認より前なのでパッケージを読み込まない(2.5 起動処理の分離)
    parser.add_argument("--mode", default=AUTO, choices=[AUTO, "main"],
                        help="開くモード(DBビューアは main の1つだけ)")
    parser.add_argument("--no-browser", action="store_true",
                        help="ブラウザを開かない(検証用)")
    parser.add_argument("--check", action="store_true",
                        help="実行環境の確認だけして終わる(診断用)")
    args = parser.parse_args(argv)

    open_browser = not args.no_browser

    try:
        run_environment_checks()
    except StartupError as exc:
        report_failure(exc, open_browser=open_browser)
        return 1

    if args.check:
        from dbviewer import app_config
        print("実行環境の確認: 問題ありません\n")
        print(app_config.describe())
        print(describe_loopback())
        return 0

    leave_app_folder()
    try:
        return start(args.mode, open_browser=open_browser)
    except StartupError as exc:
        report_failure(exc, open_browser=open_browser)
        return 1
    except KeyboardInterrupt:
        print("\n中断しました")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
