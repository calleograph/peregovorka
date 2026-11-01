#!/usr/bin/env python3
"""Значок Android-приложения из одного исходника: mobile/android/branding/icon-512.png (квадрат 512×512, свой фон) → адаптивный значок для всех плотностей экрана.

Слой 108 dp: видно центральное окно 72 dp (маску — круг, квадрат со скруглением — выбирает оболочка), безопасная зона — круг 66 dp.
Исходник ставится в центр размером ART_DP: его края (на 17 dp от края слоя) остаются за видимым окном (18 dp), а сам рисунок (пузырь ~ 0,79 ширины) — в безопасной зоне.
Фон слоя — тот же рисунок, растянутый и размытый: при сдвиге слоёв (эффект глубины у оболочки) края не видны.
Монохромный слой (значки «в цвет темы», Android 13+) — светлые части рисунка (контур пузыря и камера).

Запуск после замены исходника: python3 scripts/android_icons.py   (нужен Pillow; результат — в app/src/main/res, коммитится вместе с исходником)."""
import os, sys
from PIL import Image, ImageChops, ImageDraw, ImageFilter

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "mobile", "android")
SRC = os.path.join(ROOT, "branding", "icon-512.png")
RES = os.path.join(ROOT, "app", "src", "main", "res")
DENSITIES = {"mdpi": 1.0, "hdpi": 1.5, "xhdpi": 2.0, "xxhdpi": 3.0, "xxxhdpi": 4.0}
LAYER_DP, ART_DP = 108, 74


SEEDS = ((0.49, 0.185), (0.40, 0.37))       # верх контура пузыря и тело камеры — в долях стороны исходника


def mono_mask(src: Image.Image) -> Image.Image:
    r, g, b = src.split()
    bright = Image.merge("RGB", (r, g, b)).convert("L").point(lambda v: 255 if v >= 170 else 0)
    cyan = ImageChops.multiply(g.point(lambda v: 255 if v >= 165 else 0), b.point(lambda v: 255 if v >= 220 else 0))      # бирюзовый край пузыря
    m = ImageChops.lighter(bright, cyan)
    w, h = src.size
    for fx, fy in SEEDS:
        ImageDraw.floodfill(m, (int(fx * w), int(fy * h)), 128)
    return m.point(lambda v: 255 if v == 128 else 0).filter(ImageFilter.GaussianBlur(1))


def build(src: Image.Image, scale: float):
    n, art_px = round(LAYER_DP * scale), round(ART_DP * scale)
    art = src.resize((art_px, art_px), Image.LANCZOS)
    off = (n - art_px) // 2
    bg = src.resize((n, n), Image.LANCZOS).filter(ImageFilter.GaussianBlur(radius=max(2, n // 12)))
    bg.paste(art, (off, off))
    # монохромный: светлые и бирюзовые пиксели, но только связные с контуром пузыря и с камерой (заливка от двух точек исходника) — блики волны внизу не попадают
    lum = mono_mask(src).resize((art_px, art_px), Image.LANCZOS)
    mono = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    white = Image.new("RGBA", (art_px, art_px), (255, 255, 255, 255))
    mono.paste(white, (off, off), lum)
    return bg, mono


def main():
    src = Image.open(SRC).convert("RGB")
    if src.size[0] != src.size[1] or src.size[0] < 432:
        sys.exit(f"исходник должен быть квадратом не меньше 432 px, а он {src.size}")
    for d, k in DENSITIES.items():
        out = os.path.join(RES, f"mipmap-{d}")
        os.makedirs(out, exist_ok=True)
        bg, mono = build(src, k)
        bg.save(os.path.join(out, "ic_launcher_background.png"), optimize=True)
        mono.save(os.path.join(out, "ic_launcher_monochrome.png"), optimize=True)
    print("значки обновлены:", ", ".join(DENSITIES))


if __name__ == "__main__":
    main()
