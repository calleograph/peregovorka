"""Админка: выбор модели распознавания (ASR), её статус, тест и сравнение на встроенном аудио.

Каталог моделей и их файлы знает сам ASR (он же владеет томом моделей); backend хранит ВЫБОР в настройках и проксирует управление.
"""
from __future__ import annotations

from typing import Any

import httpx
from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import SessionUser, client_ip, get_db, require_admin
from ..services.audit import write_audit
from ..workers.asr_sync import publish_desired

router = APIRouter(prefix="/admin/asr", tags=["admin"])
UNREACHABLE = "Сервис распознавания недоступен (контейнер asr не отвечает). Проверьте scripts/status.sh и scripts/logs.sh asr."


def _client(request: Request, timeout: float) -> httpx.AsyncClient:
    tr = (getattr(request.app.state, "test_transports", {}) or {}).get("asr")
    return httpx.AsyncClient(base_url=request.app.state.settings.asr_internal_url, timeout=timeout, transport=tr)


def _headers(request: Request) -> dict[str, str]:
    return {"X-Internal-Token": request.app.state.settings.internal_api_token}


async def _call(request: Request, method: str, path: str, *, timeout: float = 10.0, json: dict | None = None) -> tuple[int, Any]:
    try:
        async with _client(request, timeout) as c:
            r = await c.request(method, path, json=json, headers=_headers(request))
        try:
            return r.status_code, r.json()
        except ValueError:
            return r.status_code, {"error": f"HTTP {r.status_code}"}
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail=UNREACHABLE) from None


@router.get("/models")
async def models(request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Выбранная модель + фактическое состояние из ASR (активная, статус каждой, runtime, размер, устройство)."""
    desired = (await request.app.state.settings_svc.get(db, "asr")).active_model
    try:
        code, data = await _call(request, "GET", "/models")
        reachable = code == 200
    except HTTPException:
        reachable, data = False, {}
    if not reachable:  # запасной источник: последний heartbeat (модели без деталей)
        hb = await request.app.state.bridge.heartbeat()
        return {"reachable": False, "desired": desired, "error": UNREACHABLE, "models": (hb or {}).get("models", []), "active_id": (hb or {}).get("active_model")}
    hb = await request.app.state.bridge.heartbeat()
    live = {k: (hb or {}).get(k) for k in ("avg_infer_ms", "avg_queue_ms", "rtf", "processed", "dropped", "queue_depth")}
    return {"reachable": True, "desired": desired, "live": live, **data}


@router.put("/active")
async def set_active(request: Request, body: dict[str, Any] = Body(...), su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Выбрать модель: проверяем наличие файлов, сохраняем выбор и сообщаем ASR (он перезагрузит модель сам, без простоя)."""
    model_id = str(body.get("model_id", "")).strip().lower()
    svc = request.app.state.settings_svc
    try:
        await svc.preview(db, "asr", {"active_model": model_id})
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=str(exc)) from None
    note = ""
    try:
        code, data = await _call(request, "GET", "/models")
        if code == 200:
            spec = next((m for m in data.get("models", []) if m["id"] == model_id), None)
            if spec is None:
                raise HTTPException(status_code=422, detail="Такой модели нет в каталоге ASR.")
            if not spec["present"]:
                raise HTTPException(status_code=409, detail=f"Модель «{spec['title']}» не установлена: нет файлов {', '.join(spec['missing'])}. Положите их в каталог моделей (scripts/models.sh) и повторите.")
            if not spec["runtime_available"]:
                raise HTTPException(status_code=409, detail=f"Runtime «{spec['runtime']}» не поддерживается этой версией ASR.")
    except HTTPException as exc:
        if exc.status_code == 502:
            note = "ASR сейчас недоступен — выбор сохранён и будет применён при его запуске."
        else:
            raise
    previous = (await svc.get(db, "asr")).active_model
    await svc.update(db, "asr", {"active_model": model_id}, actor=su.display_name)
    await publish_desired(db, svc, request.app.state.redis)
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="asr.model_change", target_type="settings", target_id="asr",
                      ip=client_ip(request), details={"from": previous or "(по умолчанию)", "to": model_id})
    await db.commit()
    return {"ok": True, "desired": model_id, "note": note or "ASR перезагрузит модель в течение нескольких секунд; прежняя работает до окончания загрузки."}


@router.post("/test")
async def test_model(request: Request, body: dict[str, Any] = Body(default_factory=dict), su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """«Протестировать модель»: встроенный тестовый WAV → скорость, CPU, RAM, RTF, качество текста, пунктуация."""
    code, data = await _call(request, "POST", "/models/test", timeout=240.0,
                             json={"model_id": body.get("model_id"), "repeat": min(int(body.get("repeat", 3)), 10), "force": bool(body.get("force"))})
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="asr.model_test", target_type="settings", target_id="asr",
                      ip=client_ip(request), details={"model": body.get("model_id") or "(активная)"})
    await db.commit()
    return data


@router.post("/compare")
async def compare_models(request: Request, body: dict[str, Any] = Body(default_factory=dict), su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Сравнить все установленные модели на одном и том же аудио."""
    code, data = await _call(request, "POST", "/models/compare", timeout=420.0,
                             json={"model_ids": body.get("model_ids"), "repeat": min(int(body.get("repeat", 3)), 10), "force": bool(body.get("force"))})
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="asr.model_compare", target_type="settings", target_id="asr",
                      ip=client_ip(request), details={"ok": bool(data.get("ok"))})
    await db.commit()
    return data
