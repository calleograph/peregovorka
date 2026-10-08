"""Администрирование → Языковая модель (LLM): состояние встроенной локальной модели (Qwen3 0.6B) и её проверка.

Загрузка и повторная загрузка файла модели выполняет помощник на сервере (исправление `llm_model`, см. api/admin_updates.py): backend не имеет доступа к
Docker и записи в каталог моделей (он смонтирован только для чтения). Ответы не содержат путей на хосте, ключей и содержимого запросов.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import SessionUser, client_ip, get_db, require_admin
from ..services.local_llm import LOCAL_MODELS

router = APIRouter(prefix="/admin/llm", tags=["admin-llm"])


@router.get("/local")
async def local_status(request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Состояние локальной модели: файл (наличие, размер, SHA-256), runtime (llama.cpp во внутренней сети), выбранный режим, предупреждения о качестве."""
    cfg = await request.app.state.settings_svc.get(db, "llm")
    st = await request.app.state.local_llm.status(cfg)         # type: ignore[arg-type]
    st["catalog"] = [{"id": k, "title": v.title, "runtime": v.runtime, "light": v.light, "source": v.source} for k, v in LOCAL_MODELS.items()]
    return st


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

