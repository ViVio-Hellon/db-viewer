# db-viewer

Excel VBA + Access（.accdb）で動いている「汎用システム」（現在は progNo=4 作業標準管理システム）を、
既存の python-web-tools（梱包資材総合ツール）の基盤に載せて Web システムとして移植するためのリポジトリです。

現在の状態: **解析と移植方針の提示まで完了。実装は確認待ち。**

## ドキュメント

| ファイル | 内容 |
|---|---|
| [docs/01_VBA解析.md](docs/01_VBA解析.md) | VBA の全体像（モジュール構成・業務フロー・設定テーブル仕様・判定/計算ロジック・依存/出力/エラー処理/ハードコード） |
| [docs/02_python-web-tools調査.md](docs/02_python-web-tools調査.md) | 既存 python-web-tools の部品ごとの流用判定 |
| [docs/03_移植計画.md](docs/03_移植計画.md) | VBA → Python Web 機能対応表、追加確認事項（Access→SQLite・共有・競合・長時間放置・多重タブ・設定タブ・印刷）への設計方針、提案ディレクトリ、工程、検証計画 |
| [docs/04_不明点・判断事項.md](docs/04_不明点・判断事項.md) | 不明点・不足資料 17 件と、確認していただきたい判断事項 D1〜D10 |

同じ内容を 1 ページにまとめた確認用アーティファクト: https://claude.ai/artifact/Y9HQ4CCojyStfStYYxH2yj

## 前提（配布先 PC の制約）

- Python は標準ライブラリ + Flask + waitress のみ（python-web-tools と同じ。判断事項 D10）
- Access の読み取りは `cscript.exe → VBScript → ADODB.Connection → Microsoft ACE OLE DB → .accdb` の経路で行い、pyodbc 等は使わない
- 業務データは共有フォルダ上の SQLite（.sqlite3）に置き、VBA と同じ業務結果になることをテストで確認する
