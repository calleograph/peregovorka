"""Администрирование → Безопасность → События и блокировки: журнал событий безопасности, заблокированные адреса (снять, посмотреть активность, заблокировать вручную),
сводка состояния защиты и настройки WAF. Только для администратора; все действия — в аудит."""
from __future__ import annotations

import csv
import io
import ipaddress
import json
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import SessionUser, client_ip, get_db, require_admin
from ..models import SecurityEvent
from ..security import waf
from ..security.events import KINDS
from ..security.ipres import in_nets, ip_key, norm_ip
from ..services.audit import write_audit

router = APIRouter(prefix="/admin/security", tags=["admin-security"])
MAX_MANUAL_MINUTES = 7 * 24 * 60
EXPORT_LIMIT = 50_000


def _when(v: str | None, default: datetime) -> datetime:
    if not v:
        return default
    try:
        d = datetime.fromisoformat(v)
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        raise HTTPException(status_code=422, detail="Дата в формате ГГГГ-ММ-ДДTЧЧ:ММ") from None


def _filters(q, *, frm, to, ip, account, kind, result, rule, text):
    now = datetime.now(timezone.utc)
    q = q.where(SecurityEvent.at >= _when(frm, now - timedelta(days=1)), SecurityEvent.at <= _when(to, now + timedelta(minutes=5)))
    if ip:
        q = q.where(SecurityEvent.ip.like(f"{ip.strip()[:64]}%"))
    if account:
        q = q.where(func.lower(SecurityEvent.account).like(f"%{account.strip().lower()[:100]}%"))
    if kind:
        q = q.where(SecurityEvent.kind == kind)
    if result:
        q = q.where(SecurityEvent.result == result)
    if rule:
        q = q.where(SecurityEvent.rule.like(f"{rule[:60]}%"))
    if text:
        t = f"%{text.strip().lower()[:100]}%"
        q = q.where(or_(func.lower(SecurityEvent.reason).like(t), func.lower(SecurityEvent.rule).like(t)))
    return q


def _row(e: SecurityEvent) -> dict:
    return {"id": e.id, "at": e.at, "ip": e.ip, "account": e.account, "kind": e.kind, "kind_title": KINDS.get(e.kind, e.kind), "result": e.result, "reason": e.reason, "rule": e.rule,
            "until": e.until, "component": e.component, "details": e.details}


async def _fresh(request: Request) -> None:
    """Перед чтением — дописать накопленные события (фоновая запись раз в 2 секунды): администратор видит то, что произошло только что."""
    try:
        await request.app.state.secevents.flush()
    except Exception:  # noqa: BLE001
        pass


@router.get("/events")
async def list_events(request: Request, frm: str | None = Query(None, alias="from"), to: str | None = None, ip: str = "", account: str = "", kind: str = "", result: str = "", rule: str = "",
                      q: str = "", limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0), su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    await _fresh(request)
    base = _filters(select(SecurityEvent), frm=frm, to=to, ip=ip, account=account, kind=kind, result=result, rule=rule, text=q)
    total = (await db.execute(_filters(select(func.count()).select_from(SecurityEvent), frm=frm, to=to, ip=ip, account=account, kind=kind, result=result, rule=rule, text=q))).scalar() or 0
    rows = (await db.execute(base.order_by(SecurityEvent.id.desc()).limit(limit).offset(offset))).scalars().all()
    return {"total": total, "items": [_row(e) for e in rows], "kinds": [{"id": k, "title": t} for k, t in KINDS.items()]}


@router.get("/events/export")
async def export_events(request: Request, format: str = Query("csv", pattern="^(csv|json)$"), frm: str | None = Query(None, alias="from"), to: str | None = None, ip: str = "", account: str = "",
                        kind: str = "", result: str = "", rule: str = "", q: str = "", su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Отчёт: те же фильтры, не более 50 000 строк; выгрузка записывается в аудит."""
    await _fresh(request)
    rows = (await db.execute(_filters(select(SecurityEvent), frm=frm, to=to, ip=ip, account=account, kind=kind, result=result, rule=rule, text=q).order_by(SecurityEvent.id.desc()).limit(EXPORT_LIMIT))).scalars().all()
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="security.events.export", target_type="security_events", target_id="-", ip=client_ip(request),
                      details={"format": format, "rows": len(rows), "kind": kind, "ip": ip, "account": account})
    await db.commit()
    if format == "json":
        return Response(json.dumps([_row(e) for e in rows], ensure_ascii=False, default=str, indent=1), media_type="application/json", headers={"Content-Disposition": 'attachment; filename="security-events.json"'})
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["Дата и время (UTC)", "IP-адрес", "Учётная запись", "Тип события", "Результат", "Причина", "Правило", "Блокировка до", "Компонент"])
    for e in rows:
        w.writerow([e.at.isoformat(), e.ip or "", e.account or "", KINDS.get(e.kind, e.kind), e.result, e.reason or "", e.rule or "", e.until.isoformat() if e.until else "", e.component])
    return Response("﻿" + out.getvalue(), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": 'attachment; filename="security-events.csv"'})


# --------------------------------------------------------------------------------------------- заблокированные адреса
@router.get("/blocks")
async def list_blocks(request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    await _fresh(request)
    blocks = await request.app.state.login_guard.list_blocks()
    now = datetime.now(timezone.utc)
    out = []
    for b in blocks:
        ev = (await db.execute(select(SecurityEvent).where(SecurityEvent.ip == b["ip"].split("/")[0] if "/" in b["ip"] else SecurityEvent.ip == b["ip"]).order_by(SecurityEvent.id.desc()).limit(5))).scalars().all()
        fails = (await db.execute(select(func.count()).select_from(SecurityEvent).where(SecurityEvent.ip == b["ip"], SecurityEvent.kind == "login_failed", SecurityEvent.at >= now - timedelta(days=1)))).scalar() or 0
        out.append({**b, "since_iso": datetime.fromtimestamp(b["since"], tz=timezone.utc), "until_iso": datetime.fromtimestamp(b["until"], tz=timezone.utc), "failures_24h": fails,
                    "recent": [{"at": e.at, "kind_title": KINDS.get(e.kind, e.kind), "reason": e.reason, "rule": e.rule} for e in ev]})
    return {"items": out}


@router.post("/blocks/unblock")
async def unblock(request: Request, body: dict[str, Any] = Body(...), su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Снять блокировку адреса. Другие меры защиты (счётчики неудач, история повторов, CAPTCHA) не отключаются и не обнуляются."""
    ip = norm_ip(str(body.get("ip") or "").split("/")[0])
    if ip is None:
        raise HTTPException(status_code=422, detail="Некорректный IP-адрес")
    done = await request.app.state.login_guard.unblock_ip(ip)
    if not done:
        raise HTTPException(status_code=404, detail="Блокировка этого адреса не найдена (возможно, уже закончилась).")
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="security.ip.unblock", target_type="ip", target_id=ip, ip=client_ip(request), details={})
    await db.commit()
    await request.app.state.secevents.emit("ip_unblocked", ip=ip, account=su.display_name, result="ok", reason="блокировка снята администратором", component="admin")
    await request.app.state.policies.refresh(request.app.state.settings_svc, request.app.state.session_maker, request.app.state.redis)
    return {"ok": True}


@router.post("/blocks")
async def block_manually(request: Request, body: dict[str, Any] = Body(...), su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Временная блокировка адреса вручную: причина и срок обязательны, срок ограничен (не бессрочно). Собственный адрес и адреса контейнеров заблокировать нельзя."""
    ip = norm_ip(str(body.get("ip") or ""))
    if ip is None:
        raise HTTPException(status_code=422, detail="Укажите корректный IP-адрес")
    reason = str(body.get("reason") or "").strip()
    if len(reason) < 3:
        raise HTTPException(status_code=422, detail="Укажите причину блокировки")
    try:
        minutes = int(body.get("minutes"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="Укажите срок блокировки в минутах") from None
    if not 1 <= minutes <= MAX_MANUAL_MINUTES:
        raise HTTPException(status_code=422, detail=f"Срок — от 1 минуты до {MAX_MANUAL_MINUTES // 1440} суток: бессрочных блокировок нет")
    if ip_key(ip) == ip_key(client_ip(request)):
        raise HTTPException(status_code=422, detail="Нельзя заблокировать собственный адрес: вы потеряли бы доступ.")
    nets = request.app.state.netcfg.cfg
    if in_nets(ip, list(nets.trusted)):
        raise HTTPException(status_code=422, detail="Этот адрес входит в доверенные прокси: блокировка отключила бы всех пользователей за ним.")
    lp, _ = await request.app.state.login_guard.cfg()
    shared = await request.app.state.login_guard.is_shared(ip_key(ip), lp, time.time())
    if shared and not body.get("confirm_shared"):
        raise HTTPException(status_code=409, detail={"code": "shared_address", "message": "С этого адреса входили несколько разных пользователей или он указан как общий (NAT, прокси). Блокировка закроет вход всем им. Подтвердите, если это нужно."})
    rec = await request.app.state.login_guard.block_ip(ip, minutes, reason, rule="manual", manual=True, actor=su.display_name)
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="security.ip.block", target_type="ip", target_id=ip, ip=client_ip(request), details={"minutes": minutes, "reason": reason[:200], "shared": shared})
    await db.commit()
    await request.app.state.secevents.emit("ip_blocked_manual", ip=ip, account=su.display_name, result="blocked", rule="manual", reason=reason[:200], until=rec["until"], component="admin")
    await request.app.state.policies.refresh(request.app.state.settings_svc, request.app.state.session_maker, request.app.state.redis)
    return {"ok": True, "until": rec["until"]}


@router.get("/activity")
async def activity(ip: str, request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """«Посмотреть активность»: хронология событий адреса за двое суток с пояснением сработавших правил."""
    addr = norm_ip(ip)
    if addr is None:
        raise HTTPException(status_code=422, detail="Некорректный IP-адрес")
    await _fresh(request)
    now = datetime.now(timezone.utc)
    rows = (await db.execute(select(SecurityEvent).where(SecurityEvent.ip == addr, SecurityEvent.at >= now - timedelta(days=2)).order_by(SecurityEvent.id.desc()).limit(300))).scalars().all()
    expl = {"ip_failures": "много неудачных попыток входа с этого адреса за окно подсчёта", "spray": "попытки входа в много разных учётных записей с одного адреса (подбор по списку)",
            "account_guard": "по учётной записи накопилось много неудач: попытки с незнакомых адресов не доходят до каталога", "rate_ip": "слишком много запросов ко входу в минуту",
            "manual": "заблокирован администратором", "allowed_networks": "адрес вне разрешённых подсетей", "waf_api_probe": "перебор путей (много ответов 404/405)"}
    return {"ip": addr, "items": [{**_row(e), "explain": expl.get((e.rule or "").replace("waf:", ""), None)} for e in rows]}


# --------------------------------------------------------------------------------------------- сводка и WAF
@router.get("/overview")
async def overview(request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Одним взглядом: что включено, сколько адресов заблокировано, сколько попыток отклонено, какие правила срабатывали, что требует внимания."""
    svc, r = request.app.state.settings_svc, request.app.state.redis
    await _fresh(request)
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=1)

    async def count(kind: str, result: str | None = None) -> int:
        q = select(func.count()).select_from(SecurityEvent).where(SecurityEvent.kind == kind, SecurityEvent.at >= since)
        if result:
            q = q.where(SecurityEvent.result == result)
        return int((await db.execute(q)).scalar() or 0)

    lp, cp = await request.app.state.login_guard.cfg()
    waf_cfg = await svc.get(db, "waf")
    ip_access = await svc.get(db, "ip_access")
    blocked_now = len(await request.app.state.login_guard.list_blocks())
    top = (await db.execute(select(SecurityEvent.rule, func.count()).where(SecurityEvent.at >= since, SecurityEvent.rule.is_not(None)).group_by(SecurityEvent.rule).order_by(func.count().desc()).limit(6))).all()
    attention: list[str] = []
    pol = request.app.state.allownets.policy
    if pol.pending_until is not None and ip_access.enabled:
        attention.append("Ограничение по подсетям ждёт подтверждения: иначе оно выключится само.")
    if not lp.enabled:
        attention.append("Защита входа от перебора выключена.")
    if cp.mode == "off":
        attention.append("CAPTCHA выключена: после серии неудач проверка не потребуется.")
    if waf_cfg.mode == "off":
        attention.append("Встроенный WAF выключен.")
    dropped = int(await r.get("sec:events:dropped") or 0)
    if dropped:
        attention.append(f"Журнал событий пропустил {dropped} событий из-за высокой частоты (защита от переполнения).")
    return {"protection": {"login_protection": lp.enabled, "captcha_mode": cp.mode, "captcha_kind": cp.kind, "ip_access": {"enabled": bool(ip_access.enabled), "active": pol.enabled, "networks": len(pol.nets)},
                           "waf_mode": waf_cfg.mode},
            "blocked_now": blocked_now, "failed_logins_24h": await count("login_failed"), "login_blocked_24h": await count("login_blocked"), "captcha_shown_24h": await count("captcha_required"),
            "captcha_passed_24h": await count("captcha_passed"), "denied_net_24h": await count("access_denied_net"), "waf_hits_24h": await count("waf_hit"),
            "top_rules": [{"rule": a, "count": int(b)} for a, b in top], "attention": attention}


def _rules_state(cfg) -> list[dict]:
    return [{"id": rid, "title": meta["title"], "blurb": meta["blurb"], "verified": meta["verified"], "mode": (cfg.rules or {}).get(rid, "inherit")} for rid, meta in waf.RULES.items()]


@router.get("/waf")
async def waf_state(request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    cfg = await request.app.state.settings_svc.get(db, "waf")
    r = request.app.state.redis
    rules = _rules_state(cfg)
    for x in rules:
        for res in ("observed", "blocked"):
            h = await r.hgetall(f"sec:stats:waf:{x['id']}:{res}")
            cutoff = time.strftime("%Y%m%d%H", time.gmtime(time.time() - 24 * 3600))
            x[f"{res}_24h"] = sum(int(v) for k, v in h.items() if k > cutoff)
        last = await r.get(f"sec:stats:waf_last:{x['id']}")
        x["last_seen"] = datetime.fromtimestamp(int(last), tz=timezone.utc) if last else None
    return {"mode": cfg.mode, "probe_threshold": cfg.probe_threshold, "probe_block_minutes": cfg.probe_block_minutes, "exclude_paths": cfg.exclude_paths, "exclude_ips": cfg.exclude_ips, "rules": rules}


@router.put("/waf")
async def waf_update(request: Request, body: dict[str, Any] = Body(...), su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    svc = request.app.state.settings_svc
    patch = {k: body[k] for k in ("mode", "probe_threshold", "probe_block_minutes", "exclude_paths", "exclude_ips", "rules") if k in body}
    try:
        changed = await svc.update(db, "waf", patch, actor=su.display_name)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=str(exc)) from None
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="security.waf.update", target_type="settings", target_id="waf", ip=client_ip(request),
                      details={"changed": changed, "mode": patch.get("mode"), "rules": patch.get("rules")})
    await db.commit()
    await request.app.state.policies.refresh(svc, request.app.state.session_maker, request.app.state.redis)
    await request.app.state.secevents.emit("security_settings_changed", ip=client_ip(request), account=su.display_name, result="ok", reason="настройки WAF изменены", component="admin")
    return await waf_state(request, su, db)
