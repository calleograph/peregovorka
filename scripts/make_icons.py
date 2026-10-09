#!/usr/bin/env python3
"""Генератор значков сайта (favicon и т. п.) из одного описания рисунка. Нужен только разработчику: готовые файлы лежат в frontend/public и в git.

    pip install pillow
    python3 scripts/make_icons.py

Рисунок — реплика-«облачко» с «звуковой дорожкой» на синем скруглённом квадрате. SVG (favicon.svg, favicon-live.svg) написаны вручную в тех же координатах;
растровые файлы рисуются здесь с четырёхкратной передискретизацией. Для мелких размеров (≤32 px) дорожка упрощена до трёх толстых полос.
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "frontend" / "public"
C1, C2 = (59, 109, 240), (29, 70, 184)       # градиент фона (светлый → тёмный)
WHITE = (255, 255, 255)
LIVE = (229, 57, 53)
SS = 4                                       # передискретизация


def gradient(n: int) -> Image.Image:
    base = Image.linear_gradient("L").resize((n, n), Image.BILINEAR)         # сверху вниз
    side = base.transpose(Image.ROTATE_90)                                    # слева направо
    mask = Image.blend(base, side, 0.5)                                       # по диагонали
    return Image.composite(Image.new("RGB", (n, n), C2), Image.new("RGB", (n, n), C1), mask)


def glyph(d: ImageDraw.ImageDraw, k: float, ox: float, oy: float, small: bool, live: bool) -> None:
    """Облачко и полосы в координатах 64×64; k — масштаб, (ox, oy) — смещение (для maskable)."""
    def p(v: float, o: float) -> float:
        return o + v * k
    d.rounded_rectangle((p(12, ox), p(14, oy), p(52, ox), p(43, oy)), radius=10 * k, fill=WHITE)
    d.polygon([(p(19, ox), p(40, oy)), (p(19, ox), p(53, oy)), (p(32, ox), p(41, oy))], fill=WHITE)
    bars = [(24, 10), (32, 20), (40, 14)] if small else [(22, 9), (29, 17), (36, 23), (43, 13)]
    w = 5.2 if small else 3.8
    for cx, h in bars:
        d.rounded_rectangle((p(cx - w / 2, ox), p(28.5 - h / 2, oy), p(cx + w / 2, ox), p(28.5 + h / 2, oy)), radius=w / 2 * k, fill=C2)
    if live:
        r = 9.5
        d.ellipse((p(46 - r - 2, ox), p(18 - r - 2, oy), p(46 + r + 2, ox), p(18 + r + 2, oy)), fill=C2)        # «кольцо» цвета фона
        d.ellipse((p(46 - r, ox), p(18 - r, oy), p(46 + r, ox), p(18 + r, oy)), fill=LIVE)


def render(n: int, *, rounded: bool = True, safe: float = 1.0, live: bool = False) -> Image.Image:
    big = n * SS
    img = gradient(big).convert("RGBA")
    d = ImageDraw.Draw(img)
    k = big / 64 * safe
    off = big * (1 - safe) / 2
    glyph(d, k, off, off, n <= 32, live)
    if rounded:
        m = Image.new("L", (big, big), 0)
        ImageDraw.Draw(m).rounded_rectangle((0, 0, big - 1, big - 1), radius=big * 14 / 64, fill=255)
        img.putalpha(ImageChops.multiply(img.getchannel("A"), m))
    return img.resize((n, n), Image.LANCZOS)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    for n in (16, 32, 48):
        render(n).save(OUT / f"favicon-{n}.png")
    ico = [render(n) for n in (48, 32, 16)]
    ico[0].save(OUT / "favicon.ico", format="ICO", sizes=[(48, 48), (32, 32), (16, 16)], append_images=ico[1:])
    render(180, rounded=False).convert("RGB").save(OUT / "apple-touch-icon.png")          # iOS скругляет сама
    render(192).save(OUT / "icon-192.png")
    render(512).save(OUT / "icon-512.png")
    render(512, rounded=False, safe=0.66).save(OUT / "icon-maskable-512.png")             # безопасная зона маски Android
    render(32, live=True).save(OUT / "favicon-live-32.png")
    print("готово:", ", ".join(sorted(p.name for p in OUT.glob("*.png")) + ["favicon.ico"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
