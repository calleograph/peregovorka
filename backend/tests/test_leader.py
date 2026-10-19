"""Несколько экземпляров backend: одиночные фоновые задачи выполняет один «ведущий», при его уходе роль переходит другому; дублей нет."""
import asyncio

from fakeredis import FakeAsyncRedis

from app.services.leader import Leader


def _mk(redis, name, log, **kw):
    ld = Leader(redis, lease_s=1.0, poll_s=0.1, instance_id=name, **kw)

    async def worker():
        log.append(("start", name))
        try:
            await asyncio.sleep(3600)
        finally:
            log.append(("stop", name))
    ld.register("job", lambda: asyncio.create_task(worker(), name=f"job-{name}"))
    return ld


def test_only_one_instance_runs_background_jobs_and_role_moves_on_failure():
    async def go():
        redis = FakeAsyncRedis(decode_responses=True)
        log: list = []
        a, b = _mk(redis, "A", log), _mk(redis, "B", log)
        ta, tb = asyncio.create_task(a.run()), asyncio.create_task(b.run())
        await asyncio.sleep(0.5)
        assert [e for e in log if e[0] == "start"] in ([("start", "A")], [("start", "B")]), log          # задача запущена ровно в одном экземпляре
        first = a if a.is_leader else b
        second = b if first is a else a
        assert first.is_leader and not second.is_leader
        (ta if first is a else tb).cancel()                                                               # ведущий остановлен штатно: роль освобождается сразу
        await asyncio.sleep(0.6)
        assert second.is_leader and sum(1 for e in log if e[0] == "start") == 2 and ("stop", first.id) in log
        tb.cancel() if second is b else ta.cancel()
        await asyncio.gather(ta, tb, return_exceptions=True)
        assert not any(x.is_leader for x in (a, b))

    asyncio.run(go())
