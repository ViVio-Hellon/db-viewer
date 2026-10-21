"""届かない「\\\\PC名\\…」の場所を、この PC の同じフォルダに読み替える(0.27.0)

かんたん設定の「場所」に `\\\\NGY-232010\\Users\\xfa-ngykonpo\\Desktop\\作業標準…` のように書いたが、
その名前の PC に届かない(WinError 53 など)とき、**この PC に同じフォルダがあればそれを使う**:

    \\\\PC名\\共有名\\残り   →   C:\\共有名\\残り(Users → C:\\Users\\…)・D:\\共有名\\残り …
    …\\Desktop\\残り           →   この人のデスクトップ\\残り(Windows の「デスクトップ」の本当の場所)

書いた場所に届くときは何もしない(読み替えるのは届かないときだけ)。届かない場所は少しのあいだ覚えて、
押すたびに Windows の「届かない」の待ち(数秒)を繰り返さない。
"""
from __future__ import annotations

import os
import string
import sys
import threading
import time
from pathlib import Path, PureWindowsPath
from typing import Optional

UNREACHABLE_TTL_SEC = 60.0
_lock = threading.Lock()
_unreachable: dict[str, float] = {}


def desktop_dir() -> Optional[Path]:
    """この人のデスクトップの本当の場所(フォルダのリダイレクトにも合わせる)。"""
    if sys.platform.startswith("win"):
        try:
            import ctypes
            from ctypes import wintypes

            class GUID(ctypes.Structure):
                _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                            ("Data4", ctypes.c_ubyte * 8)]
            # FOLDERID_Desktop {B4BFCC3A-DB2C-424C-B029-7FE99A87C641}
            fid = GUID(0xB4BFCC3A, 0xDB2C, 0x424C, (ctypes.c_ubyte * 8)(0xB0, 0x29, 0x7F, 0xE9, 0x9A, 0x87, 0xC6, 0x41))
            out = ctypes.c_wchar_p()
            if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(fid), 0, None, ctypes.byref(out)) == 0:
                path = out.value
                ctypes.windll.ole32.CoTaskMemFree(out)
                if path:
                    return Path(path)
        except Exception:                               # noqa: BLE001 - 取れなければ下の決め方
            pass
        home = os.environ.get("USERPROFILE")
        return Path(home) / "Desktop" if home else None
    cand = Path.home() / "Desktop"
    return cand if cand.is_dir() else None


def local_drives() -> list[str]:
    """この PC のドライブ(C: D: …)。Windows 以外は空。"""
    if not sys.platform.startswith("win"):
        return []
    out = []
    for letter in string.ascii_uppercase:
        try:
            if Path(f"{letter}:\\").exists():
                out.append(f"{letter}:")
        except OSError:
            continue
    return out


def alternatives(folder: Path) -> list[Path]:
    """届かないフォルダの読み替え先の候補(この PC の上で)。"""
    win = PureWindowsPath(str(folder))
    parts = list(win.parts)
    out: list[Path] = []
    if str(folder).startswith(("\\\\", "//")) and len(parts) >= 1:
        share = win.drive.replace("/", "\\").strip("\\").split("\\")      # [PC名, 共有名]
        rest = parts[1:]
        for drive in local_drives():
            if len(share) >= 2:
                out.append(Path(drive + "\\", share[1], *rest))           # \\PC\Users\… → C:\Users\…
            out.append(Path(drive + "\\", *rest))                         # \\PC\D\…   → C:\…
    lower = [p.casefold() for p in parts]
    for key in ("desktop", "デスクトップ"):
        if key in lower:
            desk = desktop_dir()
            if desk is not None:
                out.append(desk.joinpath(*parts[lower.index(key) + 1:]))
            break
    seen, uniq = set(), []
    for p in out:
        if str(p) not in seen and str(p) != str(folder):
            seen.add(str(p))
            uniq.append(p)
    return uniq


def _is_dir(folder: Path) -> bool:
    key = str(folder)
    with _lock:
        when = _unreachable.get(key)
        if when is not None and time.monotonic() - when < UNREACHABLE_TTL_SEC:
            return False
    try:
        ok = folder.is_dir()
    except OSError:
        ok = False
    if not ok:
        with _lock:
            _unreachable[key] = time.monotonic()
    return ok


def forget() -> None:
    with _lock:
        _unreachable.clear()


def split(path: Path) -> tuple[Path, str]:
    """(フォルダ, ファイル名)。Windows の書き方(\\)は、Windows 以外で動くとき(試験)も同じに分ける。"""
    path = Path(path)
    text = str(path)
    if "\\" in text and os.sep != "\\":
        cut = max(text.rfind("\\"), text.rfind("/"))
        return Path(text[:cut]), text[cut + 1:]
    return path.parent, path.name


def reroute(path: Path) -> tuple[Path, str]:
    """ファイルの場所(場所の決まりを埋めたもの)→ (探す場所, 読み替えたときの一言)。

    フォルダに届けばそのまま。届かず、この PC に同じフォルダがあれば、そちらに読み替える。"""
    path = Path(path)
    folder, name = split(path)
    if _is_dir(folder):
        return path, ""
    for alt in alternatives(folder):
        if _is_dir(alt):
            return alt / name, f"{folder} に届かないので、この PC の {alt} で探しました"
    return path, ""


def reachable(folder: Path) -> bool:
    return _is_dir(Path(folder))
