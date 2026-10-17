# db-viewer

Excel VBA + Access（.accdb）で動いている「汎用システム」（現在は progNo=4 作業標準管理システム）を、
既存の python-web-tools（梱包資材総合ツール）の基盤に載せて Web システムとして移植するためのリポジトリです。

現在の状態: **Phase 0〜8 の実装・検証・文書まで完了。0.2.0 で一覧を Web の表に作り直し、参照・出力・帳票を Excel 前提から外した。0.3.0 で脱 Excel・脱 .accdb に絞り、0.4.0 で操作の記録(記録番号・なぜなぜ分析)を足し、0.5.0 でマスタ管理から列と行を足せるようにし、0.6.0 で**デスクトップ版(Tauri/Rust の窓。ポートを使わない)**を足した(ブラウザ版も予備として残す)。0.7.0 で設定の表が無い・合わない DB でも開くようにし、0.8.0 で**設定を「見る DB」と「列ごとの 表示・入力・印刷」だけ**にし(どの表も一覧・マスタ管理で見て直す)、0.9.0 で作業標準管理_設定 も使わずに統一し、0.11.0 で**ログ**(なぜなぜシート・CSV・出力先の指定)を仕立て直し、0.12.0 で列ごとの**クリックで実行**(値をコピー・URL を開く)を足した。** 起動と使い方は [docs/05_起動と使い方.md](docs/05_起動と使い方.md)、VBA との照合結果と意図した差異は [docs/06_検証結果と意図した差異.md](docs/06_検証結果と意図した差異.md)。

## ドキュメント

| ファイル | 内容 |
|---|---|
| [docs/01_VBA解析.md](docs/01_VBA解析.md) | VBA の全体像（モジュール構成・業務フロー・設定テーブル仕様・判定/計算ロジック・依存/出力/エラー処理/ハードコード） |
| [docs/02_python-web-tools調査.md](docs/02_python-web-tools調査.md) | 既存 python-web-tools の部品ごとの流用判定 |
| [docs/03_移植計画.md](docs/03_移植計画.md) | VBA → Python Web 機能対応表、追加確認事項（Access→SQLite・共有・競合・長時間放置・多重タブ・設定タブ・印刷）への設計方針、提案ディレクトリ、工程、検証計画 |
| [docs/04_不明点・判断事項.md](docs/04_不明点・判断事項.md) | 不明点 20 件と判断事項 D1〜D10（全部推奨案で確定）、受領資料から分かったこと、帳票で確定したこと |
| [docs/05_起動と使い方.md](docs/05_起動と使い方.md) | 配布先 PC に必要なもの、起動、Access 取り込み、画面ごとの使い方（VBA との対応）、記録の場所、困ったとき |
| [docs/06_検証結果と意図した差異.md](docs/06_検証結果と意図した差異.md) | 実データで VBA と突き合わせた結果、意図した差異、VBA の癖を残したもの、未検証の範囲 |
| [docs/07_ログとなぜなぜ分析.md](docs/07_ログとなぜなぜ分析.md) | ログ(何が残るか・出力先・記録番号)、なぜなぜシート・CSV への書き出し・未対策のエラー数、エラーをなぜなぜ分析でたどる手順 |
| [docs/08_デスクトップ版.md](docs/08_デスクトップ版.md) | デスクトップ版(Rust/Tauri の窓 + Python)の作り、移行前の機能維持の確認、ブラウザ版と同時に動かない仕組み、作り方と配り方(make_dist / make_shortcuts) |
| [docs/09_かんたん設定.md](docs/09_かんたん設定.md) | 設定は 見る DB と 列ごとの 表示・入力(文字・コンボボックス・スピンボタン・カレンダー)・印刷 だけ。どの表も一覧・マスタ管理で見て直す。DB を替えたときの設定の動き |
| [CHANGELOG.md](CHANGELOG.md) | 変更履歴 |
| [FOUNDATION_ORIGIN.md](FOUNDATION_ORIGIN.md) | python-web-tools から複製・移植したもの（表を持ってくる を含む） |

全体像を 1 ページにまとめたアーティファクト（解析・計画・実装結果）: https://claude.ai/artifact/Y9HQ4CCojyStfStYYxH2yj

## 前提（配布先 PC の制約）

- Python は標準ライブラリ + Flask + waitress のみ（python-web-tools と同じ。判断事項 D10）
- Access の読み取りは `cscript.exe → VBScript → ADODB.Connection → Microsoft ACE OLE DB → .accdb` の経路で行い、pyodbc 等は使わない
- 業務データは共有フォルダ上の SQLite（.sqlite3）に置き、VBA と同じ業務結果になることをテストで確認する

## 起動とテスト

```
DBビューア.exe                                 # デスクトップ版(配布フォルダの直下。ポートを使わない)
Start.vbs                                      # ブラウザ版(予備。ブラウザが開く)
start.bat --check                              # 環境確認だけ
python3 -m unittest discover -s tests -t .     # 全テスト(183 件)
cd src-tauri && cargo test --release           # 外枠(Rust)の試験
```

ブラウザ版とデスクトップ版は同時に動きません(後から開いたほうが理由を出して止まる)。
配り方: GitHub Actions の成果物 `DBViewer.exe` を直下に置いて `scripts\make_dist.bat` → 配布フォルダを配る →
配った先で `scripts\make_shortcuts.vbs` を押す([docs/08](docs/08_デスクトップ版.md))。

初回は 設定 → Access 取り込み で `.accdb` を `.sqlite3` にします（Windows の cscript + ACE）。
その後に Access で足した表は「表を持ってくる…」で表ごとに足せます（本体を丸ごと差し替えない）。
詳しくは [docs/05_起動と使い方.md](docs/05_起動と使い方.md)。

## 構成

```
Start.vbs / start.bat / stop.bat    ブラウザ版の起動・停止(python-web-tools 複製)
bridge.py / src-tauri/              デスクトップ版: Python の入口(標準入出力) / 外枠(Rust・Tauri)
scripts/                            make_dist(配布フォルダ)・make_shortcuts.vbs・desktop_smoke・アイコン
start_app.py / server.py / …        起動制御・待機画面・監視・停止(複製)
app/                                Flask: routes(health, settings, auth, list, record, master, browse, report) / templates / static
dbviewer/                           業務: config, shared_db, settings_tables, sql_builder, filters, records, auth, editing,
                                    ado_sql, numbering, history, input_types, choices, master_admin, table_browse,
                                    reports/, xlsx_append, accdb_bridge/, presenters/, vba_compat
tests/                              ゴールデンテスト(実データ TSV) + API + 起動ファイル
docs/                               解析 / 調査 / 計画 / 不明点・判断 / 使い方 / 検証結果
```
