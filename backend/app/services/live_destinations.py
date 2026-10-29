"""Внешние площадки трансляций (RUTUBE, VK Видео, YouTube, Twitch, произвольный RTMP/RTMPS): настройки, проверка, подключение «на лету», состояние по каждой площадке.

Один процесс Egress уже формирует картинку; площадки — дополнительные адреса его RTMP-выхода (`update_stream` добавляет и убирает их, не прерывая эфир, MP4 и HLS). Ошибка одной
площадки остаётся её ошибкой: остальные, собственный просмотр и конференция продолжают работать.

Ключ потока — как пароль: хранится зашифрованно (AAD привязан к строке), наружу не отдаётся ни в API, ни в журнал, ни в сообщения об ошибках (`scrub`). Адреса проверяются от SSRF:
сервер Egress не должен ходить на внутренние адреса организации по просьбе владельца комнаты, поэтому по умолчанию разрешены только адреса в интернете (`is_global`), а внутренние сети
включает администратор (`allow_private_destinations`)."""
from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import re
import socket
import uuid
from datetime import datetime, timezone
from urllib.parse import urlsplit

from livekit.protocol import egress as eg
from sqlalchemy import func, select

from ..models import Broadcast, LiveDestination, Room
from .settings import SettingsError

log = logging.getLogger("app.live")

PLATFORMS = {"rutube": "RUTUBE", "vk": "VK Видео", "youtube": "YouTube", "twitch": "Twitch", "custom": "Другой RTMP/RTMPS-сервер"}
KEY_RE = re.compile(r"^[A-Za-z0-9_\-.~:@%+=&?/!*]{1,512}$")
PATH_RE = re.compile(r"^(/[A-Za-z0-9_\-.~%+=:@]{1,120}){0,6}$")
HOST_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9\-.]{0,251}[A-Za-z0-9])?$")
DENY_PORTS = {1433, 1521, 2375, 2376, 3306, 5432, 5672, 6379, 8200, 9200, 11211, 27017}


class DestinationError(Exception):
    def __init__(self, code: str, message: str, status: int = 422):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def scrub(text: str, *secrets: str) -> str:
    """Убирает ключ (и адрес вместе с ключом) из текста ошибки перед записью в журнал и интерфейс."""
    out = text or ""
    for s in secrets:
        if s and len(s) >= 4:
            out = out.replace(s, "***")
    return re.sub(r"(rtmps?://[^\s/]+/[^\s/]*)/[^\s]*", r"\1/***", out)[:300]


def normalize_server_url(raw: str) -> tuple[str, str, int]:
    """Адрес сервера без ключа → (адрес, хост, порт). Только rtmp/rtmps; без логина, параметров и пробелов; разумный порт и путь."""
    url = (raw or "").strip()
    if not url or len(url) > 400 or re.search(r"[\s\x00-\x1f]", url):
        raise DestinationError("bad_url", "Адрес сервера: rtmp://… или rtmps://…, без пробелов.")
    u = urlsplit(url)
    if u.scheme.lower() not in ("rtmp", "rtmps"):
        raise DestinationError("bad_scheme", "Поддерживаются только адреса rtmp:// и rtmps://.")
    if u.username or u.password or u.query or u.fragment:
        raise DestinationError("bad_url", "В адресе сервера не должно быть логина, параметров и «#»: ключ вводится отдельно.")
    host = (u.hostname or "").lower()
    if not host or not (HOST_RE.match(host) or _is_ip(host)):
        raise DestinationError("bad_host", "В адресе нет корректного имени сервера.")
    try:
        port = u.port or (443 if u.scheme.lower() == "rtmps" else 1935)
    except ValueError:
        raise DestinationError("bad_port", "Некорректный порт в адресе сервера.") from None
    if not (1 <= port <= 65535) or port in DENY_PORTS or (port < 1024 and port not in (80, 443)):
        raise DestinationError("bad_port", f"Порт {port} для вещания не разрешён (обычно 1935 для rtmp и 443 для rtmps).")
    path = (u.path or "").rstrip("/")
    if not PATH_RE.match(path):
        raise DestinationError("bad_path", "Путь в адресе сервера содержит недопустимые знаки.")
    netloc = f"[{host}]" if ":" in host else host
    return f"{u.scheme.lower()}://{netloc}{':' + str(u.port) if u.port else ''}{path}", host, port


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host.strip("[]"))
        return True
    except ValueError:
        return False


async def check_host_allowed(host: str, *, allow_private: bool) -> list[str]:
    """Имя разрешается в адреса; все они должны быть публичными (или внутренняя сеть разрешена администратором). Всегда запрещены loopback, link-local (в том числе 169.254.169.254),
    multicast и «не указан». Возвращает проверенные адреса."""
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except OSError:
        raise DestinationError("dns", "Имя сервера не найдено: проверьте адрес.") from None
    addrs = sorted({i[4][0] for i in infos})
    if not addrs:
        raise DestinationError("dns", "Имя сервера не найдено: проверьте адрес.")
    for a in addrs:
        ip = ipaddress.ip_address(a.split("%")[0])
        if ip.is_loopback and allow_private:
            continue                              # свой приёмник на этом же сервере (проверки); в продакшене внутреннюю сеть открывает администратор
        if ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified or ip.is_reserved:
            raise DestinationError("forbidden_address", "Этот адрес сервера не разрешён: он ведёт во внутреннюю или служебную сеть.")
        if not ip.is_global and not allow_private:
            raise DestinationError("private_address", "Адрес сервера ведёт во внутреннюю сеть. Администратор может разрешить такие серверы (Администрирование → Трансляции).")
    return addrs


def full_url(server_url: str, key: str) -> str:
    return f"{server_url.rstrip('/')}/{key.lstrip('/')}"


def clean_key(raw: str) -> str:
    key = (raw or "").strip()
    if not KEY_RE.match(key):
        raise DestinationError("bad_key", "Ключ потока: от 1 до 512 знаков без пробелов (латиница, цифры и знаки - _ . ~ : @ % + = & ? / ! *).")
    return key


def clean_watch_url(raw: str | None) -> str | None:
    v = (raw or "").strip()
    if not v:
        return None
    if len(v) > 400 or not re.match(r"^https://[^\s/]+(/\S*)?$", v):
        raise DestinationError("bad_watch_url", "Ссылка для зрителей на площадке: https://… без пробелов.")
    return v


class Destinations:
    def __init__(self, settings_svc, redis, api, journal=None):
        self._svc, self._r, self.api, self.journal = settings_svc, redis, api, journal

    # ------------------------------------------------------------------ шифрование
    def _aad(self, did) -> bytes:
        return f"live_destination.{did}".encode()

    def _encrypt(self, did, key: str) -> str:
        if self._svc.box is None:
            raise DestinationError("no_master_key", "APP_MASTER_KEY не задан — ключ потока сохранить нельзя.", 409)
        return self._svc.box.encrypt(key, aad=self._aad(did))

    def _decrypt(self, row: LiveDestination) -> str:
        if not row.key_enc or self._svc.box is None:
            return ""
        try:
            return self._svc.box.decrypt(row.key_enc, aad=self._aad(row.id))
        except Exception:  # noqa: BLE001 — повреждённый ключ не должен ронять запуск эфира
            return ""

    # ------------------------------------------------------------------ CRUD
    @staticmethod
    def public(row: LiveDestination) -> dict:
        return {"id": str(row.id), "platform": row.platform, "platform_name": PLATFORMS.get(row.platform, row.platform), "name": row.name, "server_url": row.server_url,
                "channel": row.channel, "watch_url": row.watch_url, "enabled": row.enabled, "has_key": bool(row.key_enc), "status": row.status,
                "last_ok_at": row.last_ok_at, "last_error": row.last_error}

    async def rows(self, db, room: Room) -> list[LiveDestination]:
        return list((await db.execute(select(LiveDestination).where(LiveDestination.room_id == room.id).order_by(LiveDestination.position, LiveDestination.created_at))).scalars())

    async def _policy(self, db, platform: str):
        cfg = await self._svc.get(db, "live")
        if platform not in PLATFORMS:
            raise DestinationError("bad_platform", "Неизвестная площадка.")
        if not getattr(cfg, f"allow_{platform}", True):
            raise DestinationError("platform_forbidden", f"Площадка «{PLATFORMS[platform]}» отключена администратором сервера.", 403)
        return cfg

    async def create(self, db, room: Room, body: dict) -> LiveDestination:
        platform = str(body.get("platform") or "")
        cfg = await self._policy(db, platform)
        if (await db.execute(select(func.count()).select_from(LiveDestination).where(LiveDestination.room_id == room.id))).scalar_one() >= cfg.max_destinations:
            raise DestinationError("too_many", f"Площадок у комнаты не больше {cfg.max_destinations}.", 409)
        url, host, _ = normalize_server_url(str(body.get("server_url") or ""))
        await check_host_allowed(host, allow_private=cfg.allow_private_destinations)
        key = clean_key(str(body.get("key") or ""))
        name = " ".join(str(body.get("name") or PLATFORMS[platform]).split())[:120] or PLATFORMS[platform]
        did = uuid.uuid4()
        row = LiveDestination(id=did, room_id=room.id, platform=platform, name=name, server_url=url, key_enc=self._encrypt(did, key), channel=(str(body.get("channel") or "").strip()[:160] or None),
                              watch_url=clean_watch_url(body.get("watch_url")), enabled=bool(body.get("enabled", True)),
                              position=int((await db.execute(select(func.coalesce(func.max(LiveDestination.position), -1)).where(LiveDestination.room_id == room.id))).scalar_one()) + 1)
        db.add(row)
        await db.commit()
        return row

    async def update(self, db, row: LiveDestination, body: dict) -> LiveDestination:
        cfg = await self._svc.get(db, "live")
        if "server_url" in body and str(body["server_url"]).strip() != row.server_url:
            url, host, _ = normalize_server_url(str(body["server_url"]))
            await check_host_allowed(host, allow_private=cfg.allow_private_destinations)
            row.server_url = url
        if body.get("key"):
            row.key_enc = self._encrypt(row.id, clean_key(str(body["key"])))        # замена ключа; прежний нигде не остаётся
            row.status, row.last_error = "idle", None
        if "name" in body:
            row.name = " ".join(str(body["name"] or "").split())[:120] or row.name
        if "channel" in body:
            row.channel = (str(body["channel"] or "").strip()[:160] or None)
        if "watch_url" in body:
            row.watch_url = clean_watch_url(body["watch_url"])
        if "enabled" in body:
            row.enabled = bool(body["enabled"])
        await db.commit()
        return row

    # ------------------------------------------------------------------ проверка (без ключа площадки: ключ проверит сама площадка при начале эфира)
    async def probe(self, db, row: LiveDestination | None = None, *, server_url: str | None = None) -> dict:
        cfg = await self._svc.get(db, "live")
        url, host, port = normalize_server_url(server_url if server_url is not None else (row.server_url if row else ""))
        addrs = await check_host_allowed(host, allow_private=cfg.allow_private_destinations)
        ok, why = False, ""
        for a in addrs[:3]:
            try:
                _r, w = await asyncio.wait_for(asyncio.open_connection(a, port), timeout=5)
                w.close()
                ok = True
                break
            except (OSError, asyncio.TimeoutError) as exc:
                why = "время ожидания истекло" if isinstance(exc, asyncio.TimeoutError) else (exc.strerror or "соединение не установлено")
        return {"ok": ok, "message": "Сервер отвечает по сети. Ключ потока проверит сама площадка при начале эфира." if ok else f"Сервер не отвечает: {why}."}

    # ------------------------------------------------------------------ запуск эфира и «на лету»
    async def start_urls(self, db, room: Room) -> tuple[list[str], list[uuid.UUID]]:
        """Адреса включённых площадок для запроса к Egress. Площадка с негодным адресом или ключом пропускается (её состояние — «ошибка»), эфир при этом начинается."""
        cfg = await self._svc.get(db, "live")
        urls, ids = [], []
        for row in await self.rows(db, room):
            if not row.enabled:
                continue
            try:
                await self._policy(db, row.platform)
                _u, host, _p = normalize_server_url(row.server_url)
                await check_host_allowed(host, allow_private=cfg.allow_private_destinations)
                key = self._decrypt(row)
                if not key:
                    raise DestinationError("no_key", "Ключ потока не задан или не читается: задайте его заново.")
                urls.append(full_url(row.server_url, key))
                ids.append(row.id)
                row.status, row.last_error = "connecting", None
            except DestinationError as exc:
                row.status, row.last_error = "failed", exc.message[:290]
        await db.commit()
        return urls, ids

    async def set_live(self, db, broadcast: Broadcast, row: LiveDestination, enabled: bool) -> None:
        """Включить или выключить площадку во время эфира: адрес добавляется в выход Egress или убирается из него."""
        row.enabled = enabled
        key = self._decrypt(row)
        if not key:
            raise DestinationError("no_key", "Ключ потока не задан или не читается: задайте его заново.")
        url = full_url(row.server_url, key)
        try:
            if enabled:
                cfg = await self._svc.get(db, "live")
                _u, host, _p = normalize_server_url(row.server_url)
                await check_host_allowed(host, allow_private=cfg.allow_private_destinations)
                await self.api.update_stream(broadcast.egress_id, add=[url])
                row.status, row.last_error = "connecting", None
            else:
                await self.api.update_stream(broadcast.egress_id, remove=[url])
                row.status = "finished"
        except DestinationError:
            raise
        except Exception as exc:  # noqa: BLE001
            row.status, row.last_error = "failed", scrub(str(exc), key, url) or "Egress не принял изменение"
            await db.commit()
            raise DestinationError("egress", f"Не удалось изменить площадку: {row.last_error}", 503) from None
        await db.commit()

    @staticmethod
    def _match(sr_url: str, row: LiveDestination, key: str) -> bool:
        """Egress скрывает ключ в адресах результатов: `rtmp://хост:порт/приложение/{начало...конец}`; порядок результатов произвольный (проверено на настоящем Egress:
        отсортированы по адресу). Площадку находим по адресу сервера и совпадению начала и конца ключа; неоднозначное совпадение не угадываем."""
        m = re.match(r"^(.*)/\{(.*?)\.\.\.(.*?)\}$", sr_url or "") or re.match(r"^(.*)/\{(\.\.\.)()\}$", sr_url or "")
        if not m:
            return False
        server, head, tail = m.group(1).rstrip("/").lower(), m.group(2).replace("...", ""), m.group(3)
        return server == row.server_url.rstrip("/").lower() and key.startswith(head) and key.endswith(tail)

    async def on_info(self, db, broadcast: Broadcast, info: eg.EgressInfo) -> None:
        """Состояния потоков из ответа Egress → состояния площадок: ACTIVE — идёт, FAILED — ошибка с причиной без ключа, FINISHED — завершена. У выключенной площадки
        прошлые результаты не учитываются (её состояние задал сам владелец)."""
        results = list(info.stream_results)
        if not results:
            return
        room = await db.get(Room, broadcast.room_id)
        if room is None:
            return
        rows = [r for r in await self.rows(db, room) if r.enabled]
        keys = {r.id: self._decrypt(r) for r in rows}
        now = datetime.now(timezone.utc)
        changed = False
        for row in rows:
            key = keys[row.id]
            mine = [sr for sr in results if self._match(sr.url, row, key)]
            others = [r for r in rows if r.id != row.id and any(self._match(sr.url, r, keys[r.id]) for sr in mine)]
            if not mine or others:
                continue                                  # нет результатов или совпадение неоднозначно — состояние не трогаем
            names = {eg.StreamInfo.Status.Name(sr.status): sr for sr in mine}
            if "ACTIVE" in names:
                new, err = "live", None
                if row.status != "live":
                    row.last_ok_at = now
            elif "FAILED" in names:
                new, err = "failed", scrub(names["FAILED"].error or "площадка не приняла поток", key) or "площадка не приняла поток"
            else:
                new, err = "finished", row.last_error if row.status == "failed" else None
            if (new, err) != (row.status, row.last_error):
                row.status, row.last_error = new, err
                changed = True
                if new == "failed" and self.journal is not None:
                    try:
                        self.journal.emit("storage", "live_destination_failed", level="warning", meeting_id=str(broadcast.meeting_id), message=f"{row.name}: {err}"[:300], data={"platform": row.platform})
                    except Exception:  # noqa: BLE001
                        pass
        if changed:
            await db.commit()

    async def finish_all(self, db, room_id: uuid.UUID) -> None:
        """Эфир закончился: «идущие» и «подключающиеся» площадки переходят в «завершена» (ошибки остаются как есть)."""
        rows = (await db.execute(select(LiveDestination).where(LiveDestination.room_id == room_id, LiveDestination.status.in_(("live", "connecting"))))).scalars()
        for r in rows:
            r.status = "finished"
        await db.commit()

    async def live_state(self, db, room: Room, broadcast: Broadcast | None) -> list[dict]:
        return [self.public(r) for r in await self.rows(db, room)]
