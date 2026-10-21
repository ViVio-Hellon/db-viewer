"""配布設定(0.30.0。python-web-tools の distribution と同じ作り)

1 台で書き出し、配った先は起動時に読む。その PC で変えた値は上書きしない。
設定ファイルの中身(接続先・かんたん設定・アクセス権限)も配れて、配った先に無ければそれで作る。
"""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

from dbviewer import admin_password, config, distribution, settings_file, user_settings
from tests._web import auth, make_client

_ROOT = Path(__file__).resolve().parent.parent


class _Case(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.dist = self.tmp / "ツール" / "配布設定"
        self.patch(distribution, "DIR", self.dist)
        self.pc("配る PC")
        admin_password.session.lock()
        self.addCleanup(admin_password.session.lock)

    def patch(self, obj, name, value) -> None:
        p = mock.patch.object(obj, name, value)
        p.start()
        self.addCleanup(p.stop)

    def pc(self, name: str) -> Path:
        """PC を替える(その PC の user_config.json とローカル領域)。"""
        home = self.tmp / name
        (home / "data").mkdir(parents=True, exist_ok=True)
        p = mock.patch.object(config, "USER_CONFIG_PATH", home / "data" / "user_config.json")
        p.start()
        self.addCleanup(p.stop)
        q = mock.patch("dbviewer.app_config.local_root", return_value=home)
        q.start()
        self.addCleanup(q.stop)
        return home


class ExportTest(_Case):
    def test_password_needed_and_only_set_items(self) -> None:
        user_settings.save(config.KEY_DB_PATH, r"\\NLM-NGY-232010\共有\作業標準.sqlite3")
        user_settings.save(config.KEY_EXPORT_FORMAT, "xlsx")
        keys = [config.KEY_DB_PATH, config.KEY_EXPORT_FORMAT, config.KEY_LOG_DIR]
        r = distribution.export("ちがう", keys, False)
        self.assertEqual((r.ok, r.reason), (False, distribution.REFUSE_NEED_PASSWORD))
        self.assertFalse(self.dist.exists())
        r = distribution.export("nisk", keys, False)
        self.assertTrue(r.ok, r.message)
        self.assertIn("記録(ログ)の置き場所", r.message)                       # 既定のままなので入れない
        data = json.loads((self.dist / "設定.json").read_text(encoding="utf-8"))
        self.assertEqual(data["settings"], {config.KEY_DB_PATH: r"\\NLM-NGY-232010\共有\作業標準.sqlite3",
                                            config.KEY_EXPORT_FORMAT: "xlsx"})
        self.assertTrue((self.dist / "はじめに読む.txt").exists())
        self.assertFalse((self.dist / "設定ファイル.json").exists())
        s = distribution.summary()
        self.assertTrue(s["exists"])
        self.assertEqual([b["label"] for b in s["bundle"]], ["開く DB", "出力の形式"])

    def test_password_value_never_shown(self) -> None:
        self.assertTrue(admin_password.change("nisk", "新しい鍵", "新しい鍵").ok)
        stored = user_settings.get(config.KEY_ADMIN_PASSWORD)
        r = distribution.export("新しい鍵", [config.KEY_ADMIN_PASSWORD], False)
        self.assertTrue(r.ok, r.message)
        readme = (self.dist / "はじめに読む.txt").read_text(encoding="utf-8-sig")
        self.assertIn("管理者パスワード: (設定済み)", readme)
        self.assertNotIn(stored, readme)
        self.assertNotIn("新しい鍵", readme)
        s = json.dumps(distribution.summary(), ensure_ascii=False)
        self.assertNotIn(stored, s)
        # 配った先は撹拌した値をそのまま持つので、同じパスワードで開ける
        self.pc("配られた PC")
        self.assertFalse(admin_password.verify("新しい鍵"))
        distribution.apply_on_start()
        self.assertTrue(admin_password.verify("新しい鍵"))

    def test_nothing_to_export_refused(self) -> None:
        r = distribution.export("nisk", [config.KEY_REF_DIR], False)
        self.assertEqual((r.ok, r.reason), (False, distribution.REFUSE_BAD_INPUT))
        self.assertIn("既定のまま", r.message)
        self.assertFalse(self.dist.exists())
        self.assertEqual(distribution.export("nisk", ["知らない鍵"], False).reason, distribution.REFUSE_BAD_INPUT)

    def test_local_place_warned(self) -> None:
        here = self.tmp / "配る PC" / "手元" / "x.sqlite3"
        user_settings.save(config.KEY_DB_PATH, str(here))
        r = distribution.export("nisk", [config.KEY_DB_PATH], False)
        self.assertTrue(r.ok)
        self.assertIn("この PC の中を指している場所", r.message)
        item = next(i for i in distribution.summary()["items"] if i["key"] == config.KEY_DB_PATH)
        self.assertTrue(item["here"])


class ApplyTest(_Case):
    def export(self, **values) -> None:
        for k, v in values.items():
            user_settings.save(k, v)
        r = distribution.export("nisk", list(values), False)
        self.assertTrue(r.ok, r.message)

    def test_fills_missing_keeps_own(self) -> None:
        self.export(**{config.KEY_DB_PATH: r"\\srv\共有\A.sqlite3", config.KEY_EXPORT_FORMAT: "xlsx",
                       config.KEY_RELOGIN_MIN: 10})
        self.pc("配られた PC")
        user_settings.save(config.KEY_EXPORT_FORMAT, "csv")                       # この PC で入れた値
        user_settings.save(config.KEY_DB_PATH, "")                                # 空は無いのと同じ
        r = distribution.apply_on_start()
        self.assertEqual(sorted(r.applied), sorted(["開く DB", "無操作で再ログイン(分)"]))
        self.assertEqual(r.kept, ["出力の形式"])
        self.assertEqual(user_settings.get(config.KEY_DB_PATH), r"\\srv\共有\A.sqlite3")
        self.assertEqual(user_settings.get(config.KEY_EXPORT_FORMAT), "csv")
        self.assertEqual(distribution.apply_on_start().applied, [])               # 同じものは 2 度は読まない

    def test_renewed_bundle_replaces_unchanged_only(self) -> None:
        self.export(**{config.KEY_DB_PATH: r"\\srv\共有\A.sqlite3", config.KEY_REF_DIR: r"\\srv\参照"})
        self.pc("配られた PC")
        distribution.apply_on_start()
        user_settings.save(config.KEY_REF_DIR, r"D:\自分の参照")                     # この PC で変えた
        self.pc("配る PC")
        self.export(**{config.KEY_DB_PATH: r"\\srv\共有\B.sqlite3", config.KEY_REF_DIR: r"\\srv\参照2"})
        self.pc("配られた PC")
        r = distribution.apply_on_start()
        self.assertEqual(r.applied, ["開く DB"])
        self.assertEqual(user_settings.get(config.KEY_DB_PATH), r"\\srv\共有\B.sqlite3")
        self.assertEqual(user_settings.get(config.KEY_REF_DIR), r"D:\自分の参照")

    def test_reapply_overwrites_with_password(self) -> None:
        self.export(**{config.KEY_EXPORT_FORMAT: "xlsx"})
        self.pc("配られた PC")
        user_settings.save(config.KEY_EXPORT_FORMAT, "csv")
        distribution.apply_on_start()
        admin_password.session.lock()                         # 書き出しで開けた鍵(アクセス権限と同じ 30 分)を掛ける
        self.assertEqual(distribution.reapply("ちがう").reason, distribution.REFUSE_NEED_PASSWORD)
        r = distribution.reapply("nisk")
        self.assertTrue(r.ok)
        self.assertEqual(user_settings.get(config.KEY_EXPORT_FORMAT), "xlsx")

    def test_remove(self) -> None:
        self.export(**{config.KEY_EXPORT_FORMAT: "xlsx"})
        admin_password.session.lock()
        self.assertEqual(distribution.remove("").reason, distribution.REFUSE_NEED_PASSWORD)
        self.assertTrue(distribution.remove("nisk").ok)
        self.assertFalse(self.dist.exists())
        self.assertEqual(user_settings.get(config.KEY_EXPORT_FORMAT), "xlsx")      # この PC の設定はそのまま
        self.assertTrue(distribution.apply_on_start().ok)                         # 無くても起動は止めない


class ContentsTest(_Case):
    """設定ファイルの中身(接続先・かんたん設定・アクセス権限)を配る。"""

    def fill(self) -> Path:
        p = settings_file.ensure()
        with closing(sqlite3.connect(str(p))) as c:
            c.execute(f'DELETE FROM "{settings_file.T_DBS}"')
            c.execute(f'INSERT INTO "{settings_file.T_DBS}" ("名前", "パス") VALUES (?, ?)',
                      ("作業標準", r"\\NLM-NGY-232010\共有\作業標準.sqlite3"))
            c.execute(f'INSERT INTO "{settings_file.T_RIGHTS}" ("ログインID", "PC名", "権限", "有効") VALUES (?, ?, ?, 1)',
                      ("xfa-ngykonpo", "", "編集可"))
            c.execute(f'INSERT INTO "{settings_file.T_EASY}" VALUES (?, ?, ?, ?, ?)',
                      ("作業標準.sqlite3", "作業", '{"columns": {}}', "xfa", "2026/10/21"))
            c.commit()
        return p

    def rows(self, p: Path, table: str) -> list[tuple]:
        with closing(sqlite3.connect(str(p))) as c:
            return c.execute(f'SELECT * FROM "{table}" ORDER BY rowid').fetchall()

    def test_local_settings_file_is_sent_and_made_there(self) -> None:
        src = self.fill()
        s = distribution.summary()
        self.assertTrue(s["contents"]["here"] and s["contents"]["default"])         # この PC の中 → 入れるのが既定
        r = distribution.export("nisk", [], True)
        self.assertTrue(r.ok, r.message)
        self.assertNotIn(".sqlite3", [p.suffix for p in self.dist.iterdir()])       # 配るのは JSON
        self.pc("配られた PC")
        self.assertFalse(settings_file.path().exists())
        r = distribution.apply_on_start()
        self.assertIn(distribution.CONTENTS_LABEL, r.applied)
        made = settings_file.path()
        for table in distribution.TABLES:
            self.assertEqual(self.rows(made, table), self.rows(src, table), table)
        self.assertEqual(distribution.apply_on_start().applied, [])

    def test_existing_settings_file_kept_until_reapply(self) -> None:
        self.fill()
        self.assertTrue(distribution.export("nisk", [], True).ok)
        self.pc("配られた PC")
        mine = settings_file.ensure()                                              # この PC の設定ファイル
        with closing(sqlite3.connect(str(mine))) as c:
            c.execute(f'INSERT INTO "{settings_file.T_DBS}" ("名前", "パス") VALUES ("自分", "D:\\x.sqlite3")')
            c.commit()
        r = distribution.apply_on_start()
        self.assertIn(distribution.CONTENTS_LABEL, r.kept)
        self.assertIn("自分", [row[1] for row in self.rows(mine, settings_file.T_DBS)])
        self.assertTrue(distribution.reapply("nisk").ok)                          # 揃える
        self.assertEqual([row[1] for row in self.rows(mine, settings_file.T_DBS)], ["作業標準"])

    def test_shared_settings_file_never_overwritten(self) -> None:
        self.fill()
        self.assertTrue(distribution.export("nisk", [], True).ok)
        self.pc("配られた PC")
        shared = self.tmp / "共有" / "DBビューア設定.sqlite3"
        shared.parent.mkdir()
        # 試験の一時フォルダは Windows では利用者のフォルダの下(= この PC の中)なので、共有と見立てる
        here = distribution.is_here
        self.patch(distribution, "is_here", lambda p: "共有" not in str(p) and here(p))
        user_settings.save(config.KEY_SETTINGS_FILE, str(shared))
        settings_file.ensure(shared)
        r = distribution.reapply("nisk")                                           # 読み込み直しでも
        self.assertTrue(any("共有フォルダ" in k for k in r.kept), r.kept)
        self.assertNotIn("作業標準", [row[1] for row in self.rows(shared, settings_file.T_DBS)])


class WebAndDistTest(_Case):
    def test_api_and_settings_tab(self) -> None:
        user_settings.save(config.KEY_EXPORT_FORMAT, "xlsx")
        client = make_client()
        page = client.get("/settings", headers=auth())
        self.assertIn("配布設定", page.get_data(as_text=True))
        s = client.get("/api/dist/state", headers=auth()).get_json()["dist"]
        self.assertFalse(s["exists"])
        r = client.post("/api/dist/export", json={"items": [config.KEY_EXPORT_FORMAT], "password": "x"}, headers=auth())
        self.assertEqual(r.status_code, 403)
        r = client.post("/api/dist/export", json={"items": "x", "password": "nisk"}, headers=auth())
        self.assertEqual(r.status_code, 400)
        r = client.post("/api/dist/export", json={"items": [config.KEY_EXPORT_FORMAT], "password": "nisk"},
                        headers=auth())
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertTrue(r.get_json()["dist"]["exists"])

    def test_make_dist_carries_it(self) -> None:
        sys.path.insert(0, str(_ROOT / "scripts"))
        import make_dist
        user_settings.save(config.KEY_DB_PATH, r"\\srv\共有\A.sqlite3")
        self.assertTrue(distribution.export("nisk", [config.KEY_DB_PATH], False).ok)
        out, lines = make_dist.build(self.tmp / "配る物", exe=None)
        self.assertTrue((out / "配布設定" / "設定.json").exists())
        self.assertTrue(any("開く DB: \\\\srv\\共有\\A.sqlite3" in line for line in lines), lines)
        memo = (out / "配布メモ.txt").read_text(encoding="utf-8-sig")
        self.assertIn("配布設定", memo)
        out2, lines2 = make_dist.build(self.tmp / "配る物2", exe=None, with_settings=False)
        self.assertFalse((out2 / "配布設定").exists())
        self.assertIn("配布設定は入れていません(--no-settings)。", lines2)


if __name__ == "__main__":
    unittest.main()
