# 変更履歴

## 0.2.0 (2026-10-01) — 一覧を Web の表に作り直し、参照・出力・帳票を Excel 前提から外す
- **一覧**: VBA の表示(フィルタ列・ListView・ページ)をやめ、全件を 1 枚の表に。見出しで並べ替え(Shift で多段)、
  ▾ で列ごとの絞り込み(値の一覧と件数・範囲)、全角/半角を区別しない全体検索、列の表示/並び/幅の設定(この PC に保存)、
  選んだ行の詳細(隠した列も)、↑↓・Enter での操作。レコード画面の ▲▼ は一覧の並びどおり
- **出力**: 見えている行・列・並びのまま「表をコピー」「ファイルに保存(CSV/タブ区切り/xlsx、Shift_JIS/UTF-8)」。
  保存は設定のフォルダへ(ダウンロードはしない)。ふつうの CSV にした
- **印刷**: 見えている表をそのまま A4 横で(列が多ければ紙の幅に縮める)
- **帳票**: 出力原紙.xlsx を使わず印刷用の画面で。行は絞り込んだ行、並びは VBA と同じ
- **閲覧**: 一覧と同じ表で見て、コピー・保存・印刷
- **設定「参照・出力」**: 作り直し。保存先フォルダの状態、既定の形式と文字コード、参照ファイルの起点(相対パスに効くようにした)と
  使っている列、帳票の設定の読み方。帳票原紙の置き場所はなくした
- **暗い配色**: ダイアログ(フォルダの参照など)の字が地に沈んで読めなかったのを直した。選んだ行の字、レコード画面の自動項目なども
- テスト 109 件


## 0.1.1 (2026-09-30) — 表を持ってくる(python-web-tools から移植)
- 設定 → Access 取り込み に「表を持ってくる…」を追加。Access(.accdb)か変換した .sqlite3 から、
  本体に**無い表だけ**を足す。もうある表は選んだものだけ Access の最新に入れ替える・作り直す
- 読むのはいつも .sqlite3。Access は bridge(cscript → ADODB → ACE)で作業フォルダに .sqlite3 を作ってから読む
- ドラッグ&ドロップで受け取れる。書く前に本体の控えを `backup` へ取り、取り込み診断に記録
- メインテーブル・H_ 履歴・ID は入れ替えない。Level 5 でログインしているときだけ書ける
- マスタ管理の一覧で、足した表に「足」の印
- テスト 10 件追加(全 106 件)

## 0.1.0 (2026-09-30) — VBA 汎用システムの Web 移植（初版）

### 解析・計画
- VBA 4 ファイル（標準モジュール 19・UserForm 11・クラス 6）と仕様 PPTX を全量読み、`docs/01_VBA解析.md` にまとめた
- python-web-tools（VER3.5.8, d478462）の部品ごとの流用判定 `docs/02_python-web-tools調査.md`
- 機能対応表・追加確認事項への設計方針・工程・検証計画 `docs/03_移植計画.md`
- 不明点 20 件と判断事項 D1〜D10 `docs/04_不明点・判断事項.md`（全部推奨案で確定）
- 受領した `作業標準管理システム.accdb` と `出力原紙.xlsx` からテスト基準データ `tests/fixtures/realdata/` を作成（PASS は伏せ字、.accdb 本体は保存しない）

### Phase 0 基盤
- python-web-tools の起動制御・監視・停止・画面の外枠・設定画面を複製し、DBビューア（app_id `nlm.db-viewer`、ポート 8743）として起動できる骨格（`FOUNDATION_ORIGIN.md`）

### Phase 1 Access 取り込み
- `cscript.exe → bridge.vbs → ADODB.Connection → ACE OLE DB → .accdb` で全テーブルを TSV に出し、`importer.py` が `.sqlite3` を作る。Windows 以外では TSV から同じ処理でテスト DB を作る

### Phase 2 設定テーブル
- 全般設定 / `_設定` / フィルタ / フィルタ_設定 / タイプ / ID の読込、`CheckColumns` `MasterFileCheck` `formatCheck` と同じ文言の整合性検査、システムプロファイル（progNo 1〜7）、設定タブ

### Phase 3 一覧
- `CODESQL`（マスタ結合・追加表示・FORMAT・ソート・別名補完）、`GetSQLFilterStr`、フィルタ候補（`NonDuplist` の並び・型変換）、`GetWidthArr`、ページ、集計（GROUP BY）
- SQLite 方言差は `FORMAT` / `VBADATE` 関数の登録と `#日付#` の書き換えで吸収し、VBA の SQL 文字列そのものは画面に表示

### Phase 4 ログイン・登録
- Login と同じ判定順・文言、`DEVELOPERFLAG`、Level、`ボタンクリック制御`
- 入力形式（s/m/calender/数値/フォルダ/フィールド/#自動項目#/#VIEW#）と補助入力、`REG_EXE`（数値変換・必須・修正なし・自動項目）、`adoSQL`（ADDNEW↔UPDATE 自動、確認文言）、`GetNewNo` / `GetNewCode`、履歴 `H_`
- 行単位の競合検知（開いたときの値と比べて 409）

### Phase 5 マスタ管理・閲覧・出力
- MF2（Level による対象表、`sortTblArr` の並び、`_設定` による補助入力、全列一致の削除）
- MF3（テーブル閲覧、xlsx / txt / csv 出力。txt/csv の行末区切り文字・CRLF・CP932 は VBA と同じ）

### Phase 6 帳票
- 帳票登録簿（REPORT / SUITOUREPORT / DAITYOREPORT / GIKAKUREPORT）、REPORT の空行・小計・Total・GrandTotal・罫線
- 標準ライブラリだけで原紙 xlsx にシートを足す `xlsx_append`、印刷用 HTML（A4 縦、余白 6mm、横をページ幅に収める）
- VBA の実出力（2024-03-28、319 件）と一致することをテストで確認

### Phase 7 運用
- 他の端末の更新通知（心拍に本体の更新時刻）、起動モードの初期選択（regUF）と初期化（LOCKBT ダブルクリック）、Access.log、無操作再ログイン（任意）

### Phase 8 検証・文書
- `docs/05_起動と使い方.md`（配布手順）、`docs/06_検証結果と意図した差異.md`、本ファイル
- テスト 96 件（`python3 -m unittest discover -s tests -t .`）
