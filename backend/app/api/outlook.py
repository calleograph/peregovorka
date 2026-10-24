"""Надстройка Outlook: выдача токена, создание/перенос/отмена запланированной конференции, публичная страница обратного отсчёта, манифест.

Токен надстройки выдаётся вошедшему пользователю (в окне Office dialog, где работают пароль, 2FA и CAPTCHA) и годится только для этих ручек. Время — в UTC (ISO 8601).
См. services/outlook.py и docs/OUTLOOK_ADDIN.md.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime

from fastapi import APIRouter, Body, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import SessionUser, client_ip, get_db, require_user
from ..auth.service import AuthError
from ..config import origin_allowed
from ..auth.throttle import within_limit
from ..services import captcha
from ..models import Meeting, Room, User, utcnow
from ..services import outlook as ol
from ..services import outlook_compat as compat
from ..services import outlook_diag as od
from ..services import storage_encryption as se
from ..services.audit import write_audit
from ..services.temp_rooms import TempRoomError

router = APIRouter(prefix="/outlook", tags=["outlook"])
PUBLIC_LIMIT, PUBLIC_WINDOW_S = 120, 60            # опросов страницы обратного отсчёта в минуту с одного IP
CREATE_LIMIT, CREATE_WINDOW_S = 30, 60             # создаёт/переносит не чаще 30 раз в минуту на пользователя


async def _cfg(request: Request, db: AsyncSession):
    return await request.app.state.settings_svc.get(db, "outlook")


def _bearer(request: Request) -> str:
    h = request.headers.get("authorization", "")
    return h[7:].strip() if h.lower().startswith("bearer ") else ""


async def _addin_user(request: Request, db: AsyncSession | None = None) -> SessionUser:
    su = await ol.principal(request.app.state.redis, _bearer(request)) if _bearer(request) else None
    if su is not None and db is not None:                                 # учётную запись отключили после входа — токен больше не действует
        u = await db.get(User, su.user_id)
        if u is None or not u.is_active:
            await ol.revoke(request.app.state.redis, _bearer(request))
            su = None
    if su is None:
        raise HTTPException(status_code=401, detail={"code": "token_invalid", "message": "Нужен вход: токен надстройки истёк или не выдан."})
    r = request.app.state.redis
    key = f"outlook:rate:{su.user_id}"
    if await r.incr(key) == 1:
        await r.expire(key, CREATE_WINDOW_S)
    if int(await r.get(key) or 0) > CREATE_LIMIT:
        raise HTTPException(status_code=429, detail="Слишком много запросов. Подождите минуту.", headers={"Retry-After": str(CREATE_WINDOW_S)})
    return su


def _dt(v, field: str) -> datetime:
    try:
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Поле «{field}»: время в формате ISO 8601 (например, 2026-10-20T07:00:00Z)") from None
    return ol.aware(d)


_EMAIL = re.compile(r"^[^@\s<>\"']{1,120}@[A-Za-z0-9.-]{1,120}$")
_ITEM_ID = re.compile(r"^[A-Za-z0-9+/=_.%-]{8,700}$")


def _link(room: Room, body: dict) -> None:
    """Связь с событием календаря для серверной сверки: организатор, идентификаторы (EWS и REST), серия, часовой пояс. Неверные значения молча отбрасываются."""
    org = str(body.get("organizer") or "").strip().lower()
    if _EMAIL.match(org):
        room.outlook_organizer = org
    for field, key in (("outlook_ews_id", "ews_id"), ("outlook_rest_id", "rest_id"), ("outlook_series_id", "series_id")):
        v = str(body.get(key) or "").strip()
        if _ITEM_ID.match(v) and getattr(room, field) != v:
            setattr(room, field, v)
            room.outlook_synced_at = None                                     # новый идентификатор — сверить при ближайшем проходе
    tz = str(body.get("tz") or "").strip()
    if re.match(r"^[A-Za-z0-9_+./ -]{1,64}$", tz):
        room.outlook_tz = tz


def _fail(exc: TempRoomError):
    raise HTTPException(status_code=exc.status, detail={"code": exc.code, "message": exc.message}) from None


def _out(room: Room, base: str, cfg, created: bool = False, meeting_active: bool = False) -> dict:
    html, text = ol.invite_texts(room, base, cfg, guest=bool(room.guest_access_enabled and cfg.guest_links))
    return {"id": str(room.id), "created": created, "url": ol.join_link(base, room), "state": ol.state(room, meeting_active=meeting_active), "name": room.name,
            "start": room.scheduled_start, "end": room.scheduled_end, "opens_at": room.join_opens_at, "recurring": room.recurring, "invite_html": html, "invite_text": text}


@router.get("/config")
async def config(request: Request, db: AsyncSession = Depends(get_db)):
    """Нужно надстройке до входа: включена ли интеграция и как называется сервер."""
    cfg = await _cfg(request, db)
    return {"enabled": cfg.enabled, "early_minutes": cfg.early_minutes}


@router.post("/token")
async def token(request: Request, su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    """Токен надстройки для вошедшего пользователя (вызывается страницей `/outlook/auth.html` в окне Office dialog)."""
    cfg = await _cfg(request, db)
    if not cfg.enabled:
        raise HTTPException(status_code=404, detail="Интеграция с Outlook отключена.")
    if not ol.allowed(cfg, su):
        raise HTTPException(status_code=403, detail="Создавать конференции из Outlook вам не разрешено.")
    tok, ttl = await ol.issue_token(request.app.state.redis, su, cfg.token_ttl_hours)
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="outlook.token", target_type="user", target_id=str(su.user_id), ip=client_ip(request))
    await db.commit()
    return Response('{"token":"%s","expires_in":%d}' % (tok, ttl), media_type="application/json", headers={"Cache-Control": "no-store"})


@router.post("/login")
async def addin_login(request: Request, body: dict = Body(...), db: AsyncSession = Depends(get_db)):
    """Вход в боковой панели Outlook логином и паролем: сразу выдаётся токен надстройки, сеанс браузера не открывается (cookie в панели Outlook ненадёжны).
    Те же проверки, что у обычного входа: ограничение попыток, CAPTCHA, правила допуска, аудит. Если нужен второй фактор, его настройка или смена пароля — ответ 409
    `browser_required`: панель предлагает продолжить вход в окне (`/outlook/auth.html`), где эти шаги проходят как в браузере."""
    origin = request.headers.get("origin")
    if origin is not None and not origin_allowed(origin, request.app.state.settings):
        raise HTTPException(status_code=403, detail="Недопустимый Origin")
    cfg = await _cfg(request, db)
    if not cfg.enabled:
        raise HTTPException(status_code=404, detail="Интеграция с Outlook отключена.")
    login_, password = str(body.get("login") or "")[:256], str(body.get("password") or "")
    ip = client_ip(request)
    await captcha.guard(request, "login")
    try:
        res = await request.app.state.auth.login(db, login_, password, ip, request.headers.get("user-agent") or "", open_session=False)
    except AuthError as exc:
        if exc.code in ("invalid_credentials", "throttled"):
            await captcha.note_failure(request, "login")
        request.app.state.journal.emit("outlook", "addin_login_failed", level="warn", ip=ip, message=exc.message, data={"code": exc.code, "where": "panel"})
        headers = {"Retry-After": str(exc.retry_after)} if exc.retry_after else None
        raise HTTPException(status_code=exc.status, detail={"code": exc.code, "message": exc.message}, headers=headers) from None
    await captcha.note_success(request, "login")
    if res.needs_browser:
        raise HTTPException(status_code=409, detail={"code": "browser_required", "reason": res.needs_browser, "message": "Для вашей учётной записи нужен дополнительный шаг входа."})
    u, p = res.user, res.params or {}
    su = SessionUser(session_id="outlook", user_id=u.id, ad_guid=u.ad_guid, sam_account_name=u.sam_account_name, display_name=u.display_name, is_admin=bool(p.get("is_admin")),
                     groups=frozenset(p.get("groups") or []), csrf="", local=bool(p.get("local")))
    if not ol.allowed(cfg, su):
        raise HTTPException(status_code=403, detail="Создавать конференции из Outlook вам не разрешено.")
    tok, ttl = await ol.issue_token(request.app.state.redis, su, cfg.token_ttl_hours)
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="outlook.token", target_type="user", target_id=str(su.user_id), ip=ip, details={"via": "panel"})
    await db.commit()
    return Response('{"token":"%s","expires_in":%d}' % (tok, ttl), media_type="application/json", headers={"Cache-Control": "no-store"})


@router.post("/token/refresh")
async def refresh(request: Request, db: AsyncSession = Depends(get_db)):
    """Продление входа: панель вызывает, когда прошла половина срока токена. Права проверяются заново; предел — 30 дней от входа паролем, затем снова вход."""
    su = await _addin_user(request, db)
    cfg = await _cfg(request, db)
    if not cfg.enabled or not ol.allowed(cfg, su):
        raise HTTPException(status_code=403, detail="Создавать конференции из Outlook вам не разрешено.")
    new = await ol.refresh_token(request.app.state.redis, _bearer(request), cfg.token_ttl_hours)
    if new is None:
        raise HTTPException(status_code=401, detail={"code": "token_invalid", "message": "Срок входа истёк: войдите снова."})
    return Response('{"token":"%s","expires_in":%d}' % new, media_type="application/json", headers={"Cache-Control": "no-store"})


@router.post("/meetings")
async def create_meeting(request: Request, body: dict = Body(...), db: AsyncSession = Depends(get_db)):
    """Создать запланированную конференцию для события календаря (идемпотентно по `key`) или вернуть уже связанную; обновляет время и название."""
    su = await _addin_user(request, db)
    cfg = await _cfg(request, db)
    s = request.app.state
    instance = bool(body.get("instance"))          # одно занятие серии (Office.js отдаёт ему шаблон всей серии и собственные идентификаторы)
    try:
        room, created = await ol.ensure(db, su, cfg, key=str(body.get("key") or ""), subject=str(body.get("subject") or ""), start=_dt(body.get("start"), "start"),
                                        end=_dt(body.get("end"), "end"), recurring=bool(body.get("recurring")) and not instance,
                                        default_text_days=s.settings.default_text_retention_days, default_audio_days=s.settings.default_audio_retention_days,
                                        encrypt_storage=se.default_for_new(await s.settings_svc.get(db, "general")), instance=instance)
    except TempRoomError as exc:
        _fail(exc)
    attendees = ol.parse_attendees(body.get("attendees"))
    if instance and room.recurring:
        # серия не меняется: ни время, ни связь с мастер-событием, ни шаблон. Приглашённые занятия добавляются к серии (не заменяют её список);
        # перенос или отмену занятия календарь сообщит при ближайшей сверке
        await ol.set_invitees(db, room, attendees, "addin", prune=False)
        room.outlook_synced_at = None
    else:
        _link(room, body)
        await ol.set_invitees(db, room, attendees, "addin")     # кто приглашён — для «Предстоящих встреч» (нет в запросе — список не меняется)
        ol.apply_recurrence(room, body.get("recurrence"))         # шаблон серии — ближайшее занятие в «Предстоящих встречах»
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, target_type="room", target_id=str(room.id), ip=client_ip(request),
                      action="outlook.create" if created else "outlook.occurrence" if instance and room.recurring else "outlook.sync",
                      details={"start": room.scheduled_start.isoformat(), "end": room.scheduled_end.isoformat(), "recurring": room.recurring})
    await db.commit()
    return _out(room, ol.public_url(s.settings.app_public_url, cfg), cfg, created)


@router.get("/me")
async def addin_me(request: Request, db: AsyncSession = Depends(get_db)):
    """Кто вошёл по токену надстройки: боковая панель проверяет вход при открытии (401 — токен истёк или отозван)."""
    su = await _addin_user(request, db)
    cfg = await _cfg(request, db)
    return {"name": su.display_name, "enabled": cfg.enabled}


@router.delete("/token", status_code=204)
async def addin_logout(request: Request):
    """Выход из надстройки: токен отзывается сразу, а не по истечении срока."""
    h = request.headers.get("authorization", "")
    if h.lower().startswith("bearer "):
        await ol.revoke(request.app.state.redis, h[7:].strip())


_LOG_CODE = re.compile(r"^[a-z0-9_]{1,40}$")


@router.post("/log", status_code=204)
async def addin_log(request: Request, body: dict = Body(...)):
    """Ошибки надстройки попадают в журнал событий («Администрирование → Интеграции → Microsoft Outlook»): этап, код ошибки, тип и версия клиента Outlook. Без токенов и тела письма."""
    su = await _addin_user(request)
    msg = " ".join(str(body.get("message") or "").split())[:300]
    if not msg:
        return
    code = str(body.get("code") or "").strip().lower()
    code = code if _LOG_CODE.match(code) else ""
    where = str(body.get("where") or "")[:40]
    event = "addin_legacy" if code == "legacy_manifest" else "addin_ok" if code == "ok" else "addin_error"      # ok — итог успешной операции (без секретов)
    request.app.state.journal.emit("outlook", event, level="warn" if event == "addin_error" else "info", user=su.display_name, ip=client_ip(request),
                                   client=" ".join(str(body.get("client") or "").split())[:160] or None,
                                   message=f"[{where}/{code}] {msg}" if code else msg, data={"where": where, "code": code})


_GIF = (b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;")
_DIAG_EVENTS = {"boot": ("addin_boot", "info"), "ready": ("addin_ready", "info"), "init_timeout": ("addin_init_timeout", "warn"), "init_failed": ("addin_init_failed", "warn")}
_DIAG_SAFE = re.compile(r"[^0-9A-Za-zА-Яа-яЁё .,:;()/_+=«»—-]")


@router.get("/diag")
async def addin_diag(request: Request):
    """Самодиагностика запуска надстройки (frontend/public/outlook/boot.js): в какой среде Outlook открыл страницу и запустил ли её Office. Без входа — так
    видна и неудачная инициализация, когда токена ещё нет; только известные события и технические признаки (режим документа IE, наличие Promise/fetch,
    загрузка Office.js, тип клиента), значения укорачиваются и очищаются, частота ограничена. Ответ — прозрачная картинка 1×1 (маячок работает и в старом IE)."""
    q = request.query_params
    ev = _DIAG_EVENTS.get(q.get("e", ""))
    pic = Response(_GIF, media_type="image/gif", headers={"Cache-Control": "no-store"})
    if ev is None or not await within_limit(request.app.state.redis, f"rl:outlook-diag:{client_ip(request)}", 120, 600):
        return pic
    def v(k: str, n: int = 40) -> str:
        return _DIAG_SAFE.sub("", q.get(k, ""))[:n]
    page = v("pg", 12) if v("pg", 12) in ("taskpane", "commands", "auth") else "?"
    dm = v("dm", 4)
    parts = [f"страница {page}", f"режим IE {dm}" if dm else "", f"Promise {v('p', 10)}", f"fetch {v('f', 10)}",
             "Office.js не загружена" if v("o", 10) == "undefined" else "Office.js загружена", "контекст Office есть" if v("oc", 2) == "1" else "контекста Office нет",
             f"через {v('ms', 7)} мс", v("why", 160), f"клиент {v('h', 20)} {v('pl', 20)} {v('cl', 60)}".strip() if v("h", 20) or v("cl", 60) else "",
             f"код {v('code', 30)}/{v('st', 20)}" if v("code", 30) else ""]
    request.app.state.journal.emit("outlook", ev[0], level=ev[1], ip=client_ip(request), client=" ".join((request.headers.get("user-agent") or "").split())[:160] or None,
                                   message="; ".join(p for p in parts if p), data={"page": page, "documentMode": dm, "event": q.get("e")})
    return pic


@router.get("/meetings/by-key/{key}")
async def by_key(key: str, request: Request, db: AsyncSession = Depends(get_db)):
    su = await _addin_user(request, db)
    cfg = await _cfg(request, db)
    room = await ol.find_by_key(db, su.user_id, key) if ol.KEY_RE.match(key) else None
    if room is None:
        raise HTTPException(status_code=404, detail="Связанной конференции нет.")
    return _out(room, ol.public_url(request.app.state.settings.app_public_url, cfg), cfg)


@router.patch("/meetings/{room_id}")
async def update_meeting(room_id: uuid.UUID, request: Request, body: dict = Body(...), db: AsyncSession = Depends(get_db)):
    su = await _addin_user(request, db)
    cfg = await _cfg(request, db)
    try:
        room = await ol.owned(db, su, room_id)
        changed = await ol.reschedule(db, room, cfg, subject=" ".join(str(body.get("subject") or "").split())[:200] or None,
                                      start=_dt(body.get("start", room.scheduled_start.isoformat()), "start"), end=_dt(body.get("end", room.scheduled_end.isoformat()), "end"),
                                      recurring=bool(body.get("recurring", room.recurring)))
    except TempRoomError as exc:
        _fail(exc)
    if await ol.set_invitees(db, room, ol.parse_attendees(body.get("attendees")), "addin") | ol.apply_recurrence(room, body.get("recurrence")):
        await db.commit()
    if changed:
        await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="outlook.reschedule", target_type="room", target_id=str(room.id), ip=client_ip(request),
                          details={"start": room.scheduled_start.isoformat(), "end": room.scheduled_end.isoformat()})
        await db.commit()
    return _out(room, ol.public_url(request.app.state.settings.app_public_url, cfg), cfg)


@router.delete("/meetings/{room_id}", status_code=204)
async def cancel_meeting(room_id: uuid.UUID, request: Request, db: AsyncSession = Depends(get_db)):
    su = await _addin_user(request, db)
    try:
        room = await ol.owned(db, su, room_id)
    except TempRoomError as exc:
        _fail(exc)
    if await ol.cancel(db, room):
        await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="outlook.cancel", target_type="room", target_id=str(room.id), ip=client_ip(request))
        await db.commit()


@router.get("/public/{code}")
async def public_info(code: str, request: Request, db: AsyncSession = Depends(get_db)):
    """Страница обратного отсчёта: название, время, состояние. Без входа; неверный код и чужая комната отвечают одинаково (404)."""
    r = request.app.state.redis
    key = f"outlook:pub:{client_ip(request)}"
    if await r.incr(key) == 1:
        await r.expire(key, PUBLIC_WINDOW_S)
    if int(await r.get(key) or 0) > PUBLIC_LIMIT:
        raise HTTPException(status_code=429, detail="Слишком много обращений.", headers={"Retry-After": str(PUBLIC_WINDOW_S)})
    cfg = await _cfg(request, db)
    code = code.strip().lower()
    if not cfg.enabled or not (20 <= len(code) <= 32) or not code.isalnum():
        raise HTTPException(status_code=404, detail="Конференция не найдена.")
    room = (await db.execute(select(Room).where(Room.slug == ol.PREFIX + code))).scalars().first()
    if room is None or room.scheduled_start is None:
        raise HTTPException(status_code=404, detail="Конференция не найдена.")
    live = (await db.execute(select(Meeting.id).where(Meeting.room_id == room.id, Meeting.ended_at.is_(None)))).first() is not None
    now = utcnow()
    return ol.public_view(room, ol.state(room, now, meeting_active=live), now=now, cfg=cfg)


# ------------------------------------------------------------------------------------------------ публичный центр установки (без входа)
def _public_base(request: Request, cfg) -> str:
    return ol.public_url(request.app.state.settings.app_public_url, cfg)


@router.get("/center")
async def center(request: Request, db: AsyncSession = Depends(get_db)):
    """Данные публичной страницы /outlook: адреса манифеста и пакета, сведения о публичном TLS, матрица совместимости. Только то, что и так видно любому клиенту; секретов и внутренних адресов нет."""
    cfg = await _cfg(request, db)
    s = request.app.state.settings
    base = _public_base(request, cfg)
    if not cfg.enabled or not base.startswith("https://"):
        return {"enabled": False, "reason": "Интеграция с Outlook выключена администратором или не задан публичный HTTPS-адрес.", "compat": compat.matrix(cfg.mail_kind, cfg.sync_on_send)}
    tls = await od.tls_snapshot(request.app, base, fetch_aia=False, extra_ca=s.check_ca)
    has_chain = bool(tls and tls.get("chain"))
    return {"enabled": True, "base": base, "version": s.app_version, "manifest_url": f"{base}/api/v1/outlook/manifest.xml", "package_url": f"{base}/api/v1/outlook/package",
            "addin_id": ol.addin_id(base), "mail_kind": cfg.mail_kind, "compat": compat.matrix(cfg.mail_kind, cfg.sync_on_send),
            "tls": od.public_tls_view(tls) if tls else None,
            "downloads": {"chain": has_chain, "fullchain": bool(tls and (tls.get("completed_chain_pem") or tls.get("_chain_pem")))}}


_TLS_FILES = {"chain.pem": "presented", "fullchain.pem": "full"}


@router.get("/tls/{name}")
async def tls_file(name: str, request: Request, db: AsyncSession = Depends(get_db)):
    """Публичные сертификаты адреса: `chain.pem` — как их отдаёт сервер, `fullchain.pem` — сертификат сайта и промежуточные без корня (дополнено из AIA, если цепочка была неполной),
    `cert-N.pem|.cer` — отдельный сертификат цепочки (PEM или DER). Это общедоступные данные: любой клиент получает их при соединении. Закрытых ключей здесь нет и быть не может."""
    cfg = await _cfg(request, db)
    base = _public_base(request, cfg)
    if not cfg.enabled or not base.startswith("https://"):
        raise HTTPException(status_code=404, detail="Интеграция выключена.")
    res = await od.tls_snapshot(request.app, base, fetch_aia=False, extra_ca=request.app.state.settings.check_ca)
    pem = (res or {}).get("completed_chain_pem") or (res or {}).get("_chain_pem")
    if not pem:
        raise HTTPException(status_code=404, detail="Сертификаты публичного адреса недоступны с сервера. Скачайте их в браузере: значок замка → сертификат → экспорт.")
    from cryptography import x509  # noqa: PLC0415
    from cryptography.hazmat.primitives import serialization  # noqa: PLC0415

    certs = x509.load_pem_x509_certificates(pem.encode("ascii"))
    headers = {"Cache-Control": "no-store", "Content-Disposition": f'attachment; filename="{name}"'}
    if name == "chain.pem" or name == "fullchain.pem":
        keep = [c for c in certs if not (name == "fullchain.pem" and c.subject == c.issuer)]
        return Response("".join(c.public_bytes(serialization.Encoding.PEM).decode("ascii") for c in keep), media_type="application/x-pem-file", headers=headers)
    m = re.fullmatch(r"cert-(\d{1,2})\.(pem|cer)", name)
    if not m or int(m.group(1)) >= len(certs):
        raise HTTPException(status_code=404, detail="Такого файла нет.")
    c = certs[int(m.group(1))]
    if m.group(2) == "pem":
        return Response(c.public_bytes(serialization.Encoding.PEM), media_type="application/x-pem-file", headers=headers)
    return Response(c.public_bytes(serialization.Encoding.DER), media_type="application/pkix-cert", headers=headers)


@router.get("/package")
async def public_package(request: Request, db: AsyncSession = Depends(get_db)):
    """Установочный пакет для администратора Exchange без входа: манифест, инструкции и сценарии PowerShell. Содержит только публичный адрес, идентификатор надстройки и версию;
    паролей, ключей и настроек сверки в нём нет (тот же состав, что у защищённого пакета администратора)."""
    cfg = await _cfg(request, db)
    s = request.app.state.settings
    base = _public_base(request, cfg)
    if not cfg.enabled or not base.startswith("https://"):
        raise HTTPException(status_code=404, detail="Интеграция выключена или не задан публичный HTTPS-адрес.")
    from ..services.outlook_package import build_package  # noqa: PLC0415

    return Response(build_package(base, s.app_version, cfg), media_type="application/zip", headers={"Content-Disposition": 'attachment; filename="Peregovorka-Outlook.zip"', "Cache-Control": "no-store"})


@router.get("/scripts/{name}")
async def public_script(name: str, request: Request, db: AsyncSession = Depends(get_db)):
    """Отдельный сценарий PowerShell из пакета (без входа): те же файлы, что в архиве; параметры сценарии берут из манифеста и аргументов."""
    from ..services import outlook_package as pk  # noqa: PLC0415

    scripts = {"Install-OutlookAddin.ps1": pk.INSTALL_PS1, "Test-OutlookAddin.ps1": pk.TEST_PS1, "Uninstall-OutlookAddin.ps1": pk.UNINSTALL_PS1}
    cfg = await _cfg(request, db)
    if not cfg.enabled or name not in scripts:
        raise HTTPException(status_code=404, detail="Такого сценария нет или интеграция выключена.")
    body = (pk.BOM + scripts[name]).replace("\r\n", "\n").replace("\n", "\r\n")
    return Response(body.encode("utf-8"), media_type="text/plain; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"})


@router.get("/manifest.xml")
async def manifest(request: Request, db: AsyncSession = Depends(get_db)):
    """Манифест надстройки для установки администратором (в нём нет секретов; отдаётся только при включённой интеграции)."""
    cfg = await _cfg(request, db)
    s = request.app.state.settings
    base = ol.public_url(s.app_public_url, cfg)
    if not cfg.enabled or not base.startswith("https://"):
        raise HTTPException(status_code=404, detail="Интеграция выключена или не задан публичный HTTPS-адрес.")
    return Response(ol.manifest_xml(base, s.app_version, sync_on_send=cfg.effective_sync_on_send), media_type="application/xml", headers={"Content-Disposition": 'attachment; filename="peregovorka-outlook-manifest.xml"', "Cache-Control": "no-store"})
