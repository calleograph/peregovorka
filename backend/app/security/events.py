"""События безопасности (этап 1): неудачные входы, CAPTCHA, блокировки. Единая точка записи; события уходят в журнал приложения (категория «security»).
Секреты не записываются: только адрес, запись (имя), тип события, результат, правило и срок. Частота ограничена, чтобы атака не заполнила журнал."""
from __future__ import annotations

import logging
import time

log = logging.getLogger("app.security.events")
MAX_PER_MINUTE = 120


class SecurityEvents:
    def __init__(self, app):
        self.app = app
        self._minute = 0
        self._n = 0

    async def emit(self, kind: str, *, ip: str = "", account: str = "", result: str = "", reason: str = "", rule: str = "", until: float | None = None,
                   component: str = "", level: str = "warn", details: dict | None = None) -> None:
        now = int(time.time() // 60)
        if now != self._minute:
            self._minute, self._n = now, 0
        self._n += 1
        if self._n > MAX_PER_MINUTE:
            try:
                await self.app.state.redis.incr("sec:events:dropped")
            except Exception:  # noqa: BLE001
                pass
            return
        j = getattr(self.app.state, "journal", None)
        if j is None:
            return
        try:
            j.emit("security", kind, level=level, user=account or None, ip=ip or None, message=reason or kind,
                   data={"result": result, "rule": rule, "until": until, "component": component, **(details or {})})
        except Exception:  # noqa: BLE001
            log.warning("Не удалось записать событие безопасности", exc_info=True)
