"""Защитный слой запросов (чистый ASGI, один на весь backend): ограничение по доверенным подсетям → блокировки адресов для анонимных запросов → лёгкий WAF (security/waf.py).

Принципы: проверка на критическом пути без обращения к базе (снимки политик в памяти процесса), WebSocket и потоковые запросы не читаются и не буферизуются (проверяются только
заголовок запроса), внутренний трафик контейнеров (без заголовка web) ограничениям не подвержен, ответ отказа нейтральный и без подробностей. Сбой защитного слоя не должен ронять приложение:
исключения внутри проверок глотаются и пишутся в журнал, запрос обслуживается дальше (кроме явных отказов)."""
from __future__ import annotations

import logging
import time

from starlette.requests import HTTPConnection
from starlette.responses import HTMLResponse, JSONResponse

from . import waf
from .allownets import ALWAYS_OPEN, MESSAGE, exempt_path, is_internal
from .ipres import in_nets, ip_key

log = logging.getLogger("app.security.gate")

_PAGE = ("<!doctype html><html lang=\"ru\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"><title>Доступ ограничен</title>"
         "<style>body{margin:0;min-height:100vh;display:grid;place-items:center;background:linear-gradient(180deg,#f4f8ff,#e8f0fb);font:16px/1.5 system-ui,sans-serif;color:#0f1b3d}"
         "main{max-width:460px;margin:24px;padding:32px 28px;border-radius:20px;background:#fff;border:1px solid #d8e3f3;box-shadow:0 14px 32px -18px rgba(20,50,120,.3);text-align:center}"
         "h1{font-size:22px;margin:0 0 10px}p{margin:0;color:#4c5c7a}</style></head><body><main><h1>Доступ ограничен</h1><p>" + MESSAGE + "</p></main></body></html>")
_HEAD = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}


def denied_response(path: str):
    """403 без подробностей: не раскрывает ни списка сетей, ни настроек. Для API — JSON с кодом (по нему интерфейс показывает нейтральную страницу)."""
    if path.startswith("/api/"):
        return JSONResponse({"detail": {"code": "access_restricted", "message": MESSAGE}}, status_code=403, headers=_HEAD)
    return HTMLResponse(_PAGE, status_code=403, headers=_HEAD)


def _has_credentials(request: HTTPConnection, cookie_name: str) -> bool:
    h = request.headers
    return bool(request.cookies.get(cookie_name) or h.get("x-guest-token") or h.get("authorization") or h.get("x-api-key"))


class SecurityGate:
    def __init__(self, app, settings):
        self.app, self.settings = app, settings

    async def __call__(self, scope, receive, send):
        kind = scope["type"]
        if kind not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        app = scope.get("app")
        if app is None:
            await self.app(scope, receive, send)
            return
        st = app.state
        allow = getattr(st, "allownets", None)
        pol = getattr(st, "policies", None)
        restricting = allow is not None and allow.policy.enabled
        inspecting = pol is not None and (pol.waf.mode != "off" or bool(pol.blocked))
        if not restricting and not inspecting:
            await self.app(scope, receive, send)
            return
        path = scope["path"]
        try:
            request = HTTPConnection(scope)                       # и для http, и для websocket (Request на websocket-области падает)
            tcp = request.client.host if request.client else None
            accept = list(st.netcfg.cfg.accept_from) if getattr(st, "netcfg", None) else []
            internal = is_internal({"x-pg-peer": request.headers.get("x-pg-peer", "")}, tcp, accept)
            if internal:
                await self.app(scope, receive, send)
                return
            from ..auth.deps import client_ip  # noqa: PLC0415

            ip = client_ip(request)
            ipk = ip_key(ip)
            # 1. доверенные подсети
            if restricting and not path.startswith(ALWAYS_OPEN) and not exempt_path(path, allow.policy) and not allow.allowed(ip):
                await self._deny(app, scope, receive, send, ip, path, "access_denied_net", "allowed_networks", "адрес вне разрешённых подсетей")
                return
            has_cred = _has_credentials(request, self.settings.cookie_name)
            # 2. заблокированный адрес: анонимные запросы не принимаются (вошедших пользователей не отключаем)
            if pol is not None and pol.is_blocked(ipk) and not has_cred and not path.startswith(ALWAYS_OPEN):
                await self._deny(app, scope, receive, send, ip, path, "blocked_ip_request", "ip_block", "адрес временно заблокирован", status=429)
                return
            # 3. WAF
            if pol is not None and pol.waf.mode != "off" and kind == "http" and path.startswith("/api/") and not path.startswith(ALWAYS_OPEN):
                w = pol.waf
                if not (any(path.startswith(p) for p in w.exclude_paths) or in_nets(ip, list(w.exclude_nets))):
                    hits = waf.inspect(scope["method"], path, scope.get("query_string", b""), request.headers.get("user-agent", ""))
                    block = None
                    for h in hits:
                        act = w.action(h.rule)
                        if act == "none":
                            continue
                        blocked = act == "block" and not has_cred
                        await self._record(app, h, ip, path, scope, "blocked" if blocked else "observed", w)
                        if blocked and block is None:
                            block = h
                    if block is not None:
                        scope["pg_gate_denied"] = True
                        resp = JSONResponse({"detail": {"code": "request_blocked", "message": "Запрос отклонён."}}, status_code=403, headers=_HEAD)
                        if kind == "http":
                            await resp(scope, receive, send)
                            return
                    # частота 404/405: считается по ответу
                    if w.action("api_probe") != "none":
                        await self._probe(app, scope, receive, send, ip, ipk, path, w, has_cred)
                        return
        except Exception:  # noqa: BLE001 — защитный слой не должен ронять приложение
            log.warning("Сбой защитного слоя запросов (запрос обслужен без проверки)", exc_info=True)
        await self.app(scope, receive, send)

    async def _probe(self, app, scope, receive, send, ip, ipk, path, w, has_cred) -> None:
        status = {"code": 0}

        async def wrap(message):
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
            await send(message)

        await self.app(scope, receive, wrap)
        if status["code"] in (404, 405):
            try:
                r = app.state.redis
                key = f"sec:probe:{ipk}"
                n = await r.incr(key)
                if n == 1:
                    await r.expire(key, 60)
                if n == w.probe_threshold:
                    act = w.action("api_probe")
                    await self._record(app, waf.Hit("api_probe", f"{n} ответов 404/405 за минуту"), ip, path, scope, "blocked" if (act == "block" and not has_cred) else "observed", w)
                    if act == "block" and not has_cred:
                        g = app.state.login_guard
                        lp, _ = await g.cfg()
                        if not await g.is_shared(ipk, lp, time.time()):
                            await g.block_ip(ip, w.probe_block_minutes, "перебор путей (много ответов 404/405)", rule="waf_api_probe")
                            await app.state.secevents.emit("ip_blocked", ip=ip, result="blocked", rule="waf:api_probe", reason="перебор путей", component="waf", details={"minutes": w.probe_block_minutes})
            except Exception:  # noqa: BLE001
                pass

    async def _record(self, app, hit: waf.Hit, ip: str, path: str, scope, result: str, w) -> None:
        try:
            r = app.state.redis
            hk = waf.hour_key()
            p = r.pipeline()
            p.hincrby(f"sec:stats:waf:{hit.rule}:{result}", hk, 1)
            p.expire(f"sec:stats:waf:{hit.rule}:{result}", 3 * 86400)
            p.set(f"sec:stats:waf_last:{hit.rule}", int(time.time()), ex=30 * 86400)
            await p.execute()
            key = f"sec:waf:{ip_key(ip)}:{hit.rule}"
            n = await r.incr(key)
            if n == 1:
                await r.expire(key, 60)
            if n == 1:                                                # событие — не чаще раза в минуту на адрес и правило
                await app.state.secevents.emit("waf_hit", ip=ip, result=result, rule=f"waf:{hit.rule}", reason=waf.RULES[hit.rule]["title"], component="waf",
                                               details={"path": path[:120], "method": scope.get("method", ""), "sample": hit.detail[:120]})
        except Exception:  # noqa: BLE001
            pass

    async def _deny(self, app, scope, receive, send, ip: str, path: str, kind: str, rule: str, reason: str, status: int = 403) -> None:
        scope["pg_gate_denied"] = True                      # отказ защитного слоя уже записан как своё событие: «ошибку прав» для него не дублируем
        # событие — не чаще раза в минуту на адрес: сканирование не заполнит журнал
        try:
            r = app.state.redis
            key = f"sec:deny:{rule}:{ip_key(ip)}"
            n = await r.incr(key)
            if n == 1:
                await r.expire(key, 60)
                await app.state.secevents.emit(kind, ip=ip, result="denied", rule=rule, reason=reason, component="gate", details={"path": path[:120], "method": scope.get("method", "WS")})
            if rule == "allowed_networks":
                await r.hincrby("sec:stats:denied_net", time.strftime("%Y%m%d%H", time.gmtime()), 1)
                await r.expire("sec:stats:denied_net", 3 * 86400)
        except Exception:  # noqa: BLE001
            pass
        if scope["type"] == "websocket":
            await receive()
            await send({"type": "websocket.close", "code": 4403})
            return
        if status == 429:
            resp = JSONResponse({"detail": {"code": "throttled", "message": "Слишком много запросов. Повторите позже."}}, status_code=429, headers={**_HEAD, "Retry-After": "60"})
        else:
            resp = denied_response(path)
        await resp(scope, receive, send)
