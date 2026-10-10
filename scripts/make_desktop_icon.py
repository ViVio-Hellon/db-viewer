#!/usr/bin/env python3
"""デスクトップ版(Tauri)のアイコンを作る

窓・タスクバー・exe・(デスクトップ版).lnk に出るアイコン。画面の帯と同じ色の角丸に白の「DB」
(`dbviewer/modes.py` の色と印、`app/shell.py` の favicon と同じ姿)。
作り直すときだけ流す(作ったものはリポジトリに入れてある)。

    python scripts/make_desktop_icon.py

要るもの: Pillow(開発機だけ。配る先には要らない)と、太字のフォント(`--font`)。
"""
from __future__ import annotations

import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "src-tauri" / "icons"
TEAL = (14, 107, 132, 255)          # #0e6b84(modes.py の色)
FONTS = ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
         "/usr/share/fonts/opentype/ipafont-gothic/ipag.ttf")


def draw(size: int, font_path: str):
    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    pen = ImageDraw.Draw(image)
    pen.rounded_rectangle((0, 0, size - 1, size - 1), radius=size // 5, fill=TEAL)
    font = ImageFont.truetype(font_path, int(size * 0.46))
    pen.text((size / 2, size / 2), "DB", font=font, fill="white", anchor="mm")
    return image


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--font", default=next((f for f in FONTS if Path(f).is_file()), FONTS[0]))
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    big = draw(512, args.font)
    big.save(OUT / "icon.png")
    for size in (32, 128):
        draw(size, args.font).save(OUT / f"{size}x{size}.png")
    big.save(OUT / "icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48),
                                      (64, 64), (128, 128), (256, 256)])
    print(f"作りました: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
