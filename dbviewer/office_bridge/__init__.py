"""Excel・Word を PDF にしてプレビューする(0.14.0)── all-tools 点検表と同じ方式

    Python ── cscript ──▶ office_pdf.vbs ── COM ──▶ 隠れた Excel / Word
           ◀── 標準出力(PDF を base64 で)──

古い .xls / .doc は Python の標準だけでは読めない。Python に部品を足さずに読むため、その PC の
Excel・Word に**読み取り専用で**開いてもらい、`ExportAsFixedFormat` で PDF にしてもらう(点検表と同じ)。
PDF にできないとき(プリンターが無い・古い Office)は .xlsx / .docx にしてもらい、表・文にして見せる。

【点検表から持ってきた決まり】
- **ファイルは受け渡さない。** Store 版の Python は %TEMP% を自分専用の控えへ振り替えるので、Python が書いた
  ファイルは cscript・Office から見えない。PDF は VBScript が自分の TEMP に書き、読んで標準出力へ送り、すぐ消す
- 起こした Excel・Word の PID と作成時刻を覚え、終わらなければ**その PID だけ**を止める(`procs`)。
  利用者がもともと開いていた Word が返ってきたとき(WORDSHARED)は決して止めない
- どこで止まったかを言えるように、VBScript は段階(STAGE)を知らせる
- 一度に 1 つだけ。同じファイル(場所・更新時刻・大きさ)は覚えておく
"""
from __future__ import annotations

import base64
import binascii
import os
import queue
import shutil
import subprocess
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from dbviewer.logging_utils import get_logger

from . import procs

log = get_logger("office_bridge")

WORKER_VBS = Path(__file__).resolve().parent / "office_pdf.vbs"
KINDS = {".xls": "excel", ".xlsx": "excel", ".xlsm": "excel",
         ".doc": "word", ".docx": "word", ".docm": "word"}
APP_LABEL = {"excel": "Excel", "word": "Word"}
TIMEOUT_SEC = 90.0
MAX_PAGES = 30                          # 長い文書・大きいブックは先頭のページまで
DUMMY_PASSWORD = "__dbv_no_password__"  # パスワード付きは待たずに失敗させる
CACHE_ITEMS = 6
CACHE_MAX_BYTES = 30 * 1024 * 1024
QUIT_WAIT_SEC = 10.0
CREATE_NO_WINDOW = 0x08000000

STAGES = {
    "excel.create": "Excel を起こす所", "excel.setup": "Excel の準備", "excel.open": "ブックを開く所",
    "excel.pdf": "ブックを PDF にする所", "excel.xlsx": "ブックを .xlsx にする所", "excel.quit": "Excel を終える所",
    "word.pid": "Word を見つける所", "word.create": "Word を起こす所", "word.setup": "Word の準備",
    "word.open": "文書を開く所", "word.pdf": "文書を PDF にする所", "word.docx": "文書を .docx にする所",
    "word.quit": "Word を終える所", "send": "PDF を送る所",
}
MESSAGES = {
    "EXCEL_CREATE_FAILED": "Excel を起こせませんでした(この PC に Excel が入っていないかもしれません)。",
    "WORD_CREATE_FAILED": "Word を起こせませんでした(この PC に Word が入っていないかもしれません)。",
    "OPEN_FAILED": "ファイルを開けませんでした(パスワード付き・壊れている・形式が違う、のどれか)。",
    "EXPORT_FAILED": "PDF にできませんでした。",
    "WORD_GONE": "Word が途中で終わりました。",
    "FILE_NOT_FOUND": "ファイルが見つかりません。",
    "BAD_JOB": "変換の指示が正しくありません。",
}


class ConvertError(Exception):
    def __init__(self, message: str, code: str = "", detail: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.detail = detail


@dataclass
class Converted:
    kind: str                       # "pdf" | "xlsx" | "docx"
    data: bytes
    app: str                        # "Excel" | "Word"
    notes: list[str] = field(default_factory=list)


def kind_of(path: Path) -> str:
    return KINDS.get(path.suffix.lower(), "")


def cscript() -> str:
    found = shutil.which("cscript") or shutil.which("cscript.exe")
    if found:
        return found
    cand = Path(os.environ.get("SystemRoot") or os.environ.get("windir") or r"C:\Windows") / "System32" / "cscript.exe"
    return str(cand) if cand.is_file() else ""


def available() -> bool:
    """この PC で Excel・Word に頼めるか(Windows で cscript があるか。Office の有無は頼んでみて分かる)。"""
    return os.name == "nt" and bool(cscript()) and WORKER_VBS.is_file()


def _command() -> list[str]:
    return [cscript(), "//nologo", "//E:vbscript", str(WORKER_VBS)]


def _hex(text: str) -> str:
    """UTF-16 の 1 単位を 4 桁の 16 進に(VBScript の UnHexW で戻す。日本語のパスも ASCII で送れる)。"""
    raw = text.encode("utf-16-be")
    return "".join(f"{raw[i]:02X}{raw[i + 1]:02X}" for i in range(0, len(raw), 2))


def _unhex(text: str) -> str:
    try:
        return bytes.fromhex(text).decode("utf-16-be", errors="replace")
    except ValueError:
        return text


# ------------------------------------------------------------------
_run_lock = threading.Lock()
_cache: "OrderedDict[tuple, Converted]" = OrderedDict()


def convert(path: Path, *, timeout: float = TIMEOUT_SEC) -> Converted:
    """Excel・Word のファイル → PDF(できなければ .xlsx / .docx)。だめなら ConvertError。"""
    kind = kind_of(path)
    if not kind:
        raise ConvertError(f"Excel・Word のファイルではありません: {path.name}", "BAD_JOB")
    try:
        st = path.stat()
    except OSError as exc:
        raise ConvertError(f"ファイルを読めません: {exc}", "FILE_NOT_FOUND") from None
    key = (str(path), st.st_mtime_ns, st.st_size)
    with _run_lock:                                    # 一度に 1 つ(Excel・Word を何個も起こさない)
        hit = _cache.get(key)
        if hit is not None:
            _cache.move_to_end(key)
            return hit
        result = _run(path, kind, timeout)
        if len(result.data) <= CACHE_MAX_BYTES:
            _cache[key] = result
            while len(_cache) > CACHE_ITEMS:
                _cache.popitem(last=False)
        return result


def forget_cache() -> None:
    _cache.clear()


def _reader(stream, out: "queue.Queue[Optional[str]]") -> None:
    try:
        for raw in iter(stream.readline, b""):
            out.put(raw.decode("ascii", errors="replace").rstrip("\r\n"))
    except (OSError, ValueError):
        pass
    finally:
        out.put(None)
        try:
            stream.close()
        except OSError:
            pass


def _run(path: Path, kind: str, timeout: float) -> Converted:
    app = APP_LABEL[kind]
    flags = CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        proc = subprocess.Popen(_command(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, creationflags=flags)
    except OSError as exc:
        raise ConvertError(f"cscript を起こせませんでした: {exc}", "WORKER_FAILED") from None
    job = [("path", str(path)), ("kind", kind), ("max_pages", str(MAX_PAGES)), ("password", DUMMY_PASSWORD)]
    try:
        proc.stdin.write("".join(f"SET {k} {_hex(v)}\n" for k, v in job).encode("ascii") + b"ENDJOB\n")
        proc.stdin.close()
    except OSError:
        pass
    lines: "queue.Queue[Optional[str]]" = queue.Queue()
    threading.Thread(target=_reader, args=(proc.stdout, lines), daemon=True, name="office-pdf").start()

    office: Optional[procs.Identity] = None
    shared = False
    stage = ""
    chunks: dict[str, list[str]] = {}
    done = ""
    results: dict[str, str] = {}
    notes: list[str] = []
    finished = False
    deadline = time.monotonic() + timeout

    def keep(ident: Optional[procs.Identity], what: str) -> Optional[procs.Identity]:
        if ident is not None:
            procs.track(ident, what)
        return ident

    try:
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                break
            try:
                line = lines.get(timeout=min(left, 1.0))
            except queue.Empty:
                continue
            if line is None:
                break
            head, _, rest = line.partition(" ")
            if head == "STAGE":
                stage = rest.strip()
            elif head == "EXCEL":
                pid = procs.pid_from_hwnd(int(rest or 0)) if rest.strip().isdigit() and int(rest) else None
                office = keep(procs.identity(pid) if pid else None, "Excel")
            elif head == "WORDPID":
                office = keep(procs.identity(int(rest)) if rest.strip().isdigit() else None, "Word")
            elif head == "WORDSHARED":
                shared = True                         # 利用者の Word: 決して止めない
                if office is not None:
                    procs.untrack(office)
                office = None
            elif head in ("PDFDATA", "XLSXDATA", "DOCXDATA"):
                chunks.setdefault(head, []).append(rest.strip())
            elif head == "DONE":
                done = rest.strip()
            elif head == "RESULT":
                key, _, value = rest.partition(" ")
                text = _unhex(value.strip())
                if key == "log":
                    notes.append(text)
                else:
                    results[key] = text
            elif head == "FINISH":
                finished = True
                break
    finally:
        _clean_up(proc, office, finished, app)

    if not finished:
        where = STAGES.get(stage, stage or "始める所")
        log.warning("%s の変換が %.0f 秒で終わりませんでした(%s): %s", app, timeout, where, path)
        raise ConvertError(f"{app} が {timeout:.0f} 秒で応答しませんでした(止まった所: {where})。"
                           f"隠れた {app} は止めました。「現物を開く」で見てください。", "TIMEOUT", where)
    if done:
        prefix = {"pdf": "PDFDATA", "xlsx": "XLSXDATA", "docx": "DOCXDATA"}.get(done, "")
        try:
            data = base64.b64decode("".join(chunks.get(prefix, [])), validate=True)
        except (binascii.Error, ValueError):
            data = b""
        if data:
            if shared:
                notes.append("もともと開いていた Word を使いました(終えていません)")
            return Converted(done, data, app, notes)
    code = results.get("code", "EXPORT_FAILED")
    detail = " ".join(x for x in (results.get("errno", ""), results.get("message", "")) if x)
    where = STAGES.get(results.get("step", ""), results.get("step", ""))
    message = MESSAGES.get(code, f"{app} で変換できませんでした。")
    log.warning("%s の変換に失敗: %s %s (%s) %s / %s", app, code, detail, where, path, " | ".join(notes[-3:]))
    raise ConvertError(message + (f"({where}: {detail})" if detail or where else "")
                       + "「現物を開く」で見てください。", code, detail)


def _clean_up(proc: subprocess.Popen, office: Optional[procs.Identity], finished: bool, app: str) -> None:
    """cscript と、起こした Excel・Word を**必ず**片付ける。"""
    if proc.poll() is None:
        try:
            proc.wait(timeout=5 if finished else 0.1)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
    # 標準出力は読み手のスレッドが終わりまで読んで閉じる(ここで閉じると、まだ読んでいるスレッドと
    # 取り合って止まる。Office が受け口を握ったままのこともある)
    try:
        if proc.stdin and not proc.stdin.closed:
            proc.stdin.close()
    except OSError:
        pass
    if office is None:
        return
    # 終えるよう頼んだ(Quit)なら、まず待つ。終わらなければ、その PID だけ止める
    if finished and procs.wait_gone(office, QUIT_WAIT_SEC):
        procs.untrack(office)
        return
    if procs.terminate_verified(office):
        log.warning("隠れた %s(pid=%s)が残ったので止めました", app, office.pid)
        procs.untrack(office)
    else:
        log.warning("隠れた %s(pid=%s)を止められませんでした。次に起動したときにもう一度止めます", app, office.pid)
