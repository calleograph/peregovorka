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
from ..services import ews_auth
from ..services import outlook_diag as od
from ..services import tls_chain, tls_check
from ..services import outlook_sync as osync
from ..services.audit import write_audit
from ..services.outlook_package import build_package

router = APIRouter(prefix="/admin/outlook", tags=["admin-outlook"])
ASSETS = ("/outlook/commands.html", "/outlook/commands.js", "/outlook/auth.html", "/outlook/icon-64.png")


def tls_probe(host: str, port: int, timeout: float = 6.0) -> dict:
    return tls_check.probe(host, port, timeout)


async def _internal_api(request: Request) -> dict:
    """Запрос к собственному API внутри процесса (без сети): доказывает, что маршрут и интеграция работают независимо от публичного адреса."""
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=request.app), base_url="http://internal", timeout=5.0) as c:
            r = await c.get("/api/v1/outlook/config")
        ok, _, text = od.http_verdict(r.status_code)
        return {"ok": ok, "message": text if ok is not True else "API надстройки отвечает внутри приложения."}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "message": f"API не ответил внутри приложения: {type(exc).__name__}."}


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
    topo = od.detect_topology(request.headers, st.settings.tls_local_configured, cfg.tls_mode)
    transport = getattr(st, "test_transports", {}).get("outlook")
    tls_res: dict | None = None
    if not base.startswith("https://"):
        add("url", "Публичный адрес", False, f"«{base or 'не задан'}»: Outlook принимает надстройки только по HTTPS. Задайте публичный HTTPS-адрес ниже или в настройках приложения.")
    else:
        add("url", "Публичный адрес", True, f"{base}")
        u = urlsplit(base)
        add("topology", "Где завершается HTTPS", None if topo["needs_choice"] else True,
            f"{topo['label']}{' (определено автоматически)' if topo['auto'] and not topo['needs_choice'] else ''}." + (" Признаки: " + "; ".join(topo["evidence"]) + "." if topo["evidence"] else "")
            + (" Выберите схему вручную в настройках («Где завершается HTTPS»)." if topo["needs_choice"] else ""))
        checks[-1]["topology"] = topo
        if transport:
            tls_res = {"state": "ok", "ok": True, "message": "проверка пропущена в тесте", "host": u.hostname, "port": u.port or 443}
        else:
            tls_res = await od.tls_snapshot(request.app, base, fetch_aia=True, force=True, extra_ca=st.settings.check_ca)
        reach = tls_res["state"] == "unreachable"
        add("tls", "Внешняя проверка с сервера недоступна" if reach else "Сертификат HTTPS", tls_res["ok"], tls_res["message"])
        checks[-1]["tls"] = {**od.public_tls_view(tls_res), "mode": topo["mode"], "client_check_url": f"{base}/api/v1/outlook/config",
                             "has_fullchain": bool(tls_res.get("completed_chain_pem")), "cert_page": f"{base}/outlook"}
        # --- API: изнутри приложения, через web внутри Docker и через публичный адрес — отдельно, чтобы причина была видна
        internal = await _internal_api(request)
        add("api_internal", "API внутри приложения", internal["ok"], internal["message"])
        web = await od.probe_json("http://web:8080/api/v1/outlook/config", transport=transport, timeout=3.0)
        if web["code"] not in ("dns", "skip", "network", "timeout", "refused"):
            add("api_web", "API через web-контейнер", web["ok"], "Маршрут web → backend внутри Docker: " + web["message"])
        verify = tls_chain.system_context(st.settings.check_ca or None)
        pub = await od.probe_json(f"{base}/api/v1/outlook/config", transport=transport, verify=verify)
        if pub["ok"] is not None or pub["code"] == "forbidden":
            add("api", "API надстройки (публичный адрес)", pub["ok"], pub["message"])
        else:
            inside_ok = internal["ok"] is True
            add("api", "API надстройки (публичный адрес)", None, pub["message"] + (" Приложение при этом работает (см. «API внутри приложения») — это особенность внутренней маршрутизации, а не отказ надстройки: "
                "доступность клиентам проверьте кнопкой «Проверить с этого компьютера»." if inside_ok else ""))
        bad: list[str] = []
        unreachable_files = pub["code"] in ("dns", "refused", "timeout", "network", "tls_untrusted")
        files_base = base if not unreachable_files else "http://web:8080"
        try:
            async with httpx.AsyncClient(transport=transport, verify=verify, timeout=httpx.Timeout(8.0, connect=5.0), follow_redirects=False) as c:
                for path in ASSETS:
                    rr = await c.get(files_base + path)
                    if rr.status_code != 200:
                        bad.append(f"{path}: HTTP {rr.status_code}")
            add("files", "Файлы надстройки", not bad if not bad else False, ("Страницы и значки надстройки доступны" + (" (проверено внутри Docker: публичный адрес с сервера недоступен)." if unreachable_files else " по адресу сервера."))
                if not bad else "Недоступны: " + "; ".join(bad) + ". Проверьте обратный прокси (путь /outlook/).")
        except httpx.HTTPError as exc:
            code, text = od.connect_failure(exc)
            add("files", "Файлы надстройки", None, text + " Проверьте их с рабочей станции (публичная страница /outlook).")
        add("client", "Доступность клиентам", None, "Достоверно проверяется из браузера клиента: откройте публичную страницу установки (/outlook) или нажмите «Проверить с этого компьютера».")
    try:
        root = ET.fromstring(ol.manifest_xml(base or "https://example.invalid", st.settings.app_version, sync_on_send=cfg.effective_sync_on_send))
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


@router.post("/ews-detect")
async def ews_detect(request: Request, body: dict[str, Any] = Body(default={}), su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Определяет, какие способы входа предлагают серверы EWS (Basic / NTLM / Negotiate), и рекомендует один. Учётные данные не нужны и не отправляются; адрес — из тела запроса или из сохранённых настроек."""
    cfg = await _cfg(request, db)
    raw = str(body.get("url") or cfg.ews_url or "")
    try:
        client = osync.EwsClient(raw, "", "", getattr(request.app.state, "test_transports", {}).get("outlook_sync"), verify=osync._default_verify())
    except osync.CalendarError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    items = await client.detect()
    rec = next((i["recommended"] for i in items if i["recommended"]), None)
    return {"servers": items, "recommended": rec, "label": ews_auth.SCHEME_LABEL.get(rec or "", ""),
            "message": ("Рекомендуется способ входа: " + ews_auth.SCHEME_LABEL[rec] + ".") if rec else "Ни один сервер не предложил поддерживаемый способ входа: проверьте адрес и доступность."}


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
