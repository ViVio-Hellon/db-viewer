"""隠れた Excel・Word の後片付け(0.14.0)── all-tools 点検表の core/process_tracking と同じ考え

Excel・Word は VBScript(cscript)から COM で起こすので、cscript の子にならない。cscript を止めても残る。
そこで起こした Excel・Word の **PID と作成時刻** を覚え、終わらなければ**その PID だけ**を止める。
作成時刻まで照合するのは、PID は使い回されるため(利用者が自分で開いている Excel・Word を巻き添えにしない)。
実行ファイル名(EXCEL.EXE・WINWORD.EXE)も確かめる。

途中でアプリごと止められたとき(stop.bat)のために、覚えた PID は `runtime/office_processes.json` にも書き、
次に起動したとき・stop.bat のときに残っていれば片付ける。

ctypes は Python の標準。Windows 以外(開発・試験)では /proc を読む。
"""
from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

IS_WINDOWS = os.name == "nt"
OFFICE_EXES = ("excel.exe", "winword.exe")


@dataclass(frozen=True)
class Identity:
    pid: int
    create_time: Optional[int]
    exe: str = ""


if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _u32 = ctypes.WinDLL("user32", use_last_error=True)
    PROCESS_TERMINATE = 0x0001
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    SYNCHRONIZE = 0x00100000
    WAIT_OBJECT_0 = 0
    _k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _k32.OpenProcess.restype = wintypes.HANDLE
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]
    _k32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    _k32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                ctypes.POINTER(wintypes.DWORD)]
    _k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    _k32.WaitForSingleObject.restype = wintypes.DWORD
    _k32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    _u32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]

    def identity(pid: int) -> Optional[Identity]:
        handle = _k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE, False, int(pid))
        if not handle:
            return None
        try:
            if _k32.WaitForSingleObject(handle, 0) == WAIT_OBJECT_0:
                return None                                # もう終わっている
            times = [wintypes.FILETIME() for _ in range(4)]
            created = None
            if _k32.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
                created = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
            size = wintypes.DWORD(1024)
            buf = ctypes.create_unicode_buffer(size.value)
            exe = buf.value if _k32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)) else ""
            return Identity(int(pid), created, exe)
        finally:
            _k32.CloseHandle(handle)

    def _terminate(pid: int) -> None:
        handle = _k32.OpenProcess(PROCESS_TERMINATE | SYNCHRONIZE, False, int(pid))
        if handle:
            try:
                _k32.TerminateProcess(handle, 1)
            finally:
                _k32.CloseHandle(handle)

    def pid_from_hwnd(hwnd: int) -> Optional[int]:
        """窓の番号から PID(Excel の Application.Hwnd 用)。"""
        pid = wintypes.DWORD(0)
        _u32.GetWindowThreadProcessId(wintypes.HWND(int(hwnd)), ctypes.byref(pid))
        return int(pid.value) or None

else:                                                       # 開発・試験用
    import signal

    def identity(pid: int) -> Optional[Identity]:
        try:
            stat = Path(f"/proc/{int(pid)}/stat").read_text(encoding="ascii", errors="replace")
            fields = stat[stat.rfind(")") + 2:].split()
            if not fields or fields[0] == "Z":
                return None
            exe = Path(f"/proc/{int(pid)}/comm").read_text(encoding="ascii", errors="replace").strip()
            return Identity(int(pid), int(fields[19]), exe)
        except (OSError, IndexError, ValueError):
            return None

    def _terminate(pid: int) -> None:
        try:
            os.kill(int(pid), signal.SIGTERM)
        except OSError:
            pass

    def pid_from_hwnd(hwnd: int) -> Optional[int]:
        return None


def wait_gone(ident: Identity, timeout: float) -> bool:
    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        now = identity(ident.pid)
        if now is None or now.create_time != ident.create_time:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.2)


def terminate_verified(ident: Identity, *, wait_sec: float = 5.0) -> bool:
    """PID・作成時刻・実行ファイル名がすべて合うときだけ止める。消えていれば True。"""
    now = identity(ident.pid)
    if now is None or ident.create_time is None or now.create_time != ident.create_time:
        return True                                        # 終わった・別のプロセス(PID の使い回し)
    if IS_WINDOWS and os.path.basename((now.exe or "").replace("\\", "/")).lower() not in OFFICE_EXES:
        return True                                        # Excel・Word でなければ触らない
    _terminate(ident.pid)
    return wait_gone(ident, wait_sec)


# ------------------------------------------------------------------
# 起こしたものの記録(アプリごと止められたときの片付け用)
# ------------------------------------------------------------------
_lock = threading.Lock()


def _file() -> Path:
    from dbviewer import app_config
    return app_config.local_dir("runtime") / "office_processes.json"


def _load() -> list[dict]:
    try:
        data = json.loads(_file().read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _save(rows: list[dict]) -> None:
    path = _file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if rows:
            path.write_text(json.dumps(rows), encoding="utf-8")
        elif path.exists():
            path.unlink()
    except OSError:
        pass


def track(ident: Identity, what: str) -> None:
    with _lock:
        rows = [r for r in _load() if r.get("pid") != ident.pid]
        rows.append({"pid": ident.pid, "create_time": ident.create_time, "exe": ident.exe, "what": what,
                     "at": time.time()})
        _save(rows)


def untrack(ident: Identity) -> None:
    with _lock:
        _save([r for r in _load() if r.get("pid") != ident.pid])


def cleanup_leftovers(log=None) -> int:
    """前に起こして残っている隠れた Excel・Word を止める(起動時・stop.bat)。止めた数。"""
    stopped = 0
    with _lock:
        rows = _load()
        for r in rows:
            try:
                ident = Identity(int(r["pid"]), r.get("create_time"), str(r.get("exe") or ""))
            except (KeyError, TypeError, ValueError):
                continue
            now = identity(ident.pid)
            if now is None or now.create_time != ident.create_time:
                continue
            if terminate_verified(ident):
                stopped += 1
                if log:
                    log.warning("プレビューで起こした %s が残っていたので止めました(pid=%s)", r.get("what"), ident.pid)
        _save([])
    return stopped
