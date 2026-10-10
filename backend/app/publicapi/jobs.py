"""Фоновые задачи публичного API (формирование протокола, резюме, карты): queued → processing → completed | failed | cancelled.

* Запрос только ставит задачу в очередь и сразу отвечает `202 Accepted` + `Location`; выполнение — воркер с ограничением числа одновременных задач (нагрузка на языковую модель
  не должна мешать звонкам и распознаванию речи). Очередь хранится в БД: перезапуск сервиса её не теряет; задача, прерванная посреди выполнения, помечается ошибкой.
* Условия проверяются при постановке (встреча завершена, модель настроена, есть материалы) — клиент получает понятный `409`, а не задачу, обречённую на сбой.
* Отменить можно только ожидающую (`queued`) задачу: выполняющуюся нельзя прервать безопасно (модель уже работает).
* Задача принадлежит интеграции, создавшей её: чужую не видно (404).
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..models import ApiClient, ApiJob, ConversationMap, Meeting, Protocol, utcnow
from ..services.settings import SettingsError
from . import ids
from .errors import ApiError

log = logging.getLogger("app")

KIND_SCOPE = {"protocol": "protocols:generate", "summary": "summaries:generate", "map": "maps:generate"}
FINAL = ("completed", "failed", "cancelled")


class JobRunner:
    def __init__(self, session_maker: async_sessionmaker[AsyncSession], settings_svc, protocols, webhooks=None, journal=None):
        self._sm, self._svc, self.ps, self.webhooks, self.journal = session_maker, settings_svc, protocols, webhooks, journal
        self._wake = asyncio.Event()
        self._runner: asyncio.Task | None = None
        self._running: set[asyncio.Task] = set()
        self._last_prune = 0.0

    # ------------------------------------------------------------------ постановка
    async def preflight(self, request, db: AsyncSession, meeting: Meeting, kind: str) -> None:
        """Те же условия, что у кнопки в интерфейсе, но с кодами для интеграций."""
        if meeting.ended_at is None:
            raise ApiError(409, "meeting_not_ended", "Документ формируется после завершения встречи.")
        if kind == "map":
            from ..api.maps import _plan as map_plan  # noqa: PLC0415

            if not await self.ps.has_materials(meeting.id):
                raise ApiError(409, "no_materials", "В встрече нет реплик — строить карту не из чего.")
            plan = await map_plan(request, db, meeting)
            if not plan["ready"]:
                raise ApiError(409, "llm_not_configured", str(plan["reason"])[:300])
            return
        try:
            plan = await self.ps.plan(db, meeting, kind)
        except SettingsError as exc:
            raise ApiError(409, "llm_not_configured", str(exc)[:300]) from None
        if not plan["llm_ready"]:
            raise ApiError(409, "llm_not_configured", "Языковая модель (LLM) не настроена администратором.")
        if not plan["anonymizer_ready"]:
            raise ApiError(409, "anonymizer_not_configured", "Для этой переговорки включено обезличивание, но сервис обезличивания не настроен.")
        if not await self.ps.has_materials(meeting.id):
            raise ApiError(409, "no_materials", "В встрече нет реплик, чата и схемы — формировать документ не из чего.")

    async def submit(self, db: AsyncSession, client_id: uuid.UUID, kind: str, meeting_id: uuid.UUID, params: dict | None) -> ApiJob:
        job = ApiJob(client_id=client_id, kind=kind, meeting_id=meeting_id, status="queued", params=params or None)
        db.add(job)
        await db.flush()
        return job

    def wake(self) -> None:
        self._wake.set()

    async def cancel(self, db: AsyncSession, job_id: uuid.UUID) -> bool:
        """Атомарно: отменяется только ещё не взятая в работу задача."""
        res = await db.execute(update(ApiJob).where(ApiJob.id == job_id, ApiJob.status == "queued").values(status="cancelled", finished_at=utcnow()))
        await db.flush()
        return res.rowcount == 1

    # ------------------------------------------------------------------ выполнение
    async def _claim(self) -> ApiJob | None:
        async with self._sm() as db:
            row = (await db.execute(select(ApiJob.id).where(ApiJob.status == "queued").order_by(ApiJob.created_at).limit(1))).scalar_one_or_none()
            if row is None:
                return None
            res = await db.execute(update(ApiJob).where(ApiJob.id == row, ApiJob.status == "queued").values(status="processing", started_at=utcnow()))
            await db.commit()
            return await db.get(ApiJob, row) if res.rowcount == 1 else None

    async def execute(self, job_id: uuid.UUID) -> None:
        async with self._sm() as db:
            job = await db.get(ApiJob, job_id)
            if job is None or job.status != "processing":
                return

            client = await db.get(ApiClient, job.client_id)
            actor = f"api:{client.name if client else 'unknown'}"
            meeting = await db.get(Meeting, job.meeting_id)
            kind, instruction, room_id = job.kind, (job.params or {}).get("instruction"), meeting.room_id if meeting else None
            meeting_id = job.meeting_id
        status, result, error = "failed", None, None
        try:
            if kind == "map":
                async with self._sm() as db:
                    rec = await self.ps.maps.request(db, meeting_id, actor)
                    map_id = rec.id
                await self.ps.maps.run(map_id)
                async with self._sm() as db:
                    cm = await db.get(ConversationMap, map_id)
                    if cm is not None and cm.status == "ready":
                        status, result = "completed", {"map_id": ids.pub("map", map_id)}
                    else:
                        error = (cm.error if cm else None) or "Карта не сформирована"
            else:
                pid = await self.ps.create_protocol_row(meeting_id, kind, actor, instruction, None)
                await self.ps.run_protocol(pid)
                async with self._sm() as db:
                    rec2 = await db.get(Protocol, pid)
                    if rec2 is not None and rec2.status == "ready":
                        status, result = "completed", {"document_id": ids.pub("protocol", pid)}
                    else:
                        error = (rec2.error if rec2 else None) or "Документ не сформирован"
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("Сбой выполнения задачи API", extra={"job": str(job_id)})
            error = "Внутренняя ошибка (см. журнал сервера)"
        async with self._sm() as db:
            await db.execute(update(ApiJob).where(ApiJob.id == job_id).values(status=status, result=result, error=(error or None) and error[:480], finished_at=utcnow()))
            await db.commit()
        if self.webhooks is not None:
            data = {"job_id": ids.pub("job", job_id), "kind": kind, "meeting_id": ids.pub("meeting", meeting_id), **(result or {}), **({"error": error} if error else {})}
            self.webhooks.emit_safe("job.completed" if status == "completed" else "job.failed", data, room_id=room_id)

    async def recover(self) -> int:
        """После перезапуска: задачи, прерванные посреди выполнения, получают понятную ошибку (повторить может сам клиент)."""
        async with self._sm() as db:
            res = await db.execute(update(ApiJob).where(ApiJob.status == "processing").values(status="failed", error="Выполнение прервано перезапуском сервиса. Создайте задачу заново.", finished_at=utcnow()))
            await db.commit()
            return res.rowcount or 0

    async def drain(self) -> None:
        """Для тестов: выполнить всё, что стоит в очереди, и дождаться завершения."""
        while True:
            job = await self._claim()
            if job is None:
                break
            await self.execute(job.id)
        if self._running:
            await asyncio.gather(*list(self._running), return_exceptions=True)

    async def _loop(self) -> None:
        import time  # noqa: PLC0415

        await self.recover()
        while True:
            try:
                async with self._sm() as db:
                    cap = int((await self._svc.get(db, "api")).jobs_concurrency)       # type: ignore[attr-defined]
                self._running = {t for t in self._running if not t.done()}
                started = False
                while len(self._running) < cap:
                    job = await self._claim()
                    if job is None:
                        break
                    t = asyncio.get_running_loop().create_task(self.execute(job.id), name=f"api-job-{job.id}")
                    self._running.add(t)
                    started = True
                if time.monotonic() - self._last_prune > 3600:
                    self._last_prune = time.monotonic()
                    await self.prune()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("Сбой воркера задач API")
                started = False
            if not started:
                self._wake.clear()
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(self._wake.wait(), timeout=3.0)

    async def prune(self) -> None:
        from sqlalchemy import delete  # noqa: PLC0415

        async with self._sm() as db:
            days = (await self._svc.get(db, "api")).log_retention_days               # type: ignore[attr-defined]
            await db.execute(delete(ApiJob).where(ApiJob.status.in_(FINAL), ApiJob.created_at < utcnow() - timedelta(days=days)))
            await db.commit()

    def start(self) -> None:
        import time  # noqa: PLC0415

        self._last_prune = time.monotonic()
        self._runner = asyncio.get_running_loop().create_task(self._loop(), name="api-jobs")

    async def stop(self) -> None:
        if self._runner:
            self._runner.cancel()
            with contextlib.suppress(asyncio.CancelledError, asyncio.TimeoutError, Exception):
                await asyncio.wait_for(asyncio.shield(self._runner), 3)
        for t in list(self._running):
            t.cancel()
        await asyncio.gather(*list(self._running), return_exceptions=True)
