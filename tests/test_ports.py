"""ポートがほかのツールと重ならないこと(0.37.0)

0.36.0 までは 8743〜8746 で、RNEConverter(8741〜8745 を使い、起動画面が 8741〜8750 の /boot.js を
1 秒ごとに探す)と看板(8741〜8744)に重なっていた。RNEConverter の問い合わせが DBビューアに届き、
「ほかのページからの読み込みを断りました」がログに出続けていた。

ほかのツールのポートを変えたら、ここも直す(同じ PC で動くもの)。
"""
from __future__ import annotations

import unittest

from dbviewer import app_config

# (ツール, 使う・探しに来るポート)。port_retry を含めた範囲
OTHER_TOOLS: dict[str, range] = {
    "入口(all-tools)": range(8700, 8710),
    "梱包資材総合ツール(python-web-tools) 現場": range(8713, 8717),
    "梱包資材総合ツール(python-web-tools) 資材": range(8723, 8727),
    "カレンダー": range(8730, 8734),
    "日報": range(8733, 8737),
    "看板 1": range(8741, 8745),
    "看板 2": range(8751, 8755),
    "看板 3": range(8761, 8765),
    "RNEConverter(起動画面が探しに来る範囲まで)": range(8741, 8751),
    "点検表": range(8781, 8785),
}


class PortTest(unittest.TestCase):
    def test_no_overlap_with_other_tools(self) -> None:
        ours = set(app_config.port_candidates())
        self.assertEqual(sorted(ours), [8790, 8791, 8792, 8793])
        for name, used in OTHER_TOOLS.items():
            with self.subTest(tool=name):
                self.assertFalse(ours & set(used), f"{name} とポートが重なっています: {sorted(ours & set(used))}")

    def test_no_overlap_between_own_modes(self) -> None:
        self.assertEqual(app_config.port_range_conflicts(), [])


if __name__ == "__main__":
    unittest.main()
