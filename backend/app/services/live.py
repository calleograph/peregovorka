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
import re
import secrets
import shutil
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from livekit.protocol import egress as eg
from livekit.protocol import models as lkm
from sqlalchemy import func, select

from ..config import Settings
from ..models import Broadcast, Meeting, Recording, Room
from . import events, media_checks, rec_scene, roles
from .live_destinations import RELAY_KEY

LOW_KEY = "live:low:{}"                 # зрителю нужно низкое качество: пока ключ жив, процесс «b-…» в службе ретрансляции держится
LOW_HOLD_S = 90
LOW_NEED_KBPS = 720                     # сколько нужно зрителю для низкого качества: 450 кбит/с видео + звук + запас
LOW_HEIGHT = 360


def low_useful(quality: str) -> bool:
    """Низкое качество (перекодирование в 360p) имеет смысл, только если сам эфир выше 360p: иначе это тот же размер, а сервер зря тратит около ядра процессора."""
    return LIVE_QUALITY.get(quality, LIVE_QUALITY["720p"])[1] > LOW_HEIGHT
from .livekit import list_present_identities, remove_viewers
from .storage import meeting_relpath
from .video_rec import ACTIVE_LK, QUALITY, STALL_GRACE_S, EgressApi, VideoError, VideoRecorder

log = logging.getLogger("app.live")

LIVE_ACTIVE = ("starting", "live", "ending")
# Битрейт эфира выше, чем у записи: площадки требуют не меньше (YouTube, H.264: 3 Мбит/с для 720p и 5 Мбит/с для 1080p) и допускают не больше (VK Видео: 720p 1500–4000, 1080p 3000–6000 кбит/с).
# 1440p (2K) — для собственного просмотра на больших экранах: мелкий текст демонстрации остаётся читаемым; 9 Мбит/с (YouTube: 9–18 Мбит/с для 1440p30).
# 4K не предлагается: браузер-компоновщик Egress на 3840×2160 требует нескольких ядер на эфир и 20+ Мбит/с на зрителя, а источники (камеры, показ экрана
# с 1080p-мониторов) такой чёткости не дают — замеры в docs/LIVE_BROADCAST.md.
LIVE_QUALITY = {"360p": (640, 360, 1000), "720p": (1280, 720, 3000), "1080p": (1920, 1080, 5000), "1440p": (2560, 1440, 9000)}   # 360p — для слабых каналов и экономии трафика
# Ключевой кадр раз в 2 с: столько просят VK, Twitch и YouTube. При выходе HLS Egress v1.15 запрашивает ключевой кадр на границе сегмента, поэтому интервал задаётся длиной
# сегмента (целое число секунд: Egress берёт int()). При 1 с выходило 1,53 с, и VK показывал «Неверные настройки». Задержку LL-HLS это не меняет (2,86 с), обычный HLS отстаёт чуть больше.
KEY_FRAME_S = 2.0
EMPTY_GRACE_S = 90                     # комната пуста дольше — эфир заканчивается (выступающие ушли)
BROADCAST_EMPTY_GRACE_S = 900          # комната трансляции: обрыв связи ведущего или перезапуск приложения — эфир и запись ждут его 15 минут
START_LOCK_S = 150                     # дольше таймаута запроса к Egress (60 с) и подготовки: иначе второй запуск проходит мимо замка
START_RETRIES = 2                      # столько раз запуск повторяется сам, если браузер Egress не поднялся: обычно это мгновенный дефицит процессора
RETRY_PAUSE_S = 4.0
STOP_GRACE_S = 180                     # после остановки столько ждём итог Egress; потом закрываем сами
SECRET_GID = 10001                     # общая группа backend (пишет секрет подписи HLS) и web (nginx читает): group_add в deployment/compose.yml
SECRET_MODE = 0o640
HLS_FILES = ("index.m3u8",)            # единственный плейлист, который отдаётся зрителям (event.m3u8 — полный, внутренний)
TMP_RESERVE = 1024**3                  # запас сверх копии файла: Egress пишет запись на том трансляций и только в конце копирует на том записей
SWEEP_EVERY_S = 60                     # уборка сегментов не чаще раза в минуту


async def purge_audience(db, meeting_ids: list) -> int:
    """Вопросы зрителей и их контакты (имя, почта) по эфирам этих встреч — вместе с текстовыми материалами встречи (сроки хранения, удаление встречи);
    почта гостей встречи стирается тогда же. Число удалённых вопросов."""
    from sqlalchemy import delete, update  # noqa: PLC0415

    from ..models import GuestParticipant, LiveQuestion, LiveViewer  # noqa: PLC0415

    if not meeting_ids:
        return 0
    bids = select(Broadcast.id).where(Broadcast.meeting_id.in_(meeting_ids))
    n = (await db.execute(delete(LiveQuestion).where(LiveQuestion.broadcast_id.in_(bids)))).rowcount or 0
    await db.execute(delete(LiveViewer).where(LiveViewer.broadcast_id.in_(bids)))
    await db.execute(update(GuestParticipant).where(GuestParticipant.meeting_id.in_(meeting_ids), GuestParticipant.email.is_not(None))
                     .values(email=None, materials_consent=False))
    return n


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
# Сообщения Egress — для разработчика. Здесь то, что администратор может проверить и сделать; исходный текст остаётся в журнале эфира.
LAUNCH_FAILED = ("websocket url timeout", "failed to launch chrome", "page load error", "start signal not received", "chrome")
EXPLAIN = (
    (LAUNCH_FAILED[:3], "Браузер службы трансляций не запустился вовремя: у службы не хватило процессора или памяти. Проверьте нагрузку сервера и повторите запуск."),
    (("pipeline frozen",), "Кодирование эфира остановилось: службе не хватало процессора. Снизьте качество эфира или разгрузите сервер."),
    (("start signal not received",), "Страница эфира не начала запись вовремя: проверьте, что сервер звонков доступен службе трансляций."),
    (("deadlineexceeded", "handler_ipc"), "Служба трансляций перестала отвечать: сервер перегружен или процесс завершился аварийно."),
)


def launch_failed(error: str | None) -> bool:
    e = (error or "").lower()
    return any(m in e for m in LAUNCH_FAILED[:4])


def explain_error(error: str | None) -> str:
    """Понятная причина. Сырой текст ошибки Egress может содержать адреса с ключами площадок: наружу идёт только наша формулировка, а неизвестная ошибка — обезличенной."""
    e = (error or "").lower()
    for marks, text in EXPLAIN:
        if any(m in e for m in marks):
            return text
    return re.sub(r"rtmps?://\S+", "rtmp://…", error or "Эфир прерван службой Egress")[:290]


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
                                                              live_playlist_name=f"{hls_prefix}/index.m3u8", segment_duration=int(KEY_FRAME_S),
                                                              # метка времени вместо номера: у постоянной «потоковой ссылки» и в кеше CDN сегмент прошлого эфира не совпадёт по имени с новым
                                                              filename_suffix=eg.SegmentedFileSuffix.TIMESTAMP, disable_manifest=True)))
    outs.append(eg.Output(stream=eg.StreamOutput(protocol=eg.StreamProtocol.RTMP, urls=urls)))
    return eg.StartEgressRequest(room_name=room_name, template=tpl, advanced=enc, outputs=outs)


class LiveBroadcaster:
    def __init__(self, settings: Settings, session_maker, redis, settings_svc, *, api: EgressApi, video: VideoRecorder, journal=None, destinations=None):
        self._s, self._sm, self._r, self._svc = settings, session_maker, redis, settings_svc
        self.api, self.video, self.journal = api, video, journal
        self.destinations = destinations           # services.live_destinations.Destinations (внешние площадки); задаётся при запуске приложения
        self.diag = None                           # services.live_diag.LiveDiag: журнал этапов и причин; задаётся при запуске приложения
        self.on_air = None                         # (db, meeting, идёт ли эфир) → распознавание и запись комнаты трансляции; задаётся при запуске приложения
        video.live_owner = self.owns_egress

    # ------------------------------------------------------------------ каталоги
    def live_dir(self) -> Path:
        return Path(os.environ.get("LIVE_DIR") or str(Path(self._s.data_dir) / "live"))

    def egress_dir(self) -> str:
        return (os.environ.get("LIVE_EGRESS_DIR") or "/live").rstrip("/")

    def prepare(self) -> str | None:
        """Каталоги тома и файл с секретом для nginx (0640, группа SECRET_GID — её же получает web через group_add: прочие локальные пользователи файл не читают).
        Замена атомарная (временный файл в том же каталоге → rename); прежний секрет не меняется — он выводится из главного ключа и остаётся тем же.
        Применяет секрет в nginx сам web (live-secret.sh: nginx -t и перечитывание). Возвращает None или причину отказа (для диагностики)."""
        base = self.live_dir()
        try:
            for sub in ("b", "acc", "nginx", "diag"):
                (base / sub).mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            log.warning("Каталог трансляций недоступен для записи: HLS работать не будет", extra={"path": str(base)})
            return f"каталог {base} недоступен: {exc.strerror or exc}"
        conf = base / "nginx" / "secret.conf"
        text = nginx_secret_conf(hls_secret(self._s))
        try:
            st = conf.stat() if conf.exists() else None
            if st is None or conf.read_text() != text or (st.st_mode & 0o777) != SECRET_MODE or st.st_gid != SECRET_GID:
                tmp = conf.with_name(f".secret.{os.getpid()}.tmp")
                fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)       # до назначения группы файл никому, кроме backend, не виден
                try:
                    os.write(fd, text.encode())
                    os.fsync(fd)
                finally:
                    os.close(fd)
                try:
                    os.chown(tmp, -1, SECRET_GID)
                except OSError:
                    pass                                  # нет группы 10001 у процесса (старый compose): каталог с setgid даёт её сам — проверит диагностика
                os.chmod(tmp, SECRET_MODE)
                os.replace(tmp, conf)
        except OSError as exc:
            log.warning("Не удалось записать секрет подписи HLS для nginx", extra={"path": str(conf)})
            return f"{conf} не записан: {exc.strerror or exc}"
        return None

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
        """Отзыв: новый идентификатор доступа и (для страницы) новый токен; ссылка со старым идентификатором удаляется — адреса перестают открываться сразу.
        Отзыв ссылки зрителя закрывает и «Ссылку этого эфира» идущего эфира: иначе по ней плеер получил бы подпись к новому идентификатору доступа."""
        cur = await self.current(db, room.id)
        if which in ("view", "all"):
            old_view = room.live_view_access
            self.unlink_access(room.live_view_access)
            room.live_view_access, room.live_token = new_access(), new_token()
            if cur is not None:
                cur.view_token = new_token()
            await self._switch_ll(cur, old_view, room.live_view_access)
        if which in ("stream", "all"):
            self.unlink_access(room.live_stream_access)
            room.live_stream_access = new_access()
        await db.commit()
        if cur is not None:
            self.link_access(room.live_view_access if which in ("view", "all") else None, cur.hls_dir)
            self.link_access(room.live_stream_access if which in ("stream", "all") else None, cur.hls_dir)

    async def rotate_view_access(self, db, room: Room) -> None:
        """Смена пароля зрителя: страница остаётся той же, но выданные подписи и «пропуска» перестают действовать (новый идентификатор доступа)."""
        old = room.live_view_access
        self.unlink_access(room.live_view_access)
        room.live_view_access = new_access()
        await db.commit()
        cur = await self.current(db, room.id)
        if cur is not None:
            self.link_access(room.live_view_access, cur.hls_dir)
            await self._switch_ll(cur, old, room.live_view_access)

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
        ends_at = b.started_at + timedelta(hours=cfg.max_hours + await self._extra_h(b.id)) if live and b else None
        out = {"ends_at": ends_at, "enabled": bool(cfg.enabled and room.live_enabled), "available": bool(cfg.enabled), "live": live, "status": b.status if b else "idle",
               "since": b.started_at if live and b else None, "quality": room.live_quality, "record": room.live_record, "own_hls": room.live_own_hls,
               "viewers": await self.viewers(room.id) if live else 0, "error": b.error if b else None, "broadcast_id": str(b.id) if b else None,
               "hls_ready": self.hls_ready(b), "limit": int(cfg.max_concurrent), "cdn": bool(((cfg.cdn_enabled and cfg.cdn_base_url) or cfg.cdn_nodes_enabled) and room.live_cdn), "can_start": bool(cfg.enabled and room.live_enabled and not live)}
        if self.destinations is not None:
            out["destinations"] = await self.destinations.live_state(db, room, b)
        return out

    async def _extra_h(self, bid) -> int:
        return int(await self._r.get(f"live:extra:{bid}") or 0)

    async def extend(self, db, room: Room) -> datetime:
        """Продлить идущий эфир на час сверх предела `max_hours` (ведущий видит предупреждение перед пределом); всего — не больше 48 часов."""
        cfg, b = await self.cfg(db), await self.current(db, room.id)
        if b is None or b.status not in ("starting", "live"):
            raise LiveError("not_live", "Эфир не идёт.")
        key = f"live:extra:{b.id}"
        if cfg.max_hours + await self._extra_h(b.id) + 1 > 48:
            raise LiveError("extend_limit", "Эфир уже продлён до предела — 48 часов.")
        extra = await self._r.incr(key)
        await self._r.expire(key, 3 * 86400)
        return b.started_at + timedelta(hours=cfg.max_hours + int(extra))

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
            raise LiveError("live_busy", f"Сейчас идёт максимально допустимое число эфиров ({cfg.max_concurrent}). Повторите позже "
                                         "или обратитесь к администратору.")
        load = await self.api.cpu_load(cfg.egress_health_url) if hasattr(self.api, "cpu_load") else None
        if load is not None and load >= cfg.max_cpu_load:
            raise LiveError("server_busy", f"Сервер трансляций сейчас загружен ({load}% процессора при пределе {cfg.max_cpu_load}%): эфир не начат, чтобы не ухудшить обычные конференции. Повторите позже "
                                          "или обратитесь к администратору.")
        base = self.live_dir()
        free = None
        try:
            free = shutil.disk_usage(base if base.exists() else base.parent).free
        except OSError:
            pass
        if free is not None and free < cfg.min_free_gb * 1024**3:
            raise LiveError("disk_low", f"Мало свободного места на томе трансляций (меньше {cfg.min_free_gb} ГБ): эфир не начат.")

    # ------------------------------------------------------------------ старт и остановка
    async def _record_guard(self, db) -> None:
        """Запись эфира обязательна: если её нельзя начать (нет места, том записей недоступен для записи, исчерпан объём видеозаписей), эфир не начинается вовсе —
        чтобы не было эфира, который идёт, а запись тихо не пишется."""
        vcfg = await self._svc.get(db, "video")
        root = Path(self._s.recordings_path)
        try:
            free = shutil.disk_usage(root if root.exists() else root.parent).free
        except OSError:
            free = None
        if free is not None and free < vcfg.min_free_gb * 1024**3:
            raise LiveError("record_disk_low", f"Запись эфира невозможна: на томе записей меньше {vcfg.min_free_gb} ГБ свободного места. Эфир не начат — освободите место или обратитесь к администратору.")
        if root.exists() and not os.access(root, os.W_OK):
            raise LiveError("record_unwritable", "Запись эфира невозможна: каталог записей недоступен для записи. Эфир не начат — сообщите администратору (Администрирование → Обзор).")
        if vcfg.quota_gb:
            used = (await db.execute(select(func.coalesce(func.sum(Recording.size_bytes), 0)).where(Recording.kind == "mix_video"))).scalar_one()
            if used >= vcfg.quota_gb * 1024**3:
                raise LiveError("record_quota", f"Запись эфира невозможна: достигнут предел объёма видеозаписей ({vcfg.quota_gb} ГБ). Эфир не начат.")

    @staticmethod
    def options(room: Room, cfg, opts: dict | None = None) -> dict:
        """Параметры этого эфира: значения комнаты, которые ведущий может поменять при запуске (название, показ записи, скачивание, срок ссылки). Срок — в пределах администратора."""
        o = opts or {}
        title = o.get("title") if isinstance(o.get("title"), str) else room.live_title
        days = o.get("link_days") if isinstance(o.get("link_days"), int) and not isinstance(o.get("link_days"), bool) else room.live_link_days
        return {"title": ((title or "").strip()[:200]) or None,
                "replay": o["replay"] if isinstance(o.get("replay"), bool) else room.live_replay,
                "download": o["download"] if isinstance(o.get("download"), bool) else room.live_download,
                "link_days": max(1, min(int(days or 5), int(cfg.replay_days_max))),
                "quality": o["quality"] if o.get("quality") in LIVE_QUALITY else None}           # качество только этого эфира; нет — как в настройках комнаты

    async def start(self, db, meeting: Meeting, *, by: str = "", opts: dict | None = None) -> Broadcast:
        lock = await self._r.set(f"live:start:{meeting.id}", "1", nx=True, ex=START_LOCK_S)
        if not lock:
            raise LiveError("live_starting", "Эфир уже запускается.")
        try:
            room = meeting.room
            cur = await self.current(db, room.id)
            if cur is not None and cur.status in ("starting", "live"):
                return cur                                   # повторное нажатие — не второй эфир
            cfg = await self.cfg(db)
            urls, _ids = await self.destinations.start_urls(db, room) if self.destinations is not None else ([], [])
            await self._guards(db, meeting, cfg, urls)
            relay_src = self.relay_src(meeting) if await self._relay_ready() else None      # площадки вне Egress: сбой одной не останавливает ни эфир, ни запись
            if relay_src:
                urls = []
            t0 = time.monotonic()
            present = await list_present_identities(self._s, meeting.livekit_room)
            if present is None:
                raise LiveError("livekit_unavailable", "Сервер звонков недоступен: эфир не начат.", 503)
            if not present:
                raise LiveError("room_empty", "В комнате никого нет: начинать эфир не с кого.")
            await self._record_guard(db)
            await self.ensure_links(db, room)
            self.unlink_access(room.live_view_access)      # подпись зрителя живёт дольше эфира: новый идентификатор на каждый эфир, чтобы прошлая подпись не открыла следующий
            room.live_view_access = new_access()
            hls_dir = new_access() if room.live_own_hls else None
            # качество эфира (и его записи: она идёт в том же процессе Egress): выбрано при запуске → настройка комнаты «Трансляция» → умолчание сервера. Качество видеозаписи встреч
            # (rooms.video_quality, 720p) к эфиру не относится.
            quality = self.options(room, cfg, opts)["quality"] or (room.live_quality if room.live_quality in LIVE_QUALITY else cfg.default_quality)
            # Запись каждого эфира обязательна и входит в этот же процесс Egress (MP4 рядом с HLS и площадками): отдельную видеозапись комнаты закрываем,
            # чтобы не гонять второй браузер. Прежняя настройка «сохранять запись» (rooms.live_record) больше не отключает запись.
            if await self.video.active_rows(db, meeting.id):
                await self.video.stop(db, meeting, by="live")
            n = (await db.execute(select(func.count()).select_from(Recording).where(Recording.meeting_id == meeting.id, Recording.kind == "mix_video"))).scalar_one() + 1
            tz = await self.video._tz(db)  # noqa: SLF001
            rel = f"{meeting_relpath(room.name, meeting.started_at.astimezone(tz), meeting.id)}/video-{n}.mp4"
            vcfg = await self._svc.get(db, "video")
            # раскладка — та же общая сцена, что у видеозаписи (показ экрана, доска, выступающие): страница-рекордер приложения, если она не выключена
            req = await self._request(meeting, room, quality, rel, hls_dir, urls, cfg, vcfg, relay_src=relay_src)
            try:
                info = await self.api.start(req)
            except Exception as exc:  # noqa: BLE001
                log.warning("Не удалось запустить Egress для эфира", extra={"meeting_id": str(meeting.id), "error": str(exc)[:300]})
                raise LiveError("egress_unavailable", "Служба трансляций (LiveKit Egress) недоступна или не установлена. Сообщите администратору: Администрирование → Видеозапись и Трансляции.", 503) from exc
            now = datetime.now(timezone.utc)
            o = self.options(room, cfg, opts)
            rec = Recording(meeting_id=meeting.id, room_id=meeting.room_id, participant_identity="meeting", path=rel, size_bytes=0, kind="mix_video", mime="video/mp4",
                            status="processing", has_video=True, egress_id=info.egress_id, started_at=now)
            db.add(rec)
            await db.flush()
            b = Broadcast(room_id=room.id, meeting_id=meeting.id, status="starting", egress_id=info.egress_id, hls_dir=hls_dir, quality=quality, started_by=by[:200] or None, started_at=now,
                          view_token=new_token(), title=o["title"], password_hash=room.live_password_hash if room.live_password_enabled else None,
                          replay=o["replay"], download=o["download"], link_days=o["link_days"], recording_id=rec.id)
            db.add(b)
            meeting.record_video = True
            await db.commit()
            if relay_src:
                await self._r.set(RELAY_KEY.format(b.id), relay_src, ex=172800)
                await self.destinations.relay_sync(db, b, room)
                await self.audio_sync(db, b, room)
            self.link_access(room.live_view_access, hls_dir)
            self.link_access(room.live_stream_access, hls_dir)
            await self.stage(b, "accepted", "Служба трансляций приняла задание", time.monotonic() - t0)
            await self._announce(meeting, True, by)
            self._emit("live_started", meeting, by=by, egress=info.egress_id, quality=quality, urls=len(urls), hls=bool(hls_dir), record=True)
            await self._air(db, meeting, True)
            return b
        finally:
            await self._r.delete(f"live:start:{meeting.id}")

    async def _request(self, meeting: Meeting, room: Room, quality: str, rel: str, hls_dir: str | None, urls: list[str], cfg, vcfg, *, relay_src: str | None = None) -> eg.StartEgressRequest:
        # раскладка — та же общая сцена, что у видеозаписи (показ экрана, доска, выступающие): страница-рекордер приложения, если она не выключена
        tpl = vcfg.template_url or (f"{self._s.recorder_base_url.rstrip('/')}/rec/template?rt={rec_scene.make_token(self._s, meeting.id)}" if vcfg.scene_template and self._s.recorder_base_url else "")
        out = [*urls, self.ll_url(room.live_view_access)] if hls_dir and cfg.ll_hls else list(urls)
        if relay_src:
            out.append(relay_src)                       # единственный выход Egress наружу: локальный поток MediaMTX, откуда площадки читает служба ретрансляции
        return build_live_request(meeting.livekit_room, quality, mp4_path=f"{self.video.out_dir()}/{rel}", hls_prefix=f"{self.egress_dir()}/b/{hls_dir}" if hls_dir else None, urls=out, template_url=tpl)

    def relay_src(self, meeting: Meeting) -> str:
        """Локальный поток для службы ретрансляции. Имя от встречи, а не от ссылки зрителя: отзыв ссылки во время эфира не обрывает поток на площадку."""
        return f"{self._s.lowlatency_rtmp_url.rstrip('/')}/r-{meeting.id.hex}"

    async def _relay_ready(self) -> bool:
        """Служба ретрансляции отвечает и локальный приёмник потока (MediaMTX) принимает соединения. Иначе площадки подключаются прямо к Egress, как раньше."""
        relay = getattr(self.destinations, "relay", None)
        if relay is None or not await relay.available():
            return False
        m = re.match(r"^rtmps?://([^/:]+)(?::(\d+))?", self._s.lowlatency_rtmp_url)
        if not m:
            return False
        try:
            _r, w = await asyncio.wait_for(asyncio.open_connection(m.group(1), int(m.group(2) or 1935)), 1.5)
        except (OSError, asyncio.TimeoutError):
            return False
        w.close()
        return True

    def audio_url(self, access: str) -> str:
        """Дорожка только со звуком для зрителей со слабой связью: отдельный поток MediaMTX рядом с основным (имя — идентификатор доступа с суффиксом)."""
        return f"{self.ll_url(access)}-a"

    async def audio_sync(self, db, b: Broadcast, room: Room) -> None:
        """Процесс «только звук» в службе ретрансляции (ffmpeg -vn -c:a copy). Идемпотентно: сверка раз в 10 с возвращает его после перезапуска службы."""
        relay = getattr(self.destinations, "relay", None)
        src = await self.destinations.relay_src(b) if self.destinations is not None else None
        if relay is None or src is None or not room.live_view_access:
            return
        cfg = await self.cfg(db)
        rid = f"a-{b.id}"
        if not cfg.adaptive_viewers:
            await relay.delete(rid)
            return
        from ..relay.manager import fingerprint  # noqa: PLC0415

        url = self.audio_url(room.live_view_access)
        st = await relay.status()
        if st is not None and (st.get(rid) or {}).get("fp") != fingerprint(src, url, True):
            await relay.put(rid, src, url, audio=True)

    def low_url(self, access: str) -> str:
        """Низкое качество (360p) для зрителей, которым не хватает канала: ещё один поток MediaMTX рядом с основным, суффикс «-l»."""
        return f"{self.ll_url(access)}-l"

    async def low_hold(self, broadcast_id: uuid.UUID) -> None:
        """Кому-то из зрителей нужно низкое качество: процесс держится ещё 90 с после последнего запроса (пульс зрителя и запрос ссылки продлевают)."""
        await self._r.set(LOW_KEY.format(broadcast_id), "1", ex=LOW_HOLD_S)

    async def low_sync(self, db, b: Broadcast, room: Room) -> None:
        """Процесс низкого качества в службе ретрансляции — только пока он кому-то нужен: перекодирование стоит процессорного времени, пустой запуск не нужен.
        Идемпотентно; сбой процесса не касается ни эфира, ни записи, ни площадок (служба ретрансляции отдельная)."""
        relay = getattr(self.destinations, "relay", None)
        src = await self.destinations.relay_src(b) if self.destinations is not None else None
        if relay is None or src is None or not room.live_view_access:
            return
        rid = f"b-{b.id}"
        cfg = await self.cfg(db)
        if not cfg.adaptive_viewers or not await self._r.get(LOW_KEY.format(b.id)):
            await relay.delete(rid)
            return
        from ..relay.manager import fingerprint  # noqa: PLC0415

        url = self.low_url(room.live_view_access)
        st = await relay.status()
        if st is not None and (st.get(rid) or {}).get("fp") != fingerprint(src, url, False, True):
            await relay.put(rid, src, url, low=True)

    async def audio_ready(self, access: str, suffix: str = "a") -> bool:
        """Дорожка (suffix «a» — только звук, «l» — низкое качество) отвечает. Один сорванный запрос не снимает предложение: после успеха 60 с отвечаем «да», иначе у зрителей кнопка мигала бы."""
        now = time.monotonic()
        key = f"{access}-{suffix}"
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(1.5)) as c:
                ok = (await c.get(f"{self._s.lowlatency_hls_url.rstrip('/')}/{key}/index.m3u8?cookieCheck=1", headers={"Cookie": "cookieCheck=1"})).status_code == 200
        except httpx.HTTPError:
            ok = False
        if ok:
            self._audio_seen = {k: v for k, v in getattr(self, "_audio_seen", {}).items() if now - v < 600} | {key: now}
            return True
        return now - getattr(self, "_audio_seen", {}).get(key, -1e9) < 60

    def ll_url(self, access: str) -> str:
        """Внутренний выход для LL-HLS. Имя потока — идентификатор доступа: так подпись зрителя и отзыв работают как у обычного HLS."""
        return f"{self._s.lowlatency_rtmp_url.rstrip('/')}/{access}"

    async def ll_ready(self, access: str) -> bool:
        """LL-HLS отвечает только после семи сегментов (около 15 с от начала эфира). До этого зритель смотрит обычный HLS, а плеер потом переходит сам."""
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(1.0)) as c:
                return (await c.get(f"{self._s.lowlatency_hls_url.rstrip('/')}/{access}/index.m3u8?cookieCheck=1", headers={"Cookie": "cookieCheck=1"})).status_code == 200
        except httpx.HTTPError:
            return False

    async def _switch_ll(self, b: Broadcast | None, old: str | None, new: str | None) -> None:
        """Отзыв ссылки или смена пароля во время эфира: поток LL-HLS переезжает на новое имя, прежние подписи перестают открывать его. Запись и площадки это не затрагивает."""
        if b is None:
            return
        meeting = await self._livekit_room(b)
        if meeting:
            await remove_viewers(self._s, meeting)
        if not b.egress_id or not b.hls_dir or not old or not new or not (await self.cfg_ll()):
            return
        try:
            await self.api.update_stream(b.egress_id, add=[self.ll_url(new)], remove=[self.ll_url(old)])
        except Exception as exc:  # noqa: BLE001 — зритель останется на обычном HLS, эфир важнее
            log.warning("Поток низкой задержки не переключён", extra={"broadcast": str(b.id), "error": type(exc).__name__})
        relay, src = getattr(self.destinations, "relay", None), await self.destinations.relay_src(b)
        if relay is not None and src is not None:
            await relay.put(f"a-{b.id}", src, self.audio_url(new), audio=True)         # дорожка «только звук» переезжает на новое имя вместе с основной
            await relay.delete(f"b-{b.id}")                                            # низкое качество поднимется само на новом имени, когда оно снова понадобится

    async def _livekit_room(self, b: Broadcast) -> str | None:
        async with self._sm() as db:
            m = await db.get(Meeting, b.meeting_id)
            return m.livekit_room if m is not None else None

    async def cfg_ll(self) -> bool:
        async with self._sm() as db:
            return bool((await self.cfg(db)).ll_hls)

    def _first_segment_s(self, b: Broadcast) -> float | None:
        """Сверка идёт раз в 10 с, поэтому время появления берётся по самому файлу."""
        try:
            first = min(f.stat().st_mtime for f in (self.live_dir() / "b" / b.hls_dir).glob("seg_*.ts")) if b.hls_dir else None
        except (OSError, ValueError):
            return None
        return first - b.started_at.timestamp() if first else None

    async def stage(self, b: Broadcast | uuid.UUID, name: str, text: str, seconds: float | None = None, *, level: str = "info") -> None:
        """Этап запуска в журнал эфира: панель диагностики показывает их лентой со временем."""
        if self.diag is not None:
            await self.diag.event(b, "broadcast", level, f"stage_{name}", text + (f" за {seconds:.1f} с" if seconds is not None else ""), details={"s": round(seconds, 2)} if seconds is not None else None)

    async def _retry_launch(self, db, b: Broadcast, info: eg.EgressInfo | None) -> bool:
        """Браузер Egress не поднялся за 20 с: при кратком дефиците процессора следующая попытка удаётся. Зритель и ведущий видят «запускается», запись продолжает ту же строку."""
        if info is None or b.status != "starting" or not launch_failed(info.error):
            return False
        n = await self._r.incr(f"live:retry:{b.id}")
        await self._r.expire(f"live:retry:{b.id}", 600)
        meeting, room, rec = await db.get(Meeting, b.meeting_id), await db.get(Room, b.room_id), await db.get(Recording, b.recording_id) if b.recording_id else None
        if n > START_RETRIES or meeting is None or room is None or rec is None or meeting.ended_at is not None:
            return False
        await self.stage(b, "retry", f"Браузер службы не запустился, попытка {n + 1} из {START_RETRIES + 1}. Причина: {explain_error(info.error)}", level="warn")
        await asyncio.sleep(RETRY_PAUSE_S)
        urls, _ids = await self.destinations.start_urls(db, room) if self.destinations is not None else ([], [])
        relay_src = await self.destinations.relay_src(b) if self.destinations is not None else None
        if relay_src:
            urls = []                                       # эфир идёт через службу ретрансляции: в Egress только локальный поток
        try:
            new = await self.api.start(await self._request(meeting, room, b.quality, rec.path, b.hls_dir, urls, await self.cfg(db), await self._svc.get(db, "video"), relay_src=relay_src))
        except Exception as exc:  # noqa: BLE001
            log.warning("Повторный запуск эфира не удался", extra={"broadcast": str(b.id), "error": str(exc)[:200]})
            return False
        rec.egress_id = b.egress_id = new.egress_id
        await db.commit()
        return True

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
                await self._air(db, m, False)
        return len(rows)

    async def _air(self, db, meeting: Meeting, on: bool) -> None:
        if self.on_air is None:
            return
        try:
            await self.on_air(db, meeting, on)
        except Exception:  # noqa: BLE001 — эфир не зависит от распознавания
            log.exception("Не удалось переключить распознавание по эфиру", extra={"meeting_id": str(meeting.id)})

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
        if failed and await self._retry_launch(db, b, info):
            return
        if failed and self.diag is not None:
            await self.diag.event(b, "egress", "error", "egress_failed", explain_error(info.error), details={"raw": re.sub(r"rtmps?://\S+", "rtmp://…", info.error or "")[:400]}, kind="confirmed")
        b.ended_at = datetime.now(timezone.utc)
        b.access_until = b.ended_at + timedelta(days=max(1, int(b.link_days or 5)))       # срок ссылки на запись — от фактического окончания эфира
        if lost:
            b.status, b.error = "failed", "Служба трансляций перестала отвечать во время эфира."
        elif failed:
            b.status, b.error = "failed", explain_error(info.error if info is not None else None)
        else:
            b.status = "ended"
            if info is not None and info.status == eg.EgressStatus.EGRESS_LIMIT_REACHED:
                b.error = "Достигнут предел длительности эфира."
        b.peak_viewers = max(b.peak_viewers, await self.viewers(b.room_id))
        await db.commit()
        # новый эфир мог начаться, пока этот завершался: тогда его площадки и объявление не трогаем
        if getattr(self.destinations, "relay", None) is not None:
            await self.destinations.relay.delete(f"a-{b.id}")
            await self.destinations.relay.delete(f"b-{b.id}")
        await self._r.delete(RELAY_KEY.format(b.id), LOW_KEY.format(b.id))
        running = await self.current(db, b.room_id)
        if self.destinations is not None and running is None:
            await self.destinations.finish_all(db, b.room_id)
        meeting = await db.get(Meeting, b.meeting_id)
        if meeting is not None and running is None:
            await self._air(db, meeting, False)
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
        if info.status == eg.EgressStatus.EGRESS_ACTIVE and await self._r.set(f"live:stage:{b.egress_id}", "1", nx=True, ex=172800):      # столько живёт эфир: площадка, добавленная через час, не даёт второй этап
            await self.stage(b, "active", "Браузер и кодировщик работают", (datetime.now(timezone.utc) - b.started_at).total_seconds())
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
                if b.hls_dir and b.status in ("starting", "live"):
                    room = await db.get(Room, b.room_id)
                    if room is not None and room.live_view_access and not os.path.lexists(self._acc_dir(room.live_view_access)):
                        log.warning("Ссылка доступа к HLS идущего эфира пропала — восстанавливается", extra={"broadcast": str(b.id)})
                        self.link_access(room.live_view_access, b.hls_dir)
                        self.link_access(room.live_stream_access, b.hls_dir)
                if b.status == "starting" and (self.hls_ready(b) or (not b.hls_dir and info.status == eg.EgressStatus.EGRESS_ACTIVE)):
                    b.status, b.live_at = "live", datetime.now(timezone.utc)
                    await db.commit()
                    await self.stage(b, "live", "Первый сегмент готов, эфир виден зрителям", self._first_segment_s(b))
                    if meeting is not None:
                        await self._announce(meeting, True, "egress")
                if self.destinations is not None:
                    await self.destinations.on_info(db, b, info)
                    if b.status in ("starting", "live"):
                        room_r = await db.get(Room, b.room_id)
                        if room_r is not None:
                            await self.destinations.relay_sync(db, b, room_r)         # площадки вне Egress: состояния и самовосстановление службы
                            await self.audio_sync(db, b, room_r)
                            await self.low_sync(db, b, room_r)
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
                if b.status == "ending" and not await self._r.get(f"live:ending:{b.id}"):
                    reason = "stuck"                  # первым: у завершённой встречи причина «meeting_end» заслонила бы её, и эфир висел бы вечно
                elif meeting is None or meeting.ended_at is not None:
                    reason = "meeting_end"
                elif cfg is not None and age > (cfg.max_hours + await self._extra_h(b.id)) * 3600:
                    reason = "limit"
                elif meeting.empty_since is not None and (datetime.now(timezone.utc) - meeting.empty_since).total_seconds() > (
                        BROADCAST_EMPTY_GRACE_S if roles.is_broadcast(meeting.room) else EMPTY_GRACE_S):
                    reason = "empty"
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

    def tmp_low(self) -> bool:
        """Хватит ли места закончить записи. Egress при нехватке падает, и файл пропадает целиком; остановка заранее оставляет место на его копию."""
        base = self.live_dir()
        return (base / "tmp").is_dir() and shutil.disk_usage(base).free < media_checks.dir_size(base / "tmp") + TMP_RESERVE

    async def _disk_guard(self, db) -> None:
        if not await asyncio.to_thread(self.tmp_low):
            return
        text = "На диске сервера заканчивается место: запись остановлена заранее, чтобы файл сохранился."
        log.error(text)
        for b in list((await db.execute(select(Broadcast).where(Broadcast.status.in_(("starting", "live"))))).scalars()):
            meeting = await db.get(Meeting, b.meeting_id)
            if meeting is not None:
                b.error = text
                try:
                    await self.stop(db, meeting, by="disk", manual=True)
                    self._emit("live_failed", meeting, level="error", error=text)
                except LiveError as exc:
                    log.warning("Не удалось остановить эфир", extra={"meeting_id": str(meeting.id), "error": str(exc)[:300]})
        for rec in await self.video.active_rows(db):
            meeting = await db.get(Meeting, rec.meeting_id)
            if meeting is not None:
                try:
                    await self.video.stop(db, meeting, by="disk")
                    self.video._emit("video_failed", meeting, level="error", error=text)
                except VideoError as exc:
                    log.warning("Не удалось остановить запись", extra={"meeting_id": str(meeting.id), "error": exc.message[:300]})

    async def _sweep(self, db) -> None:
        """Диск: старые сегменты идущего эфира, каталоги завершённых эфиров, ссылки доступа. Не чаще раза в минуту."""
        if not await self._r.set("live:sweep", "1", nx=True, ex=SWEEP_EVERY_S):
            return
        await self._disk_guard(db)
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
                    # каталог эфира удалён — ссылка бессмысленна. Свежую не трогаем: Egress создаёт каталог эфира не сразу (до ~20 с после запуска),
                    # и уборка в этот промежуток удаляла ссылку идущего эфира — зрители получали 404
                    if link.is_symlink() and not link.exists() and now - link.lstat().st_mtime > 3600:
                        link.unlink(missing_ok=True)
                except OSError:
                    continue
        await db.commit()

    def _emit(self, name: str, meeting: Meeting | None, *, level: str = "info", **extra) -> None:
        if self.journal is not None and meeting is not None:
            try:
                self.journal.emit("storage", name, level=level, meeting_id=str(meeting.id), message=str(extra.get("message") or extra.get("error") or "")[:300] or None, data=extra or None)
            except Exception:  # noqa: BLE001
                pass
