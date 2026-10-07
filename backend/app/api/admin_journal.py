"""Админка: журнал событий (поиск с фильтрами и «бесконечной» подгрузкой, статистика, удаление выбранного, выгрузка архива)
и API-профили (несколько LLM и сервисов обезличивания, выбор «по умолчанию»). Только для администратора; изменения — в аудит."""
from __future__ import annotations

import csv
import io
import json
import os
import shutil
import tempfile
import zipfile
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from sqlalchemy import and_, delete, func, not_, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.background import BackgroundTask

from ..auth.deps import SessionUser, client_ip, get_db, require_admin
from ..integrations.anonymizer import AnonymizerClient
from ..integrations.llm import LlmClient
from ..models import AuditLog, EventLog, utcnow
from ..services.api_profiles import MAIN
from ..services.audit import write_audit
from ..services.journal import CATEGORIES, LEVELS
from ..services.settings import SettingsError

router = APIRouter(prefix="/admin", tags=["admin-journal"])

FIELDS = {
    "level": EventLog.level, "category": EventLog.category, "event": EventLog.event, "user": EventLog.user_name, "room": EventLog.room,
    "ip": EventLog.ip, "client": EventLog.client, "message": EventLog.message, "meeting": EventLog.meeting_id,
}
OPS = {"eq", "ne", "contains", "not_contains", "starts", "gte"}
RANGES = {"1h": timedelta(hours=1), "24h": timedelta(hours=24), "7d": timedelta(days=7), "30d": timedelta(days=30)}
MAX_FILTERS = 12
MAX_DELETE_IDS = 5000


def _like(col, value: str, *, start: bool = False):
    esc = value.lower().replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")
    return func.lower(func.coalesce(col, "")).like(f"{esc}%" if start else f"%{esc}%", escape="\\")


def parse_filters(raw: str | None) -> list[dict]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except ValueError:
        raise HTTPException(status_code=422, detail="filters: некорректный JSON") from None
    if not isinstance(data, list) or len(data) > MAX_FILTERS:
        raise HTTPException(status_code=422, detail=f"filters: список не длиннее {MAX_FILTERS}")
    out = []
    for f in data:
        if not isinstance(f, dict) or f.get("field") not in FIELDS or f.get("op") not in OPS:
            raise HTTPException(status_code=422, detail="filters: поле или условие не поддерживается")
        v = f.get("value")
        if isinstance(v, list):
            v = [str(x)[:300] for x in v[:50]]
        else:
            v = str(v if v is not None else "")[:300]
        out.append({"field": f["field"], "op": f["op"], "value": v})
    return out


def build_where(filters: list[dict], *, q: str = "", rng: str = "", since: str = "", until: str = ""):
    conds = []
    for f in filters:
        col, op, v = FIELDS[f["field"]], f["op"], f["value"]
        if f["field"] == "level" and op == "gte":
            floor = LEVELS.get(str(v), 0)
            conds.append(col.in_([k for k, n in LEVELS.items() if n >= floor]))
        elif op == "gte":
            raise HTTPException(status_code=422, detail="Условие «не ниже» применимо только к уровню")
        elif op == "eq":
            conds.append(col.in_(v) if isinstance(v, list) else func.lower(func.coalesce(col, "")) == str(v).lower())
        elif op == "ne":
            conds.append(not_(col.in_(v)) if isinstance(v, list) else func.lower(func.coalesce(col, "")) != str(v).lower())
        elif op == "contains":
            conds.append(_like(col, str(v)))
        elif op == "not_contains":
            conds.append(not_(_like(col, str(v))))
        elif op == "starts":
            conds.append(_like(col, str(v), start=True))
    q = q.strip()[:200]
    if q:
        conds.append(or_(*[_like(c, q) for c in (EventLog.message, EventLog.event, EventLog.user_name, EventLog.room, EventLog.ip, EventLog.client)]))
    now = utcnow()
    if rng in RANGES:
        conds.append(EventLog.at >= now - RANGES[rng])
    for val, op in ((since, "gte"), (until, "lte")):
        if val:
            try:
                dt = datetime.fromisoformat(val.replace("Z", "+00:00"))
            except ValueError:
                raise HTTPException(status_code=422, detail="Дата в формате ГГГГ-ММ-ДДTЧЧ:ММ") from None
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            conds.append(EventLog.at >= dt if op == "gte" else EventLog.at <= dt)
    return and_(*conds) if conds else None


def _row(r: EventLog) -> dict:
    return {"id": r.id, "at": r.at, "level": r.level, "category": r.category, "event": r.event, "user": r.user_name, "room": r.room,
            "meeting_id": r.meeting_id, "ip": r.ip, "client": r.client, "message": r.message, "data": r.data, "request_id": r.request_id}


# ------------------------------------------------------------------------------------------- чтение
@router.get("/journal")
async def journal_list(filters: str = Query("", max_length=8000), q: str = Query("", max_length=200), range: str = Query("", max_length=8),
                       since: str = Query("", max_length=40), until: str = Query("", max_length=40),
                       before_id: int | None = Query(None, ge=1), limit: int = Query(100, ge=1, le=500),
                       su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Страница журнала от новых к старым. Подгрузка «бесконечной лентой»: следующий запрос — с before_id = next_cursor."""
    where = build_where(parse_filters(filters), q=q, rng=range, since=since, until=until)
    stmt = select(EventLog).order_by(EventLog.id.desc()).limit(limit + 1)
    if where is not None:
        stmt = stmt.where(where)
    if before_id:
        stmt = stmt.where(EventLog.id < before_id)
    rows = list((await db.execute(stmt)).scalars().all())
    more = len(rows) > limit
    rows = rows[:limit]
    return {"items": [_row(r) for r in rows], "next_cursor": rows[-1].id if (more and rows) else None}


@router.get("/journal/facets")
async def journal_facets(su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Значения для выпадающих списков фильтров (за последние 30 дней, самые частые)."""
    since = utcnow() - timedelta(days=30)

    async def top(col, n=60):
        rows = (await db.execute(select(col, func.count()).where(EventLog.at >= since, col.is_not(None)).group_by(col)
                                 .order_by(func.count().desc()).limit(n))).all()
        return [r[0] for r in rows if r[0]]
    return {"levels": list(LEVELS), "categories": sorted(set(CATEGORIES) | set(await top(EventLog.category))),
            "events": sorted(await top(EventLog.event, 200)), "users": sorted(await top(EventLog.user_name, 200)),
            "rooms": sorted(await top(EventLog.room, 100)), "clients": sorted(await top(EventLog.client, 100))}


async def _size_bytes(db: AsyncSession, table: str) -> int | None:
    try:
        if db.bind.dialect.name == "postgresql":  # type: ignore[union-attr]
            return int((await db.execute(text("SELECT pg_total_relation_size(:t)"), {"t": table})).scalar_one())
    except Exception:  # noqa: BLE001
        return None
    return None


@router.get("/journal/stats")
async def journal_stats(request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    now = utcnow()
    day = now - timedelta(hours=24)
    total = (await db.execute(select(func.count()).select_from(EventLog))).scalar_one()
    last_day = (await db.execute(select(func.count()).select_from(EventLog).where(EventLog.at >= day))).scalar_one()
    by_level = dict((await db.execute(select(EventLog.level, func.count()).where(EventLog.at >= day).group_by(EventLog.level))).all())
    by_cat = dict((await db.execute(select(EventLog.category, func.count()).where(EventLog.at >= day).group_by(EventLog.category))).all())
    oldest = (await db.execute(select(func.min(EventLog.at)))).scalar_one()
    size = await _size_bytes(db, "event_log")
    if size is None:  # SQLite/прочее: оценка по длине текстовых полей
        approx = (await db.execute(select(func.coalesce(func.sum(
            func.length(func.coalesce(EventLog.message, "")) + func.length(EventLog.event) + func.length(func.coalesce(EventLog.client, ""))
            + func.length(func.coalesce(EventLog.user_name, "")) + 120), 0)))).scalar_one()
        size = int(approx)
    audit_total = (await db.execute(select(func.count()).select_from(AuditLog))).scalar_one()
    audit_size = await _size_bytes(db, "audit_log")
    cfg = await request.app.state.settings_svc.get(db, "journal")
    j = request.app.state.journal
    return {"total": total, "last_24h": last_day, "errors_24h": by_level.get("error", 0), "warns_24h": by_level.get("warn", 0),
            "by_category_24h": by_cat, "oldest": oldest, "size_bytes": size, "avg_bytes_per_event": int(size / total) if total else 0,
            "audit": {"total": audit_total, "size_bytes": audit_size},
            "retention_days": cfg.retention_days, "keep_local": cfg.keep_local,  # type: ignore[attr-defined]
            "external": {"enabled": cfg.enabled, "mode": cfg.mode, **j.last_external}, "queue_dropped": j.dropped, "written_since_start": j.written}


# --------------------------------------------------------------------------------------- удаление
@router.post("/journal/delete")
async def journal_delete(request: Request, body: dict[str, Any] = Body(...), su: SessionUser = Depends(require_admin),
                         db: AsyncSession = Depends(get_db)):
    """Удалить выбранные записи (`ids`) либо ВСЕ записи под текущим фильтром (`all_matching: true` + те же filters/q/range)."""
    ids = body.get("ids")
    if ids is not None:
        if not isinstance(ids, list) or not ids or len(ids) > MAX_DELETE_IDS or not all(isinstance(i, int) for i in ids):
            raise HTTPException(status_code=422, detail=f"ids: список из 1–{MAX_DELETE_IDS} номеров записей")
        res = await db.execute(delete(EventLog).where(EventLog.id.in_(ids)))
        scope = {"ids": len(ids)}
    elif body.get("all_matching") is True:
        where = build_where(parse_filters(json.dumps(body.get("filters") or [])), q=str(body.get("q") or ""), rng=str(body.get("range") or ""),
                            since=str(body.get("since") or ""), until=str(body.get("until") or ""))
        res = await db.execute(delete(EventLog).where(where) if where is not None else delete(EventLog))
        scope = {"all_matching": True, "filters": body.get("filters") or [], "q": body.get("q") or "", "range": body.get("range") or ""}
    else:
        raise HTTPException(status_code=422, detail="Укажите ids или all_matching=true")
    deleted = res.rowcount or 0
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="journal.delete", target_type="journal",
                      target_id="event_log", ip=client_ip(request), details={"deleted": deleted, **scope})
    await db.commit()
    return {"deleted": deleted}


@router.post("/journal/purge-now")
async def journal_purge_now(request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Применить срок хранения немедленно (то же, что делает фоновая очистка раз в час)."""
    stats = await request.app.state.journal.purge_expired()
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="journal.purge", target_type="journal",
                      target_id="event_log", ip=client_ip(request), details=stats)
    await db.commit()
    return stats


# --------------------------------------------------------------------------------------- выгрузка
_CSV_HEAD = ["Время (UTC)", "Уровень", "Категория", "Событие", "Пользователь", "Комната", "Встреча", "IP", "Клиент", "Сообщение", "Данные (JSON)"]


def _safe_cell(v):
    """Защита от «CSV-инъекции»: ячейка, начинающаяся с = + - @ (или табуляции/CR), в Excel исполняется как формула — экранируем апострофом."""
    if isinstance(v, str) and v and v[0] in "=+-@\t\r":
        return "'" + v
    return v


def _csv_line(w: csv.writer, buf: io.StringIO, row: list) -> str:
    w.writerow([_safe_cell(x) for x in row])
    s = buf.getvalue()
    buf.seek(0)
    buf.truncate(0)
    return s


@router.get("/journal/export")
async def journal_export(request: Request, range: str = Query("30d", pattern="^(24h|7d|30d|all)$"),
                         su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """ZIP-архив: journal.csv (для Excel), journal.ndjson (для разбора программами) и audit.csv за выбранный период."""
    cutoff = None if range == "all" else utcnow() - RANGES.get(range, timedelta(days=30))
    fd, path = tempfile.mkstemp(prefix="journal-", suffix=".zip")
    os.close(fd)
    tmpdir = tempfile.mkdtemp(prefix="journal-parts-")
    n_ev = n_au = 0

    def _w(name: str):
        return open(os.path.join(tmpdir, name), "wb")  # noqa: SIM115

    try:
        # zipfile пишет по одному потоку за раз — части собираем в отдельных временных файлах, затем кладём в архив
        with _w("journal.csv") as f_csv, _w("journal.ndjson") as f_nd:
            f_csv.write("\ufeff".encode("utf-8"))  # BOM — Excel открывает кириллицу правильно
            buf = io.StringIO()
            w = csv.writer(buf, delimiter=";", lineterminator="\r\n")
            f_csv.write(_csv_line(w, buf, _CSV_HEAD).encode("utf-8"))
            last = 0
            while True:
                stmt = select(EventLog).where(EventLog.id > last).order_by(EventLog.id).limit(2000)
                if cutoff is not None:
                    stmt = stmt.where(EventLog.at >= cutoff)
                rows = (await db.execute(stmt)).scalars().all()
                if not rows:
                    break
                for r in rows:
                    d = _row(r)
                    d["at"] = r.at.astimezone(timezone.utc).isoformat()
                    f_nd.write((json.dumps(d, ensure_ascii=False, default=str) + "\n").encode("utf-8"))
                    f_csv.write(_csv_line(w, buf, [d["at"], r.level, r.category, r.event, r.user_name or "", r.room or "", r.meeting_id or "",
                                                   r.ip or "", r.client or "", r.message or "",
                                                   json.dumps(r.data, ensure_ascii=False, default=str) if r.data else ""]).encode("utf-8"))
                    n_ev += 1
                last = rows[-1].id
        with _w("audit.csv") as f_au:
            f_au.write("\ufeff".encode("utf-8"))
            buf = io.StringIO()
            w = csv.writer(buf, delimiter=";", lineterminator="\r\n")
            f_au.write(_csv_line(w, buf, ["Время (UTC)", "Кто", "Действие", "Тип объекта", "Объект", "IP", "Подробности (JSON)"]).encode("utf-8"))
            last = 0
            while True:
                stmt = select(AuditLog).where(AuditLog.id > last).order_by(AuditLog.id).limit(2000)
                if cutoff is not None:
                    stmt = stmt.where(AuditLog.at >= cutoff)
                rows = (await db.execute(stmt)).scalars().all()
                if not rows:
                    break
                for a in rows:
                    f_au.write(_csv_line(w, buf, [a.at.astimezone(timezone.utc).isoformat(), a.actor_name, a.action, a.target_type, a.target_id,
                                                  a.ip or "", json.dumps(a.details, ensure_ascii=False, default=str) if a.details else ""]).encode("utf-8"))
                    n_au += 1
                last = rows[-1].id
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
            for name in ("journal.csv", "journal.ndjson", "audit.csv"):
                zf.write(os.path.join(tmpdir, name), name)
    except Exception:
        os.unlink(path)
        raise
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="journal.export", target_type="journal",
                      target_id="event_log", ip=client_ip(request), details={"range": range, "events": n_ev, "audit": n_au})
    await db.commit()
    stamp = utcnow().strftime("%Y%m%d-%H%M")
    return FileResponse(path, media_type="application/zip", filename=f"peregovorka-journal-{range}-{stamp}.zip",
                        headers={"Cache-Control": "no-store"}, background=BackgroundTask(os.unlink, path))


# ==================================================================================== API-профили
@router.get("/api-profiles")
async def profiles_list(request: Request, kind: str = Query(pattern="^(llm|anonymizer)$"), su: SessionUser = Depends(require_admin),
                        db: AsyncSession = Depends(get_db)):
    return await request.app.state.profiles.list(db, kind)


@router.post("/api-profiles", status_code=201)
async def profiles_create(request: Request, body: dict[str, Any] = Body(...), su: SessionUser = Depends(require_admin),
                          db: AsyncSession = Depends(get_db)):
    svc = request.app.state.profiles
    try:
        out = await svc.create(db, str(body.get("kind", "")), str(body.get("name", "")), body.get("config") or {}, str(body.get("secret") or ""),
                               make_default=bool(body.get("make_default")))
    except SettingsError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="api_profile.create", target_type="api_profile",
                      target_id=out["id"], ip=client_ip(request), details={"kind": out["kind"], "name": out["name"], "default": out["is_default"]})
    await db.commit()
    return out


@router.put("/api-profiles/default")
async def profiles_default(request: Request, body: dict[str, Any] = Body(...), su: SessionUser = Depends(require_admin),
                           db: AsyncSession = Depends(get_db)):
    try:
        await request.app.state.profiles.set_default(db, str(body.get("kind", "")), str(body.get("profile_id") or MAIN))
    except SettingsError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="api_profile.default", target_type="api_profile",
                      target_id=str(body.get("profile_id") or MAIN), ip=client_ip(request), details={"kind": body.get("kind")})
    await db.commit()
    return {"ok": True}


@router.patch("/api-profiles/{profile_id}")
async def profiles_update(profile_id: str, request: Request, body: dict[str, Any] = Body(...), su: SessionUser = Depends(require_admin),
                          db: AsyncSession = Depends(get_db)):
    if profile_id == MAIN:
        raise HTTPException(status_code=409, detail="Основной профиль настраивается в разделах «Обезличивание» и «Языковая модель»")
    try:
        out = await request.app.state.profiles.update(db, profile_id, body)
    except SettingsError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="api_profile.update", target_type="api_profile",
                      target_id=profile_id, ip=client_ip(request),
                      details={"name": out["name"], "changed": sorted(k for k in body if k in ("name", "config", "secret")), "secret_changed": "secret" in body and body["secret"] is not None})
    await db.commit()
    return out


@router.delete("/api-profiles/{profile_id}", status_code=204)
async def profiles_delete(profile_id: str, request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    if profile_id == MAIN:
        raise HTTPException(status_code=409, detail="Основной профиль удалить нельзя — его можно только выключить")
    try:
        await request.app.state.profiles.delete(db, profile_id)
    except SettingsError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="api_profile.delete", target_type="api_profile",
                      target_id=profile_id, ip=client_ip(request))
    await db.commit()


@router.post("/api-profiles/{profile_id}/test")
async def profiles_test(profile_id: str, request: Request, kind: str = Query(pattern="^(llm|anonymizer)$"),
                        su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Проверка связи с API профиля (для «main» — с общими настройками)."""
    tr = getattr(request.app.state, "test_transports", {}) or {}
    ca = request.app.state.settings.ldap_ca_file or None
    try:
        res = await request.app.state.profiles.get_settings(db, kind, profile_id)
    except SettingsError as exc:
        return {"ok": False, "message": str(exc), "ms": 0}
    if kind == "anonymizer":
        ok, msg, ms = await AnonymizerClient(res.settings, ca_file=ca, transport=tr.get("anonymizer")).test()  # type: ignore[arg-type]
    else:
        ok, msg, ms = await LlmClient(res.settings, ca_file=ca, transport=tr.get("llm")).test()  # type: ignore[arg-type]
    request.app.state.journal.emit("llm", "api_profile_test", level="info" if ok else "warn", user=su.display_name, ip=client_ip(request),
                                   message=f"{kind} «{res.name}»: {'OK' if ok else msg}", data={"kind": kind, "profile": res.name, "ok": ok, "ms": ms})
    return {"ok": ok, "message": msg, "ms": ms}
