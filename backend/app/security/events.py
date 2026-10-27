"""События безопасности: неудачные входы, CAPTCHA, блокировки, отказы по подсетям, срабатывания WAF, действия администратора с правилами.

Единая точка записи. Запись не тормозит запрос: событие кладётся в очередь, фоновая задача пишет пачками в таблицу `security_events` отдельным (фоновым) соединением, без долгих
транзакций. Частота ограничена (на процесс — не более MAX_PER_MINUTE событий в минуту, неудачные входы — не чаще 20 в минуту на адрес): атака не заполнит журнал; пропущенные
события считаются в Redis. Срок хранения и максимальный размер — настройка «События безопасности» (удаление старых раз в час, одним экземпляром). Значимые события дублируются в общий журнал
приложения (категория «security»), чтобы попадать во внешнее хранилище журналов.
Секреты не записываются: только адрес, запись (имя), тип события, результат, правило и срок."""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from datetime import datetime, timezone

from sqlalchemy import delete, func, select

log = logging.getLogger("app.security.events")
MAX_PER_MINUTE = 240
FAILED_LOGIN_PER_IP_MINUTE = 20
BATCH = 500

KINDS: dict[str, str] = {
    "login_failed": "Неудачная попытка входа",
    "login_blocked": "Вход отклонён защитой от перебора",
    "captcha_required": "Показана CAPTCHA",
    "captcha_passed": "CAPTCHA пройдена",
    "captcha_failed": "CAPTCHA не пройдена",
    "ip_blocked": "Адрес заблокирован автоматически",
    "ip_blocked_manual": "Адрес заблокирован администратором",
    "ip_unblocked": "Блокировка адреса снята",
    "limit_shared": "Общий адрес: блокировка заменена CAPTCHA",
    "access_denied_net": "Отказ: адрес вне разрешённых подсетей",
    "blocked_ip_request": "Запрос заблокированного адреса",
    "waf_hit": "Срабатывание правила WAF",
    "permission_denied": "Ошибка проверки прав (403)",
    "ip_access_changed": "Изменено ограничение по подсетям",
    "security_settings_changed": "Изменены настройки безопасности",
}
WARN = {"ip_blocked", "ip_blocked_manual", "ip_unblocked", "ip_access_changed", "security_settings_changed", "access_denied_net"}


class SecurityEvents:
    def __init__(self, app):
        self.app = app
        self._q: deque[dict] = deque(maxlen=5000)
        self._minute, self._n = 0, 0
        self._ip_minute: dict[str, tuple[int, int]] = {}
        self._wake = asyncio.Event()
        self._task: asyncio.Task | None = None
        self.dropped = 0

    # ------------------------------------------------------------------ приём
    async def emit(self, kind: str, *, ip: str = "", account: str = "", result: str = "", reason: str = "", rule: str = "", until: float | None = None,
                   component: str = "", level: str = "warn", details: dict | None = None, dedupe: str = "") -> None:
        now_min = int(time.time() // 60)
        if now_min != self._minute:
            self._minute, self._n = now_min, 0
            self._ip_minute = {k: v for k, v in self._ip_minute.items() if v[0] == now_min}
            self._seen: set[str] = set()
        if dedupe:                                                           # одно и то же событие (адрес + путь) — раз в минуту
            key = f"{kind}|{ip}|{dedupe}"
            if key in getattr(self, "_seen", set()):
                return
            if not hasattr(self, "_seen"):
                self._seen = set()
            self._seen.add(key)
        if kind == "login_failed" and ip:
            m, c = self._ip_minute.get(ip, (now_min, 0))
            self._ip_minute[ip] = (now_min, c + 1)
            if c + 1 > FAILED_LOGIN_PER_IP_MINUTE:
                self.dropped += 1
                return
        self._n += 1
        if self._n > MAX_PER_MINUTE:
            self.dropped += 1
            try:
                await self.app.state.redis.incr("sec:events:dropped")
            except Exception:  # noqa: BLE001
                pass
            return
        row = {"at": datetime.now(timezone.utc), "ip": (ip or None) and ip[:64], "account": (account or None) and account[:200], "kind": kind[:40], "result": result[:16],
               "reason": (reason or None) and reason[:300], "rule": (rule or None) and rule[:80], "until": datetime.fromtimestamp(until, tz=timezone.utc) if until else None,
               "component": component[:24], "details": details or None}
        self._q.append(row)
        self._wake.set()
        if kind in WARN:
            j = getattr(self.app.state, "journal", None)
            if j is not None:
                j.emit("security", kind, level="warn", user=account or None, ip=ip or None, message=reason or KINDS.get(kind, kind), data={"result": result, "rule": rule, "component": component})

    # ------------------------------------------------------------------ запись
    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="security-events")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None
        await self.flush()

    async def flush(self) -> int:
        from ..models import SecurityEvent  # noqa: PLC0415

        batch = []
        while self._q and len(batch) < BATCH:
            batch.append(self._q.popleft())
        if not batch:
            return 0
        try:
            async with (getattr(self.app.state, "bg_session_maker", None) or self.app.state.session_maker)() as db:
                db.add_all(SecurityEvent(**r) for r in batch)
                await db.commit()
        except Exception:  # noqa: BLE001 — база недоступна: события возвращаются в очередь (в пределах её размера) и пишутся позже
            self._q.extendleft(reversed(batch))
            log.warning("Не удалось записать события безопасности: повторим позже", exc_info=False)
            return 0
        return len(batch)

    async def _run(self) -> None:
        last_prune = 0.0
        while True:
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                pass
            self._wake.clear()
            await self.flush()
            if time.monotonic() - last_prune > 3600:
                last_prune = time.monotonic()
                await self.prune()

    async def prune(self) -> int:
        """Раз в час, одним экземпляром: удаляет записи старше срока и сверх предела размера."""
        from ..models import SecurityEvent  # noqa: PLC0415

        try:
            if not await self.app.state.redis.set("sec:events:prune", "1", nx=True, ex=3000):
                return 0
            async with (getattr(self.app.state, "bg_session_maker", None) or self.app.state.session_maker)() as db:
                cfg = await self.app.state.settings_svc.get(db, "security_log")
                cutoff = datetime.fromtimestamp(time.time() - cfg.retention_days * 86400, tz=timezone.utc)
                n = (await db.execute(delete(SecurityEvent).where(SecurityEvent.at < cutoff))).rowcount or 0
                total = (await db.execute(select(func.count()).select_from(SecurityEvent))).scalar() or 0
                if total > cfg.max_rows:
                    edge = (await db.execute(select(SecurityEvent.id).order_by(SecurityEvent.id.desc()).offset(cfg.max_rows).limit(1))).scalar()
                    if edge is not None:
                        n += (await db.execute(delete(SecurityEvent).where(SecurityEvent.id <= edge))).rowcount or 0
                await db.commit()
                return int(n)
        except Exception:  # noqa: BLE001
            log.warning("Не удалось почистить журнал событий безопасности", exc_info=False)
            return 0
