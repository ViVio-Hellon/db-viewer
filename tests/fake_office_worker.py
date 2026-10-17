"""office_pdf.vbs の代わり(試験用)。同じ決まりで標準入出力をやり取りする。

FAKE_OFFICE = pdf | sheets | xlsx | fail | hang | shared | garbage
  Excel の pdf / sheets はシートごとの PDF(SHEET → PDFDATA → SHEETDONE / SHEETFAIL → DONE sheets)
FAKE_OFFICE_JOB = 受け取った指示を書き出す場所(試験が確かめる)
FAKE_OFFICE_PDF / FAKE_OFFICE_XLSX = 送る中身のファイル
FAKE_OFFICE_PIDFILE = hang / shared のとき「起こした Office」の代わりのプロセスの PID を書く場所
"""
import base64
import json
import os
import subprocess
import sys
import time


def emit(line):
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


def hexw(text):
    raw = text.encode("utf-16-be")
    return "".join(f"{raw[i]:02X}{raw[i + 1]:02X}" for i in range(0, len(raw), 2))


def result(key, value):
    emit(f"RESULT {key} {hexw(value)}")


def send(prefix, data):
    b64 = base64.b64encode(data).decode("ascii")
    for i in range(0, len(b64), 3000):
        emit(f"{prefix} {b64[i:i + 3000]}")


def main():
    job = {}
    for line in sys.stdin:
        line = line.strip()
        if line == "ENDJOB":
            break
        parts = line.split(" ")
        if parts[0] == "SET":
            job[parts[1]] = bytes.fromhex(parts[2]).decode("utf-16-be") if len(parts) > 2 else ""
    if os.environ.get("FAKE_OFFICE_JOB"):
        with open(os.environ["FAKE_OFFICE_JOB"], "w", encoding="utf-8") as f:
            json.dump(job, f, ensure_ascii=False)
    mode = os.environ.get("FAKE_OFFICE", "pdf")
    app = job.get("kind", "excel")
    emit("READY")
    emit(f"STAGE {app}.create")
    if mode in ("hang", "shared"):
        # 「起こした Excel・Word」の代わり(止められるかを試験が見る)
        office = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
        with open(os.environ["FAKE_OFFICE_PIDFILE"], "w") as f:
            f.write(str(office.pid))
        emit(f"WORDPID {office.pid}")
        if mode == "shared":
            emit("WORDSHARED 1")
        emit(f"STAGE {app}.open")
        time.sleep(120)                       # 隠れた確認の窓で止まった
        return 0
    emit(f"STAGE {app}.open")
    if mode == "fail":
        result("code", "OPEN_FAILED")
        result("errno", "0x800A03EC")
        result("message", "パスワードが違います")
        result("step", f"{app}.open")
        emit("FINISH 3")
        return 3
    if mode == "garbage":
        emit("PDFDATA !!!notbase64")
        emit("DONE pdf")
        emit("FINISH 0")
        return 0
    if mode == "xlsx":
        emit(f"STAGE {app}.pdf")
        result("log", "Workbook.ExportAsFixedFormat (PDF) failed: 0x800A03EC プリンターがありません")
        emit("STAGE excel.xlsx")
        with open(os.environ["FAKE_OFFICE_XLSX"], "rb") as f:
            send("XLSXDATA", f.read())
        emit("DONE xlsx")
        emit("FINISH 0")
        return 0
    with open(os.environ["FAKE_OFFICE_PDF"], "rb") as f:
        pdf = f.read()
    if app == "excel":
        emit("STAGE excel.sheets")
        plan = [("Sheet1", pdf)] if mode == "pdf" else [
            ("検査表", pdf), ("空", None), ("集計", pdf[::-1])]
        for i, (name, data) in enumerate(plan, start=1):
            emit(f"SHEET {i} {hexw(name)}")
            emit("STAGE excel.pdf")
            if data is None:
                result("log", "Sheet.ExportAsFixedFormat (PDF) failed: 0x800A03EC")
                emit(f"SHEETFAIL {hexw('0x800A03EC 印刷する物がありません')}")
                continue
            send("PDFDATA", data)
            emit("SHEETDONE 1")
        result("hidden_sheets", "1" if mode == "sheets" else "0")
        emit("DONE sheets")
        emit("FINISH 0")
        return 0
    emit(f"STAGE {app}.pdf")
    send("PDFDATA", pdf)
    emit("DONE pdf")
    emit(f"STAGE {app}.quit")
    emit("FINISH 0")
    return 0


if __name__ == "__main__":
    sys.exit(main())
