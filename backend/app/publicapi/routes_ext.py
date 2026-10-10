"""Публичный API v1, этап 2: фоновые задачи генерации (202 Accepted), идемпотентность, ссылки на скачивание записей. Подключается из routes.py до маршрута-ловушки."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import Depends, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import client_ip, get_db
from ..models import ApiClient, ApiJob, Recording, utcnow
from ..services.audit import write_audit
from ..services.reconcile import mark_missing
from ..services.storage import StorageError, StorageNotFound
from . import cursor, downloads, idempotency, ids
from .auth import Access, Principal, _rate, api_config
from .errors import ApiError, not_found
from .jobs import FINAL, KIND_SCOPE
from .routes import ERR, ErrorOut, Page, _limit, _pid, _utc, load_meeting, router

MAX_ACTIVE_JOBS = 5          # одновременно ожидающих и выполняющихся задач на интеграцию: защита очереди языковой модели


# ------------------------------------------------------------------------------------------------ схемы
class DocumentRequest(BaseModel):
    kind: Literal["protocol", "summary"] = Field(description="Что сформировать: протокол или краткое резюме.")
    instruction: str | None = Field(None, max_length=20000, description="Инструкция модели; пусто — инструкция по умолчанию (общая и комнаты).")


class JobOut(BaseModel):
    id: str
    kind: Literal["protocol", "summary", "map"]
    meeting_id: str
    status: Literal["queued", "processing", "completed", "failed", "cancelled"]
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None
    result: dict | None = Field(None, description="При `completed`: `document_id` (протокол/резюме) или `map_id` (карта). Содержимое читается обычными методами.")


class JobList(Page):
    items: list[JobOut]


class DownloadUrlOut(BaseModel):
    url: str = Field(description="Относительный адрес; дополните адресом сервера. Ключ API для скачивания не нужен.")
    expires_at: datetime
    expires_in_s: int
    size_bytes: int
    content_type: str = "audio/wav"


def _job_out(j: ApiJob) -> JobOut:
    return JobOut(id=ids.pub("job", j.id), kind=j.kind, meeting_id=ids.pub("meeting", j.meeting_id), status=j.status, created_at=_utc(j.created_at), started_at=_utc(j.started_at),
                  finished_at=_utc(j.finished_at), error=j.error, result=j.result)


def _need(p: Principal, scope: str) -> None:
    if not p.has(scope):
        raise ApiError(403, "insufficient_scope", f"Не хватает права «{scope}».", extra={"required_scope": scope})


async def _enqueue(request: Request, db: AsyncSession, p: Principal, meeting_id: str, kind: str, instruction: str | None, body_for_hash: dict) -> Response:
    _need(p, KIND_SCOPE[kind])
    m = await load_meeting(db, p, meeting_id)
    runner = request.app.state.jobs

    async def action() -> tuple[int, dict, dict]:
        active = (await db.execute(select(func.count()).select_from(ApiJob).where(ApiJob.client_id == p.client_id, ApiJob.status.in_(("queued", "processing"))))).scalar_one()
        if active >= MAX_ACTIVE_JOBS:
            raise ApiError(429, "too_many_jobs", f"У интеграции уже {active} незавершённых задач (предел {MAX_ACTIVE_JOBS}). Дождитесь завершения или отмените лишние.", headers={"Retry-After": "30"})
        await runner.preflight(request, db, m, kind)
        job = await runner.submit(db, p.client_id, kind, m.id, {"instruction": instruction} if instruction else None)
        await write_audit(db, actor_user_id=None, actor_name=f"api:{p.client_name}", action="api.job.create", target_type="job", target_id=str(job.id),
                          details={"kind": kind, "meeting": str(m.id), "key": p.key_id}, ip=client_ip(request))
        await db.commit()
        runner.wake()
        out = _job_out(job).model_dump(mode="json")
        return 202, out, {"Location": f"/api/public/v1/jobs/{out['id']}", "Retry-After": "5"}

    return await idempotency.run(request, db, p.client_id, body_for_hash, action)


_ACCEPTED = {202: {"model": JobOut, "description": "Задача принята и поставлена в очередь. Состояние — по адресу из заголовка `Location`."}}


@router.post("/meetings/{meeting_id}/documents", status_code=202, response_model=JobOut, tags=["jobs"], openapi_extra={"x-required-scopes": ["protocols:generate", "summaries:generate"]}, summary="Сформировать протокол или резюме (фоновая задача)",
             responses={**ERR, **_ACCEPTED, 409: {"model": ErrorOut}, 429: {"model": ErrorOut}},
             description="Возвращает `202 Accepted` и задачу. Заголовок `Idempotency-Key` защищает от дублей при повторе запроса. Нужно право `protocols:generate` или `summaries:generate` "
                         "по виду документа. Условия: встреча завершена, языковая модель настроена, есть материалы (иначе `409` с понятным кодом).")
async def create_document(meeting_id: str, body: DocumentRequest, request: Request, p: Principal = Depends(Access(None, "ai")), db: AsyncSession = Depends(get_db)):
    return await _enqueue(request, db, p, meeting_id, body.kind, (body.instruction or "").strip() or None, {"meeting": meeting_id, **body.model_dump()})


@router.post("/meetings/{meeting_id}/map", status_code=202, response_model=JobOut, tags=["jobs"], openapi_extra={"x-required-scopes": ["maps:generate"]}, summary="Сформировать карту разговора (фоновая задача)",
             responses={**ERR, **_ACCEPTED, 409: {"model": ErrorOut}, 429: {"model": ErrorOut}}, description="Нужно право `maps:generate`. Повторный вызов создаёт новую задачу; правки пользователей к карте сохраняются.")
async def create_map(meeting_id: str, request: Request, p: Principal = Depends(Access(None, "ai")), db: AsyncSession = Depends(get_db)):
    return await _enqueue(request, db, p, meeting_id, "map", None, {"meeting": meeting_id, "kind": "map"})


# ------------------------------------------------------------------------------------------------ задачи
async def _own_job(db: AsyncSession, p: Principal, job_id: str) -> ApiJob:
    j = await db.get(ApiJob, _pid("job", job_id, "Задача"))
    if j is None or j.client_id != p.client_id:
        raise not_found("Задача")                 # чужая задача неотличима от несуществующей
    return j


@router.get("/jobs", response_model=JobList, tags=["jobs"], summary="Список своих задач (новые первыми)", responses=ERR)
async def list_jobs(request: Request, status: Literal["queued", "processing", "completed", "failed", "cancelled"] | None = None, limit: int | None = Query(None, ge=1, le=1000),
                    after: str | None = Query(None, description="Курсор из `next_cursor`."), p: Principal = Depends(Access("jobs:read")), db: AsyncSession = Depends(get_db)):
    n = await _limit(request, db, limit)
    stmt = select(ApiJob).where(ApiJob.client_id == p.client_id).order_by(ApiJob.created_at.desc(), ApiJob.id.desc())
    if status:
        stmt = stmt.where(ApiJob.status == status)
    if after:
        ts, jid = cursor.decode(after, 2)
        try:
            c_ts, c_id = datetime.fromisoformat(str(ts)), uuid.UUID(hex=str(jid))
        except ValueError:
            raise ApiError(400, "invalid_cursor", "Курсор недействителен: начните выборку сначала.") from None
        stmt = stmt.where(or_(ApiJob.created_at < c_ts, and_(ApiJob.created_at == c_ts, ApiJob.id < c_id)))
    rows = (await db.execute(stmt.limit(n + 1))).scalars().all()
    page = rows[:n]
    nxt = cursor.encode(_utc(page[-1].created_at).isoformat(), page[-1].id.hex) if len(rows) > n else None
    return JobList(items=[_job_out(j) for j in page], next_cursor=nxt)


@router.get("/jobs/{job_id}", response_model=JobOut, tags=["jobs"], summary="Состояние задачи", responses=ERR)
async def get_job(job_id: str, p: Principal = Depends(Access("jobs:read")), db: AsyncSession = Depends(get_db)):
    return _job_out(await _own_job(db, p, job_id))


@router.post("/jobs/{job_id}/cancel", response_model=JobOut, tags=["jobs"], summary="Отменить ожидающую задачу", responses={**ERR, 409: {"model": ErrorOut}},
             description="Отменить можно только задачу в состоянии `queued`. Выполняющуюся прервать безопасно нельзя: `409 not_cancellable`. Повторная отмена уже отменённой — `200`.")
async def cancel_job(job_id: str, request: Request, p: Principal = Depends(Access("jobs:read", "write")), db: AsyncSession = Depends(get_db)):
    j = await _own_job(db, p, job_id)
    if j.status == "cancelled":
        return _job_out(j)
    if not await request.app.state.jobs.cancel(db, j.id):
        await db.refresh(j)
        raise ApiError(409, "not_cancellable", f"Задачу в состоянии «{j.status}» отменить нельзя: отменяются только ожидающие.")
    await write_audit(db, actor_user_id=None, actor_name=f"api:{p.client_name}", action="api.job.cancel", target_type="job", target_id=str(j.id), details={"key": p.key_id}, ip=client_ip(request))
    await db.commit()
    await db.refresh(j)
    return _job_out(j)


# ------------------------------------------------------------------------------------------------ скачивание записей
@router.post("/meetings/{meeting_id}/recordings/{recording_id}/download-url", response_model=DownloadUrlOut, tags=["recordings"], summary="Короткоживущая ссылка на скачивание записи",
             responses={**ERR, 410: {"model": ErrorOut}}, description="Ссылка действует `download_url_ttl_s` секунд (по умолчанию 300) и не требует ключа API, поэтому её можно передать плееру или стороннему сервису. "
             "При каждом использовании заново проверяются права интеграции.")
async def download_url(meeting_id: str, recording_id: str, request: Request, p: Principal = Depends(Access("recordings:download", "download")), db: AsyncSession = Depends(get_db)):
    m = await load_meeting(db, p, meeting_id)
    rec = await db.get(Recording, _pid("recording", recording_id, "Запись"))
    if rec is None or rec.meeting_id != m.id or rec.kind != "participant":      # общая запись в публичный API пока не отдаётся
        raise not_found("Запись")
    if rec.file_state == "missing":
        raise ApiError(410, "file_missing", "Файл записи удалён из хранилища.")
    cfg = await api_config(request, db)
    s = request.app.state.settings
    token, exp = downloads.issue(s.app_master_key, s.internal_api_token, client_id=str(p.client_id), recording_id=str(rec.id), ttl_s=int(cfg.download_url_ttl_s))
    await write_audit(db, actor_user_id=None, actor_name=f"api:{p.client_name}", action="api.recording.download_url", target_type="recording", target_id=str(rec.id),
                      details={"key": p.key_id, "ttl_s": int(cfg.download_url_ttl_s)}, ip=client_ip(request))
    await db.commit()
    return DownloadUrlOut(url=f"/api/public/v1/downloads/{token}", expires_at=datetime.fromtimestamp(exp, tz=timezone.utc), expires_in_s=int(cfg.download_url_ttl_s), size_bytes=rec.size_bytes)


@router.get("/downloads/{token}", tags=["recordings"], summary="Скачать запись по короткоживущей ссылке (без ключа API)", responses={200: {"content": {"audio/wav": {}}}, 403: {"model": ErrorOut},
            404: {"model": ErrorOut}, 410: {"model": ErrorOut}, 429: {"model": ErrorOut}})
async def download(token: str, request: Request, db: AsyncSession = Depends(get_db)):
    cfg = await api_config(request, db)
    if not cfg.enabled:
        raise ApiError(503, "api_disabled", "Публичный API выключен администратором.")
    s = request.app.state.settings
    data, why = downloads.verify(s.app_master_key, s.internal_api_token, token)
    if why == "expired":
        raise ApiError(410, "link_expired", "Срок действия ссылки истёк. Запросите новую.")
    if data is None:
        raise not_found("Ссылка")
    client = await db.get(ApiClient, uuid.UUID(data["c"]))
    if client is None:
        raise not_found("Ссылка")
    p = Principal(client.id, client.name, "download", frozenset(client.scopes or []), frozenset(client.rooms) if client.rooms is not None else None)
    request.state.principal = p
    if not client.enabled:
        raise ApiError(403, "client_disabled", "Сервисная учётная запись отключена.")
    if not p.has("recordings:download"):
        raise ApiError(403, "insufficient_scope", "Не хватает права «recordings:download».", extra={"required_scope": "recordings:download"})
    rec = await db.get(Recording, uuid.UUID(data["r"]))
    if rec is None or rec.kind != "participant" or not p.allows_room(rec.room_id):
        raise not_found("Запись")
    await _rate(request, cfg, p, "download")
    if rec.file_state == "missing":
        raise ApiError(410, "file_missing", "Файл записи удалён из хранилища.")
    ps = request.app.state.protocols
    try:
        content = await ps.read_recording(db, rec)
    except StorageNotFound:
        await mark_missing(db, rec, request.app.state.journal, "запись аудио")
        raise ApiError(410, "file_missing", "Файл записи не найден в хранилище.") from None
    except StorageError:
        raise not_found("Запись") from None
    await write_audit(db, actor_user_id=None, actor_name=f"api:{client.name}", action="api.recording.download", target_type="recording", target_id=str(rec.id), ip=client_ip(request))
    await db.commit()
    name = rec.path.rsplit("/", 1)[-1].encode("ascii", "ignore").decode() or "recording.wav"
    return Response(content, media_type="audio/wav", headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"})
