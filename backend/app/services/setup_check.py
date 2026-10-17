"""Чек-лист «Первоначальная настройка и проверка сервера» (Администрирование → Состояние → Обзор).

Два вида пунктов: **настройка** (сделана / не сделана: каталог, сертификаты, группы, хранилище, почта) и **проверка** (работает / нужно внимание / сбой:
публичный адрес и TLS, звонки LiveKit, порты, каталог, распознавание, языковая модель, диск, ключ шифрования). Список проверок — `HEALTH_CHECKS`: новая
возможность добавляет свою проверку в этот список, и она сама появляется в интерфейсе (интерфейс рисует всё, что вернул сервер).
Результат последней проверки хранится в Redis (`setup:last_check`), чтобы показать дату и не гонять тяжёлые проверки при каждом открытии страницы.
"""
from __future__ import annotations

import asyncio
import datetime
import json
import logging
import shutil
from typing import Any, Awaitable, Callable
from urllib.parse import urlsplit

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import CaCertificate, LdapProfile, MailProfile, StorageProfile
from . import diagnostics

log = logging.getLogger("app.setup_check")

OK, WARN, FAIL, TODO = "ok", "warn", "fail", "todo"
REDIS_KEY = "setup:last_check"
CHECK_TIMEOUT_S = 30.0
DISK_WARN_BYTES, DISK_FAIL_BYTES = 5 * 1024**3, 1024**3


def item(id_: str, title: str, status: str, detail: str, page: str) -> dict:
    return {"id": id_, "title": title, "status": status, "detail": detail, "page": page}


async def _count(db: AsyncSession, model, *where) -> int:
    return (await db.execute(select(func.count()).select_from(model).where(*where))).scalar_one()


# ------------------------------------------------------------------------------------------------ настройка (сделано / не сделано)
async def config_steps(app, db: AsyncSession) -> list[dict]:
    s, svc = app.state.settings, app.state.settings_svc
    access = await svc.get(db, "access")
    ldap_n = await _count(db, LdapProfile, LdapProfile.enabled.is_(True)) + (1 if s.ldap_uri_list else 0)
    ca_n = await _count(db, CaCertificate) + (1 if s.ldap_ca_file else 0)
    groups_n = len(access.admin_groups) + (1 if s.ldap_admin_group_dn else 0)       # type: ignore[attr-defined]
    login_n = len(access.user_groups) + (1 if s.ldap_access_group_dn else 0)        # type: ignore[attr-defined]
    storage_n = await _count(db, StorageProfile)
    mail_n = await _count(db, MailProfile, MailProfile.is_active.is_(True))
    return [
        {"id": "ldap", "title": "Подключение к каталогу (LDAPS)", "done": ldap_n > 0, "page": "ldap"},
        {"id": "ca", "title": "Сертификаты CA", "done": ca_n > 0, "page": "ca"},
        {"id": "access", "title": "Группы администраторов", "done": groups_n > 0, "page": "access"},
        {"id": "login_acl", "title": "Кто может входить в систему (группы допуска)", "done": login_n > 0, "page": "login_access"},
        {"id": "storage", "title": "Файловое хранилище", "done": storage_n > 0, "page": "storages"},
        {"id": "mail", "title": "Исходящая почта", "done": mail_n > 0, "page": "mail"},
    ]


# ------------------------------------------------------------------------------------------------ проверки состояния
Ctx = dict[str, Any]
Check = Callable[[Any, AsyncSession, Ctx], Awaitable[list[dict]]]


async def _livekit_ctx(app, ctx: Ctx) -> dict:
    if "lk" not in ctx:                                   # одна проверка LiveKit на все связанные пункты
        try:
            ctx["lk"] = await asyncio.wait_for(diagnostics.livekit_checks(app.state.settings), CHECK_TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001
            ctx["lk"] = {"error": f"проверка не выполнена ({type(exc).__name__})"}
    return ctx["lk"]


async def check_public_url(app, db: AsyncSession, ctx: Ctx) -> list[dict]:
    """Публичный адрес приложения: HTTPS (иначе браузер не даст микрофон) и доверие к сертификату — по ответу самого сервера."""
    url = app.state.settings.app_public_url
    parts = urlsplit(url)
    local = (parts.hostname or "") in ("localhost", "127.0.0.1", "::1")
    if parts.scheme != "https" and not local:
        return [item("public_url", "Публичный адрес и TLS", WARN, "Приложение открывается по HTTP: браузеры не дают доступ к микрофону и камере без HTTPS. "
                     "Настройте HTTPS на прокси или в nginx сервера и задайте APP_PUBLIC_URL с https://", "system")]
    if local:
        return [item("public_url", "Публичный адрес и TLS", WARN, "APP_PUBLIC_URL указывает на этот же компьютер (localhost): пользователи по такому адресу не подключатся", "system")]
    try:
        async with httpx.AsyncClient(timeout=6.0, follow_redirects=False) as c:
            r = await c.get(url.rstrip("/") + "/api/v1/health/live")
        if r.status_code == 200:
            return [item("public_url", "Публичный адрес и TLS", OK, f"{url} отвечает, сертификат принят сервером (проверка с самого сервера)", "system")]
        return [item("public_url", "Публичный адрес и TLS", WARN, f"{url} ответил HTTP {r.status_code}: проверьте прокси и nginx", "system")]
    except Exception as exc:  # noqa: BLE001
        stage, why = diagnostics.classify_failure(exc, None)
        return [item("public_url", "Публичный адрес и TLS", WARN, f"С самого сервера адрес не открылся ({stage.upper()}): {why}. Проверка идёт с сервера, "
                     "а не с устройства пользователя: с устройства убедитесь отдельно", "system")]


async def check_livekit(app, db: AsyncSession, ctx: Ctx) -> list[dict]:
    lk = await _livekit_ctx(app, ctx)
    if "error" in lk and len(lk) == 1:
        return [item("livekit", "Звонки (LiveKit)", FAIL, str(lk["error"]), "system")]
    out: list[dict] = []
    internal = lk.get("rtc_ws_internal") or {}
    out.append(item("livekit", "Звонки: сервер LiveKit", OK if internal.get("ok") else FAIL,
                    "WebSocket сервера звонков отвечает" if internal.get("ok") else str(internal.get("hint") or internal.get("error") or "сервер не ответил"), "system"))
    pub = lk.get("rtc_ws_public")
    review = [r for r in lk.get("public_url_review", []) if r.get("level") == "warn"]
    if pub is None:
        out.append(item("livekit_public", "Звонки: публичный адрес LiveKit", FAIL, "Публичный адрес LiveKit (LIVEKIT_PUBLIC_URL) не задан", "system"))
    elif not pub.get("ok"):
        out.append(item("livekit_public", "Звонки: публичный адрес LiveKit", FAIL, f"[{str(pub.get('stage') or 'ws').upper()}] {pub.get('hint') or pub.get('error') or 'не отвечает'}", "system"))
    elif review:
        out.append(item("livekit_public", "Звонки: публичный адрес LiveKit", WARN, " ".join(str(r["text"]) for r in review), "system"))
    else:
        out.append(item("livekit_public", "Звонки: публичный адрес LiveKit", OK, "Публичный адрес принимает WebSocket (проверка с сервера; устройства пользователей проверьте отдельно)", "system"))
    tcp = lk.get("rtc_tcp") or {}
    if tcp.get("ok") is False:
        out.append(item("rtc_ports", "Медиапорты (TCP/UDP)", FAIL, f"TCP-порт {tcp.get('port')} недоступен ({tcp.get('error') or 'нет соединения'}): без него звук и видео не пойдут, если UDP закрыт", "system"))
    elif not tcp.get("configured"):
        out.append(item("rtc_ports", "Медиапорты (TCP/UDP)", WARN, "Порты медиа не заданы в настройках установки", "system"))
    else:
        out.append(item("rtc_ports", "Медиапорты (TCP/UDP)", OK, "TCP-порт отвечает; UDP автоматически не проверяется — убедитесь, что порт открыт клиентам", "system"))
    return out


async def check_ldap(app, db: AsyncSession, ctx: Ctx) -> list[dict]:
    from ..auth.directory import DirectoryError  # noqa: PLC0415

    try:
        await asyncio.wait_for(asyncio.to_thread(app.state.directory.check_service_account), 15)
        return [item("ldap_health", "Каталог (LDAPS): связь", OK, "Сервисная учётная запись подключается", "ldap")]
    except DirectoryError as exc:
        if exc.code == "not_configured":
            return [item("ldap_health", "Каталог (LDAPS): связь", TODO, "Каталог не настроен: входят только локальные учётные записи", "ldap")]
        return [item("ldap_health", "Каталог (LDAPS): связь", FAIL, f"Каталог не отвечает: {exc.code}", "ldap")]
    except Exception as exc:  # noqa: BLE001
        return [item("ldap_health", "Каталог (LDAPS): связь", FAIL, f"Проверка каталога не выполнена ({type(exc).__name__})", "ldap")]


async def check_asr(app, db: AsyncSession, ctx: Ctx) -> list[dict]:
    try:
        hb = await app.state.bridge.heartbeat()
    except Exception as exc:  # noqa: BLE001
        return [item("asr", "Распознавание речи (ASR)", WARN, f"Служба распознавания не отвечает ({type(exc).__name__}): звонки работают, стенограммы нет", "asr")]
    if hb and hb.get("model_loaded"):
        return [item("asr", "Распознавание речи (ASR)", OK, "Модель загружена, служба отвечает", "asr")]
    return [item("asr", "Распознавание речи (ASR)", WARN, "Модель распознавания не загружена или служба остановлена: звонки работают, стенограммы нет", "asr")]


async def check_llm(app, db: AsyncSession, ctx: Ctx) -> list[dict]:
    cfg = await app.state.settings_svc.get(db, "llm")
    if not getattr(cfg, "enabled", False):
        return [item("llm", "Языковая модель (протоколы и резюме)", TODO, "Языковая модель не настроена: протоколы и резюме недоступны", "llm")]
    if getattr(cfg, "effective_provider", "") == "local":
        st = await app.state.local_llm.status(cfg)
        if st.get("ready"):
            return [item("llm", "Языковая модель (протоколы и резюме)", OK, "Локальная модель загружена и отвечает", "llm")]
        why = st["runtime"]["detail"] if st["file"]["state"] == "ok" else "файл модели не загружен или повреждён"
        return [item("llm", "Языковая модель (протоколы и резюме)", WARN, f"Локальная модель не готова: {why}", "llm")]
    return [item("llm", "Языковая модель (протоколы и резюме)", OK, "Внешняя модель подключена (доступность по сети не проверяется)", "llm")]


async def check_disk(app, db: AsyncSession, ctx: Ctx) -> list[dict]:
    try:
        free = shutil.disk_usage(app.state.settings.data_dir).free
    except OSError:
        return [item("disk", "Свободное место", WARN, "Каталог данных недоступен для проверки", "system")]
    gb = free / 1024**3
    if free < DISK_FAIL_BYTES:
        return [item("disk", "Свободное место", FAIL, f"Осталось {gb:.1f} ГБ: записи и документы скоро перестанут сохраняться", "system")]
    if free < DISK_WARN_BYTES:
        return [item("disk", "Свободное место", WARN, f"Осталось {gb:.1f} ГБ", "system")]
    return [item("disk", "Свободное место", OK, f"Свободно {gb:.0f} ГБ", "system")]


async def check_master_key(app, db: AsyncSession, ctx: Ctx) -> list[dict]:
    if app.state.settings_svc._box is None:                     # noqa: SLF001 — только флаг
        return [item("master_key", "Ключ шифрования настроек", FAIL, "APP_MASTER_KEY не задан: пароли и токены в настройках сохранить нельзя", "system")]
    return [item("master_key", "Ключ шифрования настроек", OK, "Задан. Храните его копию отдельно: без него зашифрованные настройки не восстановить", "system")]


async def check_backup(app, db: AsyncSession, ctx: Ctx) -> list[dict]:
    st = await app.state.auto_backup.status(db)
    if not st["settings"]["enabled"]:
        return [item("backup", "Резервная копия настроек", WARN, "Автоматическое копирование на сетевое хранилище выключено: при потере сервера настройки придётся вводить заново", "config_backup")]
    last = st["last_ok"]
    if st["stale"]:
        return [item("backup", "Резервная копия настроек", FAIL, "Давно нет успешной копии" + (f" (последняя: {str(last['finished_at'])[:16]})" if last else "") + ": проверьте хранилище в истории заданий", "config_backup")]
    return [item("backup", "Резервная копия настроек", OK, f"Последняя успешная копия: {str(last['finished_at'])[:16]} UTC", "config_backup")]


HEALTH_CHECKS: list[Check] = [check_public_url, check_livekit, check_ldap, check_asr, check_llm, check_disk, check_master_key, check_backup]


DB_CHECKS = {check_llm, check_backup}                                  # пользуются сессией БД: выполняются по одной, остальные — параллельно


async def run_health(app, db: AsyncSession) -> list[dict]:
    ctx: Ctx = {}

    async def one(fn: Check) -> list[dict]:
        try:
            return await asyncio.wait_for(fn(app, db, ctx), CHECK_TIMEOUT_S + 10)
        except Exception as exc:  # noqa: BLE001
            log.exception("Проверка чек-листа не выполнена", extra={"check": fn.__name__})
            key = fn.__name__.removeprefix("check_")
            return [item(key, key, FAIL, f"Проверка не выполнена ({type(exc).__name__})", "system")]

    free = [f for f in HEALTH_CHECKS if f not in DB_CHECKS]
    done = dict(zip(free, await asyncio.gather(*[one(f) for f in free])))
    for f in HEALTH_CHECKS:
        if f in DB_CHECKS:
            done[f] = await one(f)
    return [row for f in HEALTH_CHECKS for row in done[f]]               # порядок — как в списке HEALTH_CHECKS


# ------------------------------------------------------------------------------------------------ итог
def summarize(steps: list[dict], checks: list[dict]) -> dict:
    counts = {OK: 0, WARN: 0, FAIL: 0, TODO: 0}
    for st in steps:
        counts[OK if st["done"] else TODO] += 1
    for c in checks:
        counts[c["status"]] = counts.get(c["status"], 0) + 1
    total = sum(counts.values())
    return {**counts, "total": total, "attention": counts[WARN] + counts[FAIL] + counts[TODO]}


async def last_check(app) -> dict:
    raw = await app.state.redis.get(REDIS_KEY)
    try:
        d = json.loads(raw) if raw else {}
    except ValueError:
        d = {}
    return {"checks": d.get("checks") or [], "at": d.get("at")}


async def run_and_store(app, db: AsyncSession) -> dict:
    checks = await run_health(app, db)
    at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    await app.state.redis.set(REDIS_KEY, json.dumps({"at": at, "checks": checks}, ensure_ascii=False))
    return {"checks": checks, "at": at}
