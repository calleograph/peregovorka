"""Аватарки пользователей: проверка загруженной картинки, приведение к квадрату 256×256 и хранение на диске сервера.

Картинку обрезает до квадрата и уменьшает браузер, но серверу верить нельзя: файл открывается библиотекой изображений, квадрат и размер
выставляются заново, метаданные (EXIF, GPS) не сохраняются, формат на выходе один — WebP. Из каталога фото не берётся и при показе не запрашивается.
"""
from __future__ import annotations

import io
import os
import uuid
from pathlib import Path

MAX_UPLOAD_BYTES = 600 * 1024          # загрузка; браузер присылает обычно 10–30 КБ
MAX_SIDE = 4096                        # исходная картинка больше — отказ (защита от «бомб» декодирования)
SIZE = 256
MIME = "image/webp"
_MAGIC = ((b"\xff\xd8\xff", "jpeg"), (b"\x89PNG\r\n\x1a\n", "png"))


class AvatarError(Exception):
    pass


def sniff(data: bytes) -> str | None:
    for sig, kind in _MAGIC:
        if data.startswith(sig):
            return kind
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def process(data: bytes) -> bytes:
    """Загруженный JPEG/PNG/WebP → WebP 256×256 (центральный квадрат). AvatarError с понятным текстом при отказе."""
    if not data:
        raise AvatarError("Файл пустой.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise AvatarError(f"Файл больше {MAX_UPLOAD_BYTES // 1024} КБ: уменьшите картинку.")
    if sniff(data) is None:
        raise AvatarError("Допустимы только JPEG, PNG и WebP.")
    try:
        from PIL import Image, ImageOps  # noqa: PLC0415
    except ImportError:  # pragma: no cover — Pillow входит в зависимости backend
        raise AvatarError("Обработка изображений на сервере недоступна.") from None
    Image.MAX_IMAGE_PIXELS = MAX_SIDE * MAX_SIDE
    try:
        with Image.open(io.BytesIO(data)) as im:
            im.load()
            if im.width > MAX_SIDE or im.height > MAX_SIDE:
                raise AvatarError("Картинка слишком большая.")
            im = ImageOps.exif_transpose(im).convert("RGBA")
            side = min(im.width, im.height)
            left, top = (im.width - side) // 2, (im.height - side) // 2
            im = im.crop((left, top, left + side, top + side)).resize((SIZE, SIZE), Image.LANCZOS)
            bg = Image.new("RGB", (SIZE, SIZE), (255, 255, 255))
            bg.paste(im, mask=im.getchannel("A"))
            out = io.BytesIO()
            bg.save(out, "WEBP", quality=86, method=4)
            return out.getvalue()
    except AvatarError:
        raise
    except Exception:  # noqa: BLE001 — повреждённый файл, неподдерживаемая вариация формата, «бомба» декодирования
        raise AvatarError("Не удалось прочитать картинку: файл повреждён или имеет неподдерживаемый вид.") from None


class AvatarStore:
    def __init__(self, data_dir: str):
        self.dir = Path(data_dir) / "avatars"

    def path(self, user_id: uuid.UUID) -> Path:
        return self.dir / f"{user_id.hex}.webp"

    def save(self, user_id: uuid.UUID, webp: bytes) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.dir / f".{user_id.hex}.{uuid.uuid4().hex[:8]}.tmp"
        tmp.write_bytes(webp)
        os.replace(tmp, self.path(user_id))

    def read(self, user_id: uuid.UUID) -> bytes | None:
        try:
            return self.path(user_id).read_bytes()
        except OSError:
            return None

    def delete(self, user_id: uuid.UUID) -> None:
        try:
            self.path(user_id).unlink()
        except OSError:
            pass
