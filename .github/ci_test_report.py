"""CI(GitHub Actions)で Python の試験を流し、落ちた試験を「注釈」にして出す。

ログ本体は GitHub の外(blob)に置かれ、作業の環境からは読めないことがある。注釈は API で
読めるので、落ちた試験の名前とトレースバックの末尾をそこへ出す。結果の終了コードは試験と同じ。
"""
from __future__ import annotations

import re
import subprocess
import sys

done = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t", "."],
                      capture_output=True, text=True, encoding="utf-8", errors="replace")
out = done.stdout + done.stderr
print(out)
blocks = re.split(r"^={50,}\n", out, flags=re.M)
count = 0
for block in blocks:
    m = re.match(r"(FAIL|ERROR): (.+)", block)
    if not m or count >= 40:
        continue
    count += 1
    body = block.split("-" * 70, 1)[-1].strip().splitlines()
    tail = "\n".join(body[-18:])
    msg = tail.replace("%", "%25").replace("\r", "").replace("\n", "%0A")
    print(f"::error title={m.group(1)} {m.group(2)[:150]}::{msg[:3500]}")
summary = [line for line in out.splitlines() if line.startswith(("Ran ", "FAILED", "OK"))]
print(f"::notice title=試験の結果::{' / '.join(summary)}")
sys.exit(done.returncode)
