"""デスクトップ版の入口(`bridge.py`)── ポートを使わずに画面を返す。ブラウザ版と同時に動かさない

外枠(Rust/Tauri)は `bridge.py` を子として起動し、標準入出力で要求を渡す。
ここでは外枠の代わりをして、次を確かめる(python-web-tools の試験と同じ考え方):

- やりとりの形(見出し1行 + 本文)が往復で崩れない(日本語・バイナリ・大きい本文)
- **ソケットを1つも開かない**(開こうとしたら落ちるようにして起動する)
- 待機画面 → 準備完了 → 画面・操作が返る(ログイン・一覧・記録番号の見出しも)
- トークン無し・知らない宛先は今までどおり断る
- 「終了」で `quit` を知らせ、標準入力を閉じればプロセスが終わる
- **ブラウザ版とデスクトップ版は同時に動かない。後から開いたほうが止まる**
"""
from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import bridge  # noqa: E402

try:
    import flask  # noqa: F401
    HAS_WEB = True
except ImportError:                              # pragma: no cover
    HAS_WEB = False


class FrameTests(unittest.TestCase):
    def roundtrip(self, head: dict, body: bytes) -> tuple[dict, bytes]:
        out = io.BytesIO()
        bridge.FrameWriter(out).write(head, body)
        out.seek(0)
        return bridge.read_frame(out)

    def test_日本語とバイナリの本文が崩れない(self) -> None:
        body = "作業標準管理\n".encode("utf-8") + bytes(range(256))
        head, got = self.roundtrip({"id": 3, "status": 200, "headers": [["X", "あ"]]}, body)
        self.assertEqual(got, body)
        self.assertEqual(head["len"], len(body))
        self.assertEqual(head["headers"], [["X", "あ"]])

    def test_大きい本文(self) -> None:
        body = os.urandom(3 * 1024 * 1024)
        self.assertEqual(self.roundtrip({"id": 1}, body)[1], body)

    def test_知らせは本文なし(self) -> None:
        out = io.BytesIO()
        bridge.FrameWriter(out).event("quit")
        self.assertEqual(json.loads(out.getvalue()), {"event": "quit"})

    def test_終わりはNone_途中で切れたら例外(self) -> None:
        self.assertIsNone(bridge.read_frame(io.BytesIO(b"")))
        with self.assertRaises(EOFError):
            bridge.read_frame(io.BytesIO(b'{"id": 1, "len": 10}\nabc'))
        with self.assertRaises(ValueError):
            bridge.read_frame(io.BytesIO(b'{"id": 1, "len": -1}\n'))


@unittest.skipUnless(HAS_WEB, "flask が無い")
class CallWsgiTests(unittest.TestCase):
    def test_方法_経路_問い合わせ_本文_見出しが届き応答が返る(self) -> None:
        app = flask.Flask("試し")

        @app.post("/api/echo")
        def echo():                                      # noqa: ANN202
            return flask.jsonify({"q": flask.request.args.get("a"),
                                  "body": flask.request.get_json(),
                                  "host": flask.request.host,
                                  "token": flask.request.headers.get("X-Tool-Token")})

        status, headers, data = bridge.call_wsgi(app, {
            "method": "POST", "path": "/api/echo", "query": "a=%E3%81%82",
            "headers": [["Content-Type", "application/json"], ["X-Tool-Token", "t"],
                        ["Host", "app.localhost"]]},
            json.dumps({"Ⅱ": 2502.5}).encode("utf-8"))
        self.assertEqual(status, 200)
        self.assertIn(["Content-Type", "application/json"], headers)
        self.assertEqual(json.loads(data), {"q": "あ", "body": {"Ⅱ": 2502.5},
                                            "host": "app.localhost", "token": "t"})


def _env(root: Path, **extra) -> dict:
    """子プロセスの置き場所(本番の領域を汚さない)。"""
    from tests._fixture_db import fresh_db_path
    db = fresh_db_path(root)
    config = root / "user_config.json"
    config.write_text(json.dumps({"online": False, "local_dir": str(db.parent), "db_file": db.name,
                                  "access_log": False}), encoding="utf-8")
    env = dict(os.environ,
               DBVIEWER_CONFIG_PATH=str(config),
               DBVIEWER_LOG_DIR=str(root / "logs"),
               DBVIEWER_LOCAL_DIR=str(root / "local"),
               USERNAME="Xfa-Ngyinazawa", COMPUTERNAME="NLM-NGY-240638")
    env.update(extra)
    return env


class _BridgeProcess:
    """外枠(exe)の代わり。bridge.py を子として起動して要求を投げる。"""

    def __init__(self, case: unittest.TestCase, env: dict, token: str) -> None:
        self.case, self.token = case, token
        self.proc = subprocess.Popen(
            [sys.executable, str(ROOT / "bridge.py")], env=env, cwd=str(ROOT),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        case.addCleanup(self.kill)
        self.err: list[str] = []
        threading.Thread(target=lambda: self.err.extend(
            self.proc.stderr.read().decode("utf-8", "replace").splitlines()), daemon=True).start()
        self.events: list[dict] = []
        self.replies: dict[int, tuple[dict, bytes]] = {}
        self.lock = threading.Lock()
        self.next_id = 0
        threading.Thread(target=self._read, daemon=True).start()

    def kill(self) -> None:
        if self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait(timeout=10)
        for stream in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
            try:
                stream.close()
            except OSError:
                pass

    def _read(self) -> None:
        while True:
            try:
                frame = bridge.read_frame(self.proc.stdout)
            except (ValueError, EOFError, OSError):
                return
            if frame is None:
                return
            head, body = frame
            if "event" in head:
                self.events.append(head)
            else:
                self.replies[head["id"]] = (head, body)

    def wait_event(self, timeout: float = 60) -> dict:
        deadline = time.monotonic() + timeout
        while not self.events and time.monotonic() < deadline:
            time.sleep(0.01)
        self.case.assertTrue(self.events, f"知らせが来ない。stderr: {self.err[-15:]}")
        return self.events[0]

    def request(self, method: str, path: str, *, body: bytes = b"", headers=None, token: bool = True,
                host: str = "app.localhost", query: str = "", timeout: float = 30.0) -> tuple[dict, bytes]:
        with self.lock:
            self.next_id += 1
            i = self.next_id
            h = [["Host", host]] + ([["X-Tool-Token", self.token]] if token else []) + (headers or [])
            head = {"id": i, "method": method, "path": path, "query": query, "headers": h, "len": len(body)}
            self.proc.stdin.write(json.dumps(head).encode("utf-8") + b"\n" + body)
            self.proc.stdin.flush()
        deadline = time.monotonic() + timeout
        while i not in self.replies:
            if time.monotonic() > deadline:
                self.case.fail(f"{path} の応答が来ない。stderr: {self.err[-15:]}")
            time.sleep(0.01)
        return self.replies.pop(i)

    def json(self, path: str, data: dict) -> tuple[dict, dict]:
        head, body = self.request("POST", path, body=json.dumps(data).encode("utf-8"),
                                  headers=[["Content-Type", "application/json"]])
        return head, json.loads(body or b"{}")

    def wait_ready(self) -> None:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            head, body = self.request("GET", "/api/health")
            if head["status"] == 200 and json.loads(body).get("ready"):
                return
            time.sleep(0.1)
        self.case.fail(f"準備完了にならない。stderr: {self.err[-15:]}")


@unittest.skipUnless(HAS_WEB, "flask が無い")
class BridgeProcessTests(unittest.TestCase):
    """本物の子プロセスとして起動し、外枠の代わりに要求を投げる。"""

    TOKEN = "desk-test-token"

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        (self.root / "nosock").mkdir()
        # **ソケットを開こうとしたら落ちる**ようにして起動する
        (self.root / "nosock" / "sitecustomize.py").write_text(
            "import socket\n"
            "class _No(socket.socket):\n"
            "    def __init__(self, *a, **k):\n"
            "        raise RuntimeError('ソケットを開こうとしました')\n"
            "socket.socket = _No\n", encoding="utf-8")
        env = _env(self.root, DBVIEWER_TOKEN=self.TOKEN, PYTHONPATH=str(self.root / "nosock"))
        self.app = _BridgeProcess(self, env, self.TOKEN)

    def test_ポート無しで起動し画面を返し_終了で知らせて終わる(self) -> None:
        self.assertEqual(self.app.wait_event(), {"event": "started"}, self.app.err[-15:])
        self.app.wait_ready()

        # ログイン前の画面と、ログイン
        self.assertEqual(self.app.request("GET", "/login")[0]["status"], 200)
        head, body = self.app.json("/api/login", {"id": "Xfa-Ngyinazawa", "password": ""})
        self.assertEqual(head["status"], 200, body)
        for path in ("/list", "/master", "/browse", "/settings", "/static/js/app.js",
                     "/static/js/desktop.js", "/static/js/askbox.js"):
            with self.subTest(path=path):
                self.assertEqual(self.app.request("GET", path)[0]["status"], 200)
        head, data = self.app.json("/api/list/data", {})
        self.assertEqual(head["status"], 200)
        self.assertEqual(len(data["rows"]), 327)
        # 記録番号の見出しも外枠まで届く
        self.assertTrue(any(k.lower() == "x-request-id" for k, _ in head["headers"]))

        # 同時に来ても取り違えない(画面は見張り・心拍・操作を同時に出す)
        results: list[int] = []
        threads = [threading.Thread(target=lambda: results.append(
            self.app.request("GET", "/api/jobs")[0]["status"])) for _ in range(12)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(results, [200] * 12)

        # 守りは今までどおり
        self.assertEqual(self.app.request("POST", "/api/list/data", token=False, body=b"{}",
                                          headers=[["Content-Type", "application/json"]])[0]["status"], 401)
        self.assertEqual(self.app.request("GET", "/api/jobs", host="evil.example")[0]["status"], 400)

        head, body = self.app.json("/api/shutdown", {})
        self.assertEqual(head["status"], 200, body)
        deadline = time.monotonic() + 10
        while {"event": "quit"} not in self.app.events and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertIn({"event": "quit"}, self.app.events)
        self.app.proc.stdin.close()
        self.assertEqual(self.app.proc.wait(timeout=20), 0)
        self.assertFalse([line for line in self.app.err if "ソケットを開こうとしました" in line],
                         self.app.err[-20:])
        # デスクトップ版の印は、終わったら消える
        self.assertFalse((self.root / "local" / "runtime" / "desktop.json").exists())

    def test_外枠が先に閉じたら終わる(self) -> None:
        self.app.wait_event()
        self.app.wait_ready()
        self.app.proc.stdin.close()
        self.assertEqual(self.app.proc.wait(timeout=20), 0)


@unittest.skipUnless(HAS_WEB, "flask が無い")
class BothVersionsTests(unittest.TestCase):
    """ブラウザ版とデスクトップ版は同時に動かない。**後から開いたほうが止まる。**"""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.env = _env(self.root)

    def test_デスクトップ版が動いているとブラウザ版は止まる(self) -> None:
        desk = _BridgeProcess(self, dict(self.env, DBVIEWER_TOKEN="x"), "x")
        self.assertEqual(desk.wait_event(), {"event": "started"}, desk.err[-15:])
        desk.wait_ready()
        done = subprocess.run([sys.executable, str(ROOT / "start_app.py"), "--no-browser"],
                              env=self.env, cwd=str(ROOT), capture_output=True, timeout=60)
        err = done.stderr.decode("utf-8", "replace")
        self.assertEqual(done.returncode, 1, err[-800:])
        self.assertIn("デスクトップ版がもう動いています", err)
        self.assertIn("同時に使えない", err)
        # デスクトップ版はそのまま使える
        self.assertEqual(desk.request("GET", "/api/health")[0]["status"], 200)

    def test_ブラウザ版が動いているとデスクトップ版は止まる(self) -> None:
        browser = subprocess.Popen([sys.executable, str(ROOT / "start_app.py"), "--no-browser"],
                                   env=self.env, cwd=str(ROOT), stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT)
        self.addCleanup(lambda: (browser.poll() is None and browser.kill(), browser.wait(timeout=10)))
        owner = self.root / "local" / "runtime" / "app_owner.json"
        deadline = time.monotonic() + 60
        while not owner.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertTrue(owner.exists(), "ブラウザ版が錠を取らない")
        done = subprocess.run([sys.executable, str(ROOT / "bridge.py")], env=dict(self.env, DBVIEWER_TOKEN="x"),
                              cwd=str(ROOT), input=b"", capture_output=True, timeout=60)
        self.assertEqual(done.returncode, 1, done.stderr[-800:])
        event = json.loads(done.stdout.decode("utf-8").splitlines()[0])
        self.assertEqual((event["event"], event["code"]), ("fatal", "other_running"))
        self.assertIn("ブラウザ版がもう動いています", event["message"])
        self.assertIn("終了", event["hint"])
        self.assertIsNone(browser.poll(), "ブラウザ版は止めない")

    def test_錠はプロセスが終われば外れる(self) -> None:
        holder = subprocess.Popen(
            [sys.executable, "-c", "import sys, time; sys.path.insert(0, sys.argv[1]); import launch_guard;"
             "launch_guard.hold_app_lock('browser'); print('ok', flush=True); time.sleep(60)", str(ROOT)],
            env=self.env, stdout=subprocess.PIPE)
        self.addCleanup(lambda: (holder.poll() is None and holder.kill(), holder.wait(timeout=10)))
        self.assertEqual(holder.stdout.readline().strip(), b"ok")
        check = ("import sys; sys.path.insert(0, sys.argv[1]); import launch_guard;"
                 "print(launch_guard.hold_app_lock('desktop') is not None)")
        busy = subprocess.run([sys.executable, "-c", check, str(ROOT)], env=self.env, capture_output=True)
        self.assertEqual(busy.stdout.strip(), b"False")
        holder.kill()
        holder.wait(timeout=10)
        free = subprocess.run([sys.executable, "-c", check, str(ROOT)], env=self.env, capture_output=True)
        self.assertEqual(free.stdout.strip(), b"True")


@unittest.skipUnless(HAS_WEB, "flask が無い")
class FatalTests(unittest.TestCase):
    def test_起動できないときは理由を知らせる(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            blocker = Path(tmp) / "ファイル"
            blocker.write_text("x", encoding="utf-8")
            env = dict(os.environ, DBVIEWER_LOCAL_DIR=str(blocker),  # ローカル領域を作れない
                       DBVIEWER_CONFIG_PATH=str(Path(tmp) / "c.json"),
                       DBVIEWER_LOG_DIR=str(Path(tmp) / "logs"))
            done = subprocess.run([sys.executable, str(ROOT / "bridge.py")], env=env, cwd=str(ROOT),
                                  input=b"", capture_output=True, timeout=60)
        self.assertEqual(done.returncode, 1, done.stderr[-500:])
        event = json.loads(done.stdout.decode("utf-8").splitlines()[0])
        self.assertEqual(event["event"], "fatal")
        self.assertTrue(event["message"])


class VersionTests(unittest.TestCase):
    def test_外枠の版はアプリの版と同じ(self) -> None:
        """exe のプロパティに出る版と、画面の帯に出る版を食い違わせない。"""
        app = json.loads((ROOT / "config" / "app.json").read_text(encoding="utf-8"))["version"]
        conf = json.loads((ROOT / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8"))
        cargo = re.search(r'^version = "([^"]+)"', (ROOT / "src-tauri" / "Cargo.toml")
                          .read_text(encoding="utf-8"), re.M).group(1)
        self.assertEqual((conf["version"], cargo), (app, app))

    def test_画面の宛先の名前は外枠と同じ(self) -> None:
        main_rs = (ROOT / "src-tauri" / "src" / "main.rs").read_text(encoding="utf-8")
        self.assertIn('const SCHEME: &str = "app";', main_rs)
        init = (ROOT / "app" / "__init__.py").read_text(encoding="utf-8")
        self.assertIn('BRIDGE_HOSTS = ("app.localhost", "localhost")', init)

    def test_外枠は止まる理由の符号を見分ける(self) -> None:
        import start_app
        main_rs = (ROOT / "src-tauri" / "src" / "main.rs").read_text(encoding="utf-8")
        self.assertIn(f'"{start_app.CODE_OTHER_RUNNING}"', main_rs)


if __name__ == "__main__":                        # pragma: no cover
    unittest.main()
