"""Визуальная CAPTCHA: картинка с символами, ответ проверяет сервер. Без внешних сервисов.

Свойства:
  * ответ не покидает сервер: в Redis лежит соль и хэш ответа, задача привязана к адресу клиента (IP/64) и к назначению (`login`, `guest`, …);
  * задача ОДНОРАЗОВАЯ: при проверке запись удаляется сразу, правильно или неправильно (угадывать по одной картинке нельзя), повторное использование отклоняется;
  * срок жизни ограничен настройкой (`ttl_seconds`);
  * выдача задач ограничена по адресу (services/captcha.py).
Алфавит без похожих символов (0/O, 1/I/L); сравнение без учёта регистра. Картинка рисуется Pillow; шрифт — DejaVu (есть в образе, как и для PDF).
CAPTCHA — не единственная защита: перебор ограничивают счётчики (loginguard.py) и без неё. Автоматическое распознавание картинок возможно, поэтому на неё одну полагаться нельзя.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import random
import secrets
import time

from .ipres import ip_key

ALPHABET = "ABCDEFGHJKMNPRTUVWXY34689"          # без похожих: 0/O/Q/D-ish, 1/I/L/7, 2/Z, 5/S
_FONTS = ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
          "C:/Windows/Fonts/arialbd.ttf"]


def _font(size: int):
    from PIL import ImageFont  # noqa: PLC0415

    for p in _FONTS:
        try:
            return ImageFont.truetype(p, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


def render(text: str, rnd: random.Random | None = None) -> bytes:
    """PNG с искажёнными символами: поворот и сдвиг каждого знака, волна, линии и точки помех."""
    from PIL import Image, ImageDraw, ImageFilter  # noqa: PLC0415

    rnd = rnd or random.SystemRandom()
    w, h = 80 + 34 * len(text), 84
    img = Image.new("RGB", (w, h), (244, 247, 253))
    d = ImageDraw.Draw(img)
    for _ in range(5):                                         # фоновые линии
        d.line([(rnd.randint(0, w), rnd.randint(0, h)), (rnd.randint(0, w), rnd.randint(0, h))], fill=(rnd.randint(150, 210), rnd.randint(160, 215), rnd.randint(200, 240)), width=rnd.randint(1, 3))
    x = 24
    for ch in text:
        size = rnd.randint(40, 50)
        tile = Image.new("RGBA", (size + 26, size + 26), (0, 0, 0, 0))
        ImageDraw.Draw(tile).text((10, 4), ch, font=_font(size), fill=(rnd.randint(10, 70), rnd.randint(30, 90), rnd.randint(90, 170), 255))
        tile = tile.rotate(rnd.uniform(-18, 18), resample=Image.BICUBIC, expand=True)
        img.paste(tile, (x, rnd.randint(-2, 12)), tile)
        x += int(size * 0.66) + rnd.randint(2, 8)
    px = img.load()                                            # волна по вертикали
    src = img.copy().load()
    amp, per, ph = rnd.uniform(2.0, 4.0), rnd.uniform(28, 46), rnd.uniform(0, 6.28)
    import math  # noqa: PLC0415

    for xx in range(w):
        off = int(amp * math.sin(2 * math.pi * xx / per + ph))
        for yy in range(h):
            px[xx, yy] = src[xx, min(h - 1, max(0, yy + off))]
    d = ImageDraw.Draw(img)
    for _ in range(3):                                         # линии поверх текста
        d.line([(0, rnd.randint(8, h - 8)), (w, rnd.randint(8, h - 8))], fill=(rnd.randint(30, 120), rnd.randint(60, 140), rnd.randint(110, 200)), width=2)
    for _ in range(140):
        d.point((rnd.randint(0, w - 1), rnd.randint(0, h - 1)), fill=(rnd.randint(60, 190), rnd.randint(80, 200), rnd.randint(120, 230)))
    img = img.filter(ImageFilter.GaussianBlur(0.5))
    out = io.BytesIO()
    img.save(out, "PNG", optimize=True)
    return out.getvalue()


def _digest(salt: str, answer: str) -> str:
    return hashlib.sha256(f"{salt}:{answer.strip().upper()}".encode()).hexdigest()


def _norm(answer: str) -> str:
    return (answer or "").strip().upper().replace("О", "O").replace(" ", "")[:16]


async def issue(redis, ip: str, scope: str, *, ttl_s: int, length: int) -> dict:
    """Новая задача (частоту выдачи ограничивает services/captcha.issue)."""
    answer = "".join(secrets.choice(ALPHABET) for _ in range(length))
    cid, salt = secrets.token_urlsafe(18), secrets.token_hex(8)
    rec = {"h": _digest(salt, answer), "s": salt, "ip": ip_key(ip), "scope": scope, "t": int(time.time())}
    await redis.set(f"captcha:img:{cid}", json.dumps(rec), ex=ttl_s)
    png = render(answer)
    return {"kind": "image", "id": cid, "image": "data:image/png;base64," + base64.b64encode(png).decode(), "ttl": ttl_s, "length": length}


class Throttled(Exception):
    pass


async def verify(redis, cid: str, answer: str, ip: str, scope: str) -> str | None:
    """None — верно; иначе причина: missing, expired, context, wrong. Запись удаляется при ЛЮБОЙ проверке (одна попытка на картинку)."""
    if not cid or not answer or len(cid) > 64:
        return "missing"
    raw = await redis.getdel(f"captcha:img:{cid}")
    if not raw:
        return "expired"
    try:
        rec = json.loads(raw)
    except ValueError:
        return "expired"
    if rec.get("ip") != ip_key(ip) or rec.get("scope") != scope:
        return "context"
    import hmac  # noqa: PLC0415

    return None if hmac.compare_digest(_digest(rec["s"], _norm(answer)), rec["h"]) else "wrong"
