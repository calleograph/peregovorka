"""Админка: обновление проекта и версии компонентов. Только для администратора; запуск обновления — в аудит и журнал.

Сам backend ничего не обновляет: он передаёт запрос исполнителю на хосте (scripts/updater.sh), см. services/updates.py.
"""
from __future__ import annotations

import json
import time
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import SessionUser, client_ip, get_db, require_admin
from ..models import Meeting
from ..services import updates as upd
from ..services.audit import write_audit

router = APIRouter(prefix="/admin/updates", tags=["admin-updates"])
CACHE_KEY, CACHE_TTL = "updates:components", 6 * 3600


def channel(request: Request) -> upd.Channel:
    return upd.Channel(f"{request.app.state.settings.data_dir}/updater")


@router.get("")
async def overview(request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Установленная версия, состояние исполнителя обновлений, что нового в репозитории, идущие встречи."""
    s = request.app.state.settings
    ch = channel(request)
    st = ch.status()
    remote = ch.remote()
    active = (await db.execute(select(func.count()).select_from(Meeting).where(Meeting.ended_at.is_(None)))).scalar_one()
    running = st.get("state") == "updating"
    reasons = []
    if not st["available"]:
        reasons.append("На сервере не запущен исполнитель обновлений (scripts/updater.sh). Запустите его один раз — команды ниже.")
    if running:
        reasons.append("Обновление уже выполняется.")
    if st["request_pending"]:
        reasons.append("Запрос уже передан исполнителю и ожидает выполнения.")
    return {
        "installed": {"version": s.app_version, "commit": s.app_git_commit, "built_at": s.app_built_at},
        "updater": {k: st.get(k) for k in ("available", "heartbeat_age_s", "state", "request_id", "step_no", "step_total", "step_name", "started_at",
                                           "finished_at", "exit_code", "result", "request_pending", "project", "by")},
        "remote": remote, "active_meetings": active,
        "can_update": not reasons, "reasons": reasons,
        "up_to_date": bool(remote and remote.get("ok") and int(remote.get("behind", 0)) == 0),
        "commands": {"install": "./scripts/updater.sh install", "foreground": "./scripts/updater.sh run", "manual": "./scripts/update.sh"},
    }


@router.post("/check")
async def check_now(request: Request, su: SessionUser = Depends(require_admin)):
    """Попросить исполнителя заново свериться с репозиторием (git fetch)."""
    ch = channel(request)
    st = ch.status()
    if not st["available"]:
        raise HTTPException(status_code=409, detail="Исполнитель обновлений на сервере не запущен")
    if st.get("state") == "updating":
        raise HTTPException(status_code=409, detail="Идёт обновление — проверка выполнится после него")
    try:
        rid = ch.request("check", by=su.sam_account_name)
    except OSError as exc:
        raise HTTPException(status_code=503, detail=f"Не удалось передать запрос исполнителю ({exc.strerror or exc})") from None
    return {"request_id": rid}


@router.post("/run")
async def run_update(request: Request, body: dict[str, Any] = Body(default_factory=dict), su: SessionUser = Depends(require_admin),
                     db: AsyncSession = Depends(get_db)):
    """Запустить обновление проекта (scripts/update.sh). Требуется явное подтверждение: confirm=true."""
    if body.get("confirm") is not True:
        raise HTTPException(status_code=422, detail="Нужно подтверждение обновления (confirm=true)")
    force_build, pull = bool(body.get("force_build")), bool(body.get("pull"))
    ch = channel(request)
    st = ch.status()
    if not st["available"]:
        raise HTTPException(status_code=409, detail="Исполнитель обновлений на сервере не запущен — обновите командой ./scripts/update.sh на сервере")
    if st.get("state") == "updating" or st["request_pending"]:
        raise HTTPException(status_code=409, detail="Обновление уже выполняется или ожидает запуска")
    try:
        rid = ch.request("update", by=su.sam_account_name, force_build=force_build, pull=pull)
    except OSError as exc:
        raise HTTPException(status_code=503, detail=f"Не удалось передать запрос исполнителю ({exc.strerror or exc})") from None
    active = (await db.execute(select(func.count()).select_from(Meeting).where(Meeting.ended_at.is_(None)))).scalar_one()
    request.app.state.journal.emit("system", "update_requested", level="warn", user=su.sam_account_name, ip=client_ip(request),
                                   message=f"запрошено обновление (пересборка={force_build}, базовые образы={pull}); идущих встреч: {active}",
                                   data={"force_build": force_build, "pull": pull, "active_meetings": active, "request_id": rid})
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="system.update", target_type="system", target_id="update",
                      ip=client_ip(request), details={"force_build": force_build, "pull": pull, "active_meetings": active, "request_id": rid})
    await db.commit()
    return {"request_id": rid}


@router.get("/log")
async def update_log(request: Request, offset: int = Query(0, ge=-1), su: SessionUser = Depends(require_admin)):
    """Построчный вывод обновления начиная с байта offset (окно обновления опрашивает его раз в 1–2 с, в том числе во время перезапуска backend)."""
    ch = channel(request)
    st = ch.status()
    out = ch.read_log(offset)
    out["state"] = st.get("state")
    out["step_no"], out["step_total"], out["step_name"] = st.get("step_no"), st.get("step_total"), st.get("step_name")
    out["exit_code"], out["result"], out["finished_at"] = st.get("exit_code"), st.get("result"), st.get("finished_at")
    out["available"] = st["available"]
    return out


@router.get("/components")
async def components(request: Request, refresh: bool = False, su: SessionUser = Depends(require_admin)):
    """Версии компонентов: установлено на сервере · проверено с проектом · актуально в интернете. Внешние источники опрашиваются раз в 6 часов."""
    r = request.app.state.redis
    cached = None if refresh else await r.get(CACHE_KEY)
    latest = None
    fetched_at = None
    if cached:
        try:
            obj = json.loads(cached)
            latest, fetched_at = obj["latest"], obj["at"]
        except (ValueError, KeyError):
            latest = None
    if latest is None:
        transports = getattr(request.app.state, "test_transports", {}) or {}
        latest = await upd.fetch_latest(transports.get("updates"))
        fetched_at = time.time()
        if any(v.get("latest") for v in latest.values()):  # недоступный интернет не кэшируем надолго — повторим при следующем открытии
            await r.set(CACHE_KEY, json.dumps({"latest": latest, "at": fetched_at}), ex=CACHE_TTL)
    installed = await upd.installed_versions(request.app)
    rows = upd.build_rows(installed, latest)
    internet = any(v.get("latest") for v in latest.values())
    return {"rows": rows, "internet": internet, "fetched_at": fetched_at, "tested": upd.TESTED, "project_latest": (latest.get("project") or {}).get("latest")}
