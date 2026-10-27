"""Снимки настроек WAF и списка заблокированных адресов в памяти процесса (проверка на каждый запрос — без обращений к базе и Redis)."""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

from .ipres import parse_nets
from .waf import RULES

log = logging.getLogger("app.security.wafcfg")


@dataclass(frozen=True)
class WafPolicy:
    mode: str = "observe"                         # off | observe | protect
    rules: dict = field(default_factory=dict)     # rule → inherit | off | observe | block
    probe_threshold: int = 40
    probe_block_minutes: int = 10
    exclude_paths: tuple = ()
    exclude_nets: tuple = ()

    def action(self, rule: str) -> str:
        """none | observe | block — что делать с сработавшим правилом."""
        if self.mode == "off":
            return "none"
        own = self.rules.get(rule, "inherit")
        if own == "off":
            return "none"
        if self.mode == "observe":
            return "observe"                       # «Наблюдение»: ничего не блокируется, даже явно включённое правило
        if own in ("observe", "block"):
            return own
        return "block" if RULES.get(rule, {}).get("verified") else "observe"      # новые эвристические правила — сначала в наблюдении


class PolicyHolder:
    def __init__(self):
        self.waf = WafPolicy()
        self.blocked: dict[str, float] = {}       # адрес (ключ счётчиков) → когда закончится блокировка
        self._at = 0.0

    async def refresh(self, svc, session_maker, redis) -> None:
        try:
            async with session_maker() as db:
                g = await svc.get(db, "waf")
            self.waf = WafPolicy(g.mode, dict(g.rules or {}), g.probe_threshold, g.probe_block_minutes, tuple(p for p in (x.strip() for x in g.exclude_paths.splitlines()) if p), tuple(parse_nets(g.exclude_ips)))
        except Exception as exc:  # noqa: BLE001
            log.warning("Не удалось обновить настройки WAF (%s): действуют прежние", type(exc).__name__)
        try:
            now = time.time()
            await redis.zremrangebyscore("sec:blocks", 0, now)
            self.blocked = {k: float(v) for k, v in await redis.zrange("sec:blocks", 0, 1000, withscores=True)}
        except Exception:  # noqa: BLE001 — Redis недоступен: прежний список; проверка входа сама закроется (fail closed)
            pass

    async def run(self, svc, session_maker, redis, every: float = 3.0) -> None:
        while True:
            await self.refresh(svc, session_maker, redis)
            await asyncio.sleep(every)

    def is_blocked(self, ipk: str) -> bool:
        return self.blocked.get(ipk, 0) > time.time()
