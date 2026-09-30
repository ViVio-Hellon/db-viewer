# 02 python-web-tools 調査結果（流用判定）

## python-web-tools（梱包資材総合ツール VER3.5.8）調査結果

> **結論** 起動・監視・停止・心拍・自動終了・書込直列化・設定画面・フォルダ参照・マスタ管理 UI・印刷基盤・SQLite 共通操作・CSS/JS 部品はすべて **業務非依存に切り出されており、そのまま複製して使えます**（27 ファイル）。一方、DB 取り込み（総入れ替え）・モード/権限（現場/資材）・マスタ管理の判断部分は梱包資材の業務に結合しているため、**UI と型（API の形・競合検知）だけを流用し、判断は VBA の MF2 ルールで書き直します**。

### 流用判定表

| 部品 | 場所（python-web-tools） | 判定 | 使い方・理由 |
|---|---|---|---|
| 起動入口 | Start.vbs, start.bat, stop.bat | [そのまま] | CP932/CRLF 規約と検査（test_launch_files）ごと複製。表示名だけ差し替え |
| 起動制御 | start_app.py, launch_guard.py, server.py, boot_server.py, process_manager.py | [そのまま] | モード（現場/資材）を 1 つに固定して複製。ポート・app_id は config/app.json |
| アプリ固有値 | packaging_tool/app_config.py, config/app.json | [そのまま] | app_id を `nlm.db-viewer`、local_dir_name を `DBViewer`、port 8743（候補 +3）に |
| ログ | logging_utils.py | [そのまま] | 日替わりファイル、pythonw 対応。Access.log は別途 VBA 互換で追記 |
| 自動終了・心拍 | idle_exit.py, static/js/health.js, routes/health.py の /api/alive | [そのまま] | 裏に回った画面は落とさない・復帰/スリープ復帰検知・猶予・処理中は落とさない、がすでに実装済み（§9） |
| Flask 組立 | app/__init__.py | [複製して改変] | token/Host/Sec-Fetch-Site/書込 RLock/接続の要求ごと開閉/静的ファイル版付与はそのまま。モード・権限（GATED_MODES/access_control）を ID 表ログインに置換 |
| 外枠・レール | app/shell.py, templates/base.html, css 4 本, js 10 本 | [そのまま] | レール項目とリボンの内容だけ差し替え（Lot 等 → システム名・ユーザー・Level・接続先） |
| SQLite 共通操作 | sqlite_toolkit.py | [そのまま] | ロック時リトライ・簡易 CRUD・now_db_string |
| 共有 sqlite3 の開き方 | source_db.py | [複製して改変] | 読み取り専用の手元スナップショット（読む鍵を取って写す・mtime で再写し）、UNC の URI 化、CP932/UTF-8 判定、診断 Probe を流用。書込側は「共有ファイルへ直接、短いトランザクション」に変更（§9） |
| 接続の持ち方 | db.py の get_db/teardown | [そのまま] | 要求ごとに開いて閉じる。スキーマ移行部分は不要 |
| 印刷基盤 | printing.py | [そのまま] | `SAFE_MARGIN_MM = 6.0`（5mm 以上内側という現場要求に対し 1mm の余裕）。画面プレビューも紙と同寸 |
| 利用者設定 | user_settings.py, config.resolve_dir/is_relative_setting | [そのまま] | レジストリ代替の JSON。パスは絶対/相対（アプリのフォルダ基準）の両方 |
| 設定画面 | templates/settings.html, js/views/settings.js, presenters/settings.py, routes/settings.py | [構造を流用] | タブ構成（いまの状態/取り込み元/動作/パスワード）と「参照…」ダイアログ、良し悪し付きチェック一覧をそのまま。項目は db-viewer のもの（§9 設定タブ） |
| フォルダ参照 | presenters/fs_browse.py, /api/fs/list | [そのまま] | 拡張子一覧を .sqlite3/.db/.accdb/.xlsx に |
| マスタ管理 | master_admin.py 系 5 モジュール, routes/master.py, js/views/master.js, settings.html の面 | [UI と型を流用] | 表一覧＋1 ページ＋1 行ダイアログの UI、断りの HTTP 対応（400/403/409/422）、**見ていた行と同じときだけ書く競合検知（_write_row の seen）** を流用。「どの表を誰が直せるか」「新規番号」「入力形式」は VBA MF2 のルール |
| 長時間処理 | jobs.py, running.js, jobs.js | [accdb 取込にだけ使用] | Access→SQLite 変換の進捗表示に |
| Access 変換 | vendor/accdb_converter, table_bring.py | [使わない] | pyodbc / access_parser（追加ライブラリ）前提。ご指定の cscript→VBScript→ADODB→ACE 経路で書き直す（§9） |
| xlsx 書き出し | vendor/accdb_converter/xlsx_writer.py | [そのまま] | 標準ライブラリだけの xlsx writer。ExportData と帳票の xlsx 出力に |
| 画面ロック（多重タブ） | screen_lock.py, screen.js | [採用しない] | あちらは作業状態をプロセスに 1 つ持つため必要。db-viewer は状態を持たせない設計にしてタブを独立させる（判断事項 D8） |
| モード・権限 | modes.py, access_control.py | [使わない] | 現場/資材の概念が無い。VBA の ID 表（ID/PC/PASS/Level）が権限の出どころ |
| 取り込み | data_sync.py, import_specs.py, sync_*.py | [使わない] | 手元 DB への総入れ替えは不要。共有 DB を直接読み書きする（VBA と同じ運用） |
| 試験の枠組み | tests/_web.py, _isolation.py, __init__.py | [そのまま] | 本物の設定・DB を汚さない隔離と test_client の道具 |
| 文書 | docs/設計.md, UIUX設計指針.md | [規約として踏襲] | 「業務判断は Python」「API は画面ぜんぶを返す」「断りは reason で運ぶ」「色は tokens.css」 |

python-web-tools 側は Python 3.9 以上 + **Flask と waitress の 2 パッケージのみ**で動いています（pip 不可の制約とこの 2 つの扱いは判断事項 D10）。

