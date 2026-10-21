#!/usr/bin/env python3
r"""配布用フォルダを作る(python-web-tools の scripts\make_dist.py と同じ作り)

【使い方】
1. GitHub Actions「デスクトップ版(Windows)」の成果物から `DBViewer.exe` を取ってきて、
   ツールのフォルダの**直下**(Start.vbs と同じ場所)に置く
2. `scripts\make_dist.bat` をダブルクリックする
3. ツールの隣に `DBビューア_VER版` ができる。中に `DBビューア.exe`(デスクトップ版)と
   `Start.vbs`(ブラウザ版・予備)が入る。このフォルダを各 PC に配る
4. 配った先の PC で `scripts\make_shortcuts.vbs` をダブルクリックすると、ツールのフォルダに
   `DBビューア(ブラウザ版).lnk` と `DBビューア(デスクトップ版).lnk` ができる

【なぜ要るのか】
手でフォルダをコピーすると、**配ってはいけないもの**が紛れる:

    data\user_config.json … その端末の設定(接続先・記録の置き場所)
    tests\ / logs\ / __pycache__ / .git / src-tauri\(外枠のソース)…
    *.sqlite3 / *.accdb   … 業務の本体(共有フォルダに置くもの。配るものではない)

    python scripts\make_dist.py                     # ツールの隣に「DBビューア_VERx.y.z」
    python scripts\make_dist.py --out D:\配布\今回   # 置き場所を指定
    python scripts\make_dist.py --zip               # zip も作る
    python scripts\make_dist.py --no-settings       # 配布設定を入れない

配った先で同じ設定(設定ファイル・開く DB・置き場所・管理者パスワード)を使いたいときは、先に
設定 →「配布設定」で書き出しておく(ツールの直下の `配布設定\`。配った先が起動時に読み込む。0.30.0)。
"""
from __future__ import annotations

import argparse
import datetime as dt
import fnmatch
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 直下で**配るもの**
INCLUDE: tuple[str, ...] = (
    "README.md", "CHANGELOG.md", "requirements.txt",
    "Start.vbs", "start.bat", "stop.bat",
    "start_app.py", "server.py", "boot_server.py", "launch_guard.py", "process_manager.py",
    # デスクトップ版の入口(exe が子として起動する)
    "bridge.py",
    "app", "config", "docs", "dbviewer", "scripts", "vendor",
)

# リポジトリには置くが配らないもの(デスクトップ版の外枠のソースと、それを作る仕組み・試験)
DEV_ONLY: tuple[str, ...] = ("src-tauri", ".github", "tests", "FOUNDATION_ORIGIN.md")

# デスクトップ版の exe。GitHub Actions(Windows)が作る `DBViewer.exe` を、
# 配るときはこの名前でフォルダの直下に置く(押す物が分かる名前にする)
EXE_NAME = "DBビューア.exe"
# 作る配布フォルダの名前(既定の置き場所 `ツールの隣\<これ>_VER版`)
DIST_FOLDER_NAME = "DBビューア"
# exe を探す場所(先に見つかったもの)。`--exe` で指定もできる
EXE_CANDIDATES: tuple[str, ...] = (
    "DBViewer.exe",
    "src-tauri/target/release/DBViewer.exe",
)

# 中にあっても写さないもの(名前で見る。フォルダならその下ごと)
EXCLUDE_NAMES: tuple[str, ...] = (
    "__pycache__", "*.pyc", "*.pyo", ".pytest_cache", ".coverage", ".coverage.*",
    "htmlcov", "*.tmp", "*.bak", "*.bak-*", ".DS_Store", "Thumbs.db",
    "*.sqlite3", "*.sqlite3-journal", "*.db", "*.accdb", "*.laccdb", "Access.log",
)

# 配布設定のフォルダ(`dbviewer/distribution.py` の置き場所と同じ)。
# **INCLUDE には入れない** ── 入れるかどうかは --no-settings で決める
SETTINGS = Path("配布設定")

# できたフォルダに**入っていてはいけない**もの(最後に確かめる)
FORBIDDEN: tuple[str, ...] = ("data", "logs", "tests", ".git", "src-tauri")


def _version() -> str:
    try:
        return json.loads((ROOT / "config" / "app.json").read_text(encoding="utf-8"))["version"]
    except (OSError, ValueError, KeyError):
        return "unknown"


def _excluded(name: str) -> bool:
    return any(fnmatch.fnmatch(name, pattern) for pattern in EXCLUDE_NAMES)


def _ignore(directory: str, names: list[str]) -> set[str]:
    return {n for n in names if _excluded(n)}


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def _settings_src() -> Path:
    """書き出した配布設定の場所(アプリが書くところ。`DBVIEWER_DISTRIBUTION_DIR` で変えていればそこ)。"""
    sys.path.insert(0, str(ROOT))
    from dbviewer import distribution
    return Path(distribution.DIR)


def _settings_lines(folder: Path) -> list[str]:
    """配布設定の中身を、メモと画面に出す形で(管理者パスワードの値は出さない)。"""
    sys.path.insert(0, str(ROOT))
    from dbviewer import distribution
    bundle = distribution.read(folder)
    if bundle is None:
        return ["  (中身がありません・読めません)"]
    lines = [f"  {distribution.ITEM_LABELS.get(k, k)}: {distribution.show(k, v)}" for k, v in bundle.settings.items()]
    if bundle.contents is not None:
        lines.append(f"  {distribution.CONTENTS_LABEL}: {distribution.CONTENTS_NAME}")
    if bundle.created_at:
        lines.append(f"  (作成 {bundle.created_at} / {bundle.created_on})")
    return lines


def find_exe() -> Path | None:
    for name in EXE_CANDIDATES:
        path = ROOT / name
        if path.is_file():
            return path
    return None


def build(out: Path, *, force: bool = False, make_zip: bool = False,
          exe: Path | None = None, with_settings: bool = True) -> tuple[Path, list[str]]:
    """配布用フォルダを作る。戻り値は (できたフォルダ, 画面に出す行)。断るときは `SystemExit`。"""
    out = out.resolve()
    if _inside(out, ROOT):
        raise SystemExit(f"ツールのフォルダの中には作れません: {out}\n"
                         "(次に作るとき、前に作ったものまで写してしまいます)")
    if out.exists() and any(out.iterdir()):
        if not force:
            raise SystemExit(f"{out} はもうあって、中身があります。\n"
                             "別の場所を --out で指定するか、--force で作り直してください。")
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)

    missing = []
    for name in INCLUDE:
        src = ROOT / name
        if not src.exists():
            missing.append(name)
            continue
        if src.is_dir():
            shutil.copytree(src, out / name, ignore=_ignore, dirs_exist_ok=True)
        else:
            shutil.copy2(src, out / name)
    if missing:
        shutil.rmtree(out)
        raise SystemExit("配るはずのファイルがありません: " + ", ".join(missing))

    lines = [f"配布用フォルダを作りました: {out}", f"版: VER{_version()}"]

    # デスクトップ版の exe(あれば)。無くてもブラウザ版(Start.vbs)で動く
    exe = exe if exe is not None else find_exe()
    if exe is not None and exe.is_file():
        shutil.copy2(exe, out / EXE_NAME)
        lines.append(f"デスクトップ版を入れました: {EXE_NAME}(元: {exe})")
    else:
        lines.append("デスクトップ版(exe)は入っていません。GitHub Actions の「デスクトップ版(Windows)」で"
                     "作った DBViewer.exe を、ツールのフォルダの直下(Start.vbs と同じ場所)に置いてから"
                     "作り直してください(無くてもブラウザ版の Start.vbs で動きます)。")

    # 配布設定: 入れる/入れないを**はっきり決める**(--no-settings)
    settings_src = _settings_src()
    if with_settings and settings_src.is_dir():
        shutil.copytree(settings_src, out / SETTINGS, ignore=_ignore)
        lines.append(f"{SETTINGS} フォルダを入れました(配った先が起動時に読み込みます):")
        lines += _settings_lines(out / SETTINGS)
    elif with_settings:
        lines.append("配布設定は入っていません(設定 →「配布設定」で書き出すと、配った先が同じ設定で始められます)。")
    else:
        lines.append("配布設定は入れていません(--no-settings)。")

    # 入っていてはいけないものが無いか、最後に確かめる
    leaked = [p for p in FORBIDDEN if (out / p).exists()]
    leaked += [str(p.relative_to(out)) for p in out.rglob("*") if _excluded(p.name)]
    if leaked:
        shutil.rmtree(out)
        raise SystemExit("配ってはいけないものが入ったため、作るのをやめました: "
                         + ", ".join(sorted(set(leaked))))

    # 配った先で押すもの。指す先はその PC での場所なので、ここでは作らない
    lines.append("配った先で scripts\\make_shortcuts.vbs をダブルクリックすると、ツールのフォルダに"
                 f"{DIST_FOLDER_NAME}(ブラウザ版).lnk と {DIST_FOLDER_NAME}(デスクトップ版).lnk が"
                 "できます(指す先はその PC での場所)。")
    files = sum(1 for p in out.rglob("*") if p.is_file())
    lines.append(f"ファイル数: {files}")
    (out / "配布メモ.txt").write_text(_memo(lines), encoding="utf-8-sig")

    if make_zip:
        archive = shutil.make_archive(str(out), "zip", root_dir=out.parent, base_dir=out.name)
        lines.append(f"zip も作りました: {archive}")
    return out, lines


def _memo(lines: list[str]) -> str:
    today = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    return "\n".join([
        f"DBビューア 配布メモ({today})",
        "",
        *lines,
        "",
        "配った先ですること",
        "  1. このフォルダを好きな場所に置く(以前の版のフォルダに上書きしない)",
        "  2. Python 3.9 以降と、Flask・waitress が入っているか確かめる",
        "     (入っていなければ: python -m pip install -r requirements.txt)",
        "  3. scripts\\make_shortcuts.vbs をダブルクリックする。ツールのフォルダに",
        f"     {DIST_FOLDER_NAME}(ブラウザ版).lnk … Start.vbs を指す",
        f"     {DIST_FOLDER_NAME}(デスクトップ版).lnk … {EXE_NAME} を指す(アイコンも exe のもの)",
        "     ができる。デスクトップや作業フォルダへ写して使う。フォルダを移したら、もう一度押す",
        f"  4. {EXE_NAME}(デスクトップ版。ポートを使いません)で起動する。",
        "     exe が無いとき・動かないときは Start.vbs(ブラウザ版)で起動できます",
        "  5. ブラウザ版とデスクトップ版は同時に動きません。後から開いたほうが理由を出して止まります",
        "",
        "配布設定(このフォルダに「配布設定」があれば)",
        "  起動したときに読み込みます(設定ファイル・開く DB・置き場所・管理者パスワードなど)。",
        "  その PC で変えた設定は上書きしません。揃えたいときは 設定 →「配布設定」→「読み込み直す」",
        "",
        "以前の版から入れ替えるとき",
        "  設定(接続先・記録の置き場所など)は PC ごとのローカル領域(%LOCALAPPDATA%\\DBViewer)に",
        "  あるので、新しいフォルダで起動すればそのまま使えます。古い版のツールは先に閉じる(終了ボタン)",
        "",
        "入れていないもの: data・logs・tests・src-tauri(外枠のソース)・本体(.sqlite3 / .accdb)",
        "",
    ])


def default_out() -> Path:
    """既定の作る場所: ツールの隣の「DBビューア_VER版」。"""
    return ROOT.parent / f"{DIST_FOLDER_NAME}_VER{_version()}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="配布用フォルダを作る")
    parser.add_argument("--out", help=f"作る場所(既定: ツールの隣に「{DIST_FOLDER_NAME}_VER版」)")
    parser.add_argument("--force", action="store_true", help="作る場所に中身があれば消して作り直す")
    parser.add_argument("--zip", action="store_true", help="zip も作る")
    parser.add_argument("--exe", help="入れるデスクトップ版の exe(既定: 直下の DBViewer.exe など)")
    parser.add_argument("--no-settings", action="store_true", help="配布設定(ツール直下の 配布設定)を入れない")
    args = parser.parse_args(argv)

    out = Path(args.out) if args.out else default_out()
    try:
        _out, lines = build(out, force=args.force, make_zip=args.zip,
                            exe=Path(args.exe) if args.exe else None, with_settings=not args.no_settings)
    except SystemExit as exc:
        print(exc, file=sys.stderr)
        return 1
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
