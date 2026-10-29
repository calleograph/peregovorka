"""Администрирование → Интеграции → SIP-телефония: профили (транки), проверка настроек, тестовый вызов, состояние службы.

Пароль профиля хранится зашифрованно и обратно не отдаётся. В журнал и аудит попадают создание, изменение, удаление, проверки и вызовы — без паролей
и с частично скрытыми номерами. Включение службы на сервере (порты, контейнер livekit-sip) выполняет помощник обновлений, а не backend.
"""
from __future__ import annotations

import asyncio
import re
import time
import uuid
from ..services import server_components as _sc
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlalchemy import select
from urllib.parse import urlsplit
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import SessionUser, client_ip, get_db, require_admin
from ..models import Room, SipCall, SipProfile, utcnow
from ..services.audit import write_audit
from ..services.settings import SettingsError
from ..services.sip import (CODEC_RATES, SipError, connection_summary, explain_livekit_error, explain_sip_status, mask_number, normalize_number, service_status, sip_options_probe,
                            dns_probe, tcp_listen_probe)
from ..services.sip_guide import build_guide

router = APIRouter(prefix="/admin/sip", tags=["admin-sip"])


def _st(request: Request):
    return request.app.state


async def _sync(request: Request, db: AsyncSession, row: SipProfile) -> dict:
    """Приводит транки LiveKit в соответствие с профилем. Ошибка не мешает сохранению профиля — она возвращается для показа администратору."""
    st = _st(request)
    if not _sc.enabled_on_server(st.settings, _sc.channel_for(st.settings), "sip"):
        return {"ok": None, "message": "Телефония выключена на сервере: профиль сохранён, транки в LiveKit будут созданы после её включения."}
    try:
        ids = await st.sip_gateway.sync_profile(row, st.sip.decrypt(row))
        row.lk_outbound_trunk_id, row.lk_inbound_trunk_id = ids["outbound"], ids["inbound"]
        await db.flush()
        return {"ok": True, "message": "Транки LiveKit обновлены."}
    except Exception as exc:  # noqa: BLE001
        msg, _ = explain_livekit_error(exc)
        return {"ok": False, "message": msg}


def _audit(request, su, db, action, target, details) -> Any:
    return write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action=action, target_type="sip_profile", target_id=target, ip=client_ip(request), details=details)


@router.get("/status")
async def status(request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    st = _st(request)
    profiles = await st.sip.list(db)
    out = await service_status(st.settings, st.sip_gateway, profiles, getattr(st, "test_transports", {}).get("sip_health"))
    chk = [(p.name, p.last_check, p.last_check_at) for p in profiles if p.last_check]
    last = max(chk, key=lambda x: x[2] or utcnow(), default=None)
    out["last_check"] = {"profile": last[0], "result": last[1], "at": last[2].isoformat() if last[2] else None} if last else None
    default = await st.sip.default(db)
    out["active_trunk"] = default.name if default else None
    out["codecs_supported"] = list(CODEC_RATES)
    return out


@router.get("/profiles")
async def list_profiles(request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    st = _st(request)
    rooms = {}
    for r in (await db.execute(select(Room.sip_profile_id, Room.sip_mode))).all():
        rooms[str(r[0])] = rooms.get(str(r[0]), 0) + 1
    return {"items": [{**st.sip.public(p), "rooms_using": rooms.get(str(p.id), 0)} for p in await st.sip.list(db)]}


@router.post("/profiles", status_code=201)
async def create_profile(request: Request, body: dict[str, Any] = Body(...), su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    st = _st(request)
    try:
        row = await st.sip.create(db, body, str(body.get("secret") or ""))
    except SettingsError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    sync = await _sync(request, db, row)
    await _audit(request, su, db, "sip.profile.create", str(row.id), {"name": row.name, "host": row.host, "port": row.port, "transport": row.transport, "direction": row.direction})
    await db.commit()
    return {**st.sip.public(row), "sync": sync}


@router.patch("/profiles/{pid}")
async def update_profile(pid: str, request: Request, body: dict[str, Any] = Body(...), su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    st = _st(request)
    try:
        row = await st.sip.update(db, pid, body, body.get("secret") if "secret" in body else None)
    except SettingsError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    sync = await _sync(request, db, row)
    await _audit(request, su, db, "sip.profile.update", pid, {"name": row.name, "changed": sorted(k for k in body if k != "secret"), "secret_changed": body.get("secret") is not None})
    await db.commit()
    return {**st.sip.public(row), "sync": sync}


@router.delete("/profiles/{pid}", status_code=204)
async def delete_profile(pid: str, request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    st = _st(request)
    try:
        row = await st.sip.row(db, pid)
    except SettingsError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    used = (await db.execute(select(Room.name).where(Room.sip_profile_id == row.id, Room.sip_mode == "profile"))).scalars().all()
    if used:
        raise HTTPException(status_code=409, detail=f"Профиль выбран в комнатах: {', '.join(used[:5])}. Сначала выберите для них другой профиль или отключите телефонию.")
    try:
        await st.sip_gateway.delete_trunks(row)
    except Exception as exc:  # noqa: BLE001 — LiveKit может быть недоступен; профиль всё равно удаляем
        pass
    await db.delete(row)
    await _audit(request, su, db, "sip.profile.delete", pid, {"name": row.name})
    await db.commit()


@router.post("/profiles/{pid}/sync")
async def sync_profile(pid: str, request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    st = _st(request)
    try:
        row = await st.sip.row(db, pid)
    except SettingsError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    res = await _sync(request, db, row)
    await _audit(request, su, db, "sip.profile.sync", pid, {"name": row.name, "ok": res["ok"]})
    await db.commit()
    return res


@router.post("/profiles/{pid}/check")
async def check_profile(pid: str, request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """«Проверить настройки»: правильность значений → достижимость АТС (SIP OPTIONS/TLS) → транки в LiveKit → служба SIP."""
    st = _st(request)
    try:
        row = await st.sip.row(db, pid)
    except SettingsError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    stages: list[dict] = []

    def add(stage: str, ok: bool | None, message: str, ms: int = 0) -> None:
        stages.append({"stage": stage, "ok": ok, "message": message, "ms": ms})

    warn = []
    if row.direction in ("outbound", "both") and not row.caller_id:
        warn.append("не задан caller ID — многие АТС отклоняют вызовы без номера")
    if row.direction in ("inbound", "both") and not (row.allowed_addresses or row.username):
        warn.append("для входящих не задан ни список разрешённых адресов АТС, ни логин — принимать звонки с любых адресов небезопасно")
    add("config", True if not warn else None, "Значения профиля корректны." + (" Замечания: " + "; ".join(warn) + "." if warn else ""))
    dns = await asyncio.to_thread(dns_probe, row.host, row.port)
    add("dns", dns["ok"], dns["message"])
    probe = await asyncio.to_thread(sip_options_probe, row.host, row.port, row.transport)
    add("pbx", probe["ok"], probe["message"], probe["ms"])
    started = time.monotonic()
    enabled = _sc.enabled_on_server(st.settings, _sc.channel_for(st.settings), "sip")
    if not enabled:
        add("livekit", None, "Телефония выключена на сервере (SIP_ENABLED=no): проверка транков LiveKit пропущена. Включите её в разделе «Состояние».")
    else:
        try:
            ids = await st.sip_gateway.list_trunk_ids()
            want = [t for t in (row.lk_outbound_trunk_id, row.lk_inbound_trunk_id) if t]
            if not want:
                add("livekit", False, "Профиль ещё не синхронизирован с LiveKit: нажмите «Синхронизировать».", int((time.monotonic() - started) * 1000))
            elif all(t in ids for t in want):
                add("livekit", True, "Транки профиля есть в LiveKit.", int((time.monotonic() - started) * 1000))
            else:
                add("livekit", False, "Часть транков отсутствует в LiveKit (удалены вручную?): нажмите «Синхронизировать».", int((time.monotonic() - started) * 1000))
        except Exception as exc:  # noqa: BLE001
            add("livekit", False, explain_livekit_error(exc)[0], int((time.monotonic() - started) * 1000))
        svc = await service_status(st.settings, st.sip_gateway, [row], getattr(st, "test_transports", {}).get("sip_health"))
        add("service", svc["service"]["running"], svc["service"]["detail"])
        shost = urlsplit(st.settings.sip_health_url).hostname or "livekit-sip"
        lp = await asyncio.to_thread(tcp_listen_probe, shost, int(st.settings.sip_signaling_port))
        add("listen", lp["ok"], lp["message"])
    ok = not any(s["ok"] is False for s in stages)
    result = {"ok": ok, "stages": stages}
    keep = {k: v for k, v in (row.last_check or {}).items() if k in ("test_call", "audio_in", "audio_confirmed")}      # итоги тестового вызова не теряются при новой проверке
    row.last_check, row.last_check_at = {"ok": ok, "stages": [{"stage": s["stage"], "ok": s["ok"], "message": s["message"]} for s in stages], **keep}, utcnow()
    result["summary"] = connection_summary(row.last_check)
    await _audit(request, su, db, "sip.profile.check", pid, {"name": row.name, "ok": ok})
    request.app.state.journal.emit("sip", "profile_check", level="info" if ok else "warn", user=su.display_name, ip=client_ip(request), message=f"{row.name}: {'OK' if ok else 'есть проблемы'}")
    await db.commit()
    return result


@router.post("/profiles/{pid}/test-call")
async def test_call(pid: str, request: Request, body: dict[str, Any] = Body(...), su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """«Тестовый вызов»: звонок на указанный номер через исходящий транк во временную комнату; после ответа абонента вызов сразу завершается."""
    st = _st(request)
    try:
        row = await st.sip.row(db, pid)
    except SettingsError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    try:
        number = normalize_number(str(body.get("number") or ""))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    if not row.lk_outbound_trunk_id:
        raise HTTPException(status_code=409, detail="У профиля нет исходящего транка в LiveKit: проверьте направление и нажмите «Синхронизировать».")
    pol = await st.sip.policy(db)
    room_name = f"sip-test-{uuid.uuid4().hex[:10]}"
    ident = f"sip-test-{uuid.uuid4().hex[:8]}"
    t0 = time.monotonic()
    result: dict[str, Any]
    audio: dict[str, Any] | None = None
    started = utcnow()
    try:
        await st.sip_gateway.dial(trunk_id=row.lk_outbound_trunk_id, number=number, room_name=room_name, identity=ident,
                                  display_name="Тестовый вызов", ring_s=row.ring_timeout_s, wait=True, timeout=row.ring_timeout_s + 10)
        result = {"ok": True, "message": "Абонент ответил — связь и маршрут работают. Вызов завершён.", "ms": int((time.monotonic() - t0) * 1000)}
        try:
            heard = await st.sip_gateway.audio_from_remote(room_name, ident)
            audio = {"ok": heard, "message": "Звуковая дорожка абонента опубликована в комнате: звук от АТС к серверу идёт." if heard
                     else "Абонент ответил, но звуковой дорожки от АТС нет: проверьте RTP (диапазон портов, адрес в SDP, direct_media на АТС)."}
        except Exception as exc:  # noqa: BLE001
            audio = {"ok": None, "message": f"Наличие звука проверить не удалось: {explain_livekit_error(exc)[0]}"}
        result["audio"] = audio
    except Exception as exc:  # noqa: BLE001
        msg, code = explain_livekit_error(exc)
        result = {"ok": False, "message": msg, "sip_status": code, "ms": int((time.monotonic() - t0) * 1000)}
    finally:
        try:
            await st.sip_gateway.delete_room(room_name)
        except Exception:  # noqa: BLE001
            pass
    now = utcnow()
    db.add(SipCall(direction="test", initiator=su.display_name, initiator_user_id=su.user_id, number=mask_number(number) if pol.mask_numbers else number, profile_name=row.name,
                   result="answered" if result["ok"] else "failed", sip_status=result.get("sip_status"), reason=None if result["ok"] else result["message"][:300], tech_id=ident,
                   started_at=started, answered_at=now if result["ok"] else None, ended_at=now, duration_s=0 if not result["ok"] else int((now - started).total_seconds()),
                   end_reason="hangup" if result["ok"] else None))
    prev = dict(row.last_check or {})
    prev.update(test_call={"ok": result["ok"], "message": result["message"], "sip_status": result.get("sip_status"), "at": now.isoformat()})
    if audio is not None:
        prev.update(audio_in={**audio, "at": now.isoformat()})
    if not result["ok"]:
        prev.pop("audio_in", None)
    prev.pop("audio_confirmed", None)         # подтверждение относилось к прошлому вызову
    row.last_check, row.last_check_at = prev, now
    result["summary"] = connection_summary(row.last_check)
    await _audit(request, su, db, "sip.profile.test_call", pid, {"name": row.name, "number": mask_number(number), "ok": result["ok"], "sip_status": result.get("sip_status")})
    st.journal.emit("sip", "test_call", level="info" if result["ok"] else "warn", user=su.display_name, ip=client_ip(request),
                    message=f"Тестовый вызов {mask_number(number)}: {'ответили' if result['ok'] else result['message'][:160]}", data={"profile": row.name, "ok": result["ok"]})
    await db.commit()
    return result


@router.post("/profiles/{pid}/confirm-audio")
async def confirm_audio(pid: str, request: Request, body: dict[str, Any] = Body(...), su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Администратор подтверждает, что на тестовом вызове слышно в обе стороны (автоматически проверяется только звук от АТС к серверу)."""
    st = _st(request)
    try:
        row = await st.sip.row(db, pid)
    except SettingsError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    if not (row.last_check or {}).get("test_call", {}).get("ok"):
        raise HTTPException(status_code=409, detail="Сначала выполните успешный тестовый вызов.")
    heard = bool(body.get("heard"))
    prev = dict(row.last_check or {})
    prev["audio_confirmed"] = {"ok": heard, "message": "Звук слышен в обе стороны." if heard else "Администратор отметил, что звука нет или он односторонний.", "at": utcnow().isoformat(), "by": su.display_name}
    row.last_check = prev
    await _audit(request, su, db, "sip.profile.confirm_audio", pid, {"name": row.name, "heard": heard})
    await db.commit()
    return {"summary": connection_summary(row.last_check)}


@router.get("/profiles/{pid}/summary")
async def profile_summary(pid: str, request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    st = _st(request)
    try:
        row = await st.sip.row(db, pid)
    except SettingsError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    return {"summary": connection_summary(row.last_check), "at": row.last_check_at.isoformat() if row.last_check_at else None}


@router.get("/guide")
async def guide(request: Request, profile_id: str | None = None, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Инструкция для инженера АТС по фактическим настройкам сервера и выбранному профилю (без паролей)."""
    st = _st(request)
    profile = None
    if profile_id:
        try:
            profile = st.sip.public(await st.sip.row(db, profile_id))
        except SettingsError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None
    else:
        default = await st.sip.default(db)
        profile = st.sip.public(default) if default is not None else None
    exts = [{"extension": e, "room": n} for n, e in (await db.execute(select(Room.name, Room.sip_extension).where(Room.sip_extension.is_not(None), Room.sip_allow_inbound.is_(True)).order_by(Room.name))).all()]
    return build_guide(st.settings, profile, exts, version=st.settings.app_version)


@router.get("/calls")
async def calls(request: Request, limit: int = 100, direction: str | None = None, result: str | None = None, su: SessionUser = Depends(require_admin),
                db: AsyncSession = Depends(get_db)):
    """Журнал телефонных вызовов (новые сверху)."""
    q = select(SipCall).order_by(SipCall.started_at.desc()).limit(max(1, min(limit, 500)))
    if direction in ("in", "out", "test"):
        q = q.where(SipCall.direction == direction)
    if result in ("answered", "failed", "denied", "dialing"):
        q = q.where(SipCall.result == result)
    rows = (await db.execute(q)).scalars().all()
    return {"items": [{"id": str(c.id), "started_at": c.started_at.isoformat(), "direction": c.direction, "room": c.room_name, "initiator": c.initiator, "number": c.number,
                       "profile": c.profile_name, "result": c.result, "sip_status": c.sip_status, "reason": c.reason, "duration_s": c.duration_s, "end_reason": c.end_reason,
                       "tech_id": c.tech_id, "ended": c.ended_at is not None} for c in rows]}


@router.get("/explain/{code}")
async def explain(code: int, su: SessionUser = Depends(require_admin)):
    """Расшифровка SIP-кода для подсказок интерфейса."""
    return {"code": code, "message": explain_sip_status(code)}



