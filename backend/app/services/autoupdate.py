"""Автоматическое обновление: раз в сутки в заданное время проверяет, есть ли новая версия, и запускает штатное обновление тем же безопасным путём, что и
кнопка «Обновить проект» (запрос помощнику обновлений на хосте). Идущие встречи не прерываются: пока они есть, обновление откладывается до окна без
встреч (в пределах `window_hours` после назначенного времени), иначе пропускается до следующих суток. Итог пишется в состояние (видно в разделе
«Обновления и версии») и, как у любого обновления, в историю попыток (`by=auto-update`).
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from ..models import Meeting
from . import updates as upd

log = logging.getLogger("app.autoupdate")

STATE_KEY = "autoupdate:state"
CHECK_TIMEOUT_S = 600          # помощник должен ответить на проверку за 10 минут
RUN_TIMEOUT_S = 3 * 3600       # обновление не должно идти дольше 3 часов (после этого — отметка «не дождались»)
BY = "auto-update"


def scheduled_at(now: datetime, hhmm: str) -> datetime:
    h, m = (int(x) for x in hhmm.split(":"))
    return now.replace(hour=h, minute=m, second=0, microsecond=0)


class AutoUpdater:
    def __init__(self, session_maker, settings_svc, redis, data_dir: str, current_version: str = ""):
        self._sm, self._svc, self._r = session_maker, settings_svc, redis
        self.channel = upd.Channel(f"{data_dir}/updater")
        self.version = current_version

    # ------------------------------------------------------------------------------ состояние
    async def state(self) -> dict:
        raw = await self._r.get(STATE_KEY)
        try:
            st = json.loads(raw) if raw else {}
        except ValueError:
            st = {}
        return st if isinstance(st, dict) else {}

    async def _save(self, st: dict) -> None:
        await self._r.set(STATE_KEY, json.dumps(st, ensure_ascii=False))

    async def _tz(self, db) -> ZoneInfo:
        try:
            return ZoneInfo((await self._svc.get(db, "general")).timezone)     # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            return ZoneInfo("UTC")

    async def _active_meetings(self, db) -> int:
        return (await db.execute(select(func.count()).select_from(Meeting).where(Meeting.ended_at.is_(None)))).scalar_one()

    async def next_run(self) -> str | None:
        async with self._sm() as db:
            cfg = await self._svc.get(db, "autoupdate")
            if not cfg.enabled:                                                  # type: ignore[attr-defined]
                return None
            tz = await self._tz(db)
        now = datetime.now(tz)
        t = scheduled_at(now, cfg.time)                                          # type: ignore[attr-defined]
        st = await self.state()
        if t <= now and (st.get("day") == now.date().isoformat() or now - t > timedelta(hours=cfg.window_hours)):   # type: ignore[attr-defined]
            t += timedelta(days=1)
        return t.isoformat()

    async def request_now(self) -> None:
        """Выполнить цикл немедленно (по тем же правилам, включая защиту от идущих встреч): для проверки настройки."""
        st = await self.state()
        st.update(phase="idle", force=True, day=None)
        await self._save(st)

    def _finish(self, st: dict, result: str, **info) -> dict:
        now = time.time()
        st["last"] = {"at": int(now), "result": result, "installed_before": self.version, **info}
        st.update(phase="idle", force=False, request_id=None, day=st.get("today"))
        return st

    # ------------------------------------------------------------------------------ один шаг (раз в 30–60 секунд)
    async def tick(self, now: datetime | None = None) -> str:
        """Возвращает краткое слово о том, что произошло (для тестов и журнала)."""
        async with self._sm() as db:
            cfg = await self._svc.get(db, "autoupdate")
            tz = await self._tz(db)
            active = await self._active_meetings(db)
        st = await self.state()
        phase = st.get("phase") or "idle"
        local = (now.astimezone(tz) if now else datetime.now(tz))
        st["today"] = local.date().isoformat()
        ts = time.time()
        ch = self.channel

        # --- наблюдение за уже начатым (работает и при выключенной настройке: итог всё равно нужно записать)
        if phase == "checking":
            remote = ch.remote()
            if remote and float(remote.get("checked_at") or 0) >= float(st.get("phase_since") or 0) - 1:
                if not remote.get("ok"):
                    await self._save(self._finish(st, "failed", error="Не удалось проверить версию: " + str(remote.get("error") or "нет связи с GitHub")))
                    return "check_failed"
                if int(remote.get("behind") or 0) <= 0:
                    await self._save(self._finish(st, "no_update", detail="Новой версии нет", from_version=remote.get("current_version") or self.version))
                    return "no_update"
                st.update(phase="waiting", phase_since=ts, from_version=remote.get("current_version") or self.version, to_version=remote.get("remote_version") or "")
                await self._save(st)
                phase = "waiting"
            elif ts - float(st.get("phase_since") or ts) > CHECK_TIMEOUT_S:
                await self._save(self._finish(st, "failed", error="Помощник обновлений не ответил на проверку версии."))
                return "check_timeout"
            else:
                return "checking"
        if phase == "running":
            s = ch.status()
            if s.get("request_id") == st.get("request_id") and s.get("state") not in upd.BUSY_STATES and s.get("finished_at"):
                ok = s.get("result") == "ok"
                started, finished = float(s.get("started_at") or 0), float(s.get("finished_at") or ts)
                last = next((h for h in ch.history(5) if h.get("by") == BY), None)
                info = {"from_version": (last or {}).get("from_version") or st.get("from_version"), "to_version": (last or {}).get("to_version") or st.get("to_version"),
                        "duration_s": int(max(0, finished - started)) if started else None}
                if ok:
                    await self._save(self._finish(st, "updated", **info))
                else:
                    stage = (last or {}).get("stage") or s.get("step_name") or ""
                    await self._save(self._finish(st, "failed", error=f"Обновление остановилось{f' на этапе «{stage}»' if stage else ''} (код {s.get('exit_code')}). Подробности — в журнале обновления.", **info))
                return "updated" if ok else "failed"
            if ts - float(st.get("phase_since") or ts) > RUN_TIMEOUT_S:
                await self._save(self._finish(st, "failed", error="Не дождались завершения обновления."))
                return "run_timeout"
            return "running"

        if not cfg.enabled and not st.get("force"):                                  # type: ignore[attr-defined]
            return "disabled"

        sched = scheduled_at(local, cfg.time)                                        # type: ignore[attr-defined]
        window = timedelta(hours=cfg.window_hours)                                   # type: ignore[attr-defined]
        if phase == "idle":
            due = bool(st.get("force")) or (local >= sched and local - sched <= window and st.get("day") != st["today"])
            if not due:
                await self._save(st)
                return "idle"
            helper = ch.status()
            if not helper.get("available"):
                await self._save(self._finish(st, "skipped", error="Помощник обновлений на сервере не запущен — автообновление невозможно."))
                return "no_helper"
            if helper.get("state") in upd.BUSY_STATES or helper.get("request_pending"):
                return "helper_busy"                                                 # повторим на следующем шаге
            try:
                ch.request("check", by=BY)
            except OSError as exc:
                await self._save(self._finish(st, "failed", error=f"Не удалось передать запрос помощнику ({exc.strerror or exc})."))
                return "request_failed"
            st.update(phase="checking", phase_since=ts)
            await self._save(st)
            return "check_requested"

        if phase == "waiting":
            first = float(st.get("first_wait") or st.get("phase_since") or ts)
            st["first_wait"] = first
            helper = ch.status()
            blockers = []
            if active > 0:
                blockers.append(f"идут встречи: {active}")
            if helper.get("state") in upd.BUSY_STATES or helper.get("request_pending"):
                blockers.append("помощник занят")
            if not helper.get("available"):
                blockers.append("помощник не запущен")
            if blockers:
                st["deferred"] = ", ".join(blockers)
                if ts - first > window.total_seconds():
                    await self._save(self._finish(st, "deferred", error=f"За {cfg.window_hours} ч не нашлось окна без встреч ({st['deferred']}); попробуем в следующие сутки.",   # type: ignore[attr-defined]
                                                  from_version=st.get("from_version"), to_version=st.get("to_version")))
                    return "deferred_giveup"
                await self._save(st)
                return "deferred"
            try:
                rid = ch.request("update", by=BY)
            except OSError as exc:
                await self._save(self._finish(st, "failed", error=f"Не удалось передать запрос помощнику ({exc.strerror or exc})."))
                return "request_failed"
            log.info("Автообновление запущено", extra={"from": st.get("from_version"), "to": st.get("to_version")})
            st.update(phase="running", phase_since=ts, request_id=rid, deferred=None, first_wait=None)
            await self._save(st)
            return "update_started"
        return "idle"
