"""Значки комнат: библиотека, загрузка (проверка содержимого, квадрат 256×256 WebP, скруглённые углы), замена, сброс, права, кэширование, аудит и журнал комнаты."""
from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from PIL import Image

from app.services import room_icons

from .conftest import login, make_room
from .test_mail import LEADERS

API = "/api/v1"
ASSET = Path(room_icons.__file__).resolve().parent.parent / "assets" / "room_icons.json"


def png(w=300, h=200, color=(200, 30, 30), fmt="PNG") -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, fmt)
    return buf.getvalue()


def test_library_has_enough_icons_in_every_category_and_matches_frontend_manifest():
    keys = json.loads(ASSET.read_text(encoding="utf-8"))["keys"]
    assert len(keys) >= 100 and len(keys) == len(set(keys))
    ts = (Path(__file__).resolve().parents[2] / "frontend/src/roomIcons/icons.ts").read_text(encoding="utf-8")
    front = set(__import__("re").findall(r'"key": "([a-z0-9-]+)"', ts))
    assert front == set(keys), "список ключей сервера и значки приложения должны совпадать (scripts/build_room_icons.py)"
    cats = set(__import__("re").findall(r'"category": "([a-z]+)"', ts))
    assert {"it", "business", "comms", "docs", "security", "support", "general", "neutral"} <= cats


def test_process_makes_256_rounded_webp_without_metadata_and_rejects_bad_files():
    out = room_icons.process(png(640, 300))
    im = Image.open(io.BytesIO(out))
    assert im.format == "WEBP" and im.size == (256, 256)
    assert im.convert("RGBA").getpixel((0, 0))[3] == 0, "углы скруглены (прозрачные)"
    assert im.convert("RGBA").getpixel((128, 128))[3] == 255
    assert len(out) < 20_000, "итоговая версия небольшая"
    for name, data in (("пусто", b""), ("не картинка", b"hello world" * 100), ("svg", b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'),
                       ("exe под png", b"MZ" + b"\x00" * 200), ("повреждён", b"\x89PNG\r\n\x1a\n" + b"\x00" * 50), ("gif", b"GIF89a" + b"\x00" * 50)):
        with pytest.raises(room_icons.RoomIconError):
            room_icons.process(data)
    with pytest.raises(room_icons.RoomIconError):
        room_icons.process(png(8, 8))                                                   # слишком мелкая
    with pytest.raises(room_icons.RoomIconError):
        room_icons.process(b"\xff\xd8\xff" + b"0" * (room_icons.MAX_UPLOAD_BYTES + 1))  # слишком тяжёлая


def test_leader_picks_library_uploads_replaces_and_resets_with_audit_and_journal(client, directory):
    room = make_room(client, name="Серверная", moderators=LEADERS)
    login(client, "carol")
    assert client.get(f"{API}/rooms").json()[0]["icon"] is None, "по умолчанию — стандартный значок"
    r = client.put(f"{API}/rooms/{room['id']}/icon/library", json={"key": "server"})
    assert r.status_code == 200 and r.json()["icon"] == {"kind": "library", "key": "server", "url": None}
    assert client.put(f"{API}/rooms/{room['id']}/icon/library", json={"key": "../../etc/passwd"}).status_code == 422
    assert client.put(f"{API}/rooms/{room['id']}/icon/library", json={"key": "нет-такого"}).status_code == 422
    up = client.put(f"{API}/rooms/{room['id']}/icon", content=png(), headers={"content-type": "image/png"})
    assert up.status_code == 200 and up.json()["icon"]["kind"] == "image"
    url, v1 = up.json()["icon"]["url"], up.json()["icon"]["url"].split("v=")[1]
    got = client.get(url)
    assert got.status_code == 200 and got.headers["content-type"] == "image/webp" and "immutable" in got.headers["cache-control"] and got.headers["etag"] == f'"{v1}"'
    assert Image.open(io.BytesIO(got.content)).size == (256, 256)
    assert client.get(url, headers={"if-none-match": got.headers["etag"]}).status_code == 304
    # замена: адрес (версия) меняется — кэш браузера обновится
    up2 = client.put(f"{API}/rooms/{room['id']}/icon", content=png(color=(10, 10, 200), fmt="JPEG"), headers={"content-type": "image/jpeg"})
    assert up2.json()["icon"]["url"].split("v=")[1] != v1
    # любой, кто видит комнату, видит значок
    login(client, "alice")
    assert any(x["icon"] and x["icon"]["kind"] == "image" for x in client.get(f"{API}/rooms").json())
    assert client.get(up2.json()["icon"]["url"]).status_code == 200
    # обычный участник и руководитель другой комнаты менять не могут
    for who in ("alice", "bob"):
        login(client, who)
        assert client.put(f"{API}/rooms/{room['id']}/icon/library", json={"key": "star"}).status_code == 404
        assert client.put(f"{API}/rooms/{room['id']}/icon", content=png()).status_code == 404
        assert client.delete(f"{API}/rooms/{room['id']}/icon").status_code == 404
    # выбор из библиотеки удаляет загруженный файл; сброс возвращает стандартный
    login(client, "carol")
    client.put(f"{API}/rooms/{room['id']}/icon/library", json={"key": "shield"})
    assert client.get(up2.json()["icon"]["url"]).status_code == 404, "загруженный файл удалён"
    assert client.delete(f"{API}/rooms/{room['id']}/icon").json() == {"icon": None}
    assert client.get(f"{API}/rooms").json()[0]["icon"] is None
    # журнал комнаты: кто, когда, что
    j = client.get(f"{API}/rooms/{room['id']}/journal", params={"limit": 100}).json()["items"]
    ev = [e for e in j if e["action"] == "room.icon"]
    assert [e["details"]["mode"] for e in ev][:2] == ["reset", "library"] and {e["details"]["mode"] for e in ev} == {"reset", "library", "image"}
    assert all(e["actor"] == "Carol C" and e["at"].endswith("+00:00") for e in ev)
    assert [e["details"].get("key") for e in ev if e["details"]["mode"] == "library"][:1] == ["shield"]


def test_upload_limits_and_hostile_files_return_clear_errors_not_500(client, directory):
    room = make_room(client, moderators=LEADERS)
    login(client, "carol")
    url = f"{API}/rooms/{room['id']}/icon"
    for name, data in (("пустой", b""), ("текст", b"just text"), ("svg", b"<svg onload=alert(1)></svg>"), ("исполняемый", b"MZ\x90\x00" * 100)):
        r = client.put(url, content=data)
        assert r.status_code == 422 and "detail" in r.json(), name
    big = client.put(url, content=b"\xff\xd8\xff" + b"0" * (room_icons.MAX_UPLOAD_BYTES + 10))
    assert big.status_code in (413, 422)
    assert client.get(f"{API}/rooms").json()[0]["icon"] is None, "неудачные загрузки ничего не меняют"
    ok = client.put(url, content=png(4000, 4000))                                           # большое, но допустимое: исходник не хранится
    assert ok.status_code == 200 and len(client.get(ok.json()["icon"]["url"]).content) < 30_000


def test_admin_sees_icon_in_room_lists_meetings_and_manage_settings(client, directory):
    room = make_room(client, name="Витрина", moderators=LEADERS)
    login(client, "carol")
    client.put(f"{API}/rooms/{room['id']}/icon/library", json={"key": "presentation"})
    assert client.get(f"{API}/rooms/{room['id']}/manage").json()["icon"]["key"] == "presentation"
    login(client, "root")
    assert client.get(f"{API}/admin/rooms/{room['id']}").json()["icon"]["key"] == "presentation"
