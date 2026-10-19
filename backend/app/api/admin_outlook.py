"""Администрирование → Интеграции → Microsoft Outlook: состояние и проверки, установочный пакет, история конференций, проверка доступа к календарю.

Пакет содержит только адреса этой установки и стабильный идентификатор надстройки; секреты сверки (Graph, EWS) в него и в ответы не попадают.
"""
from __future__ import annotations

import asyncio
import socket
import ssl
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Body, Depends, HTTPException, Request, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import SessionUser, client_ip, get_db, require_admin
from ..models import Meeting, Room, utcnow
from ..services import outlook as ol
from ..services import tls_check
from ..services import outlook_sync as osync
from ..services.audit import write_audit
from ..services.outlook_package import build_package

router = APIRouter(prefix="/admin/outlook", tags=["admin-outlook"])
ASSETS = ("/outlook/commands.html", "/outlook/commands.js", "/outlook/auth.html", "/outlook/icon-64.png")


def tls_probe(host: str, port: int, timeout: float = 6.0) -> dict:
    return tls_check.probe(host, port, timeout)


async def _cfg(request: Request, db: AsyncSession):
    return await request.app.state.settings_svc.get(db, "outlook")


@router.get("/status")
async def status(request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Проверки готовности: включено ли, HTTPS-адрес, сертификат, доступность API и файлов надстройки, манифест, сверка."""
    cfg = await _cfg(request, db)
    st = request.app.state
    base = ol.public_url(st.settings.app_public_url, cfg)
    checks: list[dict] = []

    def add(key: str, title: str, ok: bool | None, message: str) -> None:
        checks.append({"key": key, "title": title, "ok": ok, "message": message})

    add("enabled", "Интеграция", True if cfg.enabled else None, "Интеграция включена." if cfg.enabled else "Интеграция выключена: манифест не выдаётся, ссылки /m/… не работают.")
    if not base.startswith("https://"):
        add("url", "Публичный адрес", False, f"«{base or 'не задан'}»: Outlook принимает надстройки только по HTTPS. Задайте публичный HTTPS-адрес ниже или в настройках приложения.")
    else:
        add("url", "Публичный адрес", True, f"{base}")
        u = urlsplit(base)
        tls = await asyncio.to_thread(tls_probe, u.hostname or "", u.port or 443) if not getattr(st, "test_transports", {}).get("outlook") else {"ok": True, "state": "ok", "message": "проверка пропущена в тесте"}
        # Режим: HTTPS завершает внешний обратный прокси (запрос пришёл с X-Forwarded-Proto: https) или сам сервер. Решает проверка ПУБЛИЧНОГО адреса; локальный сертификат не требуется.
        mode = cfg.tls_mode if cfg.tls_mode != "auto" else ("proxy" if request.headers.get("x-forwarded-proto", "").lower() == "https" else "direct")
        mode_text = "HTTPS завершается на внешнем обратном прокси" if mode == "proxy" else "HTTPS на самом сервере"
        add("tls", "Сертификат HTTPS" if tls["state"] != "unreachable" else "Внешняя проверка с сервера недоступна", tls["ok"], f"{tls['message']} Режим: {mode_text}"
            + (" (определён автоматически)." if cfg.tls_mode == "auto" else " (выбран вручную)."))
        checks[-1]["tls"] = {k: tls.get(k) for k in ("host", "port", "state", "issuer", "subject", "not_after", "days_left")} | {"mode": mode, "client_check_url": f"{base}/api/v1/outlook/config"}
        transport = getattr(st, "test_transports", {}).get("outlook")
        bad: list[str] = []
        try:
            async with httpx.AsyncClient(transport=transport, verify=ssl.create_default_context(), timeout=httpx.Timeout(8.0, connect=5.0), follow_redirects=False) as c:
                r = await c.get(f"{base}/api/v1/outlook/config")
                api_ok = r.status_code == 200
                for path in ASSETS:
                    rr = await c.get(base + path)
                    if rr.status_code != 200:
                        bad.append(f"{path}: HTTP {rr.status_code}")
            add("api", "API надстройки", api_ok, "API отвечает." if api_ok else f"/api/v1/outlook/config ответил HTTP {r.status_code}: проверьте обратный прокси.")
            add("files", "Файлы надстройки", not bad, "Страницы и значки надстройки доступны по адресу сервера." if not bad else "Недоступны: " + "; ".join(bad) + ". Проверьте обратный прокси (путь /outlook/).")
        except httpx.HTTPError as exc:
            add("api", "API надстройки", None, f"Сервер приложения не смог обратиться к {base} ({type(exc).__name__}). Из контейнера собственный публичный адрес может быть недоступен — "
                                               "проверьте адрес с рабочей станции скриптом Test-OutlookAddin.ps1 из пакета.")
    try:
        root = ET.fromstring(ol.manifest_xml(base or "https://example.invalid", st.settings.app_version, sync_on_send=cfg.sync_on_send))
        add("manifest", "Манифест", True, f"Идентификатор надстройки {root.findtext('{http://schemas.microsoft.com/office/appforoffice/1.1}Id')} постоянен для адреса {base}.")
    except ET.ParseError:
        add("manifest", "Манифест", False, "Манифест не разбирается — сообщите разработчикам.")
    sync_mode = cfg.sync_mode
    if sync_mode == "off":
        add("sync", "Серверная сверка календаря", None, "Выключена: изменения, сделанные вне надстройки, на сервере не отслеживаются" + (" (при отправке обновления время и тема отправляются надстройкой)." if cfg.sync_on_send else "."))
    else:
        errs = (await db.execute(select(func.count()).select_from(Room).where(Room.outlook_sync_state == "error", Room.cancelled_at.is_(None), Room.lifecycle != "closed"))).scalar_one()
        tracked = (await db.execute(select(func.count()).select_from(Room).where(Room.outlook_organizer.is_not(None), Room.cancelled_at.is_(None), Room.lifecycle != "closed"))).scalar_one()
        add("sync", "Серверная сверка календаря", None if errs else True, f"Режим {sync_mode.upper()}, отслеживается встреч: {tracked}" + (f", с ошибками: {errs} (см. историю)." if errs else "."))
    total = (await db.execute(select(func.count()).select_from(Room).where(Room.scheduled_start.is_not(None)))).scalar_one()
    return {"checks": checks, "base": base, "ok": not any(c["ok"] is False for c in checks), "conferences": total, "enabled": cfg.enabled}


@router.get("/meetings")
async def meetings(request: Request, limit: int = 100, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """История созданных из Outlook конференций (новые сверху)."""
    cfg = await _cfg(request, db)
    base = ol.public_url(request.app.state.settings.app_public_url, cfg)
    rooms = (await db.execute(select(Room).where(Room.scheduled_start.is_not(None)).order_by(Room.created_at.desc()).limit(max(1, min(limit, 500))))).scalars().all()
    live = set((await db.execute(select(Meeting.room_id).where(Meeting.ended_at.is_(None)))).scalars().all()) if rooms else set()
    return {"items": [{"id": str(r.id), "name": r.name, "organizer": r.created_by_name, "organizer_email": r.outlook_organizer, "start": r.scheduled_start, "end": r.scheduled_end,
                       "recurring": r.recurring, "state": ol.state(r, meeting_active=r.id in live), "url": ol.join_link(base, r), "sync_state": r.outlook_sync_state,
                       "sync_error": r.outlook_sync_error, "synced_at": r.outlook_synced_at, "tracked": bool(r.outlook_organizer and (r.outlook_rest_id or r.outlook_ews_id)),
                       "created_at": r.created_at} for r in rooms]}


@router.get("/package")
async def package(request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """ZIP: манифест этого сервера, инструкции и сценарии PowerShell для администратора Exchange."""
    cfg = await _cfg(request, db)
    st = request.app.state
    base = ol.public_url(st.settings.app_public_url, cfg)
    if not base.startswith("https://"):
        raise HTTPException(status_code=409, detail="Пакет нельзя создать: нужен публичный HTTPS-адрес (Outlook не принимает надстройки по HTTP). Задайте его в настройках интеграции.")
    data = build_package(base, st.settings.app_version, cfg)
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="outlook.package", target_type="outlook", target_id="package", ip=client_ip(request),
                      details={"base": base, "version": st.settings.app_version, "sync_on_send": cfg.sync_on_send})
    await db.commit()
    return Response(data, media_type="application/zip", headers={"Content-Disposition": 'attachment; filename="Peregovorka-Outlook.zip"', "Cache-Control": "no-store"})


@router.post("/sync-test")
async def sync_test(request: Request, body: dict[str, Any] = Body(...), su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Проверка доступа к календарю указанного ящика с сохранёнными настройками сверки (Graph или EWS)."""
    cfg = await _cfg(request, db)
    mailbox = str(body.get("mailbox") or "").strip()
    if "@" not in mailbox or len(mailbox) > 254 or any(c in mailbox for c in " <>\"'"):
        raise HTTPException(status_code=422, detail="Укажите адрес почтового ящика для проверки (user@example.org).")
    t0 = time.monotonic()
    try:
        client = osync.make_client(cfg, getattr(request.app.state, "test_transports", {}).get("outlook_sync"))
        msg = await client.probe(mailbox)
        res = {"ok": True, "message": msg}
    except osync.CalendarError as exc:
        res = {"ok": False, "message": str(exc)}
    res["ms"] = int((time.monotonic() - t0) * 1000)
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="outlook.sync_test", target_type="outlook", target_id=cfg.sync_mode, ip=client_ip(request),
                      details={"ok": res["ok"]})
    await db.commit()
    return res


@router.post("/sync-run")
async def sync_run(request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Сверить сейчас (не дожидаясь интервала)."""
    cfg = await _cfg(request, db)
    try:
        client = osync.make_client(cfg, getattr(request.app.state, "test_transports", {}).get("outlook_sync"))
    except osync.CalendarError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    cfg.sync_interval_minutes = 1                                                    # type: ignore[misc] — «сейчас»: берём всё, что сверялось раньше минуты назад
    counts = await osync.run_once(db, cfg, client, journal=request.app.state.journal)
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="outlook.sync_run", target_type="outlook", target_id=cfg.sync_mode, ip=client_ip(request), details=counts)
    await db.commit()
    return counts
