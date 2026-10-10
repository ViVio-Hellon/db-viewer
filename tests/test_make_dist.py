"""配布: scripts\\make_dist.py(配布フォルダ)と scripts\\make_shortcuts.vbs(配った先でショートカット)"""
from __future__ import annotations

import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "scripts"))

import make_dist  # noqa: E402


class MakeDistTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.out = self.tmp / "DBビューア_VER試験"

    def test_配るものだけが入り_exeは名前を変えて直下に入る(self) -> None:
        exe = self.tmp / "DBViewer.exe"
        exe.write_bytes(b"MZ fake exe")
        out, lines = make_dist.build(self.out, exe=exe)
        for name in ("Start.vbs", "start.bat", "stop.bat", "bridge.py", "start_app.py",
                     "dbviewer", "app", "config", "vendor", "requirements.txt",
                     "scripts/make_shortcuts.vbs", "scripts/make_dist.bat"):
            with self.subTest(name=name):
                self.assertTrue((out / name).exists())
        self.assertEqual((out / make_dist.EXE_NAME).read_bytes(), b"MZ fake exe")
        self.assertEqual(make_dist.EXE_NAME, "DBビューア.exe")
        for name in ("tests", "src-tauri", ".git", "data", "logs", "FOUNDATION_ORIGIN.md"):
            with self.subTest(name=name):
                self.assertFalse((out / name).exists())
        self.assertFalse([p for p in out.rglob("*") if p.name == "__pycache__" or p.suffix in (".pyc", ".sqlite3", ".accdb")])
        # 配った先で押すものは、手を入れずにそのまま入る
        self.assertEqual((out / "scripts" / "make_shortcuts.vbs").read_bytes(),
                         (_ROOT / "scripts" / "make_shortcuts.vbs").read_bytes())
        memo = (out / "配布メモ.txt").read_text(encoding="utf-8-sig")
        self.assertIn("scripts\\make_shortcuts.vbs", memo)
        self.assertIn("DBビューア(デスクトップ版).lnk", memo)
        self.assertIn("同時に動きません", memo)
        self.assertTrue(any("デスクトップ版を入れました" in line for line in lines))

    def test_exeが無くても作れて_そう言う(self) -> None:
        from unittest import mock
        with mock.patch.object(make_dist, "find_exe", return_value=None):
            out, lines = make_dist.build(self.out)
        self.assertFalse((out / make_dist.EXE_NAME).exists())
        self.assertTrue(any("デスクトップ版(exe)は入っていません" in line and "DBViewer.exe" in line
                            for line in lines))

    def test_ツールのフォルダの中には作らない_中身があれば断る(self) -> None:
        with self.assertRaises(SystemExit):
            make_dist.build(_ROOT / "dist_試験")
        self.out.mkdir()
        (self.out / "x.txt").write_text("x", encoding="utf-8")
        with self.assertRaises(SystemExit):
            make_dist.build(self.out)
        out, _ = make_dist.build(self.out, force=True)
        self.assertFalse((out / "x.txt").exists())

    def test_既定の置き場所はツールの隣(self) -> None:
        self.assertEqual(make_dist.default_out().parent, _ROOT.parent)
        self.assertTrue(make_dist.default_out().name.startswith("DBビューア_VER"))

    def test_batはASCIIとCRLF(self) -> None:
        data = (_ROOT / "scripts" / "make_dist.bat").read_bytes()
        self.assertTrue(data.isascii())
        self.assertNotIn(b"\n", data.replace(b"\r\n", b""))
        self.assertIn(b"python scripts\\make_dist.py", data)


class ShortcutScriptTests(unittest.TestCase):
    """配った先で押す `scripts\\make_shortcuts.vbs`。"""

    PATH = _ROOT / "scripts" / "make_shortcuts.vbs"

    def text(self) -> str:
        return self.PATH.read_bytes().decode("cp932")

    def test_CP932_CRLF_BOMなし(self) -> None:
        data = self.PATH.read_bytes()
        self.assertNotEqual(data[:3], b"\xef\xbb\xbf")
        self.assertNotIn(b"\n", data.replace(b"\r\n", b""))       # LF だけの行が無い
        self.assertIn("ショートカットを作りました", self.text())    # CP932 として正しい語になる

    def names(self) -> dict[str, str]:
        """`NAME = ["DB" &] U("30D3 …") [& ".exe"]` を文字にもどす(.vbs の中では文字の番号で持つ)。"""
        out = {}
        for name, head, codes, tail in re.findall(
                r'^(\w+) = (?:"([^"]*)" & )?U\("([0-9A-F ]+)"\)(?: & "([^"]*)")?', self.text(), re.M):
            out[name] = head + "".join(chr(int(c, 16)) for c in codes.split()) + tail
        return out

    def test_日本語の名前は文字の番号で持つ(self) -> None:
        """英語の Windows でも名前が化けないように(python-web-tools の CI で化けた)。"""
        self.assertEqual(self.names(), {
            "APP_NAME": make_dist.DIST_FOLDER_NAME, "EXE_NAME": make_dist.EXE_NAME,
            "BROWSER": "(ブラウザ版)", "DESKTOP": "(デスクトップ版)"})
        for line in self.text().splitlines():
            code = line.split("'", 1)[0]
            if '"' in code:
                quoted = code.split('"')[1::2]
                self.assertFalse(any(".lnk" in q and not q.isascii() for q in quoted), line)

    def test_作るもの_指す先_exeが無いときの知らせ(self) -> None:
        text = self.text()
        self.assertIn('MakeLink APP_NAME & BROWSER & ".lnk", "Start.vbs", False', text)
        self.assertIn('MakeLink APP_NAME & DESKTOP & ".lnk", EXE_NAME, True', text)
        # ツールのフォルダ = scripts の1つ上(押したときの場所)
        self.assertIn("GetParentFolderName(fso.GetParentFolderName(WScript.ScriptFullName))", text)
        self.assertIn('lnk.IconLocation = target & ",0"', text)          # アイコンは exe のもの
        self.assertIn("デスクトップ版のショートカットは作っていません", text)
        self.assertIn("If Not fso.FileExists(target) Then", text)
        # Windows の機能(WSH)だけで動く。Python は呼ばない(注釈は除いて見る)
        code = "\n".join(line.split("'", 1)[0] for line in text.splitlines())
        self.assertNotIn("python", code.lower())

    @unittest.skipUnless(sys.platform == "win32", "Windows の cscript で本当に作る")
    def test_Windowsでは本当にショートカットができる(self) -> None:   # pragma: no cover
        import shutil
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "scripts").mkdir()
            shutil.copy2(self.PATH, root / "scripts" / "make_shortcuts.vbs")
            (root / "Start.vbs").write_text("' x", encoding="ascii")
            done = subprocess.run(["cscript", "//nologo", str(root / "scripts" / "make_shortcuts.vbs")],
                                  capture_output=True, timeout=60)
            self.assertEqual(done.returncode, 0, done.stderr)
            # exe が無い → デスクトップ版は作らない
            self.assertFalse(list(root.glob("*デスクトップ版*.lnk")))
            (root / make_dist.EXE_NAME).write_bytes(b"MZ")
            done = subprocess.run(["cscript", "//nologo", str(root / "scripts" / "make_shortcuts.vbs")],
                                  capture_output=True, timeout=60)
            self.assertEqual(done.returncode, 0, done.stderr)


if __name__ == "__main__":
    unittest.main()
