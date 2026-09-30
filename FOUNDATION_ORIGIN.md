# 基盤の複製元

このリポジトリの起動制御・監視・停止・画面の外枠・設定画面の骨格は、
`ViVio-Hellon/python-web-tools`(梱包資材総合ツール)から複製したものです。

| 項目 | 値 |
|---|---|
| 複製元 | https://github.com/ViVio-Hellon/python-web-tools |
| 複製したコミット | `d478462`(VER3.5.8) |
| 複製日 | 2026-09-30 |

## そのまま複製したファイル(表示名・パッケージ名の置換のみ)

- `Start.vbs` `start.bat` `stop.bat`(CP932 / CRLF)
- `start_app.py`(モード決定と自動取り込みを外した) `launch_guard.py` `server.py` `boot_server.py` `process_manager.py` `requirements.txt`
- `app/routes/health.py`(モード切替・画面ロック・未保存の問い合わせを外した) `app/routes/pending.py`
- `app/templates/base.html`(帯の中身を差し替え) `app/templates/pending.html`
- `app/static/css/{tokens,base,components,layout}.css`
- `app/static/js/{api,app,busy,health,jobs,nav,running,tabs,toast,toggles,unsaved}.js`(画面ロック `screen.js` への参照を外した)
- `dbviewer/{app_config,logging_utils,idle_exit,sqlite_toolkit,printing,user_settings,boot_screen,jobs,source_db}.py`
- `dbviewer/presenters/fs_browse.py`
- `tests/{_web,_isolation,test_launch_files}.py`
- `vendor/xlsx_writer.py`(`vendor/accdb_converter/xlsx_writer.py`)
- `docs/基盤_設計_複製元.md` `docs/基盤_UIUX設計指針_複製元.md`(規約として踏襲)

## DBビューア向けに書いたもの

- `dbviewer/modes.py` … モードを1つだけ持つ(基盤がモード名を鍵にするため)
- `dbviewer/config.py` … VBA の UserSetting 定数とプロファイル(progNo)
- `dbviewer/db.py` `dbviewer/shared_db.py` … 共有 sqlite3 の開き方
- `app/__init__.py` … 権限モデル・画面ロックを外した組み立て
- `app/shell.py` … レール(一覧 / マスタ管理 / 閲覧・出力 / 設定)と帯
- `app/routes/settings.py` `dbviewer/presenters/settings.py` `app/templates/settings.html` `app/static/js/views/settings.js`

## 採らなかったもの(理由)

- `modes.py` の 2 モード、`access_control.py`(アクセス権限マスタ)… 権限は VBA と同じく `ID` テーブルの Level
- `screen_lock.py` / `screen.js`(最後のタブ勝ち)… 作業状態をプロセスに持たないので不要
- `data_sync.py` 系(手元 DB への総入れ替え)… 共有 DB を直接読み書きする
- `vendor/accdb_converter` の `engine.py`(pyodbc / access_parser)… 追加ライブラリ不可。cscript → ADODB → ACE で書き直す

## 基盤の改修を取り込むとき

複製元の `d478462` 以降の差分を、上の「そのまま複製したファイル」に当てる。
`packaging_tool` → `dbviewer`、`PACKAGING_TOOL_` → `DBVIEWER_` の置換を忘れずに。
