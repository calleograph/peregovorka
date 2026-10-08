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


def helper_info(st: dict) -> dict:
    """Помощник обновлений на сервере: запущен ли и достаточно ли у него прав (служба от root). Прежние версии запускали его от обычного пользователя."""
    available = bool(st.get("available"))
    uid = st.get("uid")
    privileged = available and uid == 0
    return {"available": available, "privileged": privileged, "uid": uid,
            "problem": None if privileged else ("not_installed" if not available else "no_privileges")}


@router.get("")
async def overview(request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Установленная версия, состояние исполнителя обновлений, что нового в репозитории, идущие встречи."""
    s = request.app.state.settings
    ch = channel(request)
    st = ch.status()
    remote = ch.remote()
    history = ch.history()
    last_ok = next((h for h in history if h["result"] == "ok"), None)
    # Результат последнего запуска из веб-интерфейса (status.json) больше не «текущий», если после него было успешное обновление (например, из терминала)
    fin = st.get("finished_at") or 0
    st["stale"] = bool(fin and st.get("state") not in upd.BUSY_STATES and last_ok and (last_ok.get("at") or 0) >= fin and st.get("result") != "ok")
    active = (await db.execute(select(func.count()).select_from(Meeting).where(Meeting.ended_at.is_(None)))).scalar_one()
    running = st.get("state") in upd.BUSY_STATES
    reasons = []
    if not st["available"]:
        reasons.append("На сервере не запущен помощник обновлений. Его нужно установить один раз — команда ниже.")
    if running:
        reasons.append("Сейчас уже выполняется обновление или исправление.")
    if st["request_pending"]:
        reasons.append("Запрос уже передан исполнителю и ожидает выполнения.")
    return {
        "installed": {"version": s.app_version, "commit": s.app_git_commit, "built_at": s.app_built_at},
        "updater": {k: st.get(k) for k in ("available", "heartbeat_age_s", "state", "action", "repair_id", "request_id", "step_no", "step_total", "step_name",
                                           "started_at", "finished_at", "exit_code", "result", "request_pending", "project", "by", "stale")},
        "outcome": upd.outcome_summary(st), "helper": helper_info(st),
        "remote": remote, "active_meetings": active, "history": history, "last_success": last_ok,
        "can_update": not reasons, "reasons": reasons,
        "up_to_date": bool(remote and remote.get("ok") and int(remote.get("behind", 0)) == 0),
        "commands": {"install": "sudo ./scripts/updater.sh install --yes", "foreground": "sudo ./scripts/updater.sh run", "manual": "sudo ./scripts/update.sh"},
    }


@router.post("/check")
async def check_now(request: Request, su: SessionUser = Depends(require_admin)):
    """Попросить исполнителя заново свериться с репозиторием (git fetch)."""
    ch = channel(request)
    st = ch.status()
    if not st["available"]:
        raise HTTPException(status_code=409, detail="Исполнитель обновлений на сервере не запущен")
    if st.get("state") in upd.BUSY_STATES:
        raise HTTPException(status_code=409, detail="Идёт обновление или исправление — проверка выполнится после него")
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
    if st.get("state") in upd.BUSY_STATES or st["request_pending"]:
        raise HTTPException(status_code=409, detail="Обновление или исправление уже выполняется либо ожидает запуска")
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
    out["action"], out["repair_id"], out["outcome"] = st.get("action"), st.get("repair_id"), upd.outcome_summary(st)
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


# ===================================================================== «Исправить автоматически»
def _items(request: Request, st: dict, rep: dict | None) -> list[dict]:
    """Список проблем для интерфейса: найденные помощником (исправляются одной кнопкой) + найденные самим приложением."""
    items: list[dict] = []
    helper = helper_info(st)
    can_fix = helper["privileged"]
    for it in (rep or {}).get("items", []):
        if it.get("id") in upd.REPAIR_IDS:
            items.append({**{k: it.get(k) for k in ("id", "title", "meaning", "fix")}, "kind": "helper", "fixable": can_fix})
    if helper["problem"]:
        items.insert(0, {"id": "updater_helper", "kind": "manual", "fixable": False,
                         "title": "Помощник обновлений не установлен" if helper["problem"] == "not_installed" else "У помощника обновлений недостаточно прав",
                         "meaning": ("Без него кнопки «Обновить» и «Исправить автоматически» в браузере не могут изменить права на каталоги, настройки веб-сервера и параметры системы. "
                                     "Это исправляется один раз на самом сервере — дальше всё делается из браузера."),
                         "fix": "Один раз выполнить на сервере от администратора системы:", "command": "sudo ./scripts/updater.sh install --yes"})
    boot = getattr(request.app.state, "boot_errors", {}) or {}
    if boot.get("ca") and not any(i["id"] == "data_dirs" for i in items):
        items.append({"id": "data_dirs", "kind": "helper", "fixable": can_fix, "title": "Не удалось записать сертификаты (CA)",
                      "meaning": f"Сервис не смог собрать набор сертификатов: {boot['ca']}. Вход по домену продолжает работать со старым набором, но новые сертификаты не сохранятся.",
                      "fix": "Создать недостающие каталоги и выдать сервису нужного владельца"})
    return items


@router.get("/repairs")
async def repairs(request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Известные проблемы установки и что с ними делать: Проблема → Что это значит → Исправить автоматически. Команд Linux пользователь не видит
    (единственное исключение — единоразовая установка самого помощника, без которого исправлять из браузера нечем)."""
    ch = channel(request)
    st = ch.status()
    rep = ch.repairs()
    items = _items(request, st, rep)
    legacy = await request.app.state.legacy_ldap.status(db, request.app.state.directory)
    if legacy["needs_import"]:
        items.append({"id": "ldap_legacy", "kind": "backend", "fixable": True, "title": "Обнаружена старая конфигурация LDAP",
                      "meaning": "Вход по домену работает по настройке из файла .env предыдущей версии. Её лучше перенести в управляемые настройки, чтобы менять из браузера.",
                      "fix": "Перенести настройки (с проверкой подключения; при неудаче ничего не меняется)"})
    return {"items": items, "checked_at": (rep or {}).get("checked_at"), "age_s": (rep or {}).get("age_s"), "helper": helper_info(st),
            "busy": st.get("state") in upd.BUSY_STATES or st["request_pending"],
            "current": ({"repair_id": st.get("repair_id"), "state": st.get("state"), "result": st.get("result"), "finished_at": st.get("finished_at")}
                        if st.get("action") == "repair" else None)}


@router.post("/repairs/scan")
async def repairs_scan(request: Request, su: SessionUser = Depends(require_admin)):
    """Заново проверить систему (повторная проверка после исправления выполняется помощником автоматически)."""
    ch = channel(request)
    st = ch.status()
    if not st["available"]:
        raise HTTPException(status_code=409, detail="Помощник обновлений не запущен")
    if st.get("state") in upd.BUSY_STATES or st["request_pending"]:
        raise HTTPException(status_code=409, detail="Сейчас выполняется другое действие")
    return {"request_id": ch.request("scan", by=su.sam_account_name)}


@router.post("/repairs/{repair_id}/fix")
async def repairs_fix(repair_id: str, request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Выполнить одно исправление. ID проверяется по белому списку и дополнительно по результату последней проверки: «подставить» произвольное нельзя."""
    if repair_id == "ldap_legacy":
        return await _fix_ldap_legacy(request, su, db)
    if repair_id not in upd.REPAIR_IDS:
        raise HTTPException(status_code=404, detail="Неизвестное исправление")
    ch = channel(request)
    st = ch.status()
    helper = helper_info(st)
    if not helper["available"]:
        raise HTTPException(status_code=409, detail="Помощник обновлений не запущен — установите его один раз (см. «Помощник обновлений»)")
    if not helper["privileged"]:
        raise HTTPException(status_code=409, detail="У помощника недостаточно прав — переустановите его от администратора системы (см. «Помощник обновлений»)")
    if st.get("state") in upd.BUSY_STATES or st["request_pending"]:
        raise HTTPException(status_code=409, detail="Сейчас уже выполняется обновление или исправление")
    rep = ch.repairs() or {}
    boot_ca = bool((getattr(request.app.state, "boot_errors", {}) or {}).get("ca")) and repair_id == "data_dirs"
    # «Скачать модель» локальной LLM доступна всегда (идемпотентно: валидный файл не скачивается), остальные исправления — только найденные последней проверкой
    if repair_id not in {i.get("id") for i in rep.get("items", [])} and not boot_ca and repair_id != "llm_model":
        raise HTTPException(status_code=409, detail="Эта проблема сейчас не обнаружена — обновите проверку")
    try:
        rid = ch.request("repair", by=su.sam_account_name, repair=repair_id)
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=503, detail=f"Не удалось передать запрос помощнику ({getattr(exc, 'strerror', None) or exc})") from None
    request.app.state.journal.emit("system", "repair_requested", level="warn", user=su.sam_account_name, ip=client_ip(request),
                                   message=f"запрошено автоматическое исправление «{repair_id}»", data={"repair": repair_id, "request_id": rid})
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="system.repair", target_type="system", target_id=repair_id,
                      ip=client_ip(request), details={"repair": repair_id, "request_id": rid})
    await db.commit()
    return {"request_id": rid}


async def _fix_ldap_legacy(request: Request, su: SessionUser, db: AsyncSession) -> dict:
    from ..services.legacy_ldap import LegacyImportError  # noqa: PLC0415

    try:
        out = await request.app.state.legacy_ldap.import_now(db, su.display_name)
    except LegacyImportError as exc:
        raise HTTPException(status_code=409, detail={"message": str(exc), "stages": exc.stages}) from None
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="ldap.legacy_import", target_type="ldap_profile", target_id="legacy",
                      ip=client_ip(request), details={"profiles": out["profiles"], "ca_added": out["ca_added"], "groups_added": out["groups_added"]})
    await db.commit()
    d = request.app.state.directory
    if hasattr(d, "reload"):
        await d.reload(db)
    request.app.state.legacy_ldap.last_error = ""
    return {"done": True, **out}
