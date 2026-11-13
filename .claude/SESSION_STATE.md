# 作業状態(SESSION_STATE)

> 圧縮のあとに読み直すためのメモ。**完了済みは短く 1 行に**まとめ、同じことを何度も足さない。
> 進めたら更新する(作業目的・未完了・次にすることを最新に)。

## 作業目的
DBビューア(VBA の汎用システムを Python Web に移植したもの)の改修と不具合修正。
ブランチ `claude/practical-faraday-uaqid6` に push(PR は頼まれたときだけ)。最新は 0.46.2。

## 未完了の作業
- 圧縮前後でこの仕組みが引き継げるかの確認(利用者に /compact を打ってもらって確かめる)
- 0.44.0〜0.46.2 のライン PC での確認(利用者の PC はまだ DBビューア_VER0.37.0 を使っている時がある)
- 構造点検の改善案 7(プロセスの確かめを 1 つに)・8(ファイル探しを 1 つの部品に): 次にその場所で不具合が出たら、試験を固めてから
- `/api/list/value` が無い行で空を返す件: 保留(利用者の判断待ち)

## 次にすること
- 利用者の返事を待つ。新しい依頼が来たらここを更新する

## 重要な決まり・決定
- 返事は日本語。ライン PC には標準ライブラリ + Flask + waitress だけ(Playwright は試験と CI だけ)
- Access は cscript→VBScript→ADODB→ACE。.accdb・アップロードされた sqlite はコミットしない。パスワードは記録しない
- コミットは `git -c user.name="Claude" -c user.email="noreply@anthropic.com" commit`、明示したパスだけ add(-A は使わない)
- Start.vbs / start.bat / stop.bat は CP932 + CRLF のまま(この環境では改行だけの差分が出るので assume-unchanged。コミットしない)
- 版を上げるときは config/app.json・src-tauri/tauri.conf.json・Cargo.toml 7 行目・Cargo.lock 663 行目・CHANGELOG
- **試験が落ちたら push しない**(終了コードで止める)。push のあと Windows の CI を確かめる
- 確かめていない推測は推測と書く
- VBA 版の処理は残す / `/api/record/open-file` の制限はそのまま(利用者の判断)

## 不具合と状況
- 0.46.1: 接続先の行を直すと見る DB がフォルダになる → 直した
- 0.46.2: 見る DB の共有に届かない(WinError 59)と起動できない → 直した
- CI run 56 の画面の試験(かんたん設定)が Windows で落ちた → 起動 URL から入るようにして run 57 は通った(原因は確定していない)

## 完了(短く)
- 0.38〜0.43 かんたん設定・見えない文字 / 0.44 PID の使い回し / 0.45 表を削除・接続先 / 0.46 構造点検の改善 1〜6 / 全体像のページ更新

## このごろ変えたファイル
- app/routes/settings.py, dbviewer/shared_db.py, dbviewer/presenters/settings.py, dbviewer/db_files.py, tests/test_api_rest.py, tests/test_ui_flow.py
- .claude/(この仕組み)
