"""Встроенная диагностика: единый отчёт о состоянии установки (версии, ядро, WebSocket, RTC, зависимости).

Отчёт строится на сервере и ПЕРЕД выдачей проходит через маскирование: пароли, токены, ключи и секреты не попадают
в вывод ни при каких обстоятельствах (см. mask_report). Все проверки только читают состояние или открывают и сразу
закрывают тестовое соединение; пользовательских данных и содержимого встреч в отчёте нет.
"""
from __future__ import annotations

import asyncio
import datetime
import importlib.metadata as md
import os
import re
import secrets
import shutil
import socket
import time
from typing import Any

import httpx
from livekit import api as lkapi

from ..config import Settings
from . import timings

# Рекомендации LiveKit/хоста для WebRTC (на shared-host применяет администратор осознанно).
KERNEL_RECOMMENDED = {"net.core.rmem_max": 5_000_000, "net.core.wmem_max": 5_000_000, "net.core.netdev_max_backlog": 5_000}

_SECRET_KEY = re.compile(r"(pass(word)?|secret|token|authorization|cookie|api[_-]?key|master[_-]?key|bind[_-]?(dn|pw)?|credential|jwt|private)", re.I)
_JWT = re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]*")
_QUERY_TOKEN = re.compile(r"(?i)((?:access_token|token|key|secret|password|api_key)=)[^&\s\"']+")
_ASSIGN = re.compile(r"(?i)\b([A-Z0-9_]*(?:PASSWORD|SECRET|TOKEN|API_KEY|MASTER_KEY|CREDENTIAL)[A-Z0-9_]*)(\s*[=:]\s*)([^\s,;&\"']+)")


def mask_text(value: str) -> str:
    value = _JWT.sub("***jwt***", value)
    value = _QUERY_TOKEN.sub(lambda m: m.group(1) + "***", value)
    return _ASSIGN.sub(lambda m: f"{m.group(1)}{m.group(2)}***", value)


def mask_report(value: Any) -> Any:
    """Рекурсивно скрывает секреты: по имени ключа и по виду значения (JWT, access_token=..., *_PASSWORD=...)."""
    if isinstance(value, dict):
        return {k: ("***" if isinstance(k, str) and _SECRET_KEY.search(k) and not isinstance(v, (bool, type(None))) else mask_report(v))
                for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [mask_report(v) for v in value]
    if isinstance(value, str):
        return mask_text(value)
    return value


def _pkg(name: str) -> str | None:
    try:
        return md.version(name)
    except md.PackageNotFoundError:
        return None


def read_proc_sys(name: str, root: str = "/proc/sys") -> int | None:
    try:
        with open(os.path.join(root, *name.split(".")), encoding="utf-8") as f:
            return int(f.read().strip().split()[0])
    except (OSError, ValueError, IndexError):
        return None


def kernel_report(root: str = "/proc/sys") -> dict:
    """Буферы UDP и очередь сетевого стека: значения хоста против рекомендаций LiveKit. Только чтение."""
    items = {}
    for name, want in KERNEL_RECOMMENDED.items():
        have = read_proc_sys(name, root)
        items[name] = {"value": have, "recommended": want, "ok": None if have is None else have >= want}
    bad = [k for k, v in items.items() if v["ok"] is False]
    return {"params": items, "ok": not bad, "note": (
        "Ниже рекомендаций: " + ", ".join(bad) + ". Применяет администратор (sysctl глобален для хоста); команды: scripts/tune-kernel.sh") if bad else ""}


def host_stats() -> dict[str, Any]:
    """CPU/RAM/нагрузка хоста из /proc (в контейнере /proc/meminfo и loadavg показывают хост)."""
    out: dict[str, Any] = {"cpus": os.cpu_count()}
    try:
        with open("/proc/loadavg", encoding="utf-8") as f:
            out["load1"], out["load5"], out["load15"] = [float(x) for x in f.read().split()[:3]]
    except (OSError, ValueError):
        pass
    try:
        mem: dict[str, int] = {}
        with open("/proc/meminfo", encoding="utf-8") as f:
            for line in f:
                k, _, v = line.partition(":")
                mem[k] = int(v.split()[0]) * 1024
        out["mem_total"], out["mem_available"] = mem.get("MemTotal"), mem.get("MemAvailable")
    except (OSError, ValueError):
        pass
    return out


# ------------------------------------------------------------------------------------ LiveKit
def _diag_token(settings: Settings, room: str) -> str:
    return (lkapi.AccessToken(settings.livekit_api_key, settings.livekit_api_secret)
            .with_identity(f"diag-{secrets.token_hex(4)}").with_name("diag")
            .with_ttl(datetime.timedelta(seconds=60))
            .with_grants(lkapi.VideoGrants(room_join=True, room=room, can_publish=False, can_subscribe=False, hidden=True))
            .to_jwt())


def ws_url(http_or_ws: str, path: str) -> str:
    base = re.sub(r"^http", "ws", http_or_ws.rstrip("/"), count=1)
    return base + path


async def ws_handshake(url: str, *, timeout: float = 6.0) -> dict:
    """Настоящий WebSocket Upgrade: успех — код 101 (соединение сразу закрывается). Токен из URL в результат не попадает."""
    started = time.monotonic()
    try:
        from websockets.asyncio.client import connect  # noqa: PLC0415

        async with connect(url, open_timeout=timeout, close_timeout=2, max_size=1 << 16) as ws:
            status = getattr(getattr(ws, "response", None), "status_code", 101)
        return {"ok": status == 101, "status": status, "ms": int((time.monotonic() - started) * 1000)}
    except Exception as exc:  # noqa: BLE001
        resp = getattr(exc, "response", None)
        status = getattr(resp, "status_code", None)
        raw = getattr(resp, "body", b"") or b""
        body = (raw.decode("utf-8", errors="replace") if isinstance(raw, (bytes, bytearray)) else str(raw)).strip()[:200]
        # Подсказка нейтральная: по одному ответу нельзя сказать, виноват прокси или сам LiveKit — это решает сравнение «напрямую / через адрес» (verdict).
        hint = ""
        if status == 404:
            hint = "404: путь не найден на этом сервере (для /rtc/v1 это признак устаревшего LiveKit)"
        elif status:
            hint = f"HTTP {status}" + (f": {body}" if body else "")
        return {"ok": False, "status": status, "error": type(exc).__name__, "hint": hint, "body": body,
                "ms": int((time.monotonic() - started) * 1000)}


def rtc_v1_route(res: dict) -> dict:
    """Проверка существования маршрута /rtc/v1. Настоящий SDK передаёт параметр join_request; без него LiveKit отвечает
    400 «join_request is required» — это ПОДТВЕРЖДАЕТ, что маршрут есть (устаревший сервер ответил бы 404). Любой другой ответ — как есть."""
    if res.get("status") == 400 and "join_request" in (res.get("body") or "").lower():
        return {**res, "ok": True, "route_exists": True,
                "hint": "маршрут /rtc/v1 существует (LiveKit ответил 400 «join_request is required» — так он отвечает на запрос без параметров SDK)"}
    return res


async def livekit_checks(settings: Settings) -> dict:
    """/rtc/v1 без 404 (иначе SDK уходит в медленный fallback), WebSocket напрямую и через публичный адрес, RTC TCP/UDP."""
    out: dict[str, Any] = {}
    room = f"diag-{secrets.token_hex(4)}"
    try:
        tok = _diag_token(settings, room)
    except Exception as exc:  # noqa: BLE001
        return {"error": f"не удалось выдать тестовый токен: {type(exc).__name__}"}
    q = f"?access_token={tok}&auto_subscribe=0&sdk=python&protocol=15&version=diag"
    # 1) маршрут /rtc/v1 (не 404 — иначе SDK уходит в медленный запасной путь); 2) настоящий WebSocket Upgrade на /rtc с тем же токеном (101)
    out["rtc_v1_internal"] = rtc_v1_route(await ws_handshake(ws_url(settings.livekit_http_url, "/rtc/v1" + q)))
    out["rtc_ws_internal"] = await ws_handshake(ws_url(settings.livekit_http_url, "/rtc" + q))
    if settings.livekit_public_url:
        pub = settings.livekit_public_url.rstrip("/")
        out["rtc_v1_public"] = rtc_v1_route(await ws_handshake(ws_url(pub, "/rtc/v1" + q)))
        out["rtc_ws_public"] = await ws_handshake(ws_url(pub, "/rtc" + q))
        out["rtc_v1_public"]["url"] = out["rtc_ws_public"]["url"] = pub
    tcp = {"configured": bool(settings.livekit_rtc_tcp_port), "port": settings.livekit_rtc_tcp_port or None,
           "node_ip": settings.livekit_node_ip or None}
    if tcp["configured"] and settings.livekit_node_ip:
        try:
            with socket.create_connection((settings.livekit_node_ip, settings.livekit_rtc_tcp_port), timeout=3):
                tcp["ok"] = True
        except OSError as exc:
            tcp.update(ok=False, error=type(exc).__name__)
        tcp["note"] = "проверено из контейнера backend; доступность с компьютеров пользователей проверяйте отдельно"
    out["rtc_tcp"] = tcp
    out["rtc_udp"] = {"configured": bool(settings.livekit_rtc_udp_port), "port": settings.livekit_rtc_udp_port or None,
                      "note": "UDP не проверяется без клиента: убедитесь, что порт открыт клиентам (см. docs/DEPLOYMENT.md)"}
    return out


# ------------------------------------------------------------------------------------ отчёт
async def build_report(app, db_ok: bool | None = None) -> dict:
    """Полный диагностический отчёт. Секреты маскируются перед возвратом."""
    from sqlalchemy import text  # noqa: PLC0415

    s: Settings = app.state.settings
    rep: dict[str, Any] = {
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "app": {"version": s.app_version, "commit": s.app_git_commit, "built_at": s.app_built_at, "public_url": s.app_public_url},
        "versions": {"livekit_server": s.livekit_server_version or "unknown", "livekit_api_python": _pkg("livekit-api"),
                     "python": ".".join(map(str, __import__("sys").version_info[:3]))},
        "host": host_stats(), "kernel": kernel_report(), "checks": {},
    }
    c = rep["checks"]
    try:
        async with app.state.session_maker() as db:
            await db.execute(text("SELECT 1"))
        c["postgres"] = {"ok": True}
    except Exception as exc:  # noqa: BLE001
        c["postgres"] = {"ok": False, "error": type(exc).__name__}
    try:
        await app.state.redis.ping()
        c["redis"] = {"ok": True}
    except Exception as exc:  # noqa: BLE001
        c["redis"] = {"ok": False, "error": type(exc).__name__}
    try:
        await asyncio.to_thread(app.state.directory.check_service_account)
        c["ldap"] = {"ok": True}
    except Exception as exc:  # noqa: BLE001
        c["ldap"] = {"ok": False, "error": getattr(exc, "code", type(exc).__name__)}
    try:
        async with httpx.AsyncClient(timeout=3.0) as cl:
            c["livekit_http"] = {"ok": (await cl.get(s.livekit_http_url + "/")).status_code == 200}
    except Exception as exc:  # noqa: BLE001
        c["livekit_http"] = {"ok": False, "error": type(exc).__name__}
    c["livekit"] = await livekit_checks(s)
    try:
        usage = shutil.disk_usage(s.data_dir)
        c["data_dir"] = {"ok": True, "free_bytes": usage.free, "total_bytes": usage.total}
    except OSError as exc:
        c["data_dir"] = {"ok": False, "error": type(exc).__name__}
    try:
        from .settings import SettingsService  # noqa: PLC0415,F401

        async with app.state.session_maker() as db:
            st = await app.state.settings_svc.get(db, "storage")
            au = await app.state.settings_svc.get(db, "audio_storage")
        c["storage"] = {"protocols": {"enabled": st.enabled, "type": st.type}, "audio": {"enabled": au.enabled, "type": au.type}}
    except Exception as exc:  # noqa: BLE001
        c["storage"] = {"ok": False, "error": type(exc).__name__}

    hb = None
    try:
        hb = await app.state.bridge.heartbeat()
    except Exception:  # noqa: BLE001
        pass
    prov = (hb or {}).get("provider") or {}
    rep["asr"] = {"ready": bool(hb and hb.get("model_loaded")), "model": prov.get("name"), "device": prov.get("device"),
                  "provider": prov.get("provider"), "runtime": prov.get("runtime"), "model_id": prov.get("model_id"), "quant": prov.get("quant"),
                  "loading_model": (hb or {}).get("loading_model"), "models": (hb or {}).get("models"), "version": (hb or {}).get("version"), "commit": (hb or {}).get("commit"),
                  "threads": (hb or {}).get("torch_threads"), "interop_threads": (hb or {}).get("torch_interop_threads"),
                  "avg_infer_ms": (hb or {}).get("avg_infer_ms"), "avg_queue_ms": (hb or {}).get("avg_queue_ms"),
                  "rtf": (hb or {}).get("rtf"), "queue_depth": (hb or {}).get("queue_depth"),
                  "dropped": (hb or {}).get("dropped"), "errors": (hb or {}).get("errors")}
    rep["versions"]["livekit_python_sdk_asr"] = (hb or {}).get("livekit_sdk")
    try:
        rep["timings"] = await timings.averages(app.state.redis)
    except Exception:  # noqa: BLE001
        rep["timings"] = {}
    rep["verdict"] = verdict(rep)
    return mask_report(rep)


def verdict(rep: dict) -> list[str]:
    """Короткий список проблем простым языком (пусто — замечаний нет)."""
    out: list[str] = []
    c = rep.get("checks", {})
    for name in ("postgres", "redis", "ldap", "livekit_http"):
        if c.get(name, {}).get("ok") is False:
            out.append(f"{name}: недоступен ({c[name].get('error', '')})")
    lk = c.get("livekit", {})
    def why(r: dict) -> str:
        return str(r.get("hint") or r.get("error") or r.get("status"))

    d_route, p_route, d_ws, p_ws = lk.get("rtc_v1_internal"), lk.get("rtc_v1_public"), lk.get("rtc_ws_internal"), lk.get("rtc_ws_public")
    if d_route and not d_route.get("ok"):
        out.append(f"LiveKit напрямую: маршрут /rtc/v1 недоступен ({why(d_route)}) — это ответ самого LiveKit, прокси здесь ни при чём")
    if d_ws and not d_ws.get("ok"):
        out.append(f"LiveKit напрямую: WebSocket на /rtc не установлен ({why(d_ws)}) — это ответ самого LiveKit (токен, ключи, версия), прокси здесь ни при чём")
    # Через публичный адрес виноват прокси только если напрямую тот же запрос проходит
    if p_route and not p_route.get("ok"):
        out.append("Через публичный адрес маршрут /rtc/v1 недоступен (" + why(p_route) + ")"
                   + (": напрямую LiveKit его отдаёт — проблема в прокси или в пути /livekit/" if d_route and d_route.get("ok") else ": напрямую тоже не работает — искать в LiveKit"))
    if p_ws and not p_ws.get("ok"):
        out.append("Через публичный адрес WebSocket на /rtc не установлен (" + why(p_ws) + ")"
                   + (": напрямую LiveKit отвечает 101 — проблема в прокси (нужны proxy_http_version 1.1, Upgrade, Connection upgrade)" if d_ws and d_ws.get("ok") else ": напрямую тоже не работает — искать в LiveKit, а не в прокси"))
    if lk.get("rtc_tcp", {}).get("ok") is False:
        out.append("RTC TCP-порт недоступен")
    if not rep.get("asr", {}).get("ready"):
        errs = [f"{m['title']}: {m['error']}" for m in (rep.get("asr", {}).get("models") or []) if m.get("error")]
        out.append("ASR не готов (модель не загружена или сервис остановлен): звонок работает, транскрибации нет" + (f". Причина: {errs[0]}" if errs else ""))
    if rep.get("kernel", {}).get("ok") is False:
        out.append(rep["kernel"]["note"])
    return out
