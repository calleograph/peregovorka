"""Администрирование → Языковая модель (LLM): состояние встроенной локальной модели (Qwen3 1.7B), эффективные модели по задачам и статистика и её проверка.

Загрузка и повторная загрузка файла модели выполняет помощник на сервере (исправление `llm_model`, см. api/admin_updates.py): backend не имеет доступа к
Docker и записи в каталог моделей (он смонтирован только для чтения). Ответы не содержат путей на хосте, ключей и содержимого запросов.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import SessionUser, client_ip, get_db, require_admin
from ..models import Room
from ..services import llm_stats
from ..services.llm_choice import apply_task_limit, system_llm, task_mode
from ..services.settings import SettingsError
from ..services.local_llm import VISIBLE_MODELS

router = APIRouter(prefix="/admin/llm", tags=["admin-llm"])


@router.get("/local")
async def local_status(request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Состояние локальной модели: файл (наличие, размер, SHA-256), runtime (llama.cpp во внутренней сети), выбранный режим, предупреждения о качестве."""
    cfg = await request.app.state.settings_svc.get(db, "llm")
    st = await request.app.state.local_llm.status(cfg)         # type: ignore[arg-type]
    st["catalog"] = [{"id": k, "title": v.title, "runtime": v.runtime, "light": v.light, "source": v.source} for k, v in VISIBLE_MODELS.items()]
    return st


TASKS = (("protocol", "Протокол"), ("summary", "Резюме"), ("map", "Карта разговора"))


@router.get("/effective")
async def effective_models(request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Какая модель будет вызвана для каждой задачи по умолчанию (системное назначение), её тип, готовность и фактический предел ответа. Порядок выбора при формировании:
    разовый выбор → встреча → переговорка → это системное значение; здесь же — сколько переговорок выбрали свою модель."""
    st = request.app.state
    local = st.local_llm
    out: dict = {}
    for purpose, label in TASKS:
        res0 = await system_llm(st.protocols.profiles, db, purpose)
        res = await apply_task_limit(st.protocols.profiles, db, _wrap(res0), purpose)           # потолок задачи учитывается в «фактическом пределе»
        eff, is_local = local.effective(res.settings)       # type: ignore[arg-type]
        lim, note = eff.output_limit(purpose) if eff.enabled else (None, "")
        item = {"label": label, "enabled": bool(eff.enabled), "local": is_local, "name": res0.name, "model": eff.model if eff.enabled else None,
                "api_type": ("local" if is_local else eff.type) if eff.enabled else None, "profile_id": res0.profile_id,
                "max_output_tokens": lim, "max_output_note": note or ("контекст модели не указан" if eff.enabled and not eff.context_window else ""),
                "context_window": eff.context_window or None, "ready": bool(eff.enabled), "problem": None}
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
    out["rooms_with_own_model"]["map"] = 0
    return out


def _wrap(res):
    """Resolved → LlmChoice для применения потолка задачи (нужны только settings/available)."""
    from ..services.llm_choice import LlmChoice  # noqa: PLC0415

    return LlmChoice(res.settings, res.name, "system", bool(res.settings.enabled))


@router.get("/choices")
async def choices(request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Из чего выбирать назначение: установленные локальные модели и внешние подключения (без ключей), плюс текущие назначения задач. Подключение «из прежних общих настроек»
    показывается обычным подключением, только если оно действительно заполнено (на чистой установке внешних API нет)."""
    st = request.app.state
    cfg = await st.settings_svc.get(db, "llm")
    locals_ = []
    for mid, m in VISIBLE_MODELS.items():
        fs = await asyncio.to_thread(st.local_llm.file_state, st.local_llm.model(mid))
        locals_.append({"id": mid, "title": m.title, "installed": fs["state"] == "ok" and st.local_llm.model_enabled(st.local_llm.model(mid))})
    ext = []
    for p in await st.protocols.profiles.list(db, "llm"):
        c = p["config"]
        if p.get("virtual") and not (c.get("model") and (p.get("secret_set") or c.get("base_url"))):
            continue
        ext.append({"id": p["id"], "name": p["name"], "model": c.get("model") or "", "type": c.get("type"), "secret_set": bool(p.get("secret_set")), "virtual": bool(p.get("virtual")),
                    "host": _host(c.get("base_url"))})
    tasks = {}
    for purpose, _label in TASKS:
        mode, pid = task_mode(cfg, purpose)      # type: ignore[arg-type]
        own = purpose != "protocol" and getattr(cfg, f"{purpose}_provider", "same") != "same"
        tasks[purpose] = {"mode": mode, "profile_id": pid, "same_as_protocol": purpose != "protocol" and not own}
    return {"local": locals_, "external": ext, "tasks": tasks, "on_missing": cfg.on_missing,      # type: ignore[attr-defined]
            "limits": {k: getattr(cfg, f"limit_{k}") for k, _ in TASKS}}


def _host(url: str | None) -> str:
    from urllib.parse import urlparse  # noqa: PLC0415

    try:
        return urlparse(url or "").netloc
    except ValueError:
        return ""


@router.get("/stats")
async def model_stats(request: Request, days: int = Query(0, ge=0, le=3650), kind: str = Query("", pattern="^(|protocol|summary|map)$"), where: str = Query("", pattern="^(|local|external)$"),
                      archived: bool = Query(False), su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Показатели моделей по созданным документам и картам. Фильтры: период (дней), задача, локальная/внешняя; снятые с вооружения модели по умолчанию скрыты (archived=true — показать)."""
    from datetime import timedelta  # noqa: PLC0415

    from ..models import utcnow  # noqa: PLC0415

    runs = await llm_stats.recent_runs(db, 1500)
    if days:
        cut = utcnow() - timedelta(days=days)
        runs = [r for r in runs if r["at"] and (r["at"] if r["at"].tzinfo else r["at"].replace(tzinfo=cut.tzinfo)) >= cut]
    if kind:
        runs = [r for r in runs if r["kind"] == kind]
    if where:
        runs = [r for r in runs if bool((r["meta"] or {}).get("llm_local")) == (where == "local")]
    rows = llm_stats.model_stats(runs)
    hidden = [m for m in rows if m["archived"]]
    return {"documents": len(runs), "models": rows if archived else [m for m in rows if not m["archived"]], "archived_hidden": 0 if archived else len(hidden)}


@router.post("/diagnose")
async def diagnose(request: Request, body: dict = Body(...), su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Диагностика: «task:protocol|summary|map», «profile:<id>» или «local». Возвращает действующую конфигурацию (без ключей), короткий ответ и проверку строгого JSON."""
    from ..integrations.llm import LlmError  # noqa: PLC0415

    st = request.app.state
    target = str(body.get("target") or "")
    local = st.local_llm
    purpose = "protocol"
    try:
        if target.startswith("task:") and target[5:] in dict(TASKS):
            purpose = target[5:]
            cfg = (await system_llm(st.protocols.profiles, db, purpose)).settings
        elif target.startswith("profile:"):
            cfg = (await st.protocols.profiles.get_settings(db, "llm", target[8:])).settings
        elif target == "local":
            base = await st.settings_svc.get(db, "llm")
            cfg = base.model_copy(update={"provider": "local", "enabled": True})        # type: ignore[attr-defined]
        else:
            raise HTTPException(status_code=422, detail="target: task:<задача> | profile:<id> | local")
    except SettingsError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    eff, is_local = local.effective(cfg)         # type: ignore[arg-type]
    info = {"provider": "local" if is_local else ("off" if not eff.enabled else "external"), "type": "local" if is_local else eff.type, "model": eff.model, "host": _host(eff.base_url) if not is_local else "внутренний контейнер",
            "max_output_tokens": eff.output_limit(purpose)[0], "context_window": eff.context_window or None, "temperature": eff.temperature if (eff.send_temperature or is_local) else "не передаётся",
            "capabilities": {"system": eff.supports_system or is_local, "json": eff.supports_json or is_local, "temperature": eff.send_temperature or is_local}, "timeout_s": eff.timeout}
    if not eff.enabled:
        return {"ok": False, "config": info, "tests": [{"name": "Подключение", "ok": False, "message": "Модель для этой задачи отключена или не настроена.", "ms": 0}]}
    client = local.client(cfg, ca_file=st.protocols._ca(), transport=st.protocols._transports.get("llm"), purpose=purpose)          # type: ignore[arg-type]  # noqa: SLF001
    tests = []
    ok, msg, ms = await client.test()
    tests.append({"name": "Короткий ответ", "ok": ok, "message": msg, "ms": ms})
    if ok:
        import json as _json  # noqa: PLC0415
        import time as _time  # noqa: PLC0415

        t0 = _time.monotonic()
        try:
            r = await client.complete("Отвечай только JSON по схеме.", "Верни {\"ok\": true}", max_tokens=40, json_schema={"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]})
            parsed = _json.loads(r.text)
            tests.append({"name": "Строгий JSON (structured output)", "ok": bool(parsed.get("ok", True)), "message": "ответ разобран как JSON по схеме", "ms": int((_time.monotonic() - t0) * 1000)})
        except (LlmError, ValueError) as exc:
            tests.append({"name": "Строгий JSON (structured output)", "ok": False, "ms": int((_time.monotonic() - t0) * 1000),
                          "message": exc.describe() if isinstance(exc, LlmError) else "ответ не разобрался как JSON — для структурного режима нужна модель с response_format"})
    return {"ok": all(t["ok"] for t in tests), "config": info, "tests": tests}


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

