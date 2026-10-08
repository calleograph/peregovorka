"""Администрирование → Интеграции → SIP-телефония: профили (транки), проверка настроек, тестовый вызов, состояние службы.

Пароль профиля хранится зашифрованно и обратно не отдаётся. В журнал и аудит попадают создание, изменение, удаление, проверки и вызовы — без паролей
и с частично скрытыми номерами. Включение службы на сервере (порты, контейнер livekit-sip) выполняет помощник обновлений, а не backend.
"""
from __future__ import annotations

import asyncio
import re
import time
import uuid
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import SessionUser, client_ip, get_db, require_admin
from ..models import Room, SipProfile, utcnow
from ..services.audit import write_audit
from ..services.settings import SettingsError
from ..services.sip import (CODEC_RATES, SipError, explain_livekit_error, explain_sip_status, mask_number, normalize_number, service_status, sip_options_probe)

router = APIRouter(prefix="/admin/sip", tags=["admin-sip"])


def _st(request: Request):
    return request.app.state


async def _sync(request: Request, db: AsyncSession, row: SipProfile) -> dict:
    """Приводит транки LiveKit в соответствие с профилем. Ошибка не мешает сохранению профиля — она возвращается для показа администратору."""
    st = _st(request)
    if str(st.settings.sip_enabled).strip().lower() not in ("yes", "true", "1", "on"):
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
    probe = await asyncio.to_thread(sip_options_probe, row.host, row.port, row.transport)
    add("pbx", probe["ok"], probe["message"], probe["ms"])
    started = time.monotonic()
    enabled = str(st.settings.sip_enabled).strip().lower() in ("yes", "true", "1", "on")
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
    ok = not any(s["ok"] is False for s in stages)
    result = {"ok": ok, "stages": stages}
    row.last_check, row.last_check_at = {"ok": ok, "stages": [{"stage": s["stage"], "ok": s["ok"], "message": s["message"]} for s in stages]}, utcnow()
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
    room_name = f"sip-test-{uuid.uuid4().hex[:10]}"
    t0 = time.monotonic()
    result: dict[str, Any]
    try:
        await st.sip_gateway.dial(trunk_id=row.lk_outbound_trunk_id, number=number, room_name=room_name, identity=f"sip-test-{uuid.uuid4().hex[:8]}",
                                  display_name="Тестовый вызов", ring_s=row.ring_timeout_s, wait=True, timeout=row.ring_timeout_s + 10)
        result = {"ok": True, "message": "Абонент ответил — связь и маршрут работают. Вызов завершён.", "ms": int((time.monotonic() - t0) * 1000)}
    except Exception as exc:  # noqa: BLE001
        msg, code = explain_livekit_error(exc)
        result = {"ok": False, "message": msg, "sip_status": code, "ms": int((time.monotonic() - t0) * 1000)}
    finally:
        try:
            await st.sip_gateway.delete_room(room_name)
        except Exception:  # noqa: BLE001
            pass
    row.last_check, row.last_check_at = {"ok": result["ok"], "test_call": True, "message": result["message"], "sip_status": result.get("sip_status")}, utcnow()
    await _audit(request, su, db, "sip.profile.test_call", pid, {"name": row.name, "number": mask_number(number), "ok": result["ok"], "sip_status": result.get("sip_status")})
    st.journal.emit("sip", "test_call", level="info" if result["ok"] else "warn", user=su.display_name, ip=client_ip(request),
                    message=f"Тестовый вызов {mask_number(number)}: {'ответили' if result['ok'] else result['message'][:160]}", data={"profile": row.name, "ok": result["ok"]})
    await db.commit()
    return result


@router.get("/explain/{code}")
async def explain(code: int, su: SessionUser = Depends(require_admin)):
    """Расшифровка SIP-кода для подсказок интерфейса."""
    return {"code": code, "message": explain_sip_status(code)}



