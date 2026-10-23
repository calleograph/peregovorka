"""Протоколы и карты при нескольких процессах backend (BACKEND_WORKERS > 1, несколько экземпляров): ход, отмена и «сироты» видны всем процессам.
Два ProtocolService с общими Redis и базой — как два процесса. Раньше другой процесс считал чужое идущее задание прерванным (запуск, отмена, опрос карты)."""
from __future__ import annotations

import asyncio
import uuid

from app.models import ConversationMap, Protocol
from app.services import protocols as protocols_mod
from app.services.conv_map import MapService
from app.services.job_budget import Budget
from app.services.protocols import ProtocolService, job_key

from .test_access_and_protocols import end_by_alice, meeting_with_two


def test_running_jobs_are_visible_cancellable_and_safe_from_other_processes(client, monkeypatch):
    monkeypatch.setattr(protocols_mod, "JOB_BEAT_S", 0.05)
    _room, mid = meeting_with_two(client)
    end_by_alice(client, mid)
    app = client.app_obj
    a: ProtocolService = app.state.protocols                     # процесс A — владелец заданий

    async def scenario():
        b = ProtocolService(app.state.settings, app.state.bg_session_maker, app.state.settings_svc)     # процесс B
        b.redis, b.maps = app.state.redis, MapService(b)
        running, orphan = uuid.uuid4(), uuid.uuid4()
        async with app.state.bg_session_maker() as db:
            db.add_all([Protocol(id=running, meeting_id=uuid.UUID(mid), kind="protocol", status="running", instruction=""),
                        Protocol(id=orphan, meeting_id=uuid.UUID(mid), kind="summary", status="running", instruction="")])
            cmap = ConversationMap(meeting_id=uuid.UUID(mid), status="running")
            db.add(cmap)
            await db.commit()
            map_id = cmap.id
        gate = asyncio.Event()
        cancelled = asyncio.Event()

        async def job():
            try:
                await gate.wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise
        a._live[running] = Budget()
        a.spawn(job(), f"protocol-{running}")
        a.spawn(gate.wait(), f"map-{map_id}")
        for _ in range(100):
            if await app.state.redis.exists(job_key(f"protocol-{running}")) and await app.state.redis.exists(job_key(f"map-{map_id}")):
                break
            await asyncio.sleep(0.02)

        prog = await b.live_progress(running)                     # ход виден из другого процесса
        assert prog is not None and "elapsed_s" in prog and "limit_s" in prog
        assert await b.recover_orphans() == 1                     # закрыт только документ без живого задания нигде
        async with app.state.bg_session_maker() as db:
            assert (await db.get(Protocol, running)).status == "running"
            assert (await db.get(Protocol, orphan)).status == "failed"
            rec = await db.get(ConversationMap, map_id)
            assert (await b.maps.refresh(db, rec)).status == "running", "опрос карты из другого процесса не объявляет её прерванной"
        assert not await b.claim(f"map-{map_id}"), "повторный запуск карты в другом процессе не создаёт второе задание"

        assert await b.cancel(running) is True                     # отмена из другого процесса доходит до владельца
        await asyncio.wait_for(cancelled.wait(), 2)
        assert running in a._cancel_requested or cancelled.is_set()
        gate.set()
        for _ in range(100):
            if not await app.state.redis.exists(job_key(f"map-{map_id}")):
                break
            await asyncio.sleep(0.02)
        assert not await b.job_alive(f"map-{map_id}"), "задание завершилось — ключ снят"
        assert await b.cancel(uuid.uuid4()) is False

        # перенос записей (ведущий процесс B) видит обработку файлов встречи и воспроизведение в процессе A
        meeting, rec = uuid.UUID(mid), uuid.uuid4()
        assert not await b.files_busy(meeting) and not await b.recently_read(rec, 120)
        a.hold(meeting)
        a.hold(meeting)
        await asyncio.sleep(0.05)
        assert await b.files_busy(meeting)
        a.release(meeting)
        await asyncio.sleep(0.05)
        assert await b.files_busy(meeting), "счётчик: одна обработка ещё идёт"
        a.release(meeting)
        await asyncio.sleep(0.05)
        assert not await b.files_busy(meeting)
        await a.mark_read(rec, 120)
        assert await b.recently_read(rec, 120)
    client.portal.call(scenario)
