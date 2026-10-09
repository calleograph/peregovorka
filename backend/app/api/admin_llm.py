"""Администрирование → Языковая модель (LLM): состояние встроенной локальной модели (Qwen3 1.7B), эффективные модели по задачам и статистика и её проверка.

Загрузка и повторная загрузка файла модели выполняет помощник на сервере (исправление `llm_model`, см. api/admin_updates.py): backend не имеет доступа к
Docker и записи в каталог моделей (он смонтирован только для чтения). Ответы не содержат путей на хосте, ключей и содержимого запросов.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import SessionUser, client_ip, get_db, require_admin
from ..models import Room
from ..services import llm_stats
from ..services.llm_choice import system_llm
from ..services.local_llm import VISIBLE_MODELS

router = APIRouter(prefix="/admin/llm", tags=["admin-llm"])


@router.get("/local")
async def local_status(request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Состояние локальной модели: файл (наличие, размер, SHA-256), runtime (llama.cpp во внутренней сети), выбранный режим, предупреждения о качестве."""
    cfg = await request.app.state.settings_svc.get(db, "llm")
    st = await request.app.state.local_llm.status(cfg)         # type: ignore[arg-type]
    st["catalog"] = [{"id": k, "title": v.title, "runtime": v.runtime, "light": v.light, "source": v.source} for k, v in VISIBLE_MODELS.items()]
    return st


@router.get("/effective")
async def effective_models(request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Какая модель будет вызвана для каждой задачи (протокол, резюме) по умолчанию, и сколько комнат выбрали свою. Сначала действует
    разовый выбор при формировании, затем настройка встречи, комнаты и, если их нет, эта системная модель."""
    st = request.app.state
    local = st.local_llm
    out: dict = {}
    for purpose in ("protocol", "summary"):
        res = await system_llm(st.protocols.profiles, db, purpose)
        eff, is_local = local.effective(res.settings)       # type: ignore[arg-type]
        item = {"enabled": bool(eff.enabled), "local": is_local, "name": res.name, "model": eff.model if eff.enabled else None,
                "api_type": ("local" if is_local else eff.type) if eff.enabled else None,
                "max_output_tokens": eff.output_limit(purpose)[0] if eff.enabled else None, "max_output_note": eff.output_limit(purpose)[1] if eff.enabled else "",
                "ready": bool(eff.enabled), "problem": None}
        if eff.enabled and is_local:
            m = local.model(res.settings.local_model)       # type: ignore[attr-defined]
            fs = await asyncio.to_thread(local.file_state, m)
            item["name"], item["ready"] = m.title, fs["state"] == "ok" and local.model_enabled(m)
            if not item["ready"]:
                item["problem"] = "файл модели не загружен или повреждён" if fs["state"] != "ok" else "локальная модель не включена на сервере"
        elif eff.enabled and not eff.model:
            item["ready"], item["problem"] = False, "не указана модель"
        out[purpose] = item
    col = {"protocol": Room.llm_mode, "summary": Room.llm_summary_mode}
    out["rooms_with_own_model"] = {p: (await db.execute(select(func.count()).select_from(Room).where(c != "inherit"))).scalar_one() for p, c in col.items()}
    return out


@router.get("/stats")
async def model_stats(request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Показатели по моделям (локальным и внешним) за последние документы: количество, ошибки, оборванные ответы, время, длина входа и выхода, скорость."""
    runs = await llm_stats.recent_runs(db, 1500)
    return {"documents": len(runs), "models": llm_stats.model_stats(runs)}


@router.post("/local/test")
async def local_test(request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Короткий тестовый запрос к локальной модели (независимо от выбранного режима): отвечает ли она на самом деле."""
    cfg = await request.app.state.settings_svc.get(db, "llm")
    local = request.app.state.local_llm
    forced = cfg.model_copy(update={"provider": "local", "enabled": True})   # type: ignore[attr-defined]
    fs = await asyncio.to_thread(local.file_state, local.model(cfg.local_model))   # type: ignore[attr-defined]
    if fs["state"] != "ok":
        return {"ok": False, "message": "Модель не загружена или повреждена — сначала скачайте её.", "ms": 0}
    ok, msg, ms = await local.client(forced).test()          # type: ignore[arg-type]
    request.app.state.journal.emit("llm", "local_llm_test", level="info" if ok else "warn", user=su.display_name, ip=client_ip(request),
                                   message=f"локальная LLM: {'OK' if ok else msg}", data={"ok": ok, "ms": ms})
    return {"ok": ok, "message": msg, "ms": ms}

