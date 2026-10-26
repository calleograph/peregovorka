"""Администрирование → Система → «Сетевая доступность»: какие порты нужно открыть для этого экземпляра.

Перечень строит `services/netrules.py` по фактической конфигурации (`.env` в окружении backend и настройки интеграций в базе). Тот же модуль
использует команда `scripts/ports.sh`, поэтому перечни совпадают. Проверка с сервера честно называется внутренней: она не проходит внешний
NAT и межсетевой экран и не доказывает доступ из интернета. Только чтение и один лёгкий запрос проверки; пароли и адреса секретов не отдаются.
"""
from __future__ import annotations

import asyncio
import os
import time
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import SessionUser, client_ip, get_db, require_admin
from ..models import ApiProfile, LdapProfile, MailProfile, StorageProfile, WebhookEndpoint
from ..services import netrules
from ..services.audit import write_audit

router = APIRouter(prefix="/admin/network", tags=["admin-network"])

PROBE_TIMEOUT = 3.0
PROBE_MAX = 30
_MEDIA = {"text": "text/plain", "md": "text/markdown", "csv": "text/csv", "json": "application/json", "request": "text/plain"}
_EXT = {"text": "txt", "md": "md", "csv": "csv", "json": "json", "request": "txt"}


def _hostport(url: str, default: int) -> tuple[str, int] | None:
    try:
        p = urlsplit(url)
        host = p.hostname
        if not host:
            return None
        return host, p.port or {"https": 443, "http": 80}.get(p.scheme, default)
    except ValueError:
        return None


async def collect_outbound(settings_svc: Any, db: AsyncSession) -> tuple[list[dict[str, Any]], list[str]]:
    """Исходящие соединения из настроек в базе. Без секретов: только адрес, порт и назначение."""
    out: list[dict[str, Any]] = []
    unknown: list[str] = []
    try:
        for r in (await db.execute(select(LdapProfile).where(LdapProfile.enabled.is_(True)))).scalars():
            out.append({"what": f"Каталог «{r.name}»", "host": r.host, "port": r.port, "proto": "TCP",
                        "why": "вход доменных пользователей и поиск в каталоге (" + ("LDAPS" if r.protocol == "ldaps" else "LDAP + STARTTLS") + ")"})
        for r in (await db.execute(select(MailProfile))).scalars():
            out.append({"what": f"Почта «{r.name}»", "host": r.host, "port": r.port, "proto": "TCP", "why": "рассылка материалов встреч (SMTP)"})
        for r in (await db.execute(select(StorageProfile).where(StorageProfile.kind == "smb"))).scalars():
            host = str((r.config or {}).get("smb_server") or "").strip()
            if host:
                out.append({"what": f"Хранилище «{r.name}»", "host": host, "port": 445, "proto": "TCP", "why": "файловое хранилище SMB"})
        outlook = await settings_svc.get(db, "outlook")
        if getattr(outlook, "sync_mode", "off") == "ews" and getattr(outlook, "ews_url", ""):
            hp = _hostport(outlook.ews_url, 443)
            if hp:
                out.append({"what": "Exchange (EWS)", "host": hp[0], "port": hp[1], "proto": "TCP", "why": "сверка календаря Outlook"})
        for kind, title in (("llm", "Языковая модель"), ("anonymizer", "Обезличивание")):
            for r in (await db.execute(select(ApiProfile).where(ApiProfile.kind == kind))).scalars():
                hp = _hostport(str((r.config or {}).get("base_url") or ""), 443)
                if hp:
                    out.append({"what": f"{title} «{r.name}»", "host": hp[0], "port": hp[1], "proto": "TCP", "why": "обращение к внешнему сервису"})
        for r in (await db.execute(select(WebhookEndpoint).where(WebhookEndpoint.enabled.is_(True)))).scalars():
            hp = _hostport(r.url, 443)
            if hp:
                out.append({"what": f"Получатель событий «{r.name}»", "host": hp[0], "port": hp[1], "proto": "TCP", "why": "доставка событий публичного API"})
    except Exception:  # noqa: BLE001 — перечень портов не должен падать из-за одной таблицы
        unknown.append("адреса интеграций из настроек (ошибка чтения базы)")
    seen: set[tuple[str, int, str]] = set()
    uniq = []
    for o in out:                                   # одинаковые «хост:порт» у разных профилей показываем одной строкой
        k = (str(o["host"]).lower(), int(o["port"]), o["what"].split(" «")[0])
        if k not in seen:
            seen.add(k)
            uniq.append(o)
    return uniq, unknown


def _env_view() -> dict[str, str]:
    """Окружение backend с теми же именами, что в `.env`: порты и режимы берутся из compose, секреты сюда не попадают."""
    keys = ["APP_PUBLIC_URL", "LIVEKIT_PUBLIC_URL", "LIVEKIT_NODE_IP", "LIVEKIT_RTC_TCP_PORT", "LIVEKIT_RTC_UDP_PORT", "NET_LIVEKIT_BIND_ADDR", "NET_WEB_BIND_ADDR",
            "NET_WEB_PORT", "NET_NGINX_LISTEN_PORT", "NET_INSTALL_PROFILE", "TLS_LOCAL_CONFIGURED", "SIP_ENABLED", "SIP_SIGNALING_PORT", "SIP_RTP_START", "SIP_RTP_END",
            "NET_SIP_BIND_ADDR", "SIP_ALLOWED_CIDRS", "LDAP_URIS", "NET_LIVEKIT_HTTP_PORT"]
    return {k: os.environ.get(k, "") for k in keys}


async def build_current(settings: Any, settings_svc: Any, db: AsyncSession, env: dict[str, str] | None = None) -> dict[str, Any]:
    """Перечень правил для текущего экземпляра. Общая часть веб-раздела и команды `python -m app.cli network`."""
    env = dict(env if env is not None else _env_view())
    env["APP_PUBLIC_URL"] = env.get("APP_PUBLIC_URL") or settings.app_public_url
    env["LIVEKIT_NODE_IP"] = env.get("LIVEKIT_NODE_IP") or settings.livekit_node_ip
    env["LIVEKIT_RTC_TCP_PORT"] = env.get("LIVEKIT_RTC_TCP_PORT") or str(settings.livekit_rtc_tcp_port or "")
    env["LIVEKIT_RTC_UDP_PORT"] = env.get("LIVEKIT_RTC_UDP_PORT") or str(settings.livekit_rtc_udp_port or "")
    env["SIP_ENABLED"] = settings.sip_enabled
    env["LIVEKIT_PUBLIC_URL"] = settings.livekit_public_url
    env["TLS_LOCAL_CONFIGURED"] = "1" if settings.tls_local_configured else ""
    env["LDAP_URIS"] = settings.ldap_uris
    outbound, unknown = await collect_outbound(settings_svc, db)
    n = netrules.from_env(env, outbound, unknown)
    if not n.profile:
        n.unknown.append("режим установки (standalone/shared-host): обновите контейнеры командой update.sh")
    if not n.web_port:
        n.unknown.append("порты внутренних служб на хосте: обновите контейнеры командой update.sh")
    res = netrules.build(n)
    res["generated_at"] = int(time.time())
    return res


async def current(request: Request, db: AsyncSession) -> dict[str, Any]:
    return await build_current(request.app.state.settings, request.app.state.settings_svc, db)


@router.get("")
async def network(request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    res = await current(request, db)
    res["opened_from"] = request.headers.get("origin") or ""
    return res


@router.get("/export")
async def export(request: Request, format: str = Query("md", pattern="^(text|md|csv|json|request)$"),
                 su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> Response:
    res = await current(request, db)
    body = netrules.FORMATS[format](res)
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="network.export", target_type="network", target_id="rules",
                      ip=client_ip(request), details={"format": format})
    await db.commit()
    return Response(body, media_type=_MEDIA[format] + "; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="peregovorka-network.{_EXT[format]}"', "Cache-Control": "no-store"})


async def _connect(host: str, port: int) -> dict[str, Any]:
    t0 = time.monotonic()
    try:
        _, w = await asyncio.wait_for(asyncio.open_connection(host, port), PROBE_TIMEOUT)
        w.close()
        try:
            await w.wait_closed()
        except Exception:  # noqa: BLE001
            pass
        return {"ok": True, "ms": round((time.monotonic() - t0) * 1000)}
    except asyncio.TimeoutError:
        return {"ok": False, "error": "нет ответа за %d с (порт закрыт или пакеты отбрасываются)" % PROBE_TIMEOUT}
    except OSError as exc:
        return {"ok": False, "error": "отказ или адрес не найден: " + (exc.strerror or type(exc).__name__)}


@router.post("/probe")
async def probe(request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """Внутренняя проверка: соединение с самого сервера (из контейнера backend). Для UDP проверки нет — достоверного способа без клиента не существует."""
    res = await current(request, db)
    s = request.app.state.settings
    targets: list[tuple[str, str, int, str]] = []
    if res["node_ip"] and s.livekit_rtc_tcp_port:
        targets.append(("rtc-tcp", res["node_ip"], int(s.livekit_rtc_tcp_port), "LiveKit TCP по адресу LIVEKIT_NODE_IP (путь с сервера до своего адреса)"))
    lh = _hostport(s.livekit_internal_url, 7880)
    if lh:
        targets.append(("livekit-internal", lh[0], lh[1], "LiveKit внутри сети проекта"))
    for r in res["rules"]:
        if r["direction"] == "out" and r["id"].startswith("out-"):
            who = r["where"].split("→", 1)[-1].strip()
            try:
                port = int(r["port"])
            except ValueError:
                continue
            targets.append((r["id"], who, port, r["component"]))
    targets = targets[:PROBE_MAX]
    results = await asyncio.gather(*(_connect(h, p) for _, h, p, _ in targets))
    checks = [{"id": i, "host": h, "port": p, "what": what, **r} for (i, h, p, what), r in zip(targets, results)]
    udp = [r for r in res["rules"] if r["proto"] == "UDP" and r["direction"] == "in"]
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="network.probe", target_type="network", target_id="rules",
                      ip=client_ip(request), details={"checked": len(checks), "failed": sum(1 for c in checks if not c["ok"])})
    await db.commit()
    return {"checks": checks,
            "not_checked": [{"id": r["id"], "proto": r["proto"], "port": r["port"], "reason": "UDP нельзя проверить без участия клиента"} for r in udp],
            "disclaimer": "Проверка выполнена с самого сервера. Она не проходит внешний NAT и межсетевой экран и не подтверждает доступ из интернета."}

