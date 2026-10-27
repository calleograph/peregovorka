"""Безопасность (администратор): диагностика определения адреса клиента, состояние защиты входа. Все ручки — только для администратора."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from ..auth.deps import SessionUser, require_admin, resolve_ip
from ..security.ipres import in_nets, ip_key

router = APIRouter(prefix="/admin/security", tags=["admin-security"])


@router.get("/ip-diagnostics")
async def ip_diagnostics(request: Request, su: SessionUser = Depends(require_admin)):
    """Что backend видит в ЭТОМ запросе и как из этого получен адрес клиента. Помогает настроить доверенные прокси и WAF: ошибка здесь — либо обход ограничений
    (доверие подделываемым заголовкам), либо один общий «адрес» для всех сотрудников."""
    holder = request.app.state.netcfg
    cfg = holder.cfg
    res = resolve_ip(request)
    hdr = lambda n: request.headers.get(n) or ""      # noqa: E731
    warnings: list[str] = []
    if res.web_peer and res.ip == res.web_peer and not res.headers_accepted and hdr("x-forwarded-for"):
        warnings.append("К серверу подключился узел, которого нет среди доверенных прокси, но он прислал заголовки с адресом клиента. Если перед сервером стоит ваш WAF или reverse proxy — добавьте его адреса в «Доверенные прокси». Если нет — всё верно: подделанные заголовки игнорируются.")
    if res.web_peer and in_nets(res.ip, list(cfg.accept_from)) and res.source != "tcp":
        warnings.append("Определённый адрес — частный/внутренний. Если вы сейчас заходите не из локальной сети, значит прокси перед сервером не передаёт настоящий адрес клиента: ограничения по IP будут действовать на все подключения вместе.")
    return {
        "client_ip": res.ip, "counter_key": ip_key(res.ip), "source": res.source, "tcp_peer": res.tcp_peer, "web_peer": res.web_peer, "headers_accepted": res.headers_accepted,
        "chain": res.chain, "notes": res.notes, "warnings": warnings,
        "headers": {"x_forwarded_for": hdr("x-forwarded-for")[:300], "x_real_ip": hdr("x-real-ip")[:80], "forwarded": hdr("forwarded")[:300], "x_pg_peer": hdr("x-pg-peer")[:80]},
        "config": {"mode": cfg.mode, "header_name": cfg.header_name, "trusted_proxies": [str(n) for n in cfg.trusted], "proxy_hops": cfg.hops, "accept_from": [str(n) for n in cfg.accept_from]},
    }


@router.get("/login-protection/status")
async def login_protection_status(request: Request, su: SessionUser = Depends(require_admin)):
    return await request.app.state.login_guard.stats()
