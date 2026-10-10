"""Оформление установки: безопасная обработка загруженных изображений (логотип, компактный логотип, favicon) и их хранение в постоянном каталоге данных.

Принимаются только растровые PNG, WebP, JPEG и (для значка вкладки) ICO — по сигнатуре файла, а не по расширению. Любое изображение перекодируется в PNG:
метаданные и возможное активное содержимое отбрасываются, пропорции сохраняются, размер ограничен. SVG и анимированные форматы не принимаются.
Файлы лежат в `<DATA_DIR>/branding/` — вне исходного кода и вне контейнера, поэтому переживают обновления и пересоздание контейнеров.
"""
from __future__ import annotations

import hashlib
import io
import os
from pathlib import Path

MAX_UPLOAD_BYTES = 2 * 1024 * 1024
MAX_SIDE = 4096
KINDS = {
    # вид: (вписать в рамку (ширина, высота), допустимые форматы, минимальная сторона)
    "logo": ((480, 160), {"png", "webp", "jpeg"}, 16),
    "logo_compact": ((128, 128), {"png", "webp", "jpeg"}, 16),
    "favicon": ((64, 64), {"png", "webp", "ico"}, 16),
}
KIND_LABEL = {"logo": "Логотип", "logo_compact": "Компактный логотип", "favicon": "Значок вкладки (favicon)"}


class BrandingError(Exception):
    pass


def sniff(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data[:4] == b"\x00\x00\x01\x00":
        return "ico"
    return None


def process(kind: str, data: bytes) -> bytes:
    """Загруженный файл → PNG, вписанный в рамку вида без искажения пропорций. BrandingError с понятным текстом при отказе."""
    if kind not in KINDS:
        raise BrandingError("Неизвестный вид изображения.")
    box, allowed, min_side = KINDS[kind]
    if not data:
        raise BrandingError("Файл пустой.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise BrandingError(f"Файл больше {MAX_UPLOAD_BYTES // (1024 * 1024)} МБ: уменьшите изображение.")
    fmt = sniff(data)
    if fmt is None or fmt not in allowed:
        names = {"png": "PNG", "webp": "WebP", "jpeg": "JPEG", "ico": "ICO"}
        raise BrandingError("Допустимые форматы: " + ", ".join(names[f] for f in ("png", "webp", "jpeg", "ico") if f in allowed) + ". SVG и анимация не поддерживаются.")
    try:
        from PIL import Image, ImageOps  # noqa: PLC0415
    except ImportError:  # pragma: no cover
        raise BrandingError("Обработка изображений на сервере недоступна.") from None
    Image.MAX_IMAGE_PIXELS = MAX_SIDE * MAX_SIDE
    try:
        with Image.open(io.BytesIO(data)) as im:
            if getattr(im, "is_animated", False) and getattr(im, "n_frames", 1) > 1:
                raise BrandingError("Анимированные изображения не поддерживаются.")
            im.load()
            if im.width > MAX_SIDE or im.height > MAX_SIDE:
                raise BrandingError(f"Изображение больше {MAX_SIDE}×{MAX_SIDE} точек.")
            if min(im.width, im.height) < min_side:
                raise BrandingError(f"Изображение слишком маленькое: сторона не меньше {min_side} точек.")
            im = ImageOps.exif_transpose(im).convert("RGBA")
            im.thumbnail(box, Image.LANCZOS)                 # вписывается в рамку, пропорции сохраняются; меньшее не растягивается
            if kind == "favicon":                            # значок — квадрат с прозрачными полями
                canvas = Image.new("RGBA", box, (0, 0, 0, 0))
                canvas.paste(im, ((box[0] - im.width) // 2, (box[1] - im.height) // 2))
                im = canvas
            out = io.BytesIO()
            im.save(out, "PNG", optimize=True)
            return out.getvalue()
    except BrandingError:
        raise
    except Exception:  # noqa: BLE001 — повреждённый файл, «бомба» декодирования, неподдерживаемая вариация формата
        raise BrandingError("Не удалось прочитать изображение: файл повреждён или имеет неподдерживаемый вид.") from None


def version_of(png: bytes) -> str:
    return hashlib.sha256(png).hexdigest()[:12]


class BrandingStore:
    def __init__(self, data_dir: str):
        self._dir = Path(data_dir) / "branding"

    def path(self, kind: str) -> Path:
        if kind not in KINDS:
            raise BrandingError("Неизвестный вид изображения.")
        return self._dir / f"{kind}.png"

    def save(self, kind: str, png: bytes) -> str:
        p = self.path(kind)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_bytes(png)
        os.replace(tmp, p)
        return version_of(png)

    def read(self, kind: str) -> bytes | None:
        try:
            return self.path(kind).read_bytes()
        except FileNotFoundError:
            return None

    def delete(self, kind: str) -> None:
        try:
            self.path(kind).unlink()
        except FileNotFoundError:
            pass
