"""Администрирование → Безопасность → Доверенные подсети: ограничение доступа по IP-адресам и подсетям с защитой от самоизоляции администратора.
Только для администратора. Все изменения — в аудит. Подробности механизма: security/allownets.py."""
from __future__ import annotations

import time
import uuid
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import SessionUser, client_ip, get_db, require_admin
from ..models import AllowedNetwork, utcnow
from ..security.allownets import now_iso, parse_cidr
from ..security.ipres import in_nets
from ..services.audit import write_audit

router = APIRouter(prefix="/admin/security/ip-access", tags=["admin-security"])

COVERED = [
    {"id": "api", "title": "Интерфейс и API (/api)", "detail": "Вход, комнаты, чаты, протоколы, административные разделы."},
    {"id": "ws", "title": "WebSocket событий и чатов", "detail": "Уведомления и сообщения в реальном времени (через backend)."},
    {"id": "files", "title": "Загрузки и выгрузки", "detail": "Файлы чатов, протоколы, записи, снимки доски."},
    {"id": "guest", "title": "Гостевые ссылки", "detail": "Гость из неразрешённой сети тоже получит отказ — если не включено исключение ниже."},
    {"id": "public_api", "title": "Публичный API (/api/public)", "detail": "У ключей есть свой список адресов; общее ограничение действует, если не включено исключение ниже."},
    {"id": "outlook", "title": "API надстройки Outlook и календаря", "detail": "Запросы приходят из рабочих мест сотрудников."},
]
NOT_COVERED = [
    {"id": "static", "title": "Статические файлы интерфейса", "detail": "Скрипты и стили отдаёт nginx контейнера web: без входа в систему они бесполезны, но сами не закрываются. Закройте на внешнем WAF/межсетевом экране, если нужно."},
    {"id": "livekit_ws", "title": "Сигналинг LiveKit (/livekit/)", "detail": "Проксируется напрямую в LiveKit. Без токена, который выдаёт backend, подключиться нельзя, но порт открыт — закрывается внешним firewall/WAF."},
    {"id": "media", "title": "Медиа LiveKit: UDP/TCP 7881 и диапазон 50000–60000", "detail": "Не HTTP. Только внешний межсетевой экран и правила NAT."},
    {"id": "turn", "title": "TURN/STUN (3478, 5349, релей)", "detail": "Не HTTP. Только внешний межсетевой экран."},
    {"id": "sip", "title": "SIP и RTP телефонии", "detail": "Свои списки адресов в «Политике телефонии» и на АТС; HTTP-ограничение их не касается."},
    {"id": "ssh_db", "title": "SSH, PostgreSQL, Redis и другие порты сервера", "detail": "Вне приложения. Не публикуйте их в интернет."},
]


def _row(r: AllowedNetwork) -> dict:
    return {"id": str(r.id), "cidr": r.cidr, "comment": r.comment, "enabled": r.enabled, "updated_at": r.updated_at, "updated_by": r.updated_by}


async def _state(request: Request, db: AsyncSession) -> dict:
    svc = request.app.state.settings_svc
    g = await svc.get(db, "ip_access")
    rows = (await db.execute(select(AllowedNetwork).order_by(AllowedNetwork.created_at))).scalars().all()
    holder = request.app.state.allownets
    await holder.refresh(svc, request.app.state.session_maker)          # свежее состояние: другой экземпляр мог изменить политику или сработал автооткат
    pol = holder.policy
    ip = client_ip(request)
    enabled_nets = [__import__("ipaddress").ip_network(r.cidr) for r in rows if r.enabled]
    left = None
    if pol.pending_until is not None and g.enabled:
        left = max(0, int(pol.pending_until - time.time()))
    denied = 0
    try:
        h = await request.app.state.redis.hgetall("sec:stats:denied_net")
        cutoff = time.strftime("%Y%m%d%H", time.gmtime(time.time() - 24 * 3600))
        denied = sum(int(v) for k, v in h.items() if k > cutoff)
    except Exception:  # noqa: BLE001
        pass
    warnings: list[str] = []
    if g.enabled and not pol.enabled and not pol.forced_off:
        warnings.append("Ограничение включено, но не действует: пробный срок вышел без подтверждения либо список разрешённых сетей пуст.")
    if pol.forced_off:
        warnings.append("Ограничение выключено аварийным параметром IP_ACCESS_DISABLE в .env сервера. Уберите параметр и перезапустите backend, чтобы вернуть настройки из этого раздела.")
    return {"enabled": bool(g.enabled), "active": pol.enabled, "forced_off": pol.forced_off, "exempt_guests": g.exempt_guests, "exempt_public_api": g.exempt_public_api, "trial_minutes": g.trial_minutes,
            "pending": left is not None and left > 0, "seconds_left": left, "confirmed_at": g.confirmed_at, "entries": [_row(r) for r in rows],
            "my_ip": ip, "my_ip_allowed": in_nets(ip, enabled_nets), "denied_24h": denied, "warnings": warnings, "covered": COVERED, "not_covered": NOT_COVERED}


@router.get("")
async def get_state(request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    return await _state(request, db)


async def _commit(request: Request, db: AsyncSession) -> None:
    await db.commit()
    await request.app.state.allownets.refresh(request.app.state.settings_svc, request.app.state.session_maker)


@router.put("/settings")
async def put_settings(request: Request, body: dict[str, Any] = Body(...), su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Включить/выключить ограничение и параметры. Включение — с проверкой списка, адреса администратора и пробным сроком; выключение — сразу."""
    svc = request.app.state.settings_svc
    cur = await svc.get(db, "ip_access")
    ip = client_ip(request)
    rows = (await db.execute(select(AllowedNetwork).where(AllowedNetwork.enabled.is_(True)))).scalars().all()
    nets = [__import__("ipaddress").ip_network(r.cidr) for r in rows]
    patch: dict[str, Any] = {k: body[k] for k in ("exempt_guests", "exempt_public_api", "trial_minutes") if k in body}
    turning_on = body.get("enabled") is True and not cur.enabled
    if body.get("enabled") is False:
        patch.update(enabled=False, pending_until=None)
    elif turning_on:
        if not nets:
            raise HTTPException(status_code=422, detail="Список разрешённых сетей пуст: ограничение закрыло бы доступ всем. Сначала добавьте хотя бы одну сеть.")
        if not in_nets(ip, nets) and not body.get("acknowledge_lockout"):
            raise HTTPException(status_code=409, detail={"code": "admin_not_allowed", "my_ip": ip, "message": f"Ваш адрес {ip} не входит в разрешённые сети. После включения вы потеряете доступ к администрированию. Добавьте свой адрес или подтвердите, что понимаете риск."})
        minutes = int(patch.get("trial_minutes", cur.trial_minutes))
        patch.update(enabled=True, pending_until=now_iso(minutes * 60), confirmed_at=None, activated_by=su.display_name)
    try:
        changed = await svc.update(db, "ip_access", patch, actor=su.display_name)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=str(exc)) from None
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="security.ip_access.settings", target_type="settings", target_id="ip_access", ip=ip,
                      details={"changed": changed, "enabled": patch.get("enabled"), "admin_allowed": in_nets(ip, nets), "acknowledged": bool(body.get("acknowledge_lockout"))})
    await _commit(request, db)
    await request.app.state.secevents.emit("ip_access_changed", ip=ip, account=su.display_name, result="ok", reason="включено" if turning_on else "выключено" if body.get("enabled") is False else "параметры изменены",
                                           component="admin", level="warn")
    return await _state(request, db)


@router.post("/confirm")
async def confirm(request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Подтверждение из разрешённой сети: сам этот запрос прошёл через ограничение — значит, доступ сохранён. Отменяет автоматический откат."""
    svc = request.app.state.settings_svc
    cur = await svc.get(db, "ip_access")
    if not cur.enabled:
        raise HTTPException(status_code=409, detail="Ограничение не включено.")
    await svc.update(db, "ip_access", {"pending_until": None, "confirmed_at": now_iso()}, actor=su.display_name)
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="security.ip_access.confirm", target_type="settings", target_id="ip_access", ip=client_ip(request))
    await _commit(request, db)
    return await _state(request, db)


def _would_lock_admin(rows: list[AllowedNetwork], ip: str) -> bool:
    import ipaddress  # noqa: PLC0415

    return not in_nets(ip, [ipaddress.ip_network(r.cidr) for r in rows if r.enabled])


async def _guard_change(request: Request, db: AsyncSession, su: SessionUser, body: dict, rows_after: list[AllowedNetwork]) -> None:
    """Изменение списка при действующем ограничении: список не должен опустеть; если админ теряет доступ — нужно явное подтверждение и заново запускается пробный срок."""
    svc = request.app.state.settings_svc
    cur = await svc.get(db, "ip_access")
    if not cur.enabled:
        return
    if not any(r.enabled for r in rows_after):
        raise HTTPException(status_code=422, detail="После изменения не останется ни одной разрешённой сети: доступ закрылся бы для всех. Сначала добавьте другую сеть либо выключите ограничение.")
    if _would_lock_admin(rows_after, client_ip(request)):
        if not body.get("acknowledge_lockout"):
            raise HTTPException(status_code=409, detail={"code": "admin_not_allowed", "my_ip": client_ip(request), "message": "После этого изменения ваш адрес не будет входить в разрешённые сети, и вы потеряете доступ к администрированию."})
        await svc.update(db, "ip_access", {"pending_until": now_iso(cur.trial_minutes * 60), "confirmed_at": None}, actor=su.display_name)       # изменение без подтверждения откатится само


def _check(body: dict) -> tuple[str, str]:
    try:
        return parse_cidr(str(body.get("cidr") or "")), str(body.get("comment") or "").strip()[:300]
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


@router.post("/entries", status_code=201)
async def add_entry(request: Request, body: dict[str, Any] = Body(...), su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    cidr, comment = _check(body)
    if (await db.execute(select(AllowedNetwork).where(AllowedNetwork.cidr == cidr))).scalars().first():
        raise HTTPException(status_code=409, detail="Такая запись уже есть.")
    now = utcnow()
    row = AllowedNetwork(id=uuid.uuid4(), cidr=cidr, comment=comment, enabled=bool(body.get("enabled", True)), created_at=now, updated_at=now, updated_by=su.display_name)
    db.add(row)
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="security.ip_access.entry_add", target_type="allowed_network", target_id=cidr, ip=client_ip(request),
                      details={"comment": comment, "enabled": row.enabled})
    await _commit(request, db)
    return await _state(request, db)


@router.patch("/entries/{entry_id}")
async def update_entry(entry_id: uuid.UUID, request: Request, body: dict[str, Any] = Body(...), su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    row = await db.get(AllowedNetwork, entry_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Запись не найдена.")
    new_cidr = row.cidr
    if "cidr" in body:
        new_cidr, _ = _check({"cidr": body["cidr"]})
        dup = (await db.execute(select(AllowedNetwork).where(AllowedNetwork.cidr == new_cidr, AllowedNetwork.id != row.id))).scalars().first()
        if dup:
            raise HTTPException(status_code=409, detail="Такая запись уже есть.")
    others = (await db.execute(select(AllowedNetwork).where(AllowedNetwork.id != row.id))).scalars().all()
    probe = AllowedNetwork(id=row.id, cidr=new_cidr, comment="", enabled=bool(body.get("enabled", row.enabled)), created_at=row.created_at, updated_at=row.updated_at, updated_by="")
    await _guard_change(request, db, su, body, [*others, probe])
    before = {"cidr": row.cidr, "enabled": row.enabled, "comment": row.comment}
    row.cidr, row.enabled = new_cidr, probe.enabled
    if "comment" in body:
        row.comment = str(body["comment"] or "").strip()[:300]
    row.updated_at, row.updated_by = utcnow(), su.display_name
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="security.ip_access.entry_update", target_type="allowed_network", target_id=row.cidr, ip=client_ip(request),
                      details={"before": before, "after": {"cidr": row.cidr, "enabled": row.enabled, "comment": row.comment}})
    await _commit(request, db)
    return await _state(request, db)


@router.delete("/entries/{entry_id}")
async def delete_entry(entry_id: uuid.UUID, request: Request, body: dict[str, Any] = Body(default={}), su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    row = await db.get(AllowedNetwork, entry_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Запись не найдена.")
    others = (await db.execute(select(AllowedNetwork).where(AllowedNetwork.id != row.id))).scalars().all()
    await _guard_change(request, db, su, body, list(others))
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="security.ip_access.entry_delete", target_type="allowed_network", target_id=row.cidr, ip=client_ip(request),
                      details={"comment": row.comment})
    await db.delete(row)
    await _commit(request, db)
    return await _state(request, db)
