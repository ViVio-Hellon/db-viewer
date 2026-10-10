"""画面の JavaScript が全部読み込める(文法の誤りで 1 つの部品が落ちると、その画面が丸ごと動かない)

node があるときだけ。ES モジュールとして `node --check` にかける。
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@unittest.skipUnless(shutil.which("node"), "node が無い")
class JsSyntaxTest(unittest.TestCase):
    def test_all_modules_parse(self):
        files = sorted((ROOT / "app" / "static" / "js").rglob("*.js"))
        self.assertGreater(len(files), 20)
        with tempfile.TemporaryDirectory() as tmp:
            for f in files:
                copy = Path(tmp) / (f.relative_to(ROOT).as_posix().replace("/", "_") + ".mjs")
                copy.write_bytes(f.read_bytes())
                done = subprocess.run(["node", "--check", str(copy)], capture_output=True, text=True, timeout=60)
                with self.subTest(file=f.name):
                    self.assertEqual(done.returncode, 0, done.stderr[-600:])


if __name__ == "__main__":
    unittest.main()
