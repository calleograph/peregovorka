"""Обогащение профиля из дополнительных источников (Bitrix24) и слияние с данными каталога.

Правила надёжности:
* вход и комната НЕ ждут портал: обновление запускается в фоне (`spawn`), любой сбой — запись в журнал и всё;
* свежие данные не запрашиваются повторно (срок `cache_hours`), после сбоя — пауза, чтобы не долбить недоступный портал;
* ручная аватарка пользователя не заменяется (если «local» стоит в приоритете раньше «bitrix»);
* если портал недоступен — остаются прежние значения и последняя аватарка.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..models import User, utcnow
from ..services import avatars as avatar_svc
from .base import FIELDS, ProviderError, SourceProfile
from .bitrix import MAX_PHOTO, Bitrix24Provider
from .merge import avatar_source, effective, priorities_from

log = logging.getLogger("app")
FAIL_PAUSE = timedelta(minutes=10)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _parse(text: object) -> datetime | None:
    try:
        return datetime.fromisoformat(str(text))
    except ValueError:
        return None


def record_ad(user: User, values: dict[str, str | None], *, clear_missing: bool = False) -> None:
    """Запомнить значения, пришедшие из каталога, как источник «ad» (пустое не стирает прежнее, если не `clear_missing`)."""
    src = dict(user.profile_sources or {})
    ad = dict(src.get("ad") or {})
    for f in FIELDS:
        v = (values.get(f) or "").strip() if f in values else None
        if v:
            ad[f] = v
        elif clear_missing and f in values:
            ad.pop(f, None)
    src["ad"] = ad
    user.profile_sources = src


def _seed_ad(user: User) -> dict[str, dict[str, str]]:
    """Источники пользователя; если «ad» ещё не записан (пользователь появился до этой версии), текущие значения карточки считаются данными каталога."""
    src = {k: dict(v) for k, v in (user.profile_sources or {}).items() if isinstance(v, dict)}
    if "ad" not in src:
        src["ad"] = {f: getattr(user, f) for f in FIELDS if getattr(user, f, None)}
    return src


def apply_effective(user: User, cfg, *, use_bitrix: bool) -> bool:
    """Пересчитать поля карточки по приоритетам. Пустой итог по полю оставляет прежнее значение. Возвращает, изменилось ли что-то."""
    src = _seed_ad(user)
    if not use_bitrix:
        src.pop("bitrix", None)
    eff = effective(src, priorities_from(cfg if use_bitrix else None))
    changed = False
    for f, v in eff.items():
        if getattr(user, f) != v:
            setattr(user, f, v)
            changed = True
    user.profile_sources = {**(user.profile_sources or {}), "ad": src["ad"]}
    return changed


class ProfileEnrichment:
    def __init__(self, session_maker: async_sessionmaker[AsyncSession], settings_svc, avatar_store, journal=None, *,
                 transports: dict | None = None, ca_file: str | None = None, concurrency: int = 3):
        self._sm, self._svc, self._avatars, self._journal = session_maker, settings_svc, avatar_store, journal
        self._transports, self._ca = transports or {}, ca_file
        self._sem = asyncio.Semaphore(concurrency)
        self._inflight: set[str] = set()
        self._tasks: set[asyncio.Task] = set()

    # ------------------------------------------------------------------ вспомогательное
    async def config(self, db: AsyncSession):
        try:
            return await self._svc.get(db, "bitrix24")
        except Exception:  # noqa: BLE001 — настройки недоступны/некорректны: интеграция просто выключена
            return None

    def provider(self, cfg) -> Bitrix24Provider:
        return Bitrix24Provider(cfg, cfg.webhook_url, transport=self._transports.get("bitrix24"), ca_file=self._ca if cfg.use_corporate_ca else None)

    def _emit(self, event: str, message: str, level: str = "info", data: dict | None = None) -> None:
        if self._journal is not None:
            self._journal.emit("profile", event, level=level, message=message, data=data)

    # ------------------------------------------------------------------ вход
    async def on_login(self, db: AsyncSession, user: User) -> None:
        """Сразу после входа (без сети): пересчитать карточку по сохранённым источникам. Никогда не бросает исключений."""
        try:
            cfg = await self.config(db)
            if apply_effective(user, cfg, use_bitrix=bool(cfg and cfg.enabled)):
                await db.commit()
        except Exception:  # noqa: BLE001
            log.exception("Не удалось пересчитать профиль при входе")

    def spawn(self, user_id, *, force: bool = False) -> None:
        """Фоновое обновление из портала; вызывающий не ждёт. Один человек — одна задача одновременно."""
        key = str(user_id)
        if key in self._inflight:
            return
        self._inflight.add(key)

        async def run() -> None:
            try:
                async with self._sem:
                    await self.enrich(user_id, force=force)
            except Exception:  # noqa: BLE001
                log.exception("Сбой фонового обновления профиля")
            finally:
                self._inflight.discard(key)

        try:
            t = asyncio.get_running_loop().create_task(run())
        except RuntimeError:
            self._inflight.discard(key)
            return
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)

    async def drain(self) -> None:
        if self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    # ------------------------------------------------------------------ обновление одного пользователя
    async def enrich(self, user_id, *, force: bool = False) -> dict:
        # Фаза 1: что спрашивать (сессия БД закрывается ДО сетевого запроса — соединение с БД не держится, пока портал думает)
        async with self._sm() as db:
            cfg = await self.config(db)
            if cfg is None or not cfg.enabled:
                return {"status": "disabled"}
            user = await db.get(User, user_id)
            if user is None or user.auth_source != "ad":
                return {"status": "skipped"}
            email, ext, login = user.email, (user.external_ids or {}).get("bitrix"), user.sam_account_name
            if not email and not ext:
                return {"status": "no_key"}
            now = utcnow()
            mine = dict((user.profile_sources or {}).get("bitrix") or {})
            if not force:
                fresh, failed = _parse(mine.get("fetched_at")), _parse(mine.get("failed_at"))
                if failed and now - failed < FAIL_PAUSE:
                    return {"status": "paused"}
                if fresh and now - fresh < timedelta(hours=cfg.cache_hours):
                    return {"status": "fresh"}
        # Фаза 2: запрос к порталу (любой сбой ловится и записывается в журнал)
        error: str | None = None
        prof: SourceProfile | None = None
        try:
            prof = await asyncio.wait_for(self.provider(cfg).fetch(email=email, external_id=ext), timeout=cfg.timeout * 3 + 2)
        except Exception as exc:  # noqa: BLE001 — любой сбой портала не должен ничего ломать
            error = str(exc) if isinstance(exc, ProviderError) else type(exc).__name__
        # Фаза 3: сохранить результат
        async with self._sm() as db:
            user = await db.get(User, user_id)
            if user is None:
                return {"status": "skipped"}
            if error is not None:
                mine["failed_at"] = _iso(now)
                user.profile_sources = {**(user.profile_sources or {}), "bitrix": mine}
                await db.commit()
                self._emit("bitrix_profile_refresh_failed", f"Bitrix profile refresh failed: {error}", "warn", {"user": login})
                return {"status": "error", "message": error}
            return await self._store(db, user, cfg, prof, dict((user.profile_sources or {}).get("bitrix") or {}), now)

    async def _store(self, db: AsyncSession, user: User, cfg, prof: SourceProfile | None, mine: dict, now: datetime) -> dict:
        if prof is None:
            mine = {**mine, "fetched_at": _iso(now), "not_found": True}
            mine.pop("failed_at", None)
            user.profile_sources = {**(user.profile_sources or {}), "bitrix": mine}
            await db.commit()
            return {"status": "not_found"}
        new = {**prof.fields, "fetched_at": _iso(now)}
        photo_changed = False
        if prof.photo:
            digest = hashlib.sha256(prof.photo).hexdigest()[:16]
            new["photo_sha"] = digest
            if digest != mine.get("photo_sha") or not user.avatar_mime:
                photo_changed = True
        elif mine.get("photo_sha"):
            new["photo_sha"] = mine["photo_sha"]
        user.profile_sources = {**(user.profile_sources or {}), "bitrix": new}
        if prof.external_id:
            user.external_ids = {**(user.external_ids or {}), "bitrix": prof.external_id}
        changed = apply_effective(user, cfg, use_bitrix=True)
        avatar_set = False
        if prof.photo and cfg.use_photos:
            decision = avatar_source(has_avatar=bool(user.avatar_mime), current=user.avatar_source, priority=priorities_from(cfg)["avatar"], bitrix_available=True)
            if decision == "bitrix" and photo_changed:
                try:
                    webp = await asyncio.to_thread(avatar_svc.process, prof.photo, max_bytes=MAX_PHOTO)
                    self._avatars.save(user.id, webp)
                    user.avatar_mime, user.avatar_updated_at, user.avatar_source = avatar_svc.MIME, now, "bitrix"
                    avatar_set = True
                except avatar_svc.AvatarError as exc:
                    self._emit("bitrix_photo_rejected", f"Фото из Bitrix не принято: {exc}", "warn", {"user": user.sam_account_name})
        user.profile_synced_at = now
        await db.commit()
        return {"status": "ok", "changed": changed, "avatar": avatar_set, "fields": sorted(prof.fields)}

    # ------------------------------------------------------------------ для администратора
    async def preview(self, email: str) -> dict:
        """Что вернёт портал для этого e-mail (ничего не сохраняется)."""
        async with self._sm() as db:
            cfg = await self.config(db)
        if cfg is None or not cfg.enabled:
            return {"ok": False, "message": "Интеграция выключена или настройки не сохранены."}
        try:
            prof = await self.provider(cfg).fetch(email=email.strip(), external_id=None)
        except ProviderError as exc:
            return {"ok": False, "message": f"Ошибка: {exc}"}
        if prof is None:
            return {"ok": False, "message": "Сотрудник не найден или найдено несколько совпадений по e-mail."}
        return {"ok": True, "message": "Найден", "fields": prof.fields, "external_id": prof.external_id, "has_photo": bool(prof.photo)}

    async def check(self) -> tuple[bool, str, int]:
        async with self._sm() as db:
            cfg = await self.config(db)
        if cfg is None or not cfg.portal_url or not cfg.webhook_url:
            return False, "Укажите адрес портала и webhook и сохраните настройки.", 0
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        try:
            msg = await self.provider(cfg).check()
            return True, msg, int((loop.time() - t0) * 1000)
        except ProviderError as exc:
            return False, f"Ошибка: {exc}", int((loop.time() - t0) * 1000)

    async def sync_all(self, *, limit: int = 200, force: bool = True) -> dict:
        """Ручная синхронизация: пользователи с e-mail (по давности последнего входа), ограниченное число за запуск, параллельно ≤ concurrency."""
        async with self._sm() as db:
            cfg = await self.config(db)
            if cfg is None or not cfg.enabled:
                return {"status": "disabled"}
            ids = [r for r in (await db.execute(select(User.id).where(User.auth_source == "ad", User.is_active.is_(True), User.email.is_not(None))
                                                .order_by(User.last_login_at.desc().nullslast()).limit(limit))).scalars().all()]
        counts: dict[str, int] = {}

        async def one(uid) -> None:
            async with self._sem:
                try:
                    r = await self.enrich(uid, force=force)
                except Exception:  # noqa: BLE001
                    r = {"status": "error"}
                counts[r["status"]] = counts.get(r["status"], 0) + 1

        await asyncio.gather(*(one(i) for i in ids))
        self._emit("bitrix_sync_done", f"Синхронизация профилей: обработано {len(ids)}", "info", counts)
        return {"status": "done", "processed": len(ids), "result": counts}
