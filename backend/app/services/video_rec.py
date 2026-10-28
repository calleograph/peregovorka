"""Видеозапись встреч через LiveKit Egress: ОДИН MP4 (H.264 + AAC) со всей встречей — сетка участников, при показе экрана он занимает основное место,
остальные — миниатюры (шаблон `speaker` служб Egress; свой шаблон задаётся `custom_base_url`).

Видео не зависит от записи аудио: другая кнопка, другая запись (`recordings.kind = mix_video`, `has_video`), свой жизненный цикл. Фрагменты — это несколько записей
одной встречи: остановка и повторный запуск не стирают прежний файл. Файл пишет сам Egress в общий том (`VIDEO_EGRESS_OUT`, по умолчанию /out = каталог записей
backend), поэтому дальше работают те же воспроизведение, скачивание, выгрузка во внешнее хранилище, перенос и копия, что и для звука.

Безопасность и надёжность: видео не пишется при сквозном шифровании (у Egress нет ключа) и при шифровании хранилища комнаты (запись не шифруется на диске Egress);
пустую комнату не пишем; число одновременных записей, свободное место и квота проверяются ДО старта; при сбое Egress остаётся запись со статусом failed и причиной,
а не потерянная запись; после перезапуска `reconcile` сверяет «идущие» записи с Egress."""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import uuid
from zoneinfo import ZoneInfo
from datetime import datetime, timezone
from pathlib import Path

from livekit import api as lkapi
from livekit.protocol import egress as eg
from sqlalchemy import func, select

from ..config import Settings
from ..models import Meeting, Recording, Room
from . import events
from .livekit import EGRESS_IDENTITY_PREFIX, list_present_identities  # noqa: F401
from .storage import meeting_relpath

log = logging.getLogger("app.video")

LAYOUT = "speaker"                          # экран — основное место, остальные — миниатюры; без экрана — говорящий и сетка
# 720p: ≈ 2,5 Мбит/с видео + 128 кбит/с звук (≈ 1,2 ГБ/час); 1080p: ≈ 4,5 Мбит/с (≈ 2,1 ГБ/час). Значения — расчётные, замер на реальных материалах: docs/VIDEO_RECORDING.md
QUALITY = {"720p": (1280, 720, 2500), "1080p": (1920, 1080, 4500)}
ACTIVE_LK = {eg.EgressStatus.EGRESS_STARTING, eg.EgressStatus.EGRESS_ACTIVE, eg.EgressStatus.EGRESS_ENDING}
DONE_OK = eg.EgressStatus.EGRESS_COMPLETE
STOP_GRACE_S = 180                          # после остановки ждём итог Egress (webhook) столько; потом сверяем сами


class VideoError(Exception):
    def __init__(self, code: str, message: str, status: int = 409):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


class EgressApi:
    """Тонкая обёртка над API LiveKit (подменяется в тестах)."""

    def __init__(self, settings: Settings):
        self._s = settings

    def _lk(self) -> lkapi.LiveKitAPI:
        return lkapi.LiveKitAPI(self._s.livekit_http_url, self._s.livekit_api_key, self._s.livekit_api_secret)

    async def start(self, req: eg.StartEgressRequest) -> eg.EgressInfo:
        async with self._lk() as lk:
            return await lk.egress.start_egress(req)

    async def stop(self, egress_id: str) -> eg.EgressInfo:
        async with self._lk() as lk:
            return await lk.egress.stop_egress(lkapi.StopEgressRequest(egress_id=egress_id))

    async def get(self, egress_id: str) -> eg.EgressInfo | None:
        async with self._lk() as lk:
            resp = await lk.egress.list_egress(lkapi.ListEgressRequest(egress_id=egress_id))
        return resp.items[0] if resp.items else None

    async def probe(self) -> None:
        """Достаточно ли связи с LiveKit, чтобы управлять записью (сама служба Egress этим не проверяется)."""
        async with self._lk() as lk:
            await lk.egress.list_egress(lkapi.ListEgressRequest(active=True))


def build_request(room_name: str, out_path: str, quality: str, *, template_url: str = "") -> eg.StartEgressRequest:
    """Запрос «записать комнату шаблоном» (новый StartEgress + TemplateSource): H.264 High + AAC, 30 кадров/с, ключевой кадр раз в 4 с."""
    w, h, vbr = QUALITY.get(quality, QUALITY["720p"])
    tpl = eg.TemplateSource(layout=LAYOUT, custom_base_url=template_url) if template_url else eg.TemplateSource(layout=LAYOUT)
    enc = eg.EncodingOptions(width=w, height=h, depth=24, framerate=30, video_bitrate=vbr, audio_bitrate=128, audio_frequency=48000, key_frame_interval=4.0)
    enc.video_codec = 3        # H264_HIGH
    enc.audio_codec = 1        # AAC (OPUS в MP4 плеерами поддерживается хуже)
    return eg.StartEgressRequest(room_name=room_name, template=tpl, advanced=enc, outputs=[eg.Output(file=eg.FileOutput(file_type=eg.EncodedFileType.MP4, filepath=out_path))])


def _ns_to_s(v: int) -> int:
    return int(v / 1_000_000_000) if v else 0


class VideoRecorder:
    def __init__(self, settings: Settings, session_maker, redis, settings_svc, *, api: EgressApi | None = None, export=None, journal=None):
        self._s, self._sm, self._r, self._svc = settings, session_maker, redis, settings_svc
        self.api = api or EgressApi(settings)
        self.export = export                 # async (db, rec) → выгрузка во внешнее хранилище (protocols.export_recording)
        self.journal = journal

    # ------------------------------------------------------------------ настройки и состояние
    async def cfg(self, db):
        return await self._svc.get(db, "video")

    async def _tz(self, db):
        try:
            return ZoneInfo((await self._svc.get(db, "general")).timezone)
        except Exception:  # noqa: BLE001 — нет базы часовых поясов: работаем в UTC
            return timezone.utc

    def out_dir(self) -> str:
        return (os.environ.get("VIDEO_EGRESS_OUT") or "/out").rstrip("/")

    async def active_rows(self, db, meeting_id: uuid.UUID | None = None, *, finishing: bool = False) -> list[Recording]:
        """Фрагменты, которые пишутся сейчас (processing); с finishing=True — и остановленные, но ещё не закрытые Egress-ом (finishing)."""
        q = select(Recording).where(Recording.kind == "mix_video", Recording.status.in_(("processing", "finishing") if finishing else ("processing",)), Recording.egress_id.is_not(None))
        if meeting_id is not None:
            q = q.where(Recording.meeting_id == meeting_id)
        return list((await db.execute(q)).scalars())

    async def state(self, db, meeting: Meeting) -> dict:
        """Состояние для интерфейса: доступна ли функция, идёт ли запись, сколько фрагментов."""
        cfg, room = await self.cfg(db), meeting.room
        rows = (await db.execute(select(Recording).where(Recording.meeting_id == meeting.id, Recording.kind == "mix_video").order_by(Recording.created_at))).scalars().all()
        running = [r for r in rows if r.status == "processing" and r.egress_id]
        return {"enabled": bool(cfg.enabled), "mode": getattr(room, "video_mode", "off"), "quality": getattr(room, "video_quality", "720p"),
                "recording": bool(running), "since": running[0].started_at if running else None, "fragments": len(rows),
                "can_start": bool(cfg.enabled and getattr(room, "video_mode", "off") != "off")}

    # ------------------------------------------------------------------ проверки перед стартом
    async def _guards(self, db, meeting: Meeting, cfg) -> None:
        room = meeting.room
        if meeting.ended_at is not None:
            raise VideoError("meeting_ended", "Встреча уже завершена.")
        if not cfg.enabled:
            raise VideoError("video_disabled", "Видеозапись выключена администратором (Администрирование → Видеозапись).")
        if getattr(room, "video_mode", "off") == "off":
            raise VideoError("video_forbidden", "В этой комнате видеозапись не включена (настройки комнаты → Запись).")
        if room.e2ee_mode != "off":
            raise VideoError("e2ee", "При сквозном шифровании видеозапись недоступна: у сервера нет ключа, чтобы собрать картинку.")
        if room.encrypt_storage:
            raise VideoError("encrypt_storage", "В комнате включено шифрование хранилища: видеозапись с ним пока не поддерживается.")
        busy = len(await self.active_rows(db))
        if busy >= cfg.max_concurrent:
            raise VideoError("video_busy", f"Сейчас идёт максимально допустимое число видеозаписей ({cfg.max_concurrent}). Попробуйте позже или обратитесь к администратору.")
        root = Path(self._s.recordings_path)
        try:
            free = shutil.disk_usage(root if root.exists() else root.parent).free
        except OSError:
            free = None
        if free is not None and free < cfg.min_free_gb * 1024**3:
            raise VideoError("disk_low", f"Мало свободного места на томе записей (меньше {cfg.min_free_gb} ГБ): видеозапись не начата.")
        if cfg.quota_gb:
            used = (await db.execute(select(func.coalesce(func.sum(Recording.size_bytes), 0)).where(Recording.kind == "mix_video"))).scalar_one()
            if used >= cfg.quota_gb * 1024**3:
                raise VideoError("quota", f"Достигнут предел объёма видеозаписей ({cfg.quota_gb} ГБ). Администратор может удалить старые записи или увеличить предел.")

    # ------------------------------------------------------------------ старт / остановка
    async def start(self, db, meeting: Meeting, *, by: str = "") -> Recording:
        lock = await self._r.set(f"video:start:{meeting.id}", "1", nx=True, ex=30)
        if not lock:
            raise VideoError("video_starting", "Запись уже запускается.")
        try:
            cfg = await self.cfg(db)
            running = await self.active_rows(db, meeting.id)
            if running:
                return running[0]                          # повторное нажатие — не второй файл
            await self._guards(db, meeting, cfg)
            present = await list_present_identities(self._s, meeting.livekit_room)
            if present is None:
                raise VideoError("livekit_unavailable", "Сервер звонков недоступен: видеозапись не начата.", 503)
            if not present:
                raise VideoError("room_empty", "В комнате никого нет: пустую комнату не записываем.")
            n = (await db.execute(select(func.count()).select_from(Recording).where(Recording.meeting_id == meeting.id, Recording.kind == "mix_video"))).scalar_one() + 1
            now = datetime.now(timezone.utc)
            rel = f"{meeting_relpath(meeting.room.name, meeting.started_at.astimezone(await self._tz(db)))}/video-{n}.mp4"
            quality = meeting.room.video_quality if meeting.room.video_quality in QUALITY else cfg.default_quality
            installed = str(getattr(self._s, "video_enabled", "yes")).strip().lower() in ("yes", "true", "1", "on")
            try:
                info = await self.api.start(build_request(meeting.livekit_room, f"{self.out_dir()}/{rel}", quality, template_url=cfg.template_url))
            except Exception as exc:  # noqa: BLE001 — Egress не установлен/недоступен: пользователю — понятная причина, подробности в журнале
                log.warning("Не удалось запустить Egress", extra={"meeting_id": str(meeting.id), "error": str(exc)[:300]})
                hint = "" if installed else " На сервере она не установлена (VIDEO_ENABLED=no): sudo scripts/video.sh enable."
                raise VideoError("egress_unavailable", "Служба видеозаписи (LiveKit Egress) недоступна или не установлена." + hint + " Сообщите администратору: Администрирование → Видеозапись.", 503) from exc
            rec = Recording(meeting_id=meeting.id, room_id=meeting.room_id, participant_identity="meeting", path=rel, size_bytes=0, kind="mix_video", mime="video/mp4",
                            status="processing", has_video=True, egress_id=info.egress_id, started_at=now)
            db.add(rec)
            meeting.record_video = True
            await db.commit()
            await events.publish(self._r, meeting.id, {"type": "video_recording_changed", "enabled": True, "by": by})
            self._emit("video_started", meeting, by=by, egress=info.egress_id, quality=quality)
            return rec
        finally:
            await self._r.delete(f"video:start:{meeting.id}")

    async def stop(self, db, meeting: Meeting, *, by: str = "", manual: bool = False) -> int:
        """Остановить видеозапись встречи (идущие фрагменты). Файл дописывает и закрывает Egress; итог приходит webhook-ом или сверкой."""
        rows = await self.active_rows(db, meeting.id)
        if manual:
            await self._r.set(f"video:manual_off:{meeting.id}", "1", ex=24 * 3600)      # автозапуск после ручной остановки не перебивает решение руководителя
        for r in rows:
            try:
                await self.api.stop(r.egress_id)
                r.status = "finishing"                         # остановлен; файл закрывает Egress. Новый запуск — уже новый фрагмент
                await db.commit()
            except Exception as exc:  # noqa: BLE001
                text = str(exc).lower()
                if "not_found" in text or "not found" in text or "already" in text or "complete" in text:
                    await self.finalize(db, r, None)          # Egress уже закончил сам
                else:
                    log.warning("Не удалось остановить Egress", extra={"meeting_id": str(meeting.id), "error": str(exc)[:300]})
                    raise VideoError("egress_unavailable", "Не удалось остановить запись: служба видеозаписи не отвечает. Повторите; запись остановится и при завершении встречи.", 503) from exc
        if meeting.record_video:
            meeting.record_video = False
            await db.commit()
        if rows:
            await events.publish(self._r, meeting.id, {"type": "video_recording_changed", "enabled": False, "by": by})
            self._emit("video_stopped", meeting, by=by)
        return len(rows)

    # ------------------------------------------------------------------ автозапуск и пустая комната
    async def on_join(self, db, meeting: Meeting) -> None:
        """Режим комнаты «auto»: запись начинается, когда в комнате появился человек, и не перезапускается, если руководитель остановил её вручную."""
        try:
            if getattr(meeting.room, "video_mode", "off") != "auto" or meeting.record_video or meeting.ended_at is not None:
                return
            if await self._r.get(f"video:manual_off:{meeting.id}") or not (await self.cfg(db)).enabled:
                return
            await self.start(db, meeting, by="auto")
        except VideoError as exc:
            log.info("Автозапуск видеозаписи не выполнен", extra={"meeting_id": str(meeting.id), "code": exc.code})
            if exc.code in ("egress_unavailable", "disk_low", "quota", "video_busy"):
                self._emit("video_auto_failed", meeting, level="warning", code=exc.code, message=exc.message)
        except Exception:  # noqa: BLE001 — вход участника не должен зависеть от записи
            log.exception("Ошибка автозапуска видеозаписи", extra={"meeting_id": str(meeting.id)})

    async def on_empty(self, db, meeting: Meeting) -> None:
        """В комнате никого не осталось — запись закрывается (иначе Egress писал бы пустую картинку до закрытия комнаты)."""
        if meeting.record_video:
            try:
                await self.stop(db, meeting, by="empty")
            except Exception:  # noqa: BLE001
                log.exception("Не удалось остановить видеозапись пустой комнаты", extra={"meeting_id": str(meeting.id)})

    # ------------------------------------------------------------------ итог файла
    async def finalize(self, db, rec: Recording, info: eg.EgressInfo | None) -> None:
        """Egress закончил (или сверка нашла итог): размер/длительность, статус, выгрузка. Ошибка Egress — запись failed с причиной, файл (если есть) остаётся."""
        if rec.status not in ("processing", "finishing"):
            return
        path = Path(self._s.recordings_path, rec.path)
        size = path.stat().st_size if path.is_file() else 0
        failed = info is not None and info.status in (eg.EgressStatus.EGRESS_FAILED, eg.EgressStatus.EGRESS_ABORTED)
        if info is not None and info.file_results:
            fr = info.file_results[0]
            rec.duration_s = _ns_to_s(fr.duration) or rec.duration_s
            if fr.started_at:
                rec.started_at = datetime.fromtimestamp(fr.started_at / 1e9, tz=timezone.utc)      # 0:00 файла — по нему реплики привязываются к плееру
            size = size or int(fr.size or 0)
        rec.size_bytes = size
        if size <= 0:
            rec.status, rec.error = "failed", ((info.error if info is not None and info.error else "") or "Egress не создал файл")[:290]
        elif failed:
            rec.status, rec.error = "failed", (info.error or "запись прервана")[:290]
        else:
            rec.status, rec.error = "ready", (info.error[:290] if info is not None and info.error else None)
            if info is not None and info.status == eg.EgressStatus.EGRESS_LIMIT_REACHED:
                rec.error = "Достигнут предел длительности записи: файл закрыт, дальше запись продолжится новым фрагментом."
        await db.commit()
        meeting = await db.get(Meeting, rec.meeting_id)
        if meeting is not None and meeting.record_video and not await self.active_rows(db, meeting.id):
            meeting.record_video = False
            await db.commit()
            await events.publish(self._r, meeting.id, {"type": "video_recording_changed", "enabled": False, "by": "egress"})
        if rec.status == "failed":
            self._emit("video_failed", meeting, level="error", error=rec.error)
        elif self.export is not None:
            try:
                await self.export(db, rec)
            except Exception:  # noqa: BLE001 — запись остаётся локально и выгрузится повторно
                log.exception("Выгрузка видеозаписи не выполнена", extra={"recording": str(rec.id)})

    async def on_egress_event(self, db, event) -> None:
        """Webhook LiveKit egress_started / egress_updated / egress_ended."""
        info = event.egress_info
        if not info or not info.egress_id:
            return
        rec = (await db.execute(select(Recording).where(Recording.egress_id == info.egress_id))).scalars().first()
        if rec is None:
            return
        if event.event == "egress_ended" or info.status not in ACTIVE_LK:
            await self.finalize(db, rec, info)

    async def reconcile(self, db) -> int:
        """Сверка «идущих» записей с Egress (после перезапуска сервера, потери webhook, падения Egress). Возвращает число закрытых записей."""
        closed = 0
        for rec in await self.active_rows(db, finishing=True):
            meeting = await db.get(Meeting, rec.meeting_id)
            info = None
            try:
                info = await self.api.get(rec.egress_id)
            except Exception:  # noqa: BLE001 — LiveKit недоступен: ничего не решаем, повторим в следующий проход
                continue
            if info is not None and info.status in ACTIVE_LK:
                limit = (await self.cfg(db)).max_hours * 3600
                age = (datetime.now(timezone.utc) - (rec.started_at or rec.created_at)).total_seconds()
                if meeting is not None and meeting.ended_at is not None:
                    try:
                        await self.api.stop(rec.egress_id)         # встреча кончилась, а запись идёт — закрываем
                    except Exception:  # noqa: BLE001
                        pass
                elif age > limit:
                    await self.stop(db, meeting, by="limit")        # предел одной записи: файл закрыт, следующий фрагмент начнётся при новой активности
                    if meeting.room.video_mode == "auto":
                        await asyncio.sleep(0)
                        await self.on_join(db, meeting)
                continue
            await self.finalize(db, rec, info)                      # Egress нет в списке или он закончил: берём то, что лежит на диске
            closed += 1
        return closed

    def _emit(self, name: str, meeting: Meeting | None, *, level: str = "info", **extra) -> None:
        if self.journal is not None and meeting is not None:
            try:
                self.journal.emit("storage", name, level=level, meeting_id=str(meeting.id), message=str(extra.get("message") or extra.get("error") or "")[:300] or None, data=extra or None)
            except Exception:  # noqa: BLE001
                pass
