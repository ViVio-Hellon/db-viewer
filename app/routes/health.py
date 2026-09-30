"""起動確認・生存監視・停止 (基盤仕様書 2.3 / 2.8 / 2.9)

- `GET  /`            … 準備中なら起動待機画面、終わっていればアプリ本体へ
- `GET  /api/health`  … 起動確認と生存監視。**トークン不要**
- `POST /api/shutdown`… 安全な停止。トークン必須
"""
from __future__ import annotations

import os
import threading
import time
from typing import Optional

from flask import Blueprint, current_app, jsonify, redirect, request

from dbviewer import app_config
from dbviewer.logging_utils import get_logger

from .. import shell

log = get_logger("app.routes.health")

bp = Blueprint("health", __name__)

# 停止要求を受けてから実際に落とすまでの猶予(秒)。
# 応答を返しきる前にサーバを止めると、利用者側は「押したのに
# 何も起きなかった」ように見えるため、少し待ってから落とす。
SHUTDOWN_DELAY_SEC = 0.4

# `POST /api/shutdown` が呼ばれたときに実行する後始末。
# `server.py` が起動時に差し込む(ここが直接 waitress を知らないようにする)。
_shutdown_hook = None


def set_shutdown_hook(func) -> None:
    """サーバの止め方を登録する。"""
    global _shutdown_hook
    _shutdown_hook = func


@bp.get("/")
def index():
    """準備が終わるまでは起動待機画面を出す。

    基盤仕様書 2.3 は「ブラウザーだけが先に開き、接続エラーや未完成の
    画面が表示されることを防ぐ」ことを求めている。サーバ自身が待機画面を
    出すことで、**起動直後でも必ず何かが表示される**状態を作る。
    """
    if not current_app.config["READY"]:
        # **骨格は `boot_screen` に1つだけ。** 起動サーバ(Flask が
        # 読み込まれる前に出すほう)と同じものを出す ── 2枚持つと、
        # 直したほうと直していないほうが場面によって出る
        from dbviewer import boot_screen

        return boot_screen.render(
            display_name=current_app.config["DISPLAY_NAME"],
            # 起動中も版が読める。帯がまだ出ていない場面なので、
            # ここに出さないと「どれが入っているか」を確かめる先が無くなる
            version_label=app_config.version_label(),
            token=current_app.config["TOKEN"],
            app_id=current_app.config["APP_ID"],
            poll_ms=app_config.job_poll_ms(),
            # 待たずに入る道。取り込みは背景で続き、帯に出る
            home_url=shell.home_url(current_app.config["MODE"]),
        )
    # 準備ができたら業務画面へ送る。
    # ここで止まる画面を出すと、`Start.vbs` から起動した利用者が
    # **アプリに入れない**(レールが無いので行き先が分からない)。
    # 実際にそうなっていた
    return redirect(shell.home_url(current_app.config["MODE"]), code=302)


@bp.get("/favicon.ico")
def favicon():
    """タブのアイコン。**中身なし(204)で返す。**

    画面(base.html)はモードの色のアイコンを埋め込んでいるが、帳票・
    起動待機・カタログのような単独のページは持たない。そのときブラウザは
    `/favicon.ico` を取りに来て、以前は 404 が返り、帳票を開くたびに
    エラーが1行記録されていた(通しの試験で見つけた)。
    """
    return "", 204


@bp.get("/api/health")
def health():
    """起動確認と生存監視。

    **`app_id` を返すのが要点**。同じポートを別のアプリが使っていても、
    HTTPが返るだけでは「自分と同じアプリが起動している」とは言えない
    (基盤仕様書 2.3)。多重起動の判定はこの値の一致で行う。

    業務データは含めないので、トークン無しで答えてよい。
    """
    config = current_app.config
    return jsonify({
        "app_id": config["APP_ID"],
        "display_name": config["DISPLAY_NAME"],
        "version": config["VERSION"],
        # **どのフォルダの、どの版が動いているか。**
        # 「入れ替えたのに古いまま」を調べるとき、これが無いと
        # 端末に行って確かめることになる(古いプロセスが残っている
        # のか、別のフォルダを起動しているのかが区別できない)
        "app_root": str(app_config.APP_ROOT),
        "mode": config["MODE"],
        "port": config["PORT"],
        "pid": os.getpid(),
        "ready": bool(config["READY"]),
        "stage": config["STAGE"],
        # どの段に居るか。**待機画面が段を組み立て直さない**ための鍵
        # (文言だけ渡すと、画面側が文字列から段を推し量ることになる)
        "stage_key": config.get("STAGE_KEY", "prepare"),
        "startup_error": config["STARTUP_ERROR"],
        "uptime_sec": round(time.time() - config["STARTED_AT"], 1),
        # いま走っているものの一言。**起動待機画面はこれを読む** ──
        # 取り込みが終わるまで待たせるので、何をどこまでやっているかを
        # 出さないと「止まっている」と受け取られる
        "job": _running_note(),
    })


def _running_note() -> Optional[dict]:
    """走っている処理の、待機画面に出すぶんだけ。"""
    from dbviewer import jobs

    running = jobs.get_registry().running()
    if running is None:
        return None
    return {"label": running.label, "pct": running.pct,
            "message": running.message}


@bp.get("/api/jobs")
def jobs():
    """長時間処理の状態(監視レベル2)。

    取り込み・書き戻しの進捗はここで取る。ブラウザを閉じて開き直しても
    ここを読めば状態が戻せる。走っているものが無くても直近の結果を返す
    ので、「さっきの取り込みはどうなったのか」が分かる。
    """
    from dbviewer.presenters import settings as settings_presenter
    return jsonify(settings_presenter.jobs_dict())


@bp.post("/api/alive")
def alive():
    """画面が生きていることの心拍(基盤仕様書 2.8「自動終了」)。

    **トークンは要らない。** ここで返すのは「受け取った」だけで、
    業務データは1つも含みません。心拍にトークンを要求すると、
    トークンが切れた画面が黙って死んだ扱いになります。

    `leaving=true` はタブを閉じた合図(`sendBeacon`)。猶予のあとで
    終わりますが、そのあいだに心拍が戻れば取り消されます。
    `visible=false` は裏に回った合図。ブラウザは裏のタブのタイマーを間引く
    (止める)ので、そう言った画面は心拍が途切れても落としません。
    `resumed=true` は表に戻った / スリープから戻った合図(`gap_ms` は止まっていた長さ)。

    タブの番号は本文で受け取ります ── `sendBeacon` にはヘッダを
    付けられないからです。
    """
    from dbviewer import idle_exit

    body = request.get_json(silent=True) or {}
    watch = idle_exit.get()
    if watch is None:
        return jsonify({"ok": True, "watching": False})

    # **画面ごとに見る。** 2つ開いているうちの1つを閉じても、もう1つは生きている
    screen = str(body.get("screen_id") or "")
    if body.get("leaving"):
        watch.leaving(screen)
    elif body.get("resumed"):
        # 表に戻った / スリープから戻った(裏のまま気づいたなら裏のまま)
        watch.resumed(screen, gap_sec=_number(body.get("gap_ms")) / 1000,
                      visible=body.get("visible") is not False)
    elif body.get("visible") is False:
        # 裏に回った。ブラウザが心拍を間引いても落とさない
        watch.hidden(screen)
    else:
        watch.beat(screen, visible=True if body.get("visible") else None)
    return jsonify({"ok": True, "watching": True})


def _number(value) -> float:
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return 0.0


@bp.post("/api/shutdown")
def shutdown():
    """安全な停止(基盤仕様書 2.8)。

    実行中の長時間処理があるときは、**止めずに理由を返す**。
    利用者に「中断して終了 / 完了を待つ / やめる」を選ばせるため。
    `force=true` を付けて呼び直すと中断して落とす。
    """
    force = bool((request.get_json(silent=True) or {}).get("force"))
    running = _running_jobs()
    if running and not force:
        return jsonify({
            "stopped": False,
            "reason": "busy",
            "running": running,
            "message": "実行中の処理があります。中断して終了しますか?",
        }), 409

    if _shutdown_hook is None:
        # サーバ抜きで組み立てた場合(テストなど)
        return jsonify({"stopped": False, "reason": "no_hook",
                        "message": "このプロセスは停止操作に対応していません"}), 501

    log.info("停止要求を受け付けました (force=%s)", force)
    threading.Timer(SHUTDOWN_DELAY_SEC, _shutdown_hook).start()
    return jsonify({"stopped": True, "message": "終了します"})


def _running_jobs() -> list[str]:
    """いま走っている長時間処理の名前。

    取り込みの途中で落とすと、DBが中途半端な状態で残る。
    止める前に必ずここを見る(基盤仕様書 2.8)。
    """
    from dbviewer import jobs as job_registry
    return job_registry.get_registry().busy_labels()
