#!/usr/bin/env python3
"""デスクトップ版(exe)を本当に起動して確かめる(GitHub Actions の Windows でも流す)

python-web-tools の scripts/desktop_smoke.py と同じ作り。確かめること:
    1. 窓の画面(WebView)が Python から届く
       ── 操作の記録(`記録_<PC>_日付.jsonl`)に「画面を開いた」(ev=screen)が出る
          (WebView → 外枠(Rust)→ Python → 外枠 → WebView が1周した証拠)
    2. **どのプロセスもポートで待ち受けていない**(exe・Python・WebView の子プロセス)
    3. exe を止めると Python も終わる(取り残さない)
    4. 2回目に起動した exe は、窓を出さずにすぐ終わる(戻り値 0)。1つ目は動いたまま
    5. **ブラウザ版とは同時に動かない**: exe が動いているとき、ブラウザ版(start_app.py)は
       理由を出して止まる(戻り値 1)
    6. Windows: 窓に「閉じて」(WM_CLOSE)と頼むと、×と同じ確かめを通って**自分で**終わる

使い方:
    python scripts/desktop_smoke.py --exe src-tauri/target/release/DBViewer.exe
    (Linux では DISPLAY が要る。例: xvfb-run python scripts/desktop_smoke.py --exe ...)

作業用のフォルダ(DB・設定・ログ)は一時フォルダに作る。本番の領域は触らない。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WINDOWS = os.name == "nt"


def children(pid: int) -> list[int]:
    """pid の子孫(孫も)。"""
    if WINDOWS:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process | ForEach-Object { \"$($_.ProcessId) $($_.ParentProcessId)\" }"],
            capture_output=True, text=True).stdout
        pairs = [tuple(map(int, line.split())) for line in out.splitlines() if line.strip()]
    else:
        pairs = []
        for name in os.listdir("/proc"):
            if name.isdigit():
                try:
                    stat = Path(f"/proc/{name}/stat").read_text()
                    pairs.append((int(name), int(stat.rsplit(")", 1)[1].split()[1])))
                except (OSError, ValueError, IndexError):
                    pass
    found, frontier = [], [pid]
    while frontier:
        parent = frontier.pop()
        for child, ppid in pairs:
            if ppid == parent and child not in found:
                found.append(child)
                frontier.append(child)
    return found


def listening_ports(pids: set[int]) -> dict[int, list[int]]:
    """その pid たちが待ち受けている TCP ポート。"""
    result: dict[int, list[int]] = {}
    if WINDOWS:
        out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True).stdout
        out += subprocess.run(["netstat", "-ano", "-p", "TCPv6"], capture_output=True, text=True).stdout
        for line in out.splitlines():
            cols = line.split()
            if len(cols) >= 5 and cols[3].upper() == "LISTENING" and cols[4].isdigit():
                pid = int(cols[4])
                if pid in pids:
                    result.setdefault(pid, []).append(int(cols[1].rsplit(":", 1)[1]))
        return result
    inodes: dict[str, int] = {}
    for name in ("/proc/net/tcp", "/proc/net/tcp6"):
        if os.path.exists(name):
            for line in Path(name).read_text().splitlines()[1:]:
                cols = line.split()
                if cols[3] == "0A":
                    inodes[cols[9]] = int(cols[1].split(":")[1], 16)
    for pid in pids:
        try:
            fds = os.listdir(f"/proc/{pid}/fd")
        except OSError:
            continue
        for fd in fds:
            try:
                target = os.readlink(f"/proc/{pid}/fd/{fd}")
            except OSError:
                continue
            if target.startswith("socket:[") and target[8:-1] in inodes:
                result.setdefault(pid, []).append(inodes[target[8:-1]])
    return result


def alive(pid: int) -> bool:
    if WINDOWS:
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
        return str(pid) in out
    return os.path.exists(f"/proc/{pid}")


def visible_windows(pids: set[int]) -> list[int]:
    """それらのプロセスが持つ、見えている最上位の窓(持ち主のいる窓は除く)。

    業務ツール統合ランチャーの `desktop._windows_of` と同じ選び方。Windows だけ。
    """
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetWindow.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    found: list[int] = []

    def visit(hwnd, _param):
        if user32.IsWindowVisible(hwnd) and not user32.GetWindow(hwnd, 4):   # 4 = GW_OWNER
            owner = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
            if owner.value in pids:
                found.append(hwnd)
        return True

    user32.EnumWindows(callback_type(visit), 0)
    return found


def ask_to_close(pids: set[int]) -> int:
    """窓に WM_CLOSE を送る(×ボタンと同じ)。送った窓の数。"""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    return sum(1 for hwnd in visible_windows(pids) if user32.PostMessageW(hwnd, 0x0010, 0, 0))


def second_launch_exits(exe: str, env: dict, first: subprocess.Popen) -> bool:
    """2回目の起動は窓を出さずに終わり、1つ目は動いたままか。"""
    second = subprocess.Popen([exe], env=env, cwd=str(ROOT))
    try:
        code = second.wait(timeout=30)
    except subprocess.TimeoutExpired:
        second.kill()
        print("[NG] 2回目に起動した exe が30秒たっても終わりません(2つ動いています)")
        return False
    if code != 0:
        print(f"[NG] 2回目に起動した exe の戻り値が {code} でした(0 のはず)")
        return False
    if first.poll() is not None:
        print(f"[NG] 2回目を起動したら1つ目が終わりました(戻り値 {first.returncode})")
        return False
    print("[OK] 2回目に起動した exe はすぐ終わり(戻り値 0)、1つ目は動いたままです")
    return True


def latest_log(work: Path) -> str:
    logs = sorted((work / "logs").glob("dbviewer_*.log"))
    return logs[-1].read_text(encoding="utf-8", errors="replace") if logs else ""


def screens(work: Path) -> list[str]:
    """操作の記録に残った「画面を開いた」の行き先。"""
    out = []
    for path in sorted((work / "logs").glob("記録_*.jsonl")):
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if rec.get("ev") == "screen":
                out.append(str(rec.get("path", "")))
    return out


def browser_refused(env: dict) -> bool:
    """デスクトップ版が動いているとき、ブラウザ版は理由を出して止まるか。"""
    done = subprocess.run([sys.executable, str(ROOT / "start_app.py"), "--no-browser"],
                          env=env, cwd=str(ROOT), capture_output=True, timeout=90)
    err = done.stderr.decode("utf-8", "replace")
    if done.returncode == 1 and "デスクトップ版がもう動いています" in err:
        print("[OK] デスクトップ版が動いているとき、ブラウザ版は理由を出して止まりました")
        return True
    print(f"[NG] ブラウザ版が止まりませんでした(戻り値 {done.returncode}): {err[-400:]}")
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exe", required=True)
    parser.add_argument("--timeout", type=float, default=120)
    args = parser.parse_args()

    work = Path(tempfile.mkdtemp(prefix="desktop_smoke_"))
    # 本体(.sqlite3)は無くてよい(設定の画面が出る)。画面が届くことを見る
    (work / "config.json").write_text(json.dumps({"online": False, "local_dir": str(work / "db"),
                                                  "access_log": False}), encoding="utf-8")
    env = dict(os.environ,
               DBVIEWER_ROOT=str(ROOT),
               DBVIEWER_CONFIG_PATH=str(work / "config.json"),
               DBVIEWER_LOCAL_DIR=str(work / "local"),
               DBVIEWER_LOG_DIR=str(work / "logs"))
    if os.environ.get("SMOKE_PYTHON"):
        env["DBVIEWER_PYTHON"] = os.environ["SMOKE_PYTHON"]
    started = time.monotonic()
    app = subprocess.Popen([args.exe], env=env, cwd=str(ROOT))
    print(f"起動しました: pid={app.pid} 作業={work}", flush=True)

    ok = True
    seen = ""
    while time.monotonic() - started < args.timeout:
        if app.poll() is not None:
            print(f"[NG] exe が先に終わりました(終了コード {app.returncode})")
            ok = False
            break
        opened = screens(work)
        if opened:
            seen = "画面を開いた " + opened[0]
            break
        time.sleep(0.5)
    if seen:
        print(f"[OK] 画面が Python から届きました({time.monotonic() - started:.1f}秒): {seen}")
    elif ok:
        print("[NG] 時間内に画面が届きませんでした。ログの末尾:")
        logs = sorted((work / "logs").glob("*.log"))
        for log in logs:
            print(f"--- {log.name}")
            print("\n".join(log.read_text(encoding="utf-8", errors="replace").splitlines()[-30:]))
        ok = False

    if app.poll() is None:
        tree = {app.pid, *children(app.pid)}
        ports = listening_ports(tree)
        if ports:
            print(f"[NG] 待ち受けているプロセスがあります: {ports}")
            ok = False
        else:
            print(f"[OK] 待ち受けはありません(調べたプロセス {len(tree)} 個)")
        pythons = [pid for pid in tree if pid != app.pid]
        ok = second_launch_exits(args.exe, env, app) and ok
        ok = browser_refused(env) and ok
        closed = False
        if WINDOWS:
            # ランチャーの［ツール停止］と同じ: 窓に「閉じて」と頼み、自分で終わるのを待つ
            sent = ask_to_close({app.pid, *children(app.pid)})
            try:
                code = app.wait(timeout=30) if sent else None
            except subprocess.TimeoutExpired:
                code = None
            if code == 0 and "停止要求を受け付けました" in latest_log(work):
                closed = True
                print(f"[OK] 窓に「閉じて」と頼むと、終了の確かめを通って自分で終わりました(窓 {sent} 枚)")
            else:
                print(f"[NG] 窓に「閉じて」と頼んでも終わりませんでした(窓 {sent} 枚・戻り値 {code})")
                ok = False
        if not closed and app.poll() is None:
            app.terminate()
        app.wait(timeout=30)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and any(alive(p) for p in pythons):
            time.sleep(0.5)
        left = [p for p in pythons if alive(p)]
        if left:
            print(f"[NG] exe を止めても残ったプロセスがあります: {left}")
            ok = False
        else:
            print("[OK] exe を止めたら子プロセス(Python など)も終わりました")
    print("結果:", "OK" if ok else "NG")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
