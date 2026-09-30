# 01 VBA 解析（作業標準管理システム／汎用DBビューア）

> 本書は移植前の解析結果です。VBA 4 ファイル（標準モジュール・UserForm・クラス）と仕様 PPTX を全量読んで作成しました。

## 概要

アップロードされた VBA 4 ファイルと仕様 PPTX（39 枚）を全量読み、既存 python-web-tools（梱包資材総合ツール VER3.5.8）の基盤を全モジュール確認したうえで、機能対応表・不明点・移植方針をまとめました。実装は着手していません。この内容の確認をもって実装に進みます。

- **13,238** VBA 総行数（標準 8,050 / UserForm 2,833 / クラス 1,776）
- **524** プロシージャ数（うち画面イベント 101、業務ロジック約 120、汎用ライブラリ約 300）
- **11 / 6** UserForm / クラスモジュール
- **27** python-web-tools からそのまま流用できる部品
- **17** 不明点・不足資料
- **10** 確認していただきたい判断事項

## このVBAは何をしているシステムか

> **結論** Access ファイル（.accdb）の中の「設定テーブル」だけで画面・入力ルール・一覧・帳票が決まる、**汎用の DB 閲覧・登録ツール**です。Excel の UserForm がフロント、ADO + Microsoft ACE OLE DB 12.0 で共有フォルダ上の .accdb を直接読み書きします。標準モジュールの `progNo`（現在 4）を切り替えるだけで 7 つの業務システムに化けます。今回の対象は **progNo=4「作業標準管理システム」**で、DB は `\\nlmfangyshrd\各課共有\0130_日軽稲沢\梱包課\AIM\【■】_参照用ファイル\作業標準DB\作業標準管理システム.accdb` です。同じ親フォルダを python-web-tools（梱包資材総合ツール）の取り込み元も使っており、部署も配布先の PC も同じ系統と見ています。

### progNo で切り替わる 7 システム（標準モジュール `プログラム番号チェック`）

| progNo | SysName（レジストリ登録名） | 本体ファイル | オンライン時のフォルダ | 専用処理 |
|---|---|---|---|---|
| 1 | ISO文書台帳管理システム | ISO文書台帳管理.accdb | \\nlmsrvngy03\各課共有\0109_仕上課\04 ISO\ISO管理台帳 | 帳票 DAITYOREPORT（R1〜R30 版数列） |
| 2 | 化学物質管理システム | 00_マスタ.accdb | \\Nlmfangyshrd\DocuWorks\0000_共通\003_化学物質管理 | PPTX の設定例はこのシステム（SDS/GHS 参照） |
| 3 | 仮払出納帳システム | 仮払出納帳.accdb | \\nlmfangyshrd\各課共有\0102_事務課\総務／環境伝票など\仮払出納帳 | 帳票 SUITOUREPORT、専用フォーム CoinInputFm（金種入力） |
| **4** | **作業標準管理システム**（現在有効） | 作業標準管理システム.accdb | \\nlmfangyshrd\各課共有\0130_日軽稲沢\梱包課\AIM\【■】_参照用ファイル\作業標準DB | 帳票 REPORT（出力原紙.xlsx に転記・小計） |
| 5 | 技革保管リスト | 技革保管リスト.accdb | \\nlmsrvngy03\各課共有\0110_技術室\003_技革-板ｻﾝﾌﾟﾙ | 帳票 GIKAKUREPORT（中身が空） |
| 6 | 4M管理システム | 4M管理.accdb | \\nlmsrvngy03\各課共有\0109_仕上課（修正必要とコメント） | — |
| 7 | 仕上課人事DB | 仕上課人事データベース.accdb | \\nlmsrvngy03\各課共有\0109_仕上課\データベース | — |

エンジン部分（一覧・フィルタ・登録・履歴・マスタメンテ・出力）は 7 システム共通で、システム固有なのは「帳票プロシージャ」と「$ で始まる専用フォーム」だけです。

## モジュール構成（標準・クラス・UserForm・シート）

アップロードされた標準モジュールは 19 モジュールが 1 ファイルに連結されたものです（`Option Explicit` の境界と、コード中の【00】〜【B1】の参照タグから復元）。行番号は連結ファイル上の位置です。

### 標準モジュール（19）

| # | 推定モジュール名 | 行 | 役割 | 主な Public 手続き | 移植 |
|---|---|---|---|---|---|
| 1 | 【00】APIControl | 1–410 | Win32 API 宣言、フォーム窓の細工（×ボタン消去、リサイズ許可、半透明）、クリップボード、プロセス制御 | FormResize, ResetCtrl, WindowMenuButtonDel, GetClipboardText, AppRun, PressKeyCheck | [不要（GUI 固有）] |
| 2 | 【01】ShDatControl | 411–630 | Excel シート操作ユーティリティ（UR/UC 最終行列、UseWs、WsCheck、AutoFilter） | UR, UC, UseWs, WsCheck, RefleshWs, ArrRange | [不要（デバッグ用 ACCDB作成経路のみ）] |
| 3 | 【03】CommonFunc | 631–1117 | COM オブジェクト取得、ファイルを開く、ネットワーク ID/PC 名、ListBox 設定、JScript 高階関数、レジストリ書換（reg add）、bat/cmd 実行 | OpenApp, NetWorkID, UsePCName, ListboxSetting, HighOrderFunc, ExcelSecurityCk, cmdexe, ExtractString | [一部移植] NetWorkID/UsePCName/HighOrderFunc(文字色) /ExtractString(VER) |
| 4 | FileFunc | 1118–1358 | パス存在確認、親フォルダ、ファイル名分解、xlsx 保存、CSV/TSV 出力、フォルダ内ファイル列挙、ブック開閉 | PathCheck, GetParentDirectoryPath, GetFileName*, SaveArrayAsWorkbook, ExportFile, FileNameIndex, xlsFileOpenWb | [移植]（xlsx は標準ライブラリ writer） |
| 5 | Array/ListBox ユーティリティ | 1359–2094 | 配列統計・重複除去・ソート・フィルタ・結合・コントロール名変換 | NonDuplist, QuickSort, ArrFilter, Compjudge, GetRepCtrlNameArr, ListBoxDBArr | [NonDuplist のみ移植]（他はアプリ未使用） |
| 6 | 【05】ListView | 2095–2849 | MSComctlLib ListView への一覧描画、列幅自動調整（メイリオ幅×2.1）、列クリックソート、選択行取得、ページラベル | CreateAll, LvField, LvRec, LvWidthFit, LvSort, GetLvKeyCode, GetLvData, GetFieldLvDic, setLvPage | [HTML テーブルに置換] |
| 7 | Dic/Find ユーティリティ | 2850–3273 | 配列→辞書、タイトル辞書、検索、フォームコントロール名取得、VBProject 手続き一覧（帳票名検証） | ArrDic, Findi, GetfmCtrlName, VbProcDic, GetTableDic | [帳票レジストリで代替] |
| 8 | TextData 変換 | 3274–3678 | テキスト→シート展開、ICAS 日付変換（DateConv） | DataUpdate, ConvDataExt, DateConv | [不要（アプリ未使用）] |
| 9 | ADO | 3679–4802 | 接続（拡張子で Provider 切替）、レコードセット→2次元配列、テーブル一覧（ADOX）、ページ読込、**adoSQL（1行更新の中核）**、Excel→accdb 変換 | adoConnection, adoRecordset, GetFieldDic, GetRecordsArr, GetTableArr, GetTblNameArr, adoReadPageCtrl, adoSQL, adoSQLcode, ExpAccdb | [sqlite3 リポジトリに置換] |
| 10 | Log | 4803–4862 | Access.log（TSV）追記。WMI で OS 名取得、ブック名から VER 抽出 | LogMessage, GetOsInfo | [移植] |
| 11 | 【20】ColorPalette | 4863–5017 | vb/rgb 色名→数値の辞書（143 色）。文字色設定の「設定部」名を引くのに使用 | myColor | [名前→CSS 色に移植] |
| 12 | 【A0】UserSetting | 5018–5525 | **システム定義**：progNo、パス、SysName、固定テーブル名・列名定数、起動、レジストリ、パス解決、入力形式の見た目設定、登録配列生成、整合性チェック、採番 | フォーム起動, プログラム番号チェック, onlineFlag, SrvPath, MasterPath, GetArrFromTag, setModifyedForm, GetArrDataFromCtrl, MasterFileCheck, cv, GetNewNo, GetNewCode, formatCheck | [移植（中核）] |
| 13 | CODESQL | 5526–5785 | **一覧 SQL 組立**：マスタ結合（LEFT JOIN）、追加表示列、金額書式、ソート、初期フィルタ、WHERE のテーブル別名補完 | CODESQL, GetStrMidArr | [移植（中核）] |
| 14 | 【A2】TextBox | 5786–6122 | **入力形式の分岐**：ダブルクリックで開く補助入力、参照モードのファイルオープン、変更検知の背景色、Enter で数値変換、削除制限、右クリックメニュー | TextDblClickEvents, TextDblClickWatchMode, TEXTChangeEvents, TextEnterEvents, TEXTDeleteEvents | [移植（中核）] |
| 15 | 【A3】FormResize | 6123–6964 | **編集画面の組立**：モード切替、レコードのセット（SET_BEF）、連動表示・必須/履歴アイコン・ファイルアイコン・ICON 画像、文字色ルール、リサイズ・2 段組配置、行数調整 | 画面切替, SET_BEF, SetDataToCtrl, MF1_Resize*, GetWidthArr, ctrlHeightAdj, getNumOfLine | [移植（レイアウトは CSS）] |
| 16 | ListRefresh / Filter | 6965–7453 | **一覧更新とフィルタ**：LV1Reflesh、集計モード、フィルタ候補生成、WHERE 生成、フィルタクリック | LV1Reflesh, LabelDblClickEvents, SetListBoxItems, ListBoxReset, GetSQLFilterStr, ListClickEvents | [移植（中核）] |
| 17 | Export | 7454–7484 | 保存ダイアログ → xlsx / txt(タブ) / csv | ExportData | [移植] |
| 18 | Login 変数・テスト | 7485–7537 | USEID/USEPC/USELV、OpenAppFlag、test1〜9（開発用） | OpenAppFlag | [移植（test は除外）] |
| 19 | 【B1】Report | 7538–8050 | 帳票プロシージャ群。全般設定「帳票」の第 4 要素の名前で Application.Run される | SUITOUREPORT, ModeValue, DAITYOREPORT, REPORT, GIKAKUREPORT, IntegrateAndFormatFiles_Final | [REPORT 移植、他は判断事項 D4] |

### UserForm（11）

| フォーム | 役割 | 主なコントロール | 主な手続き | Web での姿 |
|---|---|---|---|---|
| **Login** | 起動設定確認→認証→MF1 起動。開発者バイパスあり | TB1(ID 既定=ネットワーク ID) TB2(PASS) LOCKBT(伏字切替/ダブルクリックで設定初期化) MultiPage1 | UserForm_Initialize, LOGINEXE_Botton_Click, DEVELOPERFLAG, 再設定問いかけ, Cancel_Click | ログイン画面（ID は自動、パスワードのみ） |
| **regUF** | 初回のみ：オンライン/オフラインをレジストリに保存 | OP1/OP2/CB1 | CB1_Click | 設定タブ「接続モード」 |
| **MF1** | メイン。MultiPage: Page1=一覧（フィルタ列・ListView・ページ）、Page2=編集（動的 IL/ITB ×フィールド数） | L/FL/FLL×n(フィルタ) ListView1 SQL(現在の WHERE) PAGE/SB1 CB1〜CB4 BROWSE REPORT SETTING OUTPUT NUM/SPN(レコード送り) MODE | UserForm_Initialize, FormInitialize, ボタンクリック制御, REG_EXE, GetHisRecArr, CompStrTbx, reqConInputFlag, checkmodifiedflag, REG_BAC, REPORT_Click, OUTPUT_Click | 一覧画面 + レコード画面（新規/修正/削除/参照） |
| **MF2** | マスタメンテナンス。レベルで編集可能テーブルを制限、12 個の LB/TB で 1 行編集 | L1(テーブル選択) ListView1 PAGE/SB1 LB1〜12/TB1〜12 CB1〜CB3 CLR MODE MASTERNAME | UserForm_Initialize, sortTblArr, マスタ実行/新規/修正/削除, ラベル書き換え, 変更チェック, GetClickDataSQLFORM, CLR_Click | マスタ管理画面（python-web-tools のマスタ管理 UI を流用） |
| **MF3** | 閲覧＆出力：全般設定「テーブル閲覧」のテーブルを表示・出力・画面印刷 | L1 ListView1 PAGE/SB1 CB1(更新) CB2(出力) CB3(PrintForm) | リスト更新, LV再描画, CB2_Click, CB3_Click | テーブル閲覧画面（一覧・出力・印刷用 HTML） |
| **SF** | 選択フォーム：マスタ（単一/複数）、フォルダ内ファイル名、フィールド名の選択。マスタ連動なら結合先も同時入力 | ListView1 LB1 MODE MS L1 CB1〜CB3 | objReturn, CB1_Click | 選択モーダル |
| **CF** | カレンダー入力。祝日判定クラスで色分け、1920〜2100 年 | DAY1〜42 W1〜7 YL1 SPB/SPF IMA(干支画像) | objReturn, CreateDays, SendDate, spinCtrl | 日付ピッカー（祝日色分けは移植） |
| **NumFm** | 数値入力：MODENO 1=スピン 2=スクロールバー 3=テンキー。MIN/MAX/STEP、Enter で STEP 単位に切り上げ | MP1 SP1 SCB1 L1 B0〜B9 BCNG BDot BCLR BENT LMIN/LMAX/LCNG | objReturn, ckNum, BENT_Click, UserForm_QueryClose | 数値入力モーダル（min/max/step/切り上げ） |
| **CoinInputFm** | 金種入力（10,000〜1 円の枚数）→ 収入金額/支出金額を親フォームに書き戻し。仮払出納帳（progNo 3）専用 | T1〜9 SP1〜9 Lx1〜9 RLB OLB LSUM CB1〜5 | objReturn, CB1_Click, resetcalc | 判断事項 D4（他システム対応の範囲） |
| **Msg** | MsgBox 代替。vbYesNo 等の型番号・アイコン・任意ボタン | L1 Icon C1〜C3 | Box, BottomSetting, NumSet | トースト / 確認ダイアログ |
| **WAITMSG** | 処理中表示（ランダム画像） | IM1 LB1 | customCom | busy 表示（流用） |

### クラスモジュール（6）

| クラス | 役割 | Web での扱い |
|---|---|---|
| **DBconnection** | ADO 接続と SQL 実行のラッパ。init(パス)=SQL で開き、Fields/Records/TableData/FieldDic/RecordDic/RecordCount/PageRec（ページ切出し）/TableNameArr（ADOX）/CheckColumns（テーブル名＝2列目名の整合性）を持つ。エラー時は「結合テーブルにエラー」「書式が合わない列」を表示して End | sqlite3 リポジトリ（`shared_db.py`）に置換。ページ切出しは SQL の LIMIT/OFFSET |
| **WithEvCls** | 動的生成コントロール（フィルタラベル/リスト、編集テキスト、MF2、画像）のイベントを一括受信して標準モジュールへ委譲 | 不要（DOM イベント） |
| **WithEvEE** | ConnectToConnectionPoint で Enter/Exit を横取りし、編集中の欄をピンクにする | CSS :focus |
| **CFevent**（＋祝日判定） | カレンダーラベルのクリック/ホバー、および国民の祝日判定（1948/7/20 以降、振替休日 1973/2007 改正、国民の休日 1985、春分・秋分の計算式、〜2050 年） | 祝日判定を `holidays_jp.py` に移植（標準ライブラリ） |
| **CoinInputCls** | 金種入力のテキスト/スピンの連動と数値以外の入力拒否 | D4 次第 |
| **cCopyPaste** | テキストボックスの右クリックメニュー（コピー/切取/貼付） | 不要（ブラウザ標準） |

### シートモジュール・他ブック依存

- シートモジュールのコードは提供されていません。標準モジュールから `Sh1`（デバッグ用 ACCDB 作成、PDFCREATE、TxtToAccdb）と `check` モジュール（VBProject 信頼の検査）が参照されるだけで、**業務処理はシートを使いません**。
- 他の VBA ブックや共通アドインへの参照はありません。`ThisWorkbook` 自身のみです。帳票だけが外部ブック `出力原紙.xlsx` を開きます。
- ブック名の `(YYYYMMDD)` を VER としてログに書きます（`ExtractString(ThisWorkbook.Name, "\((\d{8})\)")`）。

### 未使用コードの扱い

静的解析で「どこからも呼ばれない」手続きは 152 個ありました。うち 101 個はイベントハンドラ（呼び出し元は実行系）なので実質的な未使用は 51 個です。うち他システム用（DAITYOREPORT / GIKAKUREPORT / CoinInputFm 経路）と開発用（test1〜9、ACCDBファイル作成、レジストリ登録/取得/全参照、EXCEL表示、PDFCREATE、IntegrateAndFormatFiles_Final）は移植対象外、汎用ライブラリの余剰分（AppRun、EndProcess、しゃべる、ExpCSV、XlsToAccdb、TxtToAccdb、lv2Lv など）も移植しません。「VBA に存在する処理を削らない」原則との整合は判断事項 D4 で確認します。

## 業務フロー（処理の順序と分岐）

### 手順の詳細（VBA の手続き名つき）

1. **起動** `フォーム起動`→`プログラム番号チェック`：progNo でサーバーパス・SysName・ACCDB 名を確定。
2. **初期設定** `Login.UserForm_Initialize`→`初期設定値確認`：レジストリ `SysName\起動設定\モード` が無ければ regUF でオンライン/オフラインを選ばせ保存。オフライン時は DB をブックと同じフォルダに探す（`SrvPath`）。
3. **認証** `LOGINEXE_Botton_Click`：親フォルダ到達確認→フォルダ内 .accdb 一覧→本体存在確認（無ければ候補名を提示して再設定）→ ID テーブルを `[ID]=入力ID`、`[PC]=PC名`、両方一致 で 3 回引く→ PASS を小文字・半角化して比較→成功で `USELV=Level`、失敗で Access.log に「認証失敗」。開発者（固定 ID/PC）は素通しで Level 5。
4. **一覧初期化** `MF1.UserForm_Initialize`：全般設定 5 項目を読み、全テーブルの「2 列目名＝テーブル名」検査（`CheckColumns`、除外: ID/全般設定/フィルタ/メイン/_設定/H_）、`MasterFileCheck`（メインと _設定の項目数・項目名の順序一致、フィルタ列の存在）で不一致なら起動中止。`FormInitialize` でフィールド数ぶんの Label/TextBox とフィルタ数ぶんの Label/ListBox を動的生成し、各欄に入力形式の色とロックを付ける（`setModifyedForm`）。
5. **一覧表示** `LV1Reflesh`：`CODESQL(WHERE)` で SELECT を組み立て、フィルタ候補を再生成（`SetListBoxItems`）、1 ページ行数 = ListView 高さ÷17.5、初期表示が空欄でフィルタ未指定なら複数ページ。
6. **フィルタ** リスト選択 → `ListClickEvents`→`GetSQLFilterStr`（型別 WHERE）→再表示。ラベルのダブルクリックで集計モード（`LabelDblClickEvents`、GROUP BY + COUNT）。
7. **モード切替** CB1〜CB4 / 行ダブルクリック → `ボタンクリック制御`：レベル判定（新規・削除は 3 以上、修正は 2 以上、ダブルクリックで権限不足なら参照に降格）→ `SET_BEF`（新規は `GetNewNo`、他は選択行の管理番号で再取得、NUM/SPN でレコード送り）→ `SetDataToCtrl` → 連動表示・必須/履歴アイコン・（参照時）文字色ルール・ファイルアイコン・ICON 画像 → `画面切替` でページ 2 へ。
8. **補助入力** 欄のダブルクリック → `TextDblClickEvents`：入力形式で SF/CF/NumFm/専用フォームを開く。Enter → `TextEnterEvents`（数値変換）。Delete/BackSpace → `TEXTDeleteEvents`（ロック欄は確認、# 自動項目は禁止）。変更欄はオレンジ（`TEXTChangeEvents`）。
9. **実行** `REG_EXE`：全欄の数値変換→必須未入力なら赤くして中止→修正で変更なしなら中止→履歴設定のある変更欄ごとに `GetHisRecArr`（10 列）を H_ テーブルへ ADDNEW → メインを `adoSQL`（新規/修正はレコード有無で ADDNEW↔UPDATE を自動切替、確認メッセージ、トランザクション）→ 一覧再表示（削除時はフィルタをクリア）。
10. **設定** SETTING → MF2：レベルで編集可能テーブルを絞り、ListView で行選択、12 欄で新規/修正/削除。閉じると MF1 を再初期化。
11. **閲覧・帳票・出力** BROWSE → MF3（テーブル閲覧・出力・PrintForm）。REPORT → 原紙ブックのシートをコピーし帳票プロシージャを実行して PrintPreview。OUTPUT → 現在の一覧（結合列込み）を xlsx/txt/csv。

## 設定テーブル仕様（システムを決めるもの）

列名・テーブル名の定数は `【A0】UserSetting` にあり、変更禁止です（PPTX 6 枚目）。以下が Web 版でもそのまま「設定スキーマ」になります。

| テーブル | 名前 | 必須 | 列 | 使われ方 |
|---|---|---|---|---|
| 全般設定 | 全般設定（固定） | 必須 | 管理番号 / 項目名 / 設定内容 | 項目名: **メインテーブル名**（必須）、**初期表示**（空欄=行数制限あり=複数ページ、何か入力=1 ページ展開）、**初期フィルタ**（`フィールド/符号/値`、例 `ソートコード/>=/20230224002`。空欄可）、**帳票**（`パス/原紙xlsx/シート名/プロシージャ名[/版数列数]`、パス空欄=ブックと同階層）、**テーブル閲覧**（`テーブル1/テーブル2/…`） |
| メイン | 任意名 | 必須 | 1 列目は **管理番号**（数値・重複なし・主キーなし） | 業務データ本体。列順が _設定 の行順と一致していないと起動中止 |
| メイン_設定 | メイン名+「_設定」 | 必須 | 管理番号 / 項目名 / 文字色設定 / 入力形式 / 登録条件 / 参照形式 / マスタ結合 / 履歴 / 表示 / 追加表示 / ソート（11 列、`formatCheck` で検査） | 1 行 = メインの 1 列。下表参照 |
| フィルタ | フィルタ（固定） | 必須 | 管理番号 / フィルタ / タイプ / 指定幅 / 備考 | フィルタ列（空欄なら無効）、タイプ（空/YYYY/YYYYMM/>=/<=/</>/マスタ名）、指定幅（数値=固定幅、空欄=残りを均等割、合計超過なら比率換算） |
| フィルタ_設定 | フィルタ_設定（固定） | 必須 | 管理番号 / 項目名 / 入力形式 | MF2 でフィルタ表を編集するときの入力形式（`フィールド`＝現在の一覧列名から選択、`sタイプ` 等） |
| タイプ | タイプ（固定） | 必須 | 管理番号 / タイプ / 備考 | 管理番号 0〜5 は固定（削除禁止）、6 以降にマスタ名を登録するとフィルタにマスタが使える |
| ID | ID（固定） | 必須 | ID / PC / PASS / Level（他列は不問） | 認証とアクセスレベル |
| 履歴 | H_ で始まる任意名 | 任意 | 管理番号 / 処理名 / メイン管理番号 / 変更前 / 変更後 / 追加 / 削除 / 更新元(PC) / 更新者 / 更新日（10 列固定） | _設定「履歴」列にテーブル名が書かれた項目の変更時に追記。MF2 では閲覧のみ |
| サブマスタ | 任意名 | 任意 | 1 列目 管理番号、**2 列目はテーブル名と同名** | s/m 入力の選択肢、フィルタのマスタ型。2 列目名が違うと起動時に警告 |
| クエリ | Q_ など | 任意 | Access のクエリ | マスタ結合の結合先（LEFT JOIN）、テーブル閲覧の対象になりうる |

### メイン_設定 の 11 列

| 列 | 書式 | 意味（VBA での実装） |
|---|---|---|
| **項目名** | メインの列名 | 行順＝列順。`MasterFileCheck` が一致を検査 |
| **文字色設定** | 通常色/変更色/引数/JS式/型/設定部 | 参照モードで値が日付のときだけ、JScript（`new Function(引数, "return "+式)`）を評価し、真なら変更色を `CallByName(TextBox, 設定部, VbLet, palette(色名))` で適用。色名は ColorPalette の 143 色（`vbRed`、`rgbCrimson` など） |
| **入力形式** | 下表 | `setModifyedForm`（見た目・ロック）と `TextDblClickEvents`（補助入力） |
| **登録条件** | 必須 | 未入力なら背景赤で登録中止。必須アイコン表示 |
| **参照形式** | 連動/フィールド名/連動キー ｜ PDF\|ICON\|TXT\|XLS\|ZIP\|CSV\|DOCX/絶対パス/相対パス | 連動: 修正・参照時に結合先の値を右側に表示。ファイル: 参照モードで `パス\値.拡張子` をダブルクリックで開く。ICON: `パス\値.jpg`（カンマ区切りで複数）を欄の上に並べ、ダブルクリックで開く。相対パスはサーバーパス基準、空欄はブックのフォルダ |
| **マスタ結合** | マスタ名/結合キー/結合フィールド[/金額] | CODESQL で `LEFT JOIN [マスタ] AS Bi ON A.[キー]=Bi.[キー]`、結合フィールドを項目の右隣に挿入。金額なら `FORMAT(…,'#,##0')` |
| **履歴** | H_テーブル名 | 変更時に履歴行を追記。履歴アイコン表示。テーブルが無ければ履歴機能全体を無効 |
| **表示** | 金額 | 一覧で `FORMAT(A.[列],'#,##0') AS 列` |
| **追加表示** | 挿入位置/SQL式/別名/書式 | 計算列を挿入位置の列の右隣に追加（`[ ]` は `A.[ ]` に補完）。編集対象外 |
| **ソート** | ASC / DESC | 入力のある最後の行がソート列。未入力なら管理番号 ASC |

### 入力形式の一覧（`setModifyedForm` / `TextDblClickEvents`）

| 入力形式の値 | 編集画面の挙動 | 登録時の値 |
|---|---|---|
| （空欄） または /… で始まる | 自由入力（象牙色）。`/行数` を付けると複数行欄（行数は改行数と設定値の大きい方） | 入力値 |
| s+マスタ名 / m+マスタ名 | ロック。ダブルクリックで SF を開き単一/複数選択（複数はカンマ連結）。表示列は「マスタ名と同名の列」、無ければ 2 列目。マスタ結合が設定されていれば「マスタ連動」で結合先の値も同時表示 | 選択値（カンマ連結） |
| calender[@過去\|@未来][/連動先/関数名/引数1,引数2] | ロック。CF カレンダー。@過去は Now より未来を拒否、@未来は過去を拒否（元の値に戻す）。/ 以下があれば `Application.Run(関数名, Array(引数1, 日付, 連動先Tag))` の結果を連動先欄に入れる（例 `calender/ソートコード/GetNewCode/ソートコード,日付`） | YYYY/MM/DD |
| 数値変換 | 自由入力。Enter・クリック移動・登録時に半角化。数値でない非空文字は 0 | 数値文字列 |
| 数値N/MIN/MAX/STEP | ロック。NumFm（N=1 スピン、2 スクロールバー、3 テンキー）。Enter で STEP の倍数に切り上げ、閉じるときに MIN/MAX に丸めて通知 | 数値 |
| フォルダ | ロック。参照形式のパスにある `*.拡張子` のファイル名（拡張子なし）から選択 | ファイル名 |
| フィールド | ロック。現在の一覧の列名から複数選択（フィルタ_設定 用） | 列名 |
| $フォーム名/欄1,欄2,… | 専用 UserForm を `UserForms.Add` で生成し `objReturn(欄, 欄名配列)`。例 `$CoinInputFm/ソートコード,収入金額,支出金額` | フォーム依存 |
| #VIEW# | 表示のみ（灰色） | 元の値 |
| #NEWUSER# #NEWPC# #NEWDATE# | 表示のみ | **新規時**に ネットワーク ID / PC 名 / `YYYY/MM/DD hh:mm:ss`。修正時は元の値 |
| #UPUSER# #USEPC# #UPDATE# | 表示のみ | **修正時**に 同上。新規時は空 |
| その他 | 「ルール適用外です」と表示し欄を赤、値 "SYSTEM ERROR" | — |

## 判定・計算ロジック（結果を一致させるもの）

Web 化で見た目は変わっても、ここに挙げた判定・計算・出力は VBA と同じ結果にします。すべてゴールデンテストの対象です。

| ロジック | VBA | ルール（実装どおり） | Python 移植先 |
|---|---|---|---|
| **認証** | Login.LOGINEXE_Botton_Click | ID は入力欄にネットワーク ID が既定で入り、変えると「使用中のIDはPC使用者と一致しません」で拒否（実質 ID 固定）。ID 未登録／PC 未登録／ID×PC 不一致／PASS 不一致を順に判定。PASS は `StrConv(LCase(x), vbNarrow)` 同士で比較。`DEVELOPERFLAG`（ID が Satoshi-Harada/satoshi、PC が NLM-NGY-221790/PCH370）は無条件で Level 5。ID×PC の登録が無く開発者でもないと Level 1 で仮設定 | dbviewer/auth.py |
| **アクセスレベル** | ボタンクリック制御, MF2.UserForm_Initialize | 新規・削除: Level≥3。修正: Level≥2（ダブルクリックで不足なら参照）。参照・出力・帳票・閲覧: 制限なし（PPTX の「1:参照・出力のみ」はコード上は出力にレベル判定なし）。MF2 の編集可能テーブル: Level≥5=メイン以外全部、≥4=メイン・タイプ以外、≥3=メイン・タイプ・ID 以外、それ未満=フィルタのみ。H_ テーブルは常に閲覧のみ | dbviewer/auth.py, master_admin.py |
| **新規番号 GetNewNo** | UserSetting | テーブルの 管理番号 を `Val()` で集め、1 から順に見て最初の欠番（無ければ最大+1）。空テーブルは 1。MF2 の新規もテーブル名を渡して同じ | numbering.py |
| **日付コード GetNewCode** | UserSetting | `YYYYMMDD+3桁`。当日の `LIKE 'YYYYMMDD%'` を集め、末尾 3 桁の最大値まで 1 から見て欠番、または現在の自分のコードならそれを再利用、無ければ最大+1。当日 0 件は 001 | numbering.py |
| **一覧 SQL CODESQL** | CODESQL | SELECT 列＝項目名を `A.[項目]`、表示=金額なら `FORMAT(A.[項目],'#,##0') AS 項目`。マスタ結合行ごとに `LEFT JOIN [マスタ] AS Bi ON A.[キー]=Bi.[キー]`（FROM は結合数ぶんの "(" で入れ子）と結合フィールド列を項目の右隣に挿入。追加表示は `[ ]→A.[ ]` 補完し挿入位置の右隣に、金額なら FORMAT、別名があれば AS。ソート列は「ソート」列に入力のある最後の行、DESC 以外は ASC、未指定は管理番号。初期フィルタ `[f] op 'v'` はユーザー WHERE と AND。WHERE 内の `[列]` はどのテーブルにあるかを辞書で引き `A.`/`Bi.` を補完、二重付与は文字列置換で除去 | sql_builder.py（SQLite 方言へ: 括弧不要、FORMAT は Python 側整形） |
| **フィルタ WHERE GetSQLFilterStr** | ListRefresh | 選択中のフィルタごとに AND 連結。タイプ `YYYY`: `[f]>=#Y/01/01# AND [f]<=#Y/12/31#`。`YYYYMM`: `>=#Y/M/01#` かつ `<#翌月/01#`。`>= <= < >`: `[f] op #Format(値,YYYY/MM/DD)#`。空タイプ: `[f]=#値#`、ただし値に "-" を含むか日付でなければ `'値'`。マスタ名: `[f] LIKE '%値%'`。結果は MF1.SQL 欄に表示 | filters.py（# 区切りは SQLite では文字列比較に置換、判断事項 D3） |
| **フィルタ候補 SetListBoxItems / ListBoxReset** | ListRefresh | 現在の一覧結果からフィルタ列の重複なし値（`NonDuplist`: Trim、QuickSort 昇順、空欄を末尾に追加）。空/YYYY/YYYYMM は日付なら `Format(値,型)` に変換して重複除去。比較型は降順に並べる。マスタ名タイプはマスタの同名列をそのまま候補。先頭に空欄（解除用）。ラベル色: 日付/比較型は RGB(112,110,110)、マスタ型は RGB(130,140,140) で「: マスタ参照」「: 以上」などの注釈 | filters.py |
| **フィルタ幅 GetWidthArr** | FormResize | 基準幅 = フォーム幅−176。指定幅が数値の列は固定、空欄は残りを均等割。固定合計が基準幅を超えると、固定分は「基準幅×(固定列数/全列数)」内で比率換算、空欄分は残りを均等割 | filters.py（CSS grid の fr/px に変換） |
| **ページング** | LV1Reflesh, DBconnection.PageRec | 1 ページ行数 = Int(ListView 高さ÷17.5)（+1 フラットスクロール時）。初期表示が空欄でフィルタが 1 つも選ばれていなければページ切替有効、フィルタ選択中はページ 1 固定。ページ数 = RoundUp(件数÷行数) | records.py（行数は設定値。既定 30、判断事項なし＝見た目の変更） |
| **集計モード** | LabelDblClickEvents | フィルタ列名に ( ) を含めば拒否。マスタ結合列なら「結合テーブルに対する集計機能は未対応」で中止。`SELECT [f], COUNT([f]) AS [カウント] FROM [メイン] GROUP BY [f]`。ボタン類非表示、CB1 で戻る | records.py |
| **必須チェック** | reqConInputFlag | 登録条件=必須 の欄が空なら赤 RGB(255,0,0) にして「必須箇所が埋まっていません」で中止 | records.py（422 + field） |
| **変更なし判定** | checkmodifiedflag | 全欄で Tag（元値）＝Value なら「修正箇所がないため、キャンセルします」。元値は "\|\|\|\|\|" を改行に戻して保持 | records.py |
| **数値変換** | TextEnterEvents | 入力形式=数値変換 の欄: 数値なら半角化、非数値で空でなければ 0。Enter・マウス移動・登録直前に全欄実行 | input_types.py |
| **日付制限** | TextDblClickEvents | @過去: 選んだ日 > Now なら拒否、@未来: 選んだ日 < Now なら拒否（元値に戻して通知） | input_types.py |
| **自動項目** | GetArrDataFromCtrl | #NEWUSER#/#NEWPC#/#NEWDATE# は新規時のみ、#UPUSER#/#USEPC#/#UPDATE# は修正時のみ現在値を入れる。日時は `YYYY/MM/DD hh:mm:ss`。比較は `cv()`（大文字・半角化） | records.py |
| **履歴 GetHisRecArr / CompStrTbx** | MF1 | 履歴設定があり **かつ その H_ テーブルが実在** するときだけ有効（1 つでも無ければ全体無効）。Value≠Tag の欄ごとに 10 列: 新番号(GetNewNo(H_))、処理名(新規/修正/削除)、メイン管理番号、変更前、変更後（削除は空）、追加、削除、PC 名、ユーザー、日時。追加/削除はカンマ分割した集合差（B−A / A−B）をカンマ連結。書込は `[管理番号]=新番号` フィルタで ADDNEW | history.py |
| **1 行更新 adoSQL** | ADO | キー（管理番号）でレコード有無を見て、新規/修正はどちらでも「無ければ ADDNEW、あれば UPDATE」に自動変換（確認: 「新規登録しますか？」「既に登録されているデータがあります。上書きしますか？」「上書き登録しますか？」）。削除はレコード無しなら「登録されていないか、既に削除済み」、ありなら「削除しますか？」。ADDNEW は全列を文字列で代入、UPDATE は 2 列目以降を `'値'` 直書きの SQL（エスケープなし。MF2 経由のみ ' を '' に）。トランザクション、失敗でロールバックとエラー表示 | records.py（プレースホルダで安全に。確認メッセージは同文言） |
| **起動時整合性** | CheckColumns, MasterFileCheck, formatCheck | ①除外以外の全テーブルで 2 列目名≠テーブル名なら警告（OK/NG 一覧を表示、起動は継続）。②メイン列数≠_設定行数、または k 番目の項目名不一致なら「起動を中止」。③フィルタ列がメインにも結合フィールドにも無ければ中止。④_設定の 11 列名が揃っていなければ警告 | schema_check.py（設定タブ「いまの状態」に表示） |
| **文字色ルール** | SET_BEF | 参照モードのみ。値が空でなく日付のとき、JS 式の結果が偽→通常色、真→変更色。設定要素が 6 未満なら無視。例: `new Date(a).setFullYear(new Date(a).getFullYear()+1)<=Date.now()`（1 年経過で色変え） | 判断事項 D5 |
| **参照モードのファイル** | TextDblClickWatchMode, OpenAppFlag | 参照形式が PDF 等: `パス\値.拡張子` を ShellExecute。存在しなければ「ファイル参照に失敗しました」。ICON: `パス\値.jpg` をカンマごとに 18px 画像で並べ、ダブルクリックで開く | files.py（サーバー側 os.startfile／画像配信） |
| **MF2 マスタメンテ** | MF2 | テーブル並び: 空, ID, 全般設定, メイン, メイン_設定, フィルタ, フィルタ_設定, タイプ, 残りは辞書順。新規は `GetNewNo(テーブル)` で 1 列目固定。_設定 テーブルがある表はその入力形式で補助入力（例: フィルタ→フィルタ_設定）。変更欄はオレンジ。実行は `[1列目名]=Tag` をキーに adoSQL（重複時は修正）。値の ' は ''、[ ] を含む値はクォート。削除は **全列一致** の WHERE で対象を引き直して削除。閉じると MF1 を再初期化しフィルタ解除 | master_admin.py |
| **閲覧 MF3** | MF3 | テーブル閲覧に列挙されたテーブルを全件（ページ行数 0 = 1 ページ）表示。出力は TableData（見出し＋全件）、CB3 は画面印刷 | table_browse.py |
| **出力 ExportData** | Export | 確認→保存ダイアログ（既定名 `YYYYMMDD_HHMMSS`、xlsx/txt/csv）。一覧出力は `GetTableArr(CODEStr)`＝現在の WHERE・結合・追加表示込みの全件。txt はタブ区切り、csv はカンマ、各行末にも区切り文字（VBA の癖） | export.py（xlsx は標準ライブラリ writer） |
| **帳票 REPORT（作業標準）** | Report.REPORT | 現在の一覧配列から列 1,2,3,4,5,6,7,8,9,12,20,21,26,27 の 13 列を転記 → `ブックのフォルダ\出力原紙.xlsx` を開き、一時シートを末尾へコピー、シート名 `yyyy-mm-dd`（重複時 _1, _2）。B 列（作業NO）の "-" 前の値が前行と変わる行の上に空行挿入。3〜6 列を小計行（空白行に「小計」）、末尾に Total/GrandTotal。罫線・メイリオ 10pt・列幅自動。ブック保存・閉じる → 印刷プレビュー | reports/sagyo_hyojun.py（HTML 印刷 + xlsx 出力） |
| **ログ** | LogMessage | `SrvPath\Access.log` に TSV: 日時, PC 名, OS 名(版), ユーザー, メッセージ, VER(ブック名の 8 桁) | auth.py / logging |
| **接続モード** | onlineFlag, SrvPath | レジストリ値がオンラインならサーバーパス、それ以外はブックのフォルダを DB の場所とする | config.py（設定タブ） |

## 依存関係・出力・印刷・エラー処理・ハードコード

#### 外部依存（実行に必要なもの）

- Microsoft ACE OLEDB 12.0（.accdb/.xlsx/.csv）、Jet 4.0（.mdb/.xls）、ADOX.Catalog（テーブル一覧・作成）
- MSComctlLib ListView（一覧）、MSForms（動的コントロール）
- Scripting.Dictionary / FileSystemObject、WScript.Network（ID/PC名）、WScript.Shell、Shell.Application（ShellExecute）
- WMI `winmgmts`（OS 名）、`htmlfile`（JScript 評価）、`reg add`（VBOM 信頼設定を書き換えて VBProject を読む）
- user32/kernel32/oleacc/shlwapi の API（窓の細工、イベント接続）
- Excel 本体（帳票のシート複製・PrintPreview・PDF 出力・xlsx 保存）
- レジストリ HKCU（SaveSetting/GetSetting）

#### 外部ファイル

- 本体 .accdb（共有フォルダ／オフラインはブックのフォルダ）
- `Access.log`（同じフォルダ、TSV）
- 帳票原紙 `出力原紙.xlsx`（全般設定「帳票」で指定、既定はブックのフォルダ）
- 参照ファイル: PDF/TXT/XLS/ZIP/CSV/DOCX フォルダ、ICON 用 .jpg フォルダ（相対パスはサーバーパス基準）
- 出力ファイル: 保存ダイアログ先の xlsx/txt/csv、PDFCREATE（デバッグ用）
- `BATFILE.BAT`（reg add 用に一時生成）
- フォーム内蔵の画像（必須/履歴/PDF 等のアイコン、干支、待機画像）

#### 入力データ

- キーボード入力（自由・数値変換）、補助フォームの選択値（マスタ/カレンダー/数値/フォルダ/フィールド/専用フォーム）
- 自動項目（ID・PC 名・日時）
- 設定テーブル（全般設定・_設定・フィルタ・タイプ・ID）
- レジストリの接続モード

#### 出力

- 一覧（ListView、結合・追加表示・金額書式込み）と集計（列値×件数）
- メインテーブルへの登録・更新・削除、H_ への履歴追記、マスタテーブルの更新
- ファイル出力 xlsx/txt/csv（一覧・閲覧テーブル）
- 帳票（原紙 xlsx へ転記して保存 → 印刷プレビュー）、MF3 の PrintForm（画面印刷）
- Access.log

#### エラー処理（VBA の実態）

- 致命エラーは `Msg.Box` の後に **End**（Excel ごと停止）: SQL の列名不一致、結合テーブルのエラー、日付書式不正列（-2147467259）、ADO 接続失敗、モード引数不正、reg add 失敗
- 起動整合性不一致は「起動を中止します」（フォームを閉じる）
- adoSQL は On Error で RollbackTrans しエラー番号・内容を表示
- パス不到達は「ネットワークにアクセスできないか、パスが間違っている」で中断。一覧更新時に到達不能なら MF1 を Unload
- フォーム閉じ時の後始末はあるが、DBconnection の Terminate でエラー番号を見て End する箇所あり

#### ハードコードされた業務ルール

- progNo=4 と 7 システムのパス・名前、developLv=5、開発者 ID/PC 名
- 固定テーブル名・列名（全般設定/フィルタ/タイプ/ID/管理番号/項目名/設定内容…）、"_設定"・"H_" 接尾辞・接頭辞
- レベル境界（新規削除 3、修正 2、MF2 の 3/4/5）、Level 1 仮設定
- 入力形式のキーワード（s/m/calender/数値/フォルダ/フィールド/$/#…#）、日時書式 `YYYY/MM/DD hh:mm:ss`、日付コード `YYYYMMDD+3桁`
- フィルタ型（YYYY/YYYYMM/比較/マスタ）と WHERE の書き方、"-" を含む値は文字列扱い
- 金額書式 `#,##0`、履歴 10 列、"|||||" 改行代替、ページ行高 17.5pt、列幅係数 2.1、フィルタ基準幅 −176
- 帳票 REPORT の列番号（1〜9,12,20,21,26,27）、小計列 3〜6、作業NO の "-" 区切り、原紙名 `出力原紙.xlsx`、シート名 `yyyy-mm-dd`、メイリオ 10pt、列幅 10.25
- 出力ファイル名 `YYYYMMDD_HHMMSS`、ログ名 `Access.log`、VER 抽出 `\((\d{8})\)`
- 色（象牙/シャンパン/オイスター/ロココ等の入力形式別背景、変更＝オレンジ、必須未入力＝赤、編集中＝ピンク RGB(248,198,189)）
- カレンダー年範囲 1920〜2100、祝日 2050 年まで

