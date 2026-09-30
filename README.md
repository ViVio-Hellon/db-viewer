# db-viewer

Excel VBA + Access（.accdb）で動いている「汎用システム」（現在は progNo=4 作業標準管理システム）を、
既存の python-web-tools（梱包資材総合ツール）の基盤に載せて Web システムとして移植するためのリポジトリです。

現在の状態: **Phase 0〜8 の実装・検証・文書まで完了（0.1.0）。** 起動と使い方は [docs/05_起動と使い方.md](docs/05_起動と使い方.md)、VBA との照合結果と意図した差異は [docs/06_検証結果と意図した差異.md](docs/06_検証結果と意図した差異.md)。

## ドキュメント

| ファイル | 内容 |
|---|---|
| [docs/01_VBA解析.md](docs/01_VBA解析.md) | VBA の全体像（モジュール構成・業務フロー・設定テーブル仕様・判定/計算ロジック・依存/出力/エラー処理/ハードコード） |
| [docs/02_python-web-tools調査.md](docs/02_python-web-tools調査.md) | 既存 python-web-tools の部品ごとの流用判定 |
| [docs/03_移植計画.md](docs/03_移植計画.md) | VBA → Python Web 機能対応表、追加確認事項（Access→SQLite・共有・競合・長時間放置・多重タブ・設定タブ・印刷）への設計方針、提案ディレクトリ、工程、検証計画 |
| [docs/04_不明点・判断事項.md](docs/04_不明点・判断事項.md) | 不明点 20 件と判断事項 D1〜D10（全部推奨案で確定）、受領資料から分かったこと、帳票で確定したこと |
| [docs/05_起動と使い方.md](docs/05_起動と使い方.md) | 配布先 PC に必要なもの、起動、Access 取り込み、画面ごとの使い方（VBA との対応）、記録の場所、困ったとき |
| [docs/06_検証結果と意図した差異.md](docs/06_検証結果と意図した差異.md) | 実データで VBA と突き合わせた結果、意図した差異、VBA の癖を残したもの、未検証の範囲 |
| [CHANGELOG.md](CHANGELOG.md) | 変更履歴 |

全体像を 1 ページにまとめたアーティファクト（解析・計画・実装結果）: https://claude.ai/artifact/Y9HQ4CCojyStfStYYxH2yj

## 前提（配布先 PC の制約）

- Python は標準ライブラリ + Flask + waitress のみ（python-web-tools と同じ。判断事項 D10）
- Access の読み取りは `cscript.exe → VBScript → ADODB.Connection → Microsoft ACE OLE DB → .accdb` の経路で行い、pyodbc 等は使わない
- 業務データは共有フォルダ上の SQLite（.sqlite3）に置き、VBA と同じ業務結果になることをテストで確認する

## 起動とテスト

```
Start.vbs                                      # 現場 PC(ブラウザが開く)
start.bat --check                              # 環境確認だけ
python3 -m unittest discover -s tests -t .     # 全テスト(96 件)
```

初回は 設定 → Access 取り込み で `.accdb` を `.sqlite3` にします（Windows の cscript + ACE）。
詳しくは [docs/05_起動と使い方.md](docs/05_起動と使い方.md)。

## 構成

```
Start.vbs / start.bat / stop.bat    起動・停止(python-web-tools 複製)
start_app.py / server.py / …        起動制御・待機画面・監視・停止(複製)
app/                                Flask: routes(health, settings, auth, list, record, master, browse, report) / templates / static
dbviewer/                           業務: config, shared_db, settings_tables, sql_builder, filters, records, auth, editing,
                                    ado_sql, numbering, history, input_types, choices, master_admin, table_browse,
                                    reports/, xlsx_append, accdb_bridge/, presenters/, vba_compat
tests/                              ゴールデンテスト(実データ TSV) + API + 起動ファイル
docs/                               解析 / 調査 / 計画 / 不明点・判断 / 使い方 / 検証結果
```
