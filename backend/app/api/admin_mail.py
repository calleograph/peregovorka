"""Администрирование исходящей почты: профили SMTP, проверка соединения, тестовое письмо, журнал отправки.

SMTP-реквизиты доступны только администраторам системы; руководители комнат выбирают лишь «что и кому» (см. api/room_manage.py).
"""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import SessionUser, client_ip, get_db, require_admin
from ..models import MailMessage, utcnow
from ..services.audit import write_audit
from ..services.mail import MailError, build_message, check_connection, send, valid_email
from ..services.settings import SettingsError

router = APIRouter(prefix="/admin/mail", tags=["admin-mail"])


@router.get("/profiles")
async def profiles(request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    return {"items": await request.app.state.mail.list(db)}


@router.post("/profiles", status_code=201)
async def profile_create(request: Request, body: dict[str, Any] = Body(...), su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    try:
        out = await request.app.state.mail.create(db, body, str(body.get("secret") or ""))
    except SettingsError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="mail.profile.create", target_type="mail_profile", target_id=out["id"],
                      ip=client_ip(request), details={"name": out["name"], "host": out["host"], "port": out["port"], "security": out["security"]})
    await db.commit()
    return out


@router.patch("/profiles/{pid}")
async def profile_update(pid: str, request: Request, body: dict[str, Any] = Body(...), su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    try:
        out = await request.app.state.mail.update(db, pid, body, body.get("secret") if "secret" in body else None)
    except SettingsError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="mail.profile.update", target_type="mail_profile", target_id=pid, ip=client_ip(request),
                      details={"name": out["name"], "changed": sorted(k for k in body if k != "secret"), "secret_changed": body.get("secret") is not None})
    await db.commit()
    return out


@router.delete("/profiles/{pid}", status_code=204)
async def profile_delete(pid: str, request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    try:
        r = await request.app.state.mail.delete(db, pid)
    except SettingsError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="mail.profile.delete", target_type="mail_profile", target_id=pid, ip=client_ip(request),
                      details={"name": r.name})
    await db.commit()


@router.post("/profiles/{pid}/activate")
async def profile_activate(pid: str, request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    try:
        out = await request.app.state.mail.activate(db, pid)
    except SettingsError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="mail.profile.activate", target_type="mail_profile", target_id=pid, ip=client_ip(request),
                      details={"name": out["name"]})
    await db.commit()
    return out


@router.post("/profiles/{pid}/check")
async def profile_check(pid: str, request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """«Проверить соединение»: DNS → подключение → защита (сертификат) → вход. Письмо не отправляется."""
    svc = request.app.state.mail
    try:
        r = await svc.row(db, pid)
    except SettingsError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    await request.app.state.ca.rebuild(db)
    res = await asyncio.to_thread(check_connection, svc.config(r))
    request.app.state.journal.emit("mail", "profile_check", level="info" if res["ok"] else "warn", user=su.display_name, ip=client_ip(request),
                                   message=f"{r.name}: {'OK' if res['ok'] else res.get('stage', 'ошибка')}", data={"profile": r.name, "ok": res["ok"]})
    return res


@router.post("/profiles/{pid}/test-send")
async def profile_test_send(pid: str, request: Request, body: dict[str, Any] = Body(...), su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """«Отправить тестовое письмо» на введённый адрес — сразу, а не через очередь, чтобы ошибка была видна на месте."""
    to = str(body.get("to") or "").strip()
    if not valid_email(to):
        raise HTTPException(status_code=422, detail="Укажите корректный адрес получателя тестового письма.")
    svc = request.app.state.mail
    try:
        r = await svc.row(db, pid)
    except SettingsError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    await request.app.state.ca.rebuild(db)
    cfg = svc.config(r)
    msg = build_message(cfg, to, "Проверка исходящей почты Peregovorka",
                        "Это тестовое письмо от системы Peregovorka.\nЕсли вы его получили, исходящая почта настроена верно.\n\nОтвечать на письмо не нужно.")
    ok, detail = True, ""
    try:
        await asyncio.to_thread(send, cfg, msg)
    except MailError as err:
        ok, detail = False, err.short()
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="mail.test_send", target_type="mail_profile", target_id=pid, ip=client_ip(request),
                      details={"to": to, "ok": ok, "error": detail})
    await db.commit()
    request.app.state.journal.emit("mail", "test_sent" if ok else "test_failed", level="info" if ok else "warn", user=su.display_name, ip=client_ip(request),
                                   message=f"Тестовое письмо на {to}: {'отправлено' if ok else detail}", data={"to": to, "ok": ok})
    return {"ok": ok, "message": "Тестовое письмо отправлено." if ok else detail}


# ------------------------------------------------------------------------------------------------ журнал отправки
def _row(m: MailMessage) -> dict:
    return {"id": str(m.id), "created_at": m.created_at.isoformat(), "sent_at": m.sent_at.isoformat() if m.sent_at else None, "state": m.state,
            "attempts": m.attempts, "max_attempts": m.max_attempts, "next_attempt_at": m.next_attempt_at.isoformat(), "recipient": m.recipient,
            "recipient_name": m.recipient_name, "room": m.room_name, "meeting_id": str(m.meeting_id) if m.meeting_id else None, "subject": m.subject,
            "kinds": m.kinds or [], "trigger": m.trigger, "requested_by": m.requested_by, "last_error": m.last_error, "delivery": m.delivery}


@router.get("/messages")
async def messages(state: str = Query("", pattern="^(|queued|sending|sent|failed)$"), q: str = Query("", max_length=100), limit: int = Query(100, ge=1, le=500),
                   offset: int = Query(0, ge=0), su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    stmt = select(MailMessage).order_by(MailMessage.created_at.desc()).limit(limit).offset(offset)
    if state:
        stmt = stmt.where(MailMessage.state == state)
    if q.strip():
        like = f"%{q.strip().lower()}%"
        stmt = stmt.where(func.lower(MailMessage.recipient).like(like) | func.lower(func.coalesce(MailMessage.room_name, "")).like(like))
    rows = (await db.execute(stmt)).scalars().all()
    counts = dict((await db.execute(select(MailMessage.state, func.count()).group_by(MailMessage.state))).all())
    return {"items": [_row(m) for m in rows], "counts": {k: counts.get(k, 0) for k in ("queued", "sending", "sent", "failed")}}


@router.post("/messages/{mid}/retry")
async def message_retry(mid: str, request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    import uuid  # noqa: PLC0415

    try:
        m = await db.get(MailMessage, uuid.UUID(mid))
    except ValueError:
        m = None
    if m is None:
        raise HTTPException(status_code=404, detail="Письмо не найдено")
    if m.state not in ("failed", "queued"):
        raise HTTPException(status_code=409, detail="Повторить можно только неотправленное письмо")
    m.state, m.next_attempt_at, m.attempts = "queued", utcnow(), 0
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="mail.retry", target_type="mail_message", target_id=mid, ip=client_ip(request),
                      details={"recipient": m.recipient})
    await db.commit()
    return _row(m)
