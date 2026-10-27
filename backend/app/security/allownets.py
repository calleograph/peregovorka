"""Доверенные подсети: ограничение доступа к Peregovorka разрешёнными IP-адресами и подсетями.

Что охватывает: всё, что проходит через backend — API, WebSocket событий, выгрузки и документы, публичный API, гостевые ссылки, API надстройки Outlook.
Чего НЕ охватывает: статические файлы интерфейса (их отдаёт nginx контейнера web), сигналинг LiveKit (`/livekit/` проксируется напрямую), медиа-порты LiveKit/TURN/SIP
и любые другие порты вне HTTP: их закрывает внешний межсетевой экран (см. список в разделе администрирования и вики).

Безопасное включение: при включении запускается «пробный» срок — подтвердить настройку нужно запросом из РАЗРЕШЁННОЙ сети; не подтвердили — ограничение само выключается.
Независимо от интерфейса: консольная команда `scripts/security-reset.sh` и переменная `IP_ACCESS_DISABLE=1` в `.env`.
Запросы без заголовка web (`X-PG-Peer`) с частного адреса — внутренний трафик контейнеров (webhook LiveKit и т. п.) — ограничением не затрагиваются.
Не путать с «доверенными прокси» (security/ipres.py): те сообщают настоящий адрес клиента; здесь — какие клиентские сети допущены.
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select, update

from .ipres import in_nets, ip_key

log = logging.getLogger("app.security.allownets")
REFRESH_S = 5
ALWAYS_OPEN = ("/api/v1/health",)          # проверки готовности из мониторинга: ничего чувствительного не отдают
MESSAGE = "Доступ к сервису ограничен. Обратитесь к администратору организации."


@dataclass(frozen=True)
class Policy:
    enabled: bool = False                        # действует ли ограничение прямо сейчас (с учётом пробного срока и аварийного выключателя)
    nets: tuple = ()
    exempt_guests: bool = False
    exempt_public_api: bool = False
    pending_until: float | None = None           # пробный срок: до этого времени нужно подтвердить настройку из разрешённой сети
    forced_off: bool = False                     # выключено переменной окружения IP_ACCESS_DISABLE
    configured_enabled: bool = False


def parse_cidr(raw: str) -> str:
    """Адрес или подсеть → каноничная запись. Ошибка — ValueError с понятным текстом."""
    s = (raw or "").strip()
    if not s or len(s) > 64:
        raise ValueError("Укажите IP-адрес или подсеть (например 192.0.2.10, 198.51.100.0/24, 2001:db8::/32)")
    try:
        net = ipaddress.ip_network(s, strict=False)
    except ValueError:
        raise ValueError(f"«{s[:64]}» — не IP-адрес и не подсеть. Примеры: 192.0.2.10, 198.51.100.0/24, 2001:db8::/32") from None
    if net.prefixlen == 0:
        raise ValueError("Подсеть /0 разрешает всё — тогда ограничение не нужно. Укажите конкретные сети.")
    return str(net)


def is_internal(scope_headers: dict, tcp_peer: str | None, accept_nets: list) -> bool:
    """Внутренний трафик контейнеров: пришёл не через web (нет X-PG-Peer) и с частного/локального адреса."""
    return not scope_headers.get("x-pg-peer") and in_nets(tcp_peer, accept_nets)


def exempt_path(path: str, pol: Policy) -> bool:
    if path.startswith(ALWAYS_OPEN):
        return True
    if pol.exempt_guests and (path.startswith("/api/v1/guest/") or path.startswith("/api/v1/captcha/")):
        return True
    return bool(pol.exempt_public_api and path.startswith("/api/public/"))


class AllowHolder:
    """Снимок политики в памяти процесса: проверка на каждый запрос без обращения к базе. Обновляется фоновой задачей и сразу после записи в админке."""

    def __init__(self, settings):
        self._s = settings
        self.policy = Policy()
        self.ready = False

    async def refresh(self, svc, session_maker) -> None:
        from ..models import AllowedNetwork  # noqa: PLC0415

        try:
            async with session_maker() as db:
                g = await svc.get(db, "ip_access")
                rows = (await db.execute(select(AllowedNetwork.cidr).where(AllowedNetwork.enabled.is_(True)))).scalars().all()
        except Exception as exc:  # noqa: BLE001 — сбой базы не должен ни включать, ни выключать ограничение: остаётся прежняя политика
            log.warning("Не удалось обновить политику доверенных подсетей (%s): действует прежняя", type(exc).__name__)
            return
        pending = _ts(g.pending_until)
        forced = bool(getattr(self._s, "ip_access_disable", False))
        active = bool(g.enabled) and not forced and bool(rows) and not (pending is not None and time.time() > pending)
        self.policy = Policy(active, tuple(ipaddress.ip_network(c) for c in rows), bool(g.exempt_guests), bool(g.exempt_public_api), pending, forced, bool(g.enabled))
        self.ready = True
        if g.enabled and pending is not None and time.time() > pending and not forced:
            await self._auto_revert(svc, session_maker)

    async def _auto_revert(self, svc, session_maker) -> None:
        """Пробный срок вышел без подтверждения — ограничение выключается само (администратор мог потерять доступ). Запись идемпотентна: выигрывает любой экземпляр."""
        try:
            async with session_maker() as db:
                g = await svc.get(db, "ip_access")
                if not (g.enabled and _ts(g.pending_until) is not None and time.time() > _ts(g.pending_until)):
                    return
                await svc.update(db, "ip_access", {"enabled": False, "pending_until": None}, actor="(автооткат)")
                from ..services.audit import write_audit  # noqa: PLC0415

                await write_audit(db, actor_user_id=None, actor_name="(система)", action="security.ip_access.auto_revert", target_type="settings", target_id="ip_access",
                                  actor_type="system", details={"reason": "пробный срок вышел без подтверждения"})
                await db.commit()
            log.warning("Ограничение по подсетям выключено автоматически: пробный срок вышел без подтверждения")
        except Exception:  # noqa: BLE001
            log.warning("Автооткат ограничения по подсетям не выполнен", exc_info=True)

    async def run(self, svc, session_maker) -> None:
        while True:
            await self.refresh(svc, session_maker)
            await asyncio.sleep(REFRESH_S)

    def allowed(self, ip: str) -> bool:
        p = self.policy
        return (not p.enabled) or in_nets(ip, list(p.nets))


def _ts(v) -> float | None:
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v)).timestamp()
    except ValueError:
        return None


def now_iso(plus_s: float = 0) -> str:
    return datetime.fromtimestamp(time.time() + plus_s, tz=timezone.utc).isoformat()
