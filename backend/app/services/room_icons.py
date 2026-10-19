"""Значки комнат: проверка и приведение загруженной картинки, хранение на диске сервера, ключи встроенной библиотеки.

Значок — квадратный WebP 256×256 со скруглёнными углами. Картинку обрезает до квадрата и уменьшает браузер (редактор кадрирования), но серверу верить нельзя: файл открывается
библиотекой изображений, тип определяется по содержимому (не по расширению и не по заявленному типу), размеры и вес ограничены, квадрат и размер выставляются заново,
метаданные (EXIF, GPS) не сохраняются, формат на выходе один. Исходный файл не сохраняется — только итоговая версия. SVG не принимается (нужна отдельная безопасная очистка).
Встроенная библиотека — набор значков Lucide (ISC): на сервере хранится только список допустимых ключей, сами значки встроены в приложение.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import uuid
from pathlib import Path

MAX_UPLOAD_BYTES = 3 * 1024 * 1024     # то, что присылает браузер после кадрирования, — обычно 10–40 КБ; запас на прямой вызов API
MAX_SIDE = 8000                        # исходная картинка больше по стороне — отказ (защита от «бомб» декодирования)
MAX_PIXELS = 40_000_000
SIZE = 256
MIME = "image/webp"
RADIUS = 0.22                          # радиус скругления углов как доля стороны
_ASSET = Path(__file__).resolve().parent.parent / "assets" / "room_icons.json"


class RoomIconError(Exception):
    pass


def library_keys() -> frozenset[str]:
    try:
        return frozenset(json.loads(_ASSET.read_text(encoding="utf-8"))["keys"])
    except (OSError, ValueError, KeyError):                    # без файла библиотеки выбор значков из неё отключён, остальное работает
        return frozenset()


def sniff(data: bytes) -> str | None:
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def process(data: bytes, *, max_bytes: int = MAX_UPLOAD_BYTES) -> bytes:
    """Загруженный PNG/JPEG/WebP → WebP 256×256 со скруглёнными углами (центральный квадрат). RoomIconError с понятным текстом при отказе."""
    if not data:
        raise RoomIconError("Файл пустой.")
    if len(data) > max_bytes:
        raise RoomIconError(f"Файл больше {max_bytes // (1024 * 1024)} МБ: уменьшите картинку.")
    if sniff(data) is None:
        raise RoomIconError("Допустимы только изображения PNG, JPG и WEBP (по содержимому файла, а не по расширению).")
    try:
        from PIL import Image, ImageDraw, ImageOps  # noqa: PLC0415
    except ImportError:  # pragma: no cover — Pillow входит в зависимости backend
        raise RoomIconError("Обработка изображений на сервере недоступна.") from None
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    try:
        with Image.open(io.BytesIO(data)) as im:
            if im.width > MAX_SIDE or im.height > MAX_SIDE or im.width < 16 or im.height < 16:
                raise RoomIconError("Размер картинки должен быть от 16 до 8000 пикселей по каждой стороне.")
            im.load()
            im = ImageOps.exif_transpose(im).convert("RGBA")
            side = min(im.width, im.height)
            left, top = (im.width - side) // 2, (im.height - side) // 2
            im = im.crop((left, top, left + side, top + side)).resize((SIZE, SIZE), Image.LANCZOS)
            mask = Image.new("L", (SIZE * 4, SIZE * 4), 0)                      # маска рисуется в 4× и сжимается — гладкие углы
            ImageDraw.Draw(mask).rounded_rectangle((0, 0, SIZE * 4 - 1, SIZE * 4 - 1), radius=int(SIZE * 4 * RADIUS), fill=255)
            mask = mask.resize((SIZE, SIZE), Image.LANCZOS)
            alpha = Image.composite(im.getchannel("A"), Image.new("L", (SIZE, SIZE), 0), mask)
            im.putalpha(alpha)
            out = io.BytesIO()
            im.save(out, "WEBP", quality=88, method=4)
            return out.getvalue()
    except RoomIconError:
        raise
    except Exception:  # noqa: BLE001 — повреждённый файл, неподдерживаемая вариация формата, «бомба» декодирования
        raise RoomIconError("Не удалось прочитать картинку: файл повреждён или имеет неподдерживаемый вид.") from None


def revision(webp: bytes) -> str:
    return hashlib.sha256(webp).hexdigest()[:12]


def icon_out(room) -> dict | None:
    """Сведения о значке для интерфейса: None — стандартный (цветной квадрат с буквами)."""
    if room.icon_kind == "library" and room.icon_key:
        return {"kind": "library", "key": room.icon_key, "url": None}
    if room.icon_kind == "image" and room.icon_rev:
        return {"kind": "image", "key": None, "url": f"/api/v1/rooms/{room.id}/icon?v={room.icon_rev}"}
    return None


class RoomIconStore:
    def __init__(self, data_dir: str):
        self.dir = Path(data_dir) / "room-icons"

    def path(self, room_id: uuid.UUID) -> Path:
        return self.dir / f"{room_id.hex}.webp"

    def save(self, room_id: uuid.UUID, webp: bytes) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.dir / f".{room_id.hex}.{uuid.uuid4().hex[:8]}.tmp"
        tmp.write_bytes(webp)
        os.replace(tmp, self.path(room_id))

    def read(self, room_id: uuid.UUID) -> bytes | None:
        try:
            return self.path(room_id).read_bytes()
        except OSError:
            return None

    def delete(self, room_id: uuid.UUID) -> None:
        try:
            self.path(room_id).unlink()
        except OSError:
            pass
