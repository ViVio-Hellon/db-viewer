"""画面が居なくなったら終わる(dbviewer/idle_exit.py)── 誤って終わらないこと

ほかのツールで、スリープから戻ったときの時計のずれ・ブラウザのタブの休止で心拍が止まり、
**使っている最中にサーバが終了した**(0.35.0 で調べて直した)。時計を差し替えて確かめる。
"""
from __future__ import annotations

import unittest
from unittest import mock

from dbviewer import idle_exit


class _Clock:
    def __init__(self) -> None:
        self.mono = 1000.0
        self.wall = 1_700_000_000.0

    def step(self, mono: float, wall: float | None = None) -> None:
        self.mono += mono
        self.wall += mono if wall is None else wall


class IdleWatchTest(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = _Clock()
        for name, fn in (("time.monotonic", lambda: self.clock.mono), ("time.time", lambda: self.clock.wall)):
            p = mock.patch(name, fn)
            p.start()
            self.addCleanup(p.stop)
        self.busy = False
        self.stopped: list[int] = []
        self.w = idle_exit.IdleWatch(lambda: self.stopped.append(1), lambda: self.busy, tick_sec=2.0)
        self.last = (self.clock.mono, self.clock.wall)

    def tick(self, mono: float = 2.0, wall: float | None = None):
        """見張りの 1 周(_loop と同じ順)。終了の判断を返す。"""
        self.clock.step(mono, wall)
        self.w.check_sleep(idle_exit.tick_gap(self.clock.mono - self.last[0], self.clock.wall - self.last[1]))
        self.last = (self.clock.mono, self.clock.wall)
        return self.w.overdue()

    def run_for(self, seconds: float):
        """見張りを回す。最初に「終わってよい」と出たらその理由(見張りはそこで止まる)。"""
        for _ in range(int(seconds / 2)):
            why = self.tick()
            if why is not None:
                return why
        return None

    # -- 基本 ---------------------------------------------------------
    def test_never_connected_never_stops(self) -> None:
        self.assertIsNone(self.run_for(600))

    def test_silence_stops_after_idle_sec(self) -> None:
        self.w.beat("tab", visible=True)
        self.assertIsNone(self.run_for(80))
        self.assertIn("心拍がありません", self.run_for(20))

    def test_heartbeats_keep_alive(self) -> None:
        self.w.beat("tab", visible=True)
        for _ in range(60):                              # 20 分、20 秒ごとに心拍
            self.assertIsNone(self.run_for(20))
            self.w.beat("tab", visible=True)

    def test_closed_stops_after_grace(self) -> None:
        self.w.beat("tab", visible=True)
        self.w.leaving("tab")
        self.assertIsNone(self.tick())
        self.assertEqual(self.run_for(10), "画面が閉じられました")

    def test_reload_cancels_close(self) -> None:
        self.w.beat("tab", visible=True)
        self.w.leaving("tab")
        self.tick()
        self.w.beat("tab", visible=True)                  # 再読込で戻ってきた
        self.assertIsNone(self.run_for(30))

    def test_hidden_tab_never_stops(self) -> None:
        """裏に回ったタブ(ブラウザが心拍を間引く・止める)は、心拍が途切れても落とさない。"""
        self.w.beat("tab", visible=True)
        self.w.hidden("tab")
        self.assertIsNone(self.run_for(3 * 3600))

    def test_hidden_then_closed_stops(self) -> None:
        self.w.beat("tab", visible=True)
        self.w.leaving("tab")
        self.w.hidden("tab")                             # 「隠れます」が後から届いても閉じたことは消えない
        self.assertEqual(self.run_for(10), "画面が閉じられました")

    def test_busy_never_stops(self) -> None:
        self.w.beat("tab", visible=True)
        self.busy = True
        self.assertIsNotNone(self.run_for(120))          # 終わってよい状態でも
        # _loop は busy なら止めない(ここでは判断だけ見る)

    # -- スリープ・時計のずれ(0.35.0) ---------------------------------
    def _sleep(self, seconds: float, wall: float):
        """表のタブのまま PC が寝た。monotonic は寝ていたぶん進む(Windows)。"""
        self.w.beat("tab", visible=True)
        self.tick()
        return self.tick(mono=seconds, wall=wall)

    def test_sleep_with_correct_clock_waits(self) -> None:
        self.assertIsNone(self._sleep(3600, 3600))
        self.assertIsNone(self.run_for(80))              # 画面が戻るのを待つ

    def test_sleep_with_late_clock_fix_waits(self) -> None:
        """戻った直後に PC の時刻の直しが遅れても(壁時計は 2 秒しか進んでいない)待つ。以前はここで終了した。"""
        self.assertIsNone(self._sleep(3600, 2))
        self.assertIsNone(self.run_for(80))

    def test_sleep_with_clock_going_back_waits(self) -> None:
        """戻った直後に時刻が巻き戻っても待つ。"""
        self.assertIsNone(self._sleep(3600, -30))
        self.assertIsNone(self.run_for(80))

    def test_clock_jump_without_sleep_only_waits(self) -> None:
        """時刻だけが大きく動いた(時刻合わせ)ときも、待ち直すだけ(終わらない)。"""
        self.w.beat("tab", visible=True)
        self.assertIsNone(self.tick(mono=2, wall=-3600))
        self.assertIsNone(self.run_for(80))

    def test_screen_back_after_sleep_keeps_running(self) -> None:
        self._sleep(3600, 2)
        self.w.resumed("tab", gap_sec=3600)
        self.assertIsNone(self.run_for(80))
        self.w.beat("tab", visible=True)
        self.assertIsNone(self.run_for(80))

    def test_screen_gone_after_sleep_stops_later(self) -> None:
        """戻っても画面が来なければ(閉じたまま寝た など)、待ち直したあとで終わる。"""
        self._sleep(3600, 3600)
        self.assertIsNone(self.run_for(80))
        self.assertIn("心拍がありません", self.run_for(20))

    def test_regular_ticks_are_not_sleep(self) -> None:
        for _ in range(100):
            self.clock.step(2.0)
            self.assertFalse(self.w.check_sleep(idle_exit.tick_gap(2.0, 2.0)))

    def test_tick_gap(self) -> None:
        self.assertEqual(idle_exit.tick_gap(2.0, 2.0), 2.0)
        self.assertEqual(idle_exit.tick_gap(3600.0, 2.0), 3600.0)     # 時刻の直しが遅れた
        self.assertEqual(idle_exit.tick_gap(2.0, -30.0), 30.0)        # 時刻が戻った
        self.assertEqual(idle_exit.tick_gap(2.0, 3600.0), 3600.0)     # Linux(monotonic は寝ているあいだ止まる)


if __name__ == "__main__":
    unittest.main()
