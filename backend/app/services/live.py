"""Трансляции: один процесс Egress формирует картинку комнаты и пишет её сразу в три места — MP4 (запись в историю), HLS (собственный просмотр) и RTMP (внешние площадки).

Зрители не подключаются к LiveKit: страница `/live/<токен>` играет HLS. HLS раздаёт nginx контейнера web прямо с общего тома (backend не проксирует сегменты); доступ —
подпись в пути `/hls/<доступ>/<срок>/<подпись>/index.m3u8`: `nginx secure_link` проверяет её без обращения к backend. Идентификатор доступа — имя ссылки в каталоге `acc/`
на каталог текущего эфира; отзыв ссылки или смена пароля зрителя = новый идентификатор и удаление ссылки: старые адреса перестают открываться сразу, а не по истечении срока.

Надёжность: эфир не мешает конференции (при отказе площадки или Egress участники продолжают говорить); число эфиров, свободное место и диск проверяются ДО старта; «вечно идущего»
эфира не бывает: сверка закрывает эфир, если Egress или его рекордер пропали, комната опустела или вышел предел времени; старые сегменты HLS удаляются во время эфира."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import logging
import os
import secrets
import shutil
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from livekit.protocol import egress as eg
from livekit.protocol import models as lkm
from sqlalchemy import func, select

from ..config import Settings
from ..models import Broadcast, Meeting, Recording, Room
from . import events
from .livekit import list_present_identities
from .storage import meeting_relpath
from .video_rec import ACTIVE_LK, QUALITY, STALL_GRACE_S, EgressApi, VideoRecorder

log = logging.getLogger("app.live")

LIVE_ACTIVE = ("starting", "live", "ending")
# Битрейт эфира выше, чем у записи: площадки требуют не меньше (YouTube, H.264: 3 Мбит/с для 720p и 5 Мбит/с для 1080p) и допускают не больше (VK Видео: 720p 1500–4000, 1080p 3000–6000 кбит/с).
LIVE_QUALITY = {"720p": (1280, 720, 3000), "1080p": (1920, 1080, 5000)}
KEY_FRAME_S = 2.0                      # ключевой кадр раз в 2 с: так требуют площадки (YouTube, Twitch, VK), он же задаёт длину сегмента HLS
EMPTY_GRACE_S = 90                     # комната пуста дольше — эфир заканчивается (выступающие ушли)
STOP_GRACE_S = 180                     # после остановки столько ждём итог Egress; потом закрываем сами
HLS_FILES = ("index.m3u8",)            # единственный плейлист, который отдаётся зрителям (event.m3u8 — полный, внутренний)
SWEEP_EVERY_S = 60                     # уборка сегментов не чаще раза в минуту


class LiveError(Exception):
    def __init__(self, code: str, message: str, status: int = 409):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


# ------------------------------------------------------------------------------------------------ подписи и идентификаторы
def new_token() -> str:
    return secrets.token_urlsafe(24)               # 192 бита: перебором не угадывается


def new_access() -> str:
    return secrets.token_hex(12)                   # 96 бит; только [0-9a-f] — безопасно для пути и для регулярного выражения nginx


def hls_secret(settings: Settings) -> str:
    """Общий секрет подписи HLS: производный от главного ключа приложения (хранить отдельно не нужно). nginx читает его из файла, который backend пишет в том трансляций."""
    master = (settings.app_master_key or settings.livekit_api_secret or "").encode()
    return hmac.new(master, b"peregovorka/live-hls/v1", hashlib.sha256).hexdigest()


def sign(secret: str, access: str, exp: int) -> str:
    """Подпись для nginx `secure_link_md5 "$exp$acc $secret"`: base64url от MD5 без заполнителя."""
    raw = hashlib.md5(f"{exp}{access} {secret}".encode()).digest()  # noqa: S324 — формат задаёт ngx_http_secure_link_module; секрет не раскрывается, срок в подписи
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def hls_path(secret: str, access: str, ttl_s: int, *, now: float | None = None, bucket_s: int = 300, name: str = "index.m3u8") -> tuple[str, int]:
    """Путь воспроизведения и момент истечения. Срок округляется вверх до `bucket_s`: все зрители в одном окне получают ОДИН адрес — CDN кэширует сегменты, а не по копии на зрителя."""
    t = int(now if now is not None else time.time())
    exp = ((t + ttl_s) // bucket_s + 1) * bucket_s
    return f"/hls/{access}/{exp}/{sign(secret, access, exp)}/{name}", exp


def nginx_secret_conf(secret: str) -> str:
    return f'set $hls_secret "{secret}";\n'


# ------------------------------------------------------------------------------------------------ запрос к Egress
def build_live_request(room_name: str, quality: str, *, mp4_path: str | None, hls_prefix: str | None, urls: list[str], template_url: str = "") -> eg.StartEgressRequest:
    """Один запрос — три выхода. RTMP начинается без адресов: площадки добавляются и убираются на лету (`update_stream`), не прерывая эфир (проверено на настоящем Egress)."""
    from .video_rec import LAYOUT  # noqa: PLC0415

    w, h, vbr = LIVE_QUALITY.get(quality, LIVE_QUALITY["720p"])
    tpl = eg.TemplateSource(layout=LAYOUT, custom_base_url=template_url) if template_url else eg.TemplateSource(layout=LAYOUT)
    enc = eg.EncodingOptions(width=w, height=h, depth=24, framerate=30, video_bitrate=vbr, audio_bitrate=128, audio_frequency=48000, key_frame_interval=KEY_FRAME_S)
    enc.video_codec = lkm.VideoCodec.H264_HIGH
    enc.audio_codec = lkm.AudioCodec.AAC
    outs: list[eg.Output] = []
    if mp4_path:
        outs.append(eg.Output(file=eg.FileOutput(file_type=eg.EncodedFileType.MP4, filepath=mp4_path, disable_manifest=True)))
    if hls_prefix:
        outs.append(eg.Output(segments=eg.SegmentedFileOutput(protocol=eg.SegmentedFileProtocol.HLS_PROTOCOL, filename_prefix=f"{hls_prefix}/seg", playlist_name=f"{hls_prefix}/event.m3u8",
                                                              live_playlist_name=f"{hls_prefix}/index.m3u8", segment_duration=int(KEY_FRAME_S), disable_manifest=True)))
    outs.append(eg.Output(stream=eg.StreamOutput(protocol=eg.StreamProtocol.RTMP, urls=urls)))
    return eg.StartEgressRequest(room_name=room_name, template=tpl, advanced=enc, outputs=outs)


class LiveBroadcaster:
    def __init__(self, settings: Settings, session_maker, redis, settings_svc, *, api: EgressApi, video: VideoRecorder, journal=None, destinations=None):
        self._s, self._sm, self._r, self._svc = settings, session_maker, redis, settings_svc
        self.api, self.video, self.journal = api, video, journal
        self.destinations = destinations           # services.live_destinations.Destinations (внешние площадки); задаётся при запуске приложения
        video.live_owner = self.owns_egress

    # ------------------------------------------------------------------ каталоги
    def live_dir(self) -> Path:
        return Path(os.environ.get("LIVE_DIR") or str(Path(self._s.data_dir) / "live"))

    def egress_dir(self) -> str:
        return (os.environ.get("LIVE_EGRESS_DIR") or "/live").rstrip("/")

    def prepare(self) -> None:
        """Каталоги тома и файл с секретом для nginx. Вызывается при старте backend (web стартует позже и читает файл)."""
        base = self.live_dir()
        try:
            for sub in ("b", "acc", "nginx"):
                (base / sub).mkdir(parents=True, exist_ok=True)
        except OSError:
            log.warning("Каталог трансляций недоступен для записи: HLS работать не будет", extra={"path": str(base)})
            return
        conf = base / "nginx" / "secret.conf"
        text = nginx_secret_conf(hls_secret(self._s))
        try:
            if not conf.exists() or conf.read_text() != text:
                tmp = conf.with_suffix(".tmp")
                tmp.write_text(text)
                os.chmod(tmp, 0o644)
                os.replace(tmp, conf)
        except OSError:
            log.warning("Не удалось записать секрет подписи HLS для nginx", extra={"path": str(conf)})

    async def cfg(self, db):
        return await self._svc.get(db, "live")

    # ------------------------------------------------------------------ доступ к HLS
    def _acc_dir(self, access: str) -> Path:
        return self.live_dir() / "acc" / access

    def link_access(self, access: str | None, hls_dir: str | None) -> None:
        if not access or not hls_dir:
            return
        p = self._acc_dir(access)
        tmp = p.with_name(p.name + ".tmp")
        try:
            tmp.unlink(missing_ok=True)
            os.symlink(f"../b/{hls_dir}", tmp)
            os.replace(tmp, p)
        except OSError:
            log.warning("Не удалось создать ссылку доступа к HLS", extra={"dir": hls_dir})

    def unlink_access(self, access: str | None) -> None:
        if access:
            try:
                self._acc_dir(access).unlink(missing_ok=True)
            except OSError:
                pass

    async def ensure_links(self, db, room: Room) -> bool:
        """Токен зрителя и идентификаторы доступа создаются при первом обращении (а не при включении трансляции в комнате)."""
        changed = False
        if not room.live_token:
            room.live_token, changed = new_token(), True
        if not room.live_view_access:
            room.live_view_access, changed = new_access(), True
        if not room.live_stream_access:
            room.live_stream_access, changed = new_access(), True
        if changed:
            await db.commit()
        return changed

    async def current(self, db, room_id: uuid.UUID) -> Broadcast | None:
        q = select(Broadcast).where(Broadcast.room_id == room_id, Broadcast.status.in_(LIVE_ACTIVE)).order_by(Broadcast.started_at.desc())
        return (await db.execute(q)).scalars().first()

    async def revoke(self, db, room: Room, which: str) -> None:
        """Отзыв: новый идентификатор доступа и (для страницы) новый токен; ссылка со старым идентификатором удаляется — адреса перестают открываться сразу."""
        if which in ("view", "all"):
            self.unlink_access(room.live_view_access)
            room.live_view_access, room.live_token = new_access(), new_token()
        if which in ("stream", "all"):
            self.unlink_access(room.live_stream_access)
            room.live_stream_access = new_access()
        await db.commit()
        cur = await self.current(db, room.id)
        if cur is not None:
            self.link_access(room.live_view_access if which in ("view", "all") else None, cur.hls_dir)
            self.link_access(room.live_stream_access if which in ("stream", "all") else None, cur.hls_dir)

    async def rotate_view_access(self, db, room: Room) -> None:
        """Смена пароля зрителя: страница остаётся той же, но выданные подписи и «пропуска» перестают действовать (новый идентификатор доступа)."""
        self.unlink_access(room.live_view_access)
        room.live_view_access = new_access()
        await db.commit()
        cur = await self.current(db, room.id)
        if cur is not None:
            self.link_access(room.live_view_access, cur.hls_dir)

    # ------------------------------------------------------------------ зрители
    async def viewer_ping(self, room_id: uuid.UUID, vid: str) -> int:
        key, now = f"live:v:{room_id}", time.time()
        pipe = self._r.pipeline()
        pipe.zadd(key, {vid[:40]: now})
        pipe.zremrangebyscore(key, 0, now - 90)
        pipe.zcount(key, now - 60, now)
        pipe.expire(key, 300)
        res = await pipe.execute()
        return int(res[2])

    async def viewers(self, room_id: uuid.UUID) -> int:
        now = time.time()
        return int(await self._r.zcount(f"live:v:{room_id}", now - 60, now))

    # ------------------------------------------------------------------ состояние
    def hls_ready(self, b: Broadcast | None) -> bool:
        return bool(b and b.hls_dir and (self.live_dir() / "b" / b.hls_dir / "index.m3u8").is_file())

    async def state(self, db, room: Room, meeting: Meeting | None = None) -> dict:
        cfg = await self.cfg(db)
        b = await self.current(db, room.id)
        if b is None:
            # эфир не идёт: если последний закончился ошибкой совсем недавно, интерфейс показывает «прерван» и причину (иначе упавший эфир выглядел бы как «ничего не было»)
            last = (await db.execute(select(Broadcast).where(Broadcast.room_id == room.id).order_by(Broadcast.started_at.desc()).limit(1))).scalars().first()
            if last is not None and last.status == "failed" and last.ended_at is not None and (datetime.now(timezone.utc) - last.ended_at).total_seconds() < 900:
                b = last
        live = b is not None and b.status in ("starting", "live")
        out = {"enabled": bool(cfg.enabled and room.live_enabled), "available": bool(cfg.enabled), "live": live, "status": b.status if b else "idle",
               "since": b.started_at if live and b else None, "quality": room.live_quality, "record": room.live_record, "own_hls": room.live_own_hls,
               "viewers": await self.viewers(room.id) if live else 0, "error": b.error if b else None, "broadcast_id": str(b.id) if b else None,
               "hls_ready": self.hls_ready(b), "limit": int(cfg.max_concurrent), "can_start": bool(cfg.enabled and room.live_enabled and not live)}
        if self.destinations is not None:
            out["destinations"] = await self.destinations.live_state(db, room, b)
        return out

    # ------------------------------------------------------------------ проверки перед стартом
    async def _guards(self, db, meeting: Meeting, cfg, urls: list[str]) -> None:
        room = meeting.room
        if meeting.ended_at is not None:
            raise LiveError("meeting_ended", "Встреча уже завершена.")
        if not cfg.enabled:
            raise LiveError("live_disabled", "Трансляции выключены администратором (Администрирование → Трансляции).")
        if not room.live_enabled:
            raise LiveError("live_forbidden", "В этой комнате трансляция не включена (настройки комнаты → Трансляция).")
        if room.e2ee_mode != "off":
            raise LiveError("e2ee", "При сквозном шифровании трансляция недоступна: у сервера нет ключа, чтобы собрать картинку.")
        if room.encrypt_storage:
            raise LiveError("encrypt_storage", "В комнате включено шифрование хранилища: запись и трансляция с ним пока не поддерживаются.")
        if not room.live_own_hls and not urls:
            raise LiveError("no_outputs", "Нечего вещать: включите собственный просмотр или добавьте хотя бы одну внешнюю площадку.")
        busy = (await db.execute(select(func.count()).select_from(Broadcast).where(Broadcast.status.in_(LIVE_ACTIVE)))).scalar_one()
        if busy >= cfg.max_concurrent:
            raise LiveError("live_busy", f"Сейчас идёт максимально допустимое число эфиров ({cfg.max_concurrent}). Обычные конференции от этого не страдают; "
                                         "повторите позже или обратитесь к администратору.")
        base = self.live_dir()
        free = None
        try:
            free = shutil.disk_usage(base if base.exists() else base.parent).free
        except OSError:
            pass
        if free is not None and free < cfg.min_free_gb * 1024**3:
            raise LiveError("disk_low", f"Мало свободного места на томе трансляций (меньше {cfg.min_free_gb} ГБ): эфир не начат.")

    # ------------------------------------------------------------------ старт и остановка
    async def start(self, db, meeting: Meeting, *, by: str = "") -> Broadcast:
        lock = await self._r.set(f"live:start:{meeting.id}", "1", nx=True, ex=30)
        if not lock:
            raise LiveError("live_starting", "Эфир уже запускается.")
        try:
            room = meeting.room
            cur = await self.current(db, room.id)
            if cur is not None and cur.status in ("starting", "live"):
                return cur                                   # повторное нажатие — не второй эфир
            cfg = await self.cfg(db)
            urls = await self.destinations.start_urls(db, room) if self.destinations is not None else []
            await self._guards(db, meeting, cfg, urls)
            present = await list_present_identities(self._s, meeting.livekit_room)
            if present is None:
                raise LiveError("livekit_unavailable", "Сервер звонков недоступен: эфир не начат.", 503)
            if not present:
                raise LiveError("room_empty", "В комнате никого нет: начинать эфир не с кого.")
            await self.ensure_links(db, room)
            hls_dir = new_access() if room.live_own_hls else None
            quality = room.live_quality if room.live_quality in LIVE_QUALITY else cfg.default_quality
            rel = None
            if room.live_record:
                # запись входит в этот же процесс: отдельную видеозапись комнаты закрываем, чтобы не гонять второй браузер
                if await self.video.active_rows(db, meeting.id):
                    await self.video.stop(db, meeting, by="live")
                n = (await db.execute(select(func.count()).select_from(Recording).where(Recording.meeting_id == meeting.id, Recording.kind == "mix_video"))).scalar_one() + 1
                tz = await self.video._tz(db)  # noqa: SLF001
                rel = f"{meeting_relpath(room.name, meeting.started_at.astimezone(tz))}/video-{n}.mp4"
            vcfg = await self._svc.get(db, "video")
            req = build_live_request(meeting.livekit_room, quality, mp4_path=f"{self.video.out_dir()}/{rel}" if rel else None,
                                     hls_prefix=f"{self.egress_dir()}/b/{hls_dir}" if hls_dir else None, urls=urls, template_url=vcfg.template_url)
            try:
                info = await self.api.start(req)
            except Exception as exc:  # noqa: BLE001
                log.warning("Не удалось запустить Egress для эфира", extra={"meeting_id": str(meeting.id), "error": str(exc)[:300]})
                raise LiveError("egress_unavailable", "Служба трансляций (LiveKit Egress) недоступна или не установлена. Сообщите администратору: Администрирование → Видеозапись и Трансляции.", 503) from exc
            now = datetime.now(timezone.utc)
            b = Broadcast(room_id=room.id, meeting_id=meeting.id, status="starting", egress_id=info.egress_id, hls_dir=hls_dir, quality=quality, started_by=by[:200] or None, started_at=now)
            db.add(b)
            if rel:
                db.add(Recording(meeting_id=meeting.id, room_id=meeting.room_id, participant_identity="meeting", path=rel, size_bytes=0, kind="mix_video", mime="video/mp4",
                                 status="processing", has_video=True, egress_id=info.egress_id, started_at=now))
                meeting.record_video = True
            await db.commit()
            self.link_access(room.live_view_access, hls_dir)
            self.link_access(room.live_stream_access, hls_dir)
            await self._announce(meeting, True, by)
            self._emit("live_started", meeting, by=by, egress=info.egress_id, quality=quality, urls=len(urls), hls=bool(hls_dir), record=bool(rel))
            return b
        finally:
            await self._r.delete(f"live:start:{meeting.id}")

    async def stop(self, db, meeting: Meeting | None, *, by: str = "", room_id: uuid.UUID | None = None, manual: bool = False) -> int:
        """Остановка эфира. Egress дописывает файл и закрывает HLS (ENDLIST); итог приходит событием или сверкой."""
        rid = meeting.room_id if meeting is not None else room_id
        rows = list((await db.execute(select(Broadcast).where(Broadcast.room_id == rid, Broadcast.status.in_(("starting", "live"))))).scalars())
        if manual and meeting is not None:
            await self._r.set(f"live:manual_off:{meeting.id}", "1", ex=24 * 3600)      # автозапуск не перебивает решение руководителя
        for b in rows:
            try:
                if b.egress_id:
                    await self.api.stop(b.egress_id)
                b.status = "ending"
                await db.commit()
                await self._r.set(f"live:ending:{b.id}", "1", ex=STOP_GRACE_S)        # отсчёт ожидания итога от остановки, а не от старта
            except Exception as exc:  # noqa: BLE001
                text = str(exc).lower()
                if "not_found" in text or "not found" in text or "already" in text or "complete" in text:
                    await self.finalize(db, b, None)
                else:
                    log.warning("Не удалось остановить Egress эфира", extra={"broadcast": str(b.id), "error": str(exc)[:300]})
                    raise LiveError("egress_unavailable", "Не удалось остановить эфир: служба трансляций не отвечает. Повторите; эфир закончится и при завершении встречи.", 503) from exc
        if rows:
            m = meeting if meeting is not None else await db.get(Meeting, rows[0].meeting_id)
            if m is not None:
                await self._announce(m, False, by)
                self._emit("live_stopped", m, by=by)
        return len(rows)

    async def _announce(self, meeting: Meeting, live: bool, by: str = "") -> None:
        await events.publish(self._r, meeting.id, {"type": "live_changed", "live": live, "by": by})

    # ------------------------------------------------------------------ автозапуск и конец встречи
    async def on_join(self, db, meeting: Meeting) -> None:
        """Режим «автоматически»: эфир начинается, когда в комнате появился выступающий (если руководитель не останавливал его вручную)."""
        try:
            room = meeting.room
            if not room.live_enabled or room.live_start != "auto" or meeting.ended_at is not None:
                return
            if await self._r.get(f"live:manual_off:{meeting.id}") or not (await self.cfg(db)).enabled:
                return
            await self.start(db, meeting, by="auto")
        except LiveError as exc:
            log.info("Автозапуск эфира не выполнен", extra={"meeting_id": str(meeting.id), "code": exc.code})
            if exc.code in ("egress_unavailable", "disk_low", "live_busy"):
                self._emit("live_auto_failed", meeting, level="warning", code=exc.code, message=exc.message)
        except Exception:  # noqa: BLE001 — вход участника не должен зависеть от трансляции
            log.exception("Ошибка автозапуска эфира", extra={"meeting_id": str(meeting.id)})

    async def on_meeting_end(self, db, meeting: Meeting) -> None:
        try:
            await self.stop(db, meeting, by="meeting_end")
        except Exception:  # noqa: BLE001
            log.exception("Не удалось остановить эфир при завершении встречи", extra={"meeting_id": str(meeting.id)})

    async def owns_egress(self, db, egress_id: str | None) -> bool:
        """Принадлежит ли процесс Egress идущему эфиру (его MP4-запись нельзя остановить отдельно от эфира)."""
        if not egress_id:
            return False
        return (await db.execute(select(Broadcast.id).where(Broadcast.egress_id == egress_id, Broadcast.status.in_(LIVE_ACTIVE)))).first() is not None

    # ------------------------------------------------------------------ итог
    async def finalize(self, db, b: Broadcast, info: eg.EgressInfo | None, *, lost: bool = False) -> None:
        if b.status in ("ended", "failed"):
            return
        failed = info is not None and info.status in (eg.EgressStatus.EGRESS_FAILED, eg.EgressStatus.EGRESS_ABORTED)
        b.ended_at = datetime.now(timezone.utc)
        if lost:
            b.status, b.error = "failed", "Служба трансляций перестала отвечать во время эфира."
        elif failed:
            b.status, b.error = "failed", ((info.error if info is not None and info.error else "") or "Эфир прерван службой Egress")[:290]
        else:
            b.status = "ended"
            if info is not None and info.status == eg.EgressStatus.EGRESS_LIMIT_REACHED:
                b.error = "Достигнут предел длительности эфира."
        b.peak_viewers = max(b.peak_viewers, await self.viewers(b.room_id))
        await db.commit()
        meeting = await db.get(Meeting, b.meeting_id)
        if meeting is not None:
            await self._announce(meeting, False, "egress")
            if b.status == "failed":
                self._emit("live_failed", meeting, level="error", error=b.error)

    async def on_egress_event(self, db, event) -> None:
        info = event.egress_info
        if not info or not info.egress_id:
            return
        b = (await db.execute(select(Broadcast).where(Broadcast.egress_id == info.egress_id))).scalars().first()
        if b is None:
            return
        if self.destinations is not None:
            await self.destinations.on_info(db, b, info)
        if event.event == "egress_ended" or info.status not in ACTIVE_LK:
            await self.finalize(db, b, info)

    # ------------------------------------------------------------------ сверка (каждые 10 с из reaper) и уборка
    async def reconcile(self, db) -> int:
        closed = 0
        rows = list((await db.execute(select(Broadcast).where(Broadcast.status.in_(LIVE_ACTIVE)))).scalars())
        cfg = await self.cfg(db) if rows else None
        for b in rows:
            meeting = await db.get(Meeting, b.meeting_id)
            try:
                info = await self.api.get(b.egress_id) if b.egress_id else None
            except Exception:  # noqa: BLE001 — LiveKit недоступен: решения откладываем
                continue
            age = (datetime.now(timezone.utc) - b.started_at).total_seconds()
            if info is not None and info.status in ACTIVE_LK:
                if b.status == "starting" and (self.hls_ready(b) or (not b.hls_dir and info.status == eg.EgressStatus.EGRESS_ACTIVE)):
                    b.status, b.live_at = "live", datetime.now(timezone.utc)
                    await db.commit()
                    if meeting is not None:
                        await self._announce(meeting, True, "egress")
                if self.destinations is not None:
                    await self.destinations.on_info(db, b, info)
                if b.status in ("starting", "live") and meeting is not None and meeting.ended_at is None and age > STALL_GRACE_S \
                        and await self.api.recorder_present(meeting.livekit_room, b.egress_id) is False:
                    log.warning("Рекордер эфира пропал из комнаты — служба Egress, вероятно, упала", extra={"broadcast": str(b.id)})
                    try:
                        await asyncio.wait_for(self.api.stop(b.egress_id), 10)
                    except Exception:  # noqa: BLE001
                        pass
                    await self.finalize(db, b, None, lost=True)
                    closed += 1
                    continue
                reason = ""
                if meeting is None or meeting.ended_at is not None:
                    reason = "meeting_end"
                elif cfg is not None and age > cfg.max_hours * 3600:
                    reason = "limit"
                elif meeting.empty_since is not None and (datetime.now(timezone.utc) - meeting.empty_since).total_seconds() > EMPTY_GRACE_S:
                    reason = "empty"
                elif b.status == "ending" and not await self._r.get(f"live:ending:{b.id}"):
                    reason = "stuck"
                if reason and b.status != "ending":
                    try:
                        await self.stop(db, meeting, by=reason, room_id=b.room_id)
                    except LiveError:
                        pass
                elif reason == "stuck":
                    try:
                        await asyncio.wait_for(self.api.stop(b.egress_id), 10)
                    except Exception:  # noqa: BLE001
                        pass
                    await self.finalize(db, b, None)
                    closed += 1
                continue
            await self.finalize(db, b, info)                 # Egress закончил или его нет в списке
            closed += 1
        await self._sweep(db)
        return closed

    async def _sweep(self, db) -> None:
        """Диск: старые сегменты идущего эфира, каталоги завершённых эфиров, ссылки доступа. Не чаще раза в минуту."""
        if not await self._r.set("live:sweep", "1", nx=True, ex=SWEEP_EVERY_S):
            return
        cfg = await self.cfg(db)
        now = time.time()
        base = self.live_dir() / "b"
        if not base.is_dir():
            return
        rows = {b.hls_dir: b for b in (await db.execute(select(Broadcast).where(Broadcast.hls_dir.is_not(None)))).scalars()}
        for d in list(base.iterdir()):
            b = rows.get(d.name)
            try:
                if b is not None and b.status in LIVE_ACTIVE:
                    keep = cfg.segment_keep_min * 60
                    for f in d.glob("seg_*.ts"):
                        if now - f.stat().st_mtime > keep:
                            f.unlink(missing_ok=True)
                    continue
                ended = b.ended_at if b is not None else None
                age = (datetime.now(timezone.utc) - ended).total_seconds() if ended else (now - d.stat().st_mtime)
                if b is None and age < 3600:
                    continue                              # каталог без строки в БД (запуск ещё не записан): не трогаем в первый час
                if age > cfg.after_end_keep_min * 60:
                    shutil.rmtree(d, ignore_errors=True)
                    if b is not None:
                        b.hls_cleaned = True
            except OSError:
                continue
        acc = self.live_dir() / "acc"
        if acc.is_dir():
            for link in list(acc.iterdir()):
                try:
                    if link.is_symlink() and not link.exists():
                        link.unlink(missing_ok=True)         # каталог эфира удалён — ссылка бессмысленна
                except OSError:
                    continue
        await db.commit()

    def _emit(self, name: str, meeting: Meeting | None, *, level: str = "info", **extra) -> None:
        if self.journal is not None and meeting is not None:
            try:
                self.journal.emit("storage", name, level=level, meeting_id=str(meeting.id), message=str(extra.get("message") or extra.get("error") or "")[:300] or None, data=extra or None)
            except Exception:  # noqa: BLE001
                pass
