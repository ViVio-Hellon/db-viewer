"""届かない「\\\\PC名\\…」の場所を、この PC の同じフォルダに読み替える(0.27.0)

かんたん設定の「場所」に `\\\\NGY-232010\\Users\\xfa-ngykonpo\\Desktop\\作業標準…` のように書いたが、
その名前の PC に届かない(WinError 53 など)とき、**この PC に同じフォルダがあればそれを使う**:

    \\\\PC名\\共有名\\残り   →   C:\\共有名\\残り(Users → C:\\Users\\…)・D:\\共有名\\残り …
    …\\Desktop\\残り           →   この人のデスクトップ\\残り(Windows の「デスクトップ」の本当の場所)

0.29.0: PC 名の頭の抜け(この PC が NLM-NGY-… なら \\\\NGY-232010 → \\\\NLM-NGY-232010)も候補にし、
フォルダ名は 1 段ずつ全角・半角などを問わずに辿る。届かないときは、どこまで辿れたか(`diagnose`)を出す。

書いた場所に届くときは何もしない(読み替えるのは届かないときだけ)。届かない場所は少しのあいだ覚えて、
押すたびに Windows の「届かない」の待ち(数秒)を繰り返さない。
"""
from __future__ import annotations

import os
import re
import socket
import stat
import string
import sys
import threading
import time
from pathlib import Path, PureWindowsPath
from typing import Optional

UNREACHABLE_TTL_SEC = 60.0
_lock = threading.Lock()
_unreachable: dict[str, tuple[float, str]] = {}


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


def this_pc() -> str:
    """この PC の名前(COMPUTERNAME)。"""
    return (os.environ.get("COMPUTERNAME") or socket.gethostname() or "").strip()


def host_of(folder) -> str:
    """\\\\PC名\\… の PC名。UNC でなければ空。"""
    text = str(folder)
    if not text.startswith(("\\\\", "//")):
        return ""
    return re.split(r"[\\\\/]", text.lstrip("\\/"), maxsplit=1)[0]


def host_guesses(host: str, me: str = "") -> list[str]:
    """PC 名の頭が抜けた・多い書き間違いの候補(0.29.0)。この PC の名前の形に合わせる。

    この PC が NLM-NGY-240638 で、書いた名前が NGY-232010 なら → NLM-NGY-232010
    (逆に、余分な頭が付いていれば外した名前も)。"""
    me = (me or this_pc()).upper()
    host = host.upper()
    if not me or not host or host == me:
        return []
    mine, theirs = me.split("-"), host.split("-")
    out = []
    if len(mine) > len(theirs) >= 2:
        k = len(mine) - len(theirs)                       # 頭が抜けている: この PC の頭を足す
        if mine[k:-1] == theirs[:-1]:
            out.append("-".join(mine[:k] + theirs))
    if len(theirs) > len(mine) >= 2:
        k = len(theirs) - len(mine)                       # 頭が多い: 外す
        if theirs[k:-1] == mine[:-1]:
            out.append("-".join(theirs[k:]))
    return [h for h in out if h != host]


def alternatives(folder: Path) -> list[Path]:
    """届かないフォルダの読み替え先の候補(PC 名の書き間違い・この PC の上の同じフォルダ)。"""
    win = PureWindowsPath(str(folder))
    parts = list(win.parts)
    out: list[Path] = []
    if str(folder).startswith(("\\\\", "//")) and len(parts) >= 1:
        share = win.drive.replace("/", "\\").strip("\\").split("\\")      # [PC名, 共有名]
        rest = parts[1:]
        if len(share) >= 2:
            for guess in host_guesses(share[0]):          # \\NGY-232010\… → \\NLM-NGY-232010\…
                out.append(Path("\\\\" + "\\".join([guess, share[1], *rest])))
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
    """フォルダか。届かなかった場所(とその理由)は少しのあいだ覚えて、Windows の待ちを繰り返さない。"""
    key = str(folder)
    with _lock:
        hit = _unreachable.get(key)
        if hit is not None and time.monotonic() - hit[0] < UNREACHABLE_TTL_SEC:
            return False
    why = ""
    try:
        ok = stat.S_ISDIR(os.stat(folder).st_mode)
        if not ok:
            why = "フォルダではありません"
    except OSError as exc:
        ok = False
        why = exc.strerror or str(exc)
        code = getattr(exc, "winerror", None)
        if code:
            why = f"{why}(WinError {code})"
    if not ok:
        with _lock:
            _unreachable[key] = (time.monotonic(), why)
    return ok


def why_not(folder: Path) -> str:
    """覚えている「届かなかった理由」(無ければ空)。"""
    with _lock:
        hit = _unreachable.get(str(folder))
    return hit[1] if hit else ""


def anchor(folder: Path) -> Path:
    """いちばん上(\\\\PC名\\共有\\ や C:\\)。"""
    parts = Path(folder).parts
    return Path(parts[0]) if parts else Path(folder)


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


def _walk_dir(folder: Path) -> tuple[Optional[Path], Optional[Path], str]:
    """フォルダを先頭から 1 段ずつ辿る(0.29.0)。名前は全角・半角・大文字小文字・空白を問わない
    (「(最新・移動禁止)」と「（最新・移動禁止）」は同じ)。

    答え: (辿り着いたフォルダ or None, 辿れた所まで, 次に無かった名前)。"""
    from .simple_table import name_key
    folder = Path(folder)
    parts = folder.parts
    if not parts:
        return None, None, ""
    cur = Path(parts[0])
    if not _is_dir(cur):                                   # PC・共有・ドライブに届かない
        return None, None, parts[0]
    for seg in parts[1:]:
        nxt = cur / seg
        try:
            if nxt.is_dir():
                cur = nxt
                continue
            key = name_key(seg)
            hits = sorted(e.path for e in os.scandir(cur) if e.is_dir() and name_key(e.name) == key)
        except OSError:
            hits = []
        if not hits:
            return None, cur, seg
        cur = Path(hits[0])
    return cur, cur, ""


def resolve_dir(folder: Path) -> Optional[Path]:
    """フォルダの本当の場所(名前の全角・半角などの違いを許す)。無ければ None。

    先に いちばん上(PC・共有・ドライブ)に届くかを 1 回だけ確かめる(届かない PC で何度も待たない)。"""
    folder = Path(folder)
    if not _is_dir(anchor(folder)):
        return None
    if _is_dir(folder):
        return folder
    return _walk_dir(folder)[0]


def diagnose(folder: Path, limit: int = 12, rows: int = 4) -> list[dict]:
    """届かなかったとき: 書いた場所と、読み替えの候補それぞれで、どこまで辿れて何が無かったか。

    [{place, reached(辿れた所。無ければ ""), missing(無かった名前), children(そこにあるフォルダ名・近い順)}]
    書いた場所はいつも先頭。読み替えの候補は、少しでも辿れたものを深い順に。"""
    import difflib

    from .simple_table import name_key
    out = []
    for i, cand in enumerate([Path(folder), *alternatives(Path(folder))]):
        got, upto, missing = _walk_dir(cand)
        if i and upto is None:
            continue
        row = {"place": str(cand), "reached": str(upto) if upto else "", "missing": missing, "children": [],
               "found": str(got) if got else ""}
        if got is None and upto is not None:
            try:
                names = [e.name for e in os.scandir(upto) if e.is_dir()]
            except OSError:
                names = []
            key = name_key(missing)
            names.sort(key=lambda n: (-difflib.SequenceMatcher(None, key, name_key(n), autojunk=False).ratio(), n))
            row["children"] = names[:limit]
        out.append(row)
    head, rest = out[:1], out[1:]
    rest.sort(key=lambda r: -len(Path(r["reached"]).parts) if r["reached"] else 0)
    return head + rest[:rows - 1]


def reroute(path: Path) -> tuple[Path, str]:
    """ファイルの場所(場所の決まりを埋めたもの)→ (探す場所, 読み替えたときの一言)。

    フォルダに届けばそのまま。名前の全角・半角などが違うだけなら本当の名前に。届かず、この PC に同じフォルダが
    あれば、そちらに読み替える。"""
    path = Path(path)
    folder, name = split(path)
    got = resolve_dir(folder)
    if got is not None:
        if str(got) == str(folder):
            return path, ""
        return got / name, f"「{folder}」は「{got}」として探しました(名前の全角・半角などの違い)"
    for alt in alternatives(folder):
        got = resolve_dir(alt)
        if got is None:
            continue
        old, new = host_of(folder), host_of(alt)
        if old and new and old.upper() != new.upper():
            return got / name, f"PC「{old}」に届かないので、PC 名を「{new}」として探しました"
        return got / name, f"{folder} に届かないので、この PC の {got} で探しました"
    return path, ""


def reachable(folder: Path) -> bool:
    """フォルダに届くか(先に いちばん上を確かめる)。"""
    folder = Path(folder)
    return _is_dir(anchor(folder)) and _is_dir(folder)


def unreachable_text(folder: Path) -> str:
    """届かないときの説明(どこで止まったか・PC 名の書き間違いの心当たり)。"""
    folder = Path(folder)
    top = anchor(folder)
    host = host_of(folder)
    if not _is_dir(top):
        why = why_not(top)
        if host:
            msg = f"PC「{host}」(またはその共有)に届きません" + (f"({why})" if why else "")
            msg += f"。この PC は「{this_pc()}」です。\\\\ のすぐ後ろの PC 名が、エクスプローラで開けるときの名前と同じか確かめてください"
            return msg + "。"
        return f"「{top}」に届きません" + (f"({why})" if why else "") + "。"
    got, upto, missing = _walk_dir(folder)
    if got is None and upto is not None:
        return f"「{upto}」までは有りますが、その中に「{missing}」がありません。"
    return f"「{folder}」に届きません" + (f"({why_not(folder)})" if why_not(folder) else "") + "。"
