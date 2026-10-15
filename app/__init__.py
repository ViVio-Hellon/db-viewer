"""Flask アプリの組み立て

python-web-tools(梱包資材総合ツール)の `app/__init__.py` を複製し、
モード(現場/資材)・アクセス権限マスタ・画面ロック(最後のタブ勝ち)を
外したもの。残しているのは基盤の守り:

- Host 検証(DNS リバインディング対策)
- 起動トークン(`X-Tool-Token` / `?t=`)と同一オリジン確認
- ほかのサイトからの画面読み込みの拒否
- 書く要求の直列化(`_WRITE_LOCK`)と、要求ごとの DB 接続の開閉
- 静的ファイルの URL への版付与とキャッシュ方針

【DBビューアの権限】
誰が何をできるかは VBA と同じく `ID` テーブルの Level が決める
(`dbviewer/auth.py`)。ログインは Phase 4 で `before_request` に足す。
"""
from __future__ import annotations

import secrets
import sys
import threading
import time
from html import escape
from pathlib import Path
from typing import Optional

from flask import Flask, g, jsonify, request

from dbviewer import app_config, config, db, idle_exit, modes
from dbviewer.logging_utils import get_logger


log = get_logger("app")

APP_DIR = Path(__file__).resolve().parent

# 起動トークンと同一オリジン確認を要求する経路。
#
# **業務データを返す経路はここに入れる。** `/api/*` だけを見ていたころ、
# Phase 6d で足した `/report/label` `/report/cut-request` が素通しになって
# いました(帳票にはLot番号・品名・板厚・寸法・梱包数が丸ごと入ります)。
# 画面側は `?t=` を付けて開いていたので、書いた側は守られているつもりで、
# テストもトークン付きで叩いていたため気づけませんでした。
#
# 画面のHTML(`/selection` 等)を入れないのは、ブラウザのアドレス欄から
# 開く経路だからです。中身は空の器で、業務データは `/api/*` から取ります。
TOKEN_REQUIRED_PREFIXES = ("/api/", "/report/")

# `/api/*` のうち、起動トークンを要求しないもの。
#
# `/api/health` を素通しにするのは、**まだトークンを知らない相手**が
# 正当に問い合わせる場面があるため:
#   - 多重起動の判定(基盤仕様書 2.4)。後から起動したプロセスは、
#     そのポートに居るのが自分と同じアプリかどうかを知る必要がある
#   - 起動待機画面が、アプリの準備が終わったかを確かめる(同 2.3)
# 返すのは識別情報と状態だけで、業務データは一切含めない。
#
# `/api/alive`(心拍)も素通しにします。返すのは「受け取った」だけで
# 業務データを含みません。トークンを要求すると、**トークンが切れた
# 画面が黙って死んだ扱いになり**、開いているのに終了してしまいます。
TOKEN_EXEMPT_PATHS = frozenset({"/api/health", "/api/alive"})



def create_app(mode: str = modes.DEFAULT, *,
               token: Optional[str] = None,
               port: Optional[int] = None) -> Flask:
    """アプリを1つ組み立てる。

    `mode` は基盤の起動制御が渡してくる名前(DBビューアは1つだけ)。
    `token` を省略すると起動ごとに新しく作る。テストからは固定値を渡せる。
    """
    requested = modes.normalize(mode)

    app = Flask(__name__,
                template_folder=str(APP_DIR / "templates"),
                static_folder=str(APP_DIR / "static"))

    app.config.update(
        MODE=requested,
        # 起動ごとの合言葉。同じPC上の別プロセスや、利用者が偶然開いた
        # 外部のWebページから叩かれないようにする
        TOKEN=token or secrets.token_urlsafe(32),
        PORT=port or app_config.port(requested),
        APP_ID=app_config.app_id(),
        VERSION=app_config.version(),
        DISPLAY_NAME=app_config.display_name(),
        STARTED_AT=time.time(),
        # 起動直後は準備中。重い初期化(スキーマ適用・自動取り込み)が
        # 終わってから True にする。起動待機画面はこれを見て切り替える
        READY=False,
        STAGE="アプリを準備中",
        STAGE_KEY="prepare",
        STARTUP_ERROR="",
        # セッションクッキーは使わないが、Flask の secret_key は
        # flash などが暗黙に要求することがあるので入れておく
        SECRET_KEY=secrets.token_hex(16),
    )

    _register_trace(app)          # 記録番号を振るので、いちばん先
    _register_security(app)
    _register_db(app)
    _register_static_version(app)
    _register_routes(app)

    log.info("create_app: mode=%s port=%s", requested, app.config["PORT"])
    _start_records(app)
    return app


# ------------------------------------------------------------------
# 記録(なぜなぜ分析のため)  dbviewer/trace.py
# ------------------------------------------------------------------
# 何度も来るだけの問い合わせ。記録すると埋もれる
_QUIET_PATHS = frozenset({"/api/health", "/api/alive", "/api/jobs", "/favicon.ico", "/api/log/client"})
RID_HEADER = "X-Request-Id"


def _start_records(app: Flask) -> None:
    """記録の置き場所(設定)を当て、古い記録を消し、起動を 1 行残す。"""
    from dbviewer import logging_utils, shared_db, trace, user_settings
    state = logging_utils.apply_settings()
    app.config["LOG_STATE"] = state
    keep = user_settings.get(config.KEY_LOG_KEEP_DAYS, config.DEFAULT_LOG_KEEP_DAYS)
    try:
        gone = trace.cleanup(int(keep))
    except (TypeError, ValueError):
        gone = 0
    try:
        db = str(shared_db.db_path())
    except Exception:                                  # noqa: BLE001
        db = ""
    trace.event("app.start", f"起動しました(版 {app.config['VERSION']})",
                level="WARN" if state["problem"] else "INFO",
                data={"app_root": str(config.BASE_DIR), "db": db, "log_dir": state["dir"],
                      "log_dir_problem": state["problem"], "removed_old_logs": gone,
                      "python": sys.version.split()[0], "mode": app.config["MODE"]})


def _register_trace(app: Flask) -> None:
    """要求ごとに記録番号を振り、書く操作・断り・エラーを残す。

    【記録番号】画面のエラーにも出す(`X-Request-Id`)。「エラーが出た」と言われたら、
    その番号で設定「記録」を引けば、どの画面で何を押し、何が返り、その前に何が
    あったかが一続きで読める。
    """
    import uuid

    from werkzeug.exceptions import HTTPException

    from dbviewer import trace

    @app.before_request
    def _rid():                                 # noqa: ANN202 - Flaskのフック
        g.rid = uuid.uuid4().hex[:8]
        g.t0 = time.perf_counter()

    @app.after_request
    def _record(response):                      # noqa: ANN202 - Flaskのフック
        rid = g.get("rid", "")
        if rid:
            response.headers[RID_HEADER] = rid
        path = request.path
        if path.startswith("/static/") or path in _QUIET_PATHS or g.get("recorded"):
            return response
        status = response.status_code
        is_api = path.startswith("/api/")
        if request.method == "GET" and status < 400:
            if not is_api and response.mimetype == "text/html":
                trace.event("screen", st=status)          # どの画面を開いたか
            return response
        ms = round((time.perf_counter() - g.get("t0", time.perf_counter())) * 1000)
        err = {}
        if response.is_json:
            try:
                body = response.get_json(silent=True) or {}
                err = body.get("error") or {}
                if not err and isinstance(body, dict) and body.get("ok") is False:
                    err = {"code": body.get("reason", ""), "message": body.get("message", "")}
            except Exception:                          # noqa: BLE001
                err = {}
        sent = trace.sent_body()
        level = "ERROR" if status >= 500 else "WARN" if status >= 400 or err else "INFO"
        trace.event("request", (err or {}).get("message", "") if isinstance(err, dict) else "",
                    level=level, st=status, ms=ms,
                    data={"sent": sent, "reason": (err or {}).get("code", "") if isinstance(err, dict) else ""})
        return response

    @app.errorhandler(Exception)
    def _unexpected(exc):                       # noqa: ANN202 - Flaskのフック
        """思わぬエラー。トレースバックを残し、画面には記録番号を出す。"""
        if isinstance(exc, HTTPException):
            return exc
        rid = g.get("rid", "")
        log.exception("思わぬエラー rid=%s %s %s", rid, request.method, request.path)
        sent = trace.sent_body()
        ms = round((time.perf_counter() - g.get("t0", time.perf_counter())) * 1000)
        trace.event("error.server", str(exc) or type(exc).__name__, level="ERROR", exc=exc,
                    st=500, ms=ms, data={"sent": sent})
        g.recorded = True
        message = (f"思わぬエラーが起きました。記録番号 {rid} を管理者に伝えてください"
                   f"(設定 →「記録」で中身を見られます)。")
        if request.path.startswith("/api/"):
            return jsonify(_error("server_error", message) | {"rid": rid}), 500
        return (f"<!doctype html><meta charset='utf-8'><title>エラー</title>"
                f"<p style='font:16px sans-serif;margin:2em'>{escape(message)}</p>"), 500


# ------------------------------------------------------------------
# セキュリティ (設計書 §3.6)
# ------------------------------------------------------------------
# ほかのサイトから読み込まれた画面を断るときの文。**画面そのもの(HTML)で返す**
# ── 利用者の目に入るのは新しいタブか、何も見えない埋め込みのどちらか
FOREIGN_PAGE_HTML = """<!doctype html><meta charset="utf-8">
<title>DBビューア</title>
<body style="font-family:sans-serif;padding:2em;line-height:1.8">
<h1 style="font-size:1.2em">この画面は、ほかのページから開かれたため表示しませんでした</h1>
<p>いま使っている画面を守るためです(ほかのページがこのツールを読み込むと、
使っている画面の操作ができなくなっていました)。</p>
<p>ツールはデスクトップのショートカット(Start.vbs)か、ツールの画面から開いてください。</p>
</body>"""


def _refuse_foreign_page():
    """**ほかのサイトのページ**が、このツールの画面を読み込んだら断る。

    画面(HTML)はアドレス欄から開けるようにトークン無しで返している。
    中身は読まれない(ブラウザが別のサイトには見せない)が、**読み込まれた
    だけで「新しいタブが開いた」扱いになり、利用者の画面が「このタブは、
    あとから開いたタブに操作を譲りました」で止まっていた**。
    ほかのサイトのタブが `<img>`・`<iframe>`・リンク・`window.open` で
    このアドレスを読むだけで起きる(試験で確かめた)。

    ブラウザは読み込みのたびに、どこから来たか(`Sec-Fetch-Site`)を付ける:
        none         … アドレス欄・ショートカット・ブックマーク → 通す
        same-origin  … このツールの画面から → 通す
        same-site    … 同じPCの別のポート(ほかのローカルの道具など) → 断る
        cross-site   … ほかのサイト → 断る
    付いていない(古いブラウザ・curl・試験)ときは通す。
    断るときは**画面を開いた扱いにしない**(使っているタブを取り上げない)。
    """
    if request.method != "GET" or request.path.startswith("/static/"):
        return None
    site = request.headers.get("Sec-Fetch-Site", "")
    if site not in ("cross-site", "same-site"):
        return None
    log.warning("ほかのページからの画面の読み込みを断りました: %s (%s)",
                request.path, site)
    return FOREIGN_PAGE_HTML, 403, {"Content-Type": "text/html; charset=utf-8"}


# ログイン無しで通す経路(VBA の Login フォームより前に触れるもの + 基盤)
LOGIN_EXEMPT_PREFIXES = ("/static/", "/api/settings/", "/api/fs/", "/api/log/")
LOGIN_EXEMPT_PATHS = frozenset({"/", "/login", "/api/login", "/api/logout", "/api/session",
                                "/api/health", "/api/alive", "/api/jobs", "/api/shutdown",
                                "/settings", "/favicon.ico"})


def _login_required():
    """VBA は Login を通らないと MF1 を出さない。Web も同じ(設定だけは前から触れる)。"""
    from dbviewer import auth
    from flask import redirect

    path = request.path
    if path in LOGIN_EXEMPT_PATHS or any(path.startswith(p) for p in LOGIN_EXEMPT_PREFIXES):
        s = auth.current()
        if s:
            s.touch()
        return None
    s = auth.current()
    if s is None:
        if path.startswith("/api/"):
            return jsonify(_error("login", auth.MSG_NEED_LOGIN)), 401
        return redirect("/login?next=" + path)
    s.touch()
    return None


# デスクトップ版(`bridge.py`)の画面の宛先。Windows(WebView2)は http://app.localhost、
# ほか(WebKitGTK など)は app://localhost(`src-tauri/src/main.rs` の `origin`)
BRIDGE_HOSTS = ("app.localhost", "localhost")


def _register_security(app: Flask) -> None:
    @app.before_request
    def _check_request():                       # noqa: ANN202 - Flaskのフック
        # --- Host 検証 (DNSリバインディング対策) ---
        # 攻撃者のドメインを 127.0.0.1 に向けられても、Hostヘッダが
        # 一致しないので弾ける
        host = (request.host or "").split(":")[0]
        allowed = ("127.0.0.1", "localhost")
        if app.config.get("BRIDGE"):
            # デスクトップ版: 外枠(Tauri)の窓の宛先。TCP を通らないので
            # リバインディングの心配は無いが、知らない宛先は今までどおり断る
            allowed = allowed + BRIDGE_HOSTS
        if host not in allowed:
            log.warning("Host不一致で拒否: %s", request.host)
            return jsonify(_error("bad_host", "このアドレスからは利用できません")), 400

        if not any(request.path.startswith(p) for p in TOKEN_REQUIRED_PREFIXES):
            refused = _refuse_foreign_page()
            return refused if refused is not None else _login_required()

        # --- 同一オリジンの確認 ---
        # ブラウザが付ける Fetch Metadata。付いていない場合(古い
        # クライアント・curl)は素通しし、トークンで守る
        fetch_site = request.headers.get("Sec-Fetch-Site")
        if fetch_site and fetch_site not in ("same-origin", "none"):
            log.warning("別オリジンからの要求を拒否: %s %s", fetch_site, request.path)
            return jsonify(_error("cross_origin", "別のページからは利用できません")), 403

        # --- 起動トークン ---
        if request.path in TOKEN_EXEMPT_PATHS:
            return None
        supplied = (request.headers.get("X-Tool-Token")
                    or request.args.get("t", ""))
        # バイト列で比べる。`compare_digest` に str を渡すと非ASCIIで
        # TypeError になり、**500 を返してしまう**(送られた値は誰にでも
        # 決められるので、素直に 401 を返さなければならない)
        if not secrets.compare_digest(supplied.encode("utf-8"),
                                      app.config["TOKEN"].encode("utf-8")):
            log.warning("トークン不一致で拒否: %s", request.path)
            return jsonify(_error(
                "bad_token",
                "この画面は無効になりました。アプリを開き直してください")), 401
        return _login_required()

        return None

    @app.after_request
    def _headers(response):                     # noqa: ANN202 - Flaskのフック
        # CORS ヘッダは**一切返さない**(返さないことが対策)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["Referrer-Policy"] = "no-referrer"
        _apply_cache_policy(response)
        return response


# 静的ファイルを控えておいてよい期間(秒)。URLに版が入っているので、
# 入れ替えれば URL が変わり、**必ず取り直される**
STATIC_MAX_AGE = 7 * 24 * 60 * 60


def _apply_cache_policy(response) -> None:
    """何を控えてよくて、何を控えてはいけないか。

    【なぜここを明示するのか】
    **「アプリを入れ替えたのに古いまま」の正体がここでした。**
    以前は `/api/` にだけ `no-store` を付けていて、**画面のHTMLには
    キャッシュの指示が1つもありませんでした**。指示が無いHTMLは、
    ブラウザが自分の判断で控えます(発見的キャッシュ・戻る操作・
    `nav.js` の取得)。版のバッジはそのHTMLの中にあるので、
    入れ替えても古い版が出続けます。

    分け方は1行で言えます:

    - **版がURLに入っているもの(静的ファイル)は、長く控えてよい。**
      入れ替えればURLが変わるので、古いものが出ることはない
    - **それ以外は控えない。** 画面のHTMLも、APIの応答も、
      いま作ったものを毎回渡す

    【版が入るのは入口だけ、という落とし穴】
    版を付けるのは `url_for('static', ...)`(`_register_static_version`)で、
    それが効くのは**テンプレートが名指しするファイルだけ**です。
    その中の

        import * as mapedit from "../mapedit.js";

    は版の付かない素のURLで取りに行きます。ここに `immutable` を
    付けると、ブラウザは**再確認すらしません** ── 入れ替えても
    共有モジュールだけが何日も古いまま残り、

        dragger?.clearSelection is not a function

    のように「入口は新しいのに、その中身が古い」形で壊れます
    (現場で実際に踏みました)。版が入っていないものは
    `no-cache`(=使う前に必ず確かめる)にします。ETag が付いているので
    中身が同じなら 304 が返るだけで、手元のサーバでは事実上ただです。
    """
    if request.path.startswith("/static/"):
        if request.args.get("v"):
            response.headers["Cache-Control"] = (
                f"public, max-age={STATIC_MAX_AGE}, immutable")
        else:
            response.headers["Cache-Control"] = "no-cache"
        return
    response.headers["Cache-Control"] = "no-store"
    # 発見的キャッシュを使う古いブラウザ向け。`no-store` を読まない
    # 実装でも、この2つがあれば控えない
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"


def _error(code: str, message: str, field: str = "") -> dict:
    """エラー応答の形(設計書 §6.1)。文言はサーバが持つ。"""
    body = {"code": code, "message": message}
    if field:
        body["field"] = field
    return {"error": body}


# ------------------------------------------------------------------
# DB接続 (設計書 §3.5)
# ------------------------------------------------------------------
# **書く要求は1つずつ通す。**
#
# waitress はスレッドプールで動くので、要求は同時に走る。読むだけなら
# WAL があるので困らないが、書くほうが重なると次のことが起きる:
#
#   ・作業状態(`*_session` / `work_context`)はプロセスに1つしかない。
#     2つの要求が同時に書き換えると、片方の結果がもう片方に混ざる
#   ・同じ行を2か所から書くと、後から書いたほうが黙って勝つ
#   ・sqlite3 の書き込みロックに当たると `database is locked` で断られる
#
# マスタを画面から直せるようになって(VER2.1.0)、書く口が増えた。
# **通し方は単純でよい** ── この道具は1台のPCを1人が使う前提なので、
# 書く要求が重なるのは押し間違いか二重送信で、待たせても誰も困らない。
_WRITE_LOCK = threading.RLock()

# 直列化しないもの。**待たせてはいけない**種類の POST。
#   /api/jobs      … 進捗を見に行くだけ(長い処理の最中に呼ばれる)
#   /api/mode      … 画面の切り替え。DBを書かない
# 前方一致で見る
#   /api/alive     … 心拍。20秒ごとに来るので、待たせると自動終了が誤る
_NO_LOCK_PREFIXES = ("/api/jobs", "/api/alive")


# 画面(タブ)の番号を運ぶヘッダ。自動終了の見張りが**画面ごと**に生き死にを
# 数えるために使う(`dbviewer/idle_exit.py`)。python-web-tools の
# 画面ロック(最後のタブ勝ち)は採らない ── DBビューアは作業状態を
# プロセスに持たないので、タブどうしが取り合うものが無い
SCREEN_HEADER = "X-Screen-Id"


def _is_write(req) -> bool:
    """その要求は「書く」か。**方法だけで決める** ── 経路ごとの表を
    持つと、画面を1つ足すたびに更新が要り、忘れたぶんだけ穴が開く。
    """
    if req.method in ("GET", "HEAD", "OPTIONS"):
        return False
    return not req.path.startswith(_NO_LOCK_PREFIXES)


def _register_db(app: Flask) -> None:
    """リクエストごとに1本開いて、終わったら閉じる。

    waitress はスレッドプールで動くため、`sqlite3` の接続を
    プロセス全体で共有できない(既定で `check_same_thread=True`)。
    WALは設定済みなので読み取りは書き込みとぶつからない。

    加えて、**書く要求は1つずつ通す**(上の `_WRITE_LOCK`)。
    """

    @app.before_request
    def _note_someone_is_here():                # noqa: ANN202 - Flaskのフック
        """**要求が来ている = 誰かが見ている。**

        自動終了は心拍(`/api/alive`)だけを見ていた。ところが画面を
        移ると、古いページの `pagehide` が「閉じました」を送り、新しい
        ページの最初の心拍が届くまでのあいだ**猶予の8秒が走り続ける**。
        頁の読み込みと ES モジュールの取得が続けば、そのあいだに猶予が
        切れる ── 現場で実際に起きた「使っている最中にフリーズして
        閉じた」がこれで、ログには

            08:41:15 画面が閉じました。8秒 待って終了します
            08:41:20 資材選択のセッションを開始しました   ← 使っている
            08:41:25 誰も見ていないので終了します

        と残っていた。真ん中の行が「見ている」証拠なのに、心拍だけを
        見ていたので届かなかった。

        心拍の届き方(`sendBeacon` は順番も時刻も保証しない)に頼らず、
        **要求そのものを在席の合図にする。**

        `/api/alive` だけは除く ── あちらは「閉じました」も同じ口で
        受けるので、ここで先に取り消すと閉じたことが伝わらない。
        """
        if request.path == "/api/alive":
            return
        watch = idle_exit.get()
        if watch is not None:
            # どの画面からの要求か(ヘッダ)。無ければ「どれかの画面」
            watch.beat(request.headers.get(SCREEN_HEADER, ""))

    @app.before_request
    def _take_write_lock():                     # noqa: ANN202 - Flaskのフック
        if _is_write(request):
            _WRITE_LOCK.acquire()
            g.holds_write_lock = True

    @app.teardown_request
    def _release_write_lock(_exc):              # noqa: ANN202 - Flaskのフック
        # **接続を閉じるより先に放す。** `teardown_request` は
        # `teardown_appcontext` より前に呼ばれる
        if g.pop("holds_write_lock", False):
            _WRITE_LOCK.release()

    @app.teardown_appcontext
    def _close_db(_exc):                        # noqa: ANN202 - Flaskのフック
        conn = g.pop("db", None)
        if conn is not None:
            conn.close()


def _register_static_version(app: Flask) -> None:
    """CSS/JS の URL に版を付ける。

    **入れ替えたら必ず取り直させる。** 配布はフォルダごとコピーなので、
    ファイル名は版が上がっても変わりません。ブラウザは同じURLの控えを
    持っているので、`Cache-Control` を無視する場面(戻る操作・
    オフライン復帰・企業のプロキシ)では**古い CSS が出続けます**。
    「アプリを入れ替えたのに見た目が古いまま」の正体がこれです。

    版は `config/app.json` ただ1つが出どころなので、**上げ忘れなければ
    必ず変わります**(上げ方は docs/変更履歴.md)。
    """
    stamp = app.config["VERSION"]

    @app.url_defaults
    def _stamp(endpoint, values):               # noqa: ANN202 - Flaskのフック
        if endpoint == "static" and "v" not in values:
            values["v"] = stamp


def get_db():
    """このリクエスト用のDB接続。`app` の外からは呼ばない。"""
    if "db" not in g:
        g.db = db.get_connection()
    return g.db


# ------------------------------------------------------------------
# ルーティング
# ------------------------------------------------------------------
def _register_routes(app: Flask) -> None:
    from .routes import auth as auth_routes
    from .routes import browse, easy, files, health, master, pending, record, report, settings
    from .routes import list as list_routes
    from .routes import log as log_routes

    app.register_blueprint(health.bp)
    app.register_blueprint(settings.bp)
    app.register_blueprint(auth_routes.bp)
    app.register_blueprint(list_routes.bp)
    app.register_blueprint(record.bp)
    app.register_blueprint(master.bp)
    app.register_blueprint(browse.bp)
    app.register_blueprint(report.bp)
    app.register_blueprint(log_routes.bp)
    app.register_blueprint(easy.bp)
    app.register_blueprint(files.bp)

    # レールに出ているのに中身がまだ無い画面へ、「準備中」の案内を置く
    pending.register(app)

    @app.errorhandler(404)
    def _not_found(_e):                         # noqa: ANN202 - Flaskのフック
        if request.path.startswith("/api/"):
            return jsonify(_error("not_found", "その操作はありません")), 404
        return jsonify(_error("not_found", "ページが見つかりません")), 404
