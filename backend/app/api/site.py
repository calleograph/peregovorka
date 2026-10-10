"""Настройки сайта (оформление установки), юридические документы организации и подтверждения.

Публично (без входа) отдаётся только опубликованное и нужное интерфейсу: название, цвета, ссылки на изображения, контакты (по режиму показа), опубликованные документы.
Изменять оформление и документы может только администратор; все изменения пишутся в журнал аудита. Тексты документов и необходимость подтверждения определяет организация,
юридических формулировок продукт не содержит. Изображения и документы хранятся вне исходного кода (каталог данных и база), поэтому переживают обновления.
"""
from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import SessionUser, client_ip, current_session, get_db, require_admin, require_user
from ..models import LegalConsent, LegalDocument, LegalRevision, utcnow
from ..services.audit import write_audit
from ..services.branding import KIND_LABEL, KINDS, BrandingError, process, version_of
from ..services.settings import GROUPS, SettingsError

router = APIRouter(tags=["site"])

LEGAL_KINDS = {
    "privacy_policy": "Политика обработки персональных данных",
    "personal_data_consent": "Согласие на обработку персональных данных",
    "terms": "Пользовательское соглашение",
    "usage_rules": "Правила использования корпоративной системы",
}
MAX_DOC = 200_000
ASSET_FIELD = {"logo": "logo_v", "logo_compact": "logo_compact_v", "favicon": "favicon_v"}
VERSION_FIELDS = tuple(ASSET_FIELD.values())
MAX_IMPORT = 12 * 1024 * 1024
ASSET_CSP = "default-src 'none'; sandbox"


def _kind(kind: str) -> str:
    if kind not in LEGAL_KINDS:
        raise HTTPException(status_code=404, detail="Неизвестный документ")
    return kind


async def _doc(db: AsyncSession, kind: str) -> LegalDocument:
    d = await db.get(LegalDocument, kind)
    if d is None:
        d = LegalDocument(kind=kind, title=LEGAL_KINDS[kind], draft_md="", content_md="", published_title="", version=0, published=False, require_consent=False,
                          updated_at=utcnow())
        db.add(d)
        await db.flush()
    return d


def _asset_url(kind: str, v: str) -> str | None:
    return f"/api/v1/public/site/asset/{kind}?v={v}" if v else None


# ======================================================================================= публичное
@router.get("/public/site")
async def public_site(request: Request, db: AsyncSession = Depends(get_db)):
    """Оформление и сведения установки для интерфейса: без входа; контакты — по режиму показа (всем / только вошедшим / скрыты)."""
    cfg = await request.app.state.settings_svc.get(db, "site")
    c = cfg.model_dump()
    show_support = c["support_mode"] == "all" or (c["support_mode"] == "auth" and await current_session(request) is not None)
    docs = (await db.execute(select(LegalDocument).where(LegalDocument.published.is_(True)).order_by(LegalDocument.kind))).scalars().all()
    support = None
    if show_support and any(c[k] for k in ("support_email", "support_phone", "support_url", "portal_url", "support_text")):
        support = {k: c[k] for k in ("support_email", "support_phone", "support_url", "portal_url", "support_text")}
    defaults = type(cfg)().model_dump()
    customized = any(c[k] != defaults[k] for k in c if k not in VERSION_FIELDS) or any(c[k] for k in VERSION_FIELDS)
    out = {
        "name": c["name"], "short_name": c["short_name"] or c["name"], "subtitle": c["subtitle"], "description": c["description"], "theme": c["theme"],
        "primary_color": c["primary_color"], "accent2_color": c["accent2_color"],
        "assets": {k: _asset_url(k, c[f]) for k, f in ASSET_FIELD.items()},
        "org": {k: c[f"org_{k}"] for k in ("full", "short", "url", "unit", "legal_name", "address")},
        "footer_text": c["footer_text"], "support": support, "welcome_text": c["welcome_text"], "guest_text": c["guest_text"], "recording_text": c["recording_text"],
        "documents": [{"kind": d.kind, "title": d.published_title or LEGAL_KINDS.get(d.kind, d.kind), "version": d.version, "require_consent": d.require_consent,
                       "published_at": d.published_at} for d in docs],
        "customized": customized,
    }
    return Response(json.dumps(out, ensure_ascii=False, default=str), media_type="application/json", headers={"Cache-Control": "no-cache"})


@router.get("/public/site/asset/{kind}")
async def public_asset(kind: str, request: Request, v: str = ""):
    """Изображение оформления. Адрес содержит версию (?v=…): при смене изображения адрес меняется, и браузер не показывает старое; по версии кэш долгий."""
    if kind not in KINDS:
        raise HTTPException(status_code=404, detail="Нет изображения")
    data = request.app.state.branding.read(kind)
    if data is None:
        raise HTTPException(status_code=404, detail="Нет изображения")
    cache = "public, max-age=31536000, immutable" if v == version_of(data) else "no-cache"
    return Response(data, media_type="image/png", headers={"Cache-Control": cache, "X-Content-Type-Options": "nosniff", "Content-Security-Policy": ASSET_CSP})


@router.get("/public/site/manifest")
async def public_manifest(request: Request, db: AsyncSession = Depends(get_db)):
    """Манифест устанавливаемого веб-приложения с названием и значками (если организация загрузила свои)."""
    c = (await request.app.state.settings_svc.get(db, "site")).model_dump()
    if c["logo_compact_v"] or c["favicon_v"]:
        kind, v = ("logo_compact", c["logo_compact_v"]) if c["logo_compact_v"] else ("favicon", c["favicon_v"])
        icons = [{"src": _asset_url(kind, v), "sizes": "any", "type": "image/png", "purpose": "any"}]
    else:
        icons = [{"src": "/icon-192.png", "sizes": "192x192", "type": "image/png"}, {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png"}]
    m = {"name": c["name"], "short_name": c["short_name"] or c["name"], "description": c["subtitle"], "start_url": "/", "display": "standalone",
         "background_color": "#0e1218", "theme_color": c["primary_color"] or "#2b5be0", "icons": icons}
    return Response(json.dumps(m, ensure_ascii=False), media_type="application/manifest+json", headers={"Cache-Control": "no-cache"})


@router.get("/public/legal/{kind}")
async def public_legal(kind: str, db: AsyncSession = Depends(get_db)):
    d = await db.get(LegalDocument, _kind(kind))
    if d is None or not d.published:
        raise HTTPException(status_code=404, detail="Документ не опубликован")
    return {"kind": d.kind, "title": d.published_title or LEGAL_KINDS[d.kind], "content_md": d.content_md, "version": d.version, "published_at": d.published_at,
            "require_consent": d.require_consent}


# ======================================================================================= подтверждения
async def required_documents(db: AsyncSession) -> list[LegalDocument]:
    return list((await db.execute(select(LegalDocument).where(LegalDocument.published.is_(True), LegalDocument.require_consent.is_(True)).order_by(LegalDocument.kind))).scalars().all())


async def accepted_versions(db: AsyncSession, subject_type: str, subject_id: str) -> dict[str, int]:
    rows = (await db.execute(select(LegalConsent.kind, func.max(LegalConsent.version)).where(LegalConsent.subject_type == subject_type, LegalConsent.subject_id == subject_id)
                             .group_by(LegalConsent.kind))).all()
    return {k: v for k, v in rows}


async def record_consents(db: AsyncSession, request: Request, subject_type: str, subject_id: str, name: str, docs: list[LegalDocument]) -> None:
    ua = (request.headers.get("user-agent") or "")[:300]
    for d in docs:
        db.add(LegalConsent(subject_type=subject_type, subject_id=subject_id, subject_name=name[:300], kind=d.kind, version=d.version, ip=client_ip(request), user_agent=ua))


@router.get("/legal/pending")
async def pending_consents(request: Request, su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    """Документы, которые нужно подтвердить этому пользователю (опубликованные, с обязательным подтверждением, в действующей версии)."""
    have = await accepted_versions(db, "user", str(su.user_id))
    return {"items": [{"kind": d.kind, "title": d.published_title or LEGAL_KINDS[d.kind], "version": d.version} for d in await required_documents(db) if have.get(d.kind, 0) < d.version]}


@router.post("/legal/consent")
async def give_consent(request: Request, body: dict[str, Any] = Body(...), su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    """Пользователь явно подтверждает перечисленные документы. Записываются: кто, версия, время, адрес и браузер."""
    kinds = body.get("kinds")
    if not isinstance(kinds, list) or not kinds or not all(isinstance(k, str) for k in kinds):
        raise HTTPException(status_code=422, detail="kinds: список документов")
    required = {d.kind: d for d in await required_documents(db)}
    if [k for k in kinds if k not in required]:
        raise HTTPException(status_code=422, detail="Подтверждение этого документа не требуется или он не опубликован")
    have = await accepted_versions(db, "user", str(su.user_id))
    await record_consents(db, request, "user", str(su.user_id), su.display_name, [required[k] for k in dict.fromkeys(kinds) if have.get(k, 0) < required[k].version])
    await db.commit()
    return {"ok": True}


# ======================================================================================= администратор: изображения
async def _body(request: Request, limit: int, what: str) -> bytes:
    if int(request.headers.get("content-length") or 0) > limit:
        raise HTTPException(status_code=413, detail=f"{what} больше {limit // (1024 * 1024)} МБ")
    data = await request.body()
    if len(data) > limit:
        raise HTTPException(status_code=413, detail=f"{what} больше {limit // (1024 * 1024)} МБ")
    return data


@router.put("/admin/site/assets/{kind}")
async def upload_asset(kind: str, request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Изображение: тело запроса — сам файл (PNG, WebP, JPEG или ICO для значка вкладки до 2 МБ)."""
    if kind not in KINDS:
        raise HTTPException(status_code=404, detail="Неизвестный вид изображения")
    data = await _body(request, 2 * 1024 * 1024, "Файл")
    try:
        png = process(kind, data)
    except BrandingError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    v = request.app.state.branding.save(kind, png)
    await request.app.state.settings_svc.update(db, "site", {ASSET_FIELD[kind]: v}, actor=su.display_name)
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="site.asset_upload", target_type="site", target_id=kind, ip=client_ip(request),
                      details={"kind": kind, "bytes": len(png)})
    await db.commit()
    return {"kind": kind, "url": _asset_url(kind, v), "label": KIND_LABEL[kind]}


@router.delete("/admin/site/assets/{kind}", status_code=204)
async def delete_asset(kind: str, request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    if kind not in KINDS:
        raise HTTPException(status_code=404, detail="Неизвестный вид изображения")
    request.app.state.branding.delete(kind)
    await request.app.state.settings_svc.update(db, "site", {ASSET_FIELD[kind]: ""}, actor=su.display_name)
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="site.asset_delete", target_type="site", target_id=kind, ip=client_ip(request), details={"kind": kind})
    await db.commit()


# ======================================================================================= администратор: документы
def _doc_out(d: LegalDocument) -> dict:
    return {"kind": d.kind, "label": LEGAL_KINDS[d.kind], "title": d.title or LEGAL_KINDS[d.kind], "draft_md": d.draft_md, "content_md": d.content_md, "version": d.version,
            "published": d.published, "require_consent": d.require_consent, "published_at": d.published_at, "updated_at": d.updated_at, "updated_by": d.updated_by,
            "has_changes": d.draft_md != d.content_md}


@router.get("/admin/site/legal")
async def list_legal(su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    items = [_doc_out(await _doc(db, k)) for k in LEGAL_KINDS]
    await db.commit()
    return {"items": items}


@router.put("/admin/site/legal/{kind}")
async def save_legal(kind: str, request: Request, body: dict[str, Any] = Body(...), su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Сохранить черновик (название, текст Markdown) и параметр «Требовать подтверждение при входе». Опубликованная редакция не меняется."""
    d = await _doc(db, _kind(kind))
    unknown = set(body) - {"title", "draft_md", "require_consent"}
    if unknown:
        raise HTTPException(status_code=422, detail=f"Неизвестные поля: {', '.join(sorted(unknown))}")
    if "title" in body:
        if not isinstance(body["title"], str) or len(body["title"]) > 200:
            raise HTTPException(status_code=422, detail="title: строка до 200 символов")
        d.title = body["title"].strip()
    if "draft_md" in body:
        if not isinstance(body["draft_md"], str) or len(body["draft_md"]) > MAX_DOC:
            raise HTTPException(status_code=422, detail=f"Текст документа — не больше {MAX_DOC // 1000} тыс. знаков")
        d.draft_md = body["draft_md"].replace("\r\n", "\n")
    if "require_consent" in body:
        if not isinstance(body["require_consent"], bool):
            raise HTTPException(status_code=422, detail="require_consent: true|false")
        d.require_consent = body["require_consent"]
    d.updated_by = su.display_name
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="site.legal_save", target_type="site", target_id=kind, ip=client_ip(request),
                      details={"fields": sorted(body), "require_consent": d.require_consent, "chars": len(d.draft_md)})
    await db.commit()
    return _doc_out(d)


@router.post("/admin/site/legal/{kind}/publish")
async def publish_legal(kind: str, request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Опубликовать черновик новой редакцией (версия +1; прежняя остаётся в истории). Если подтверждение включено, пользователям оно понадобится заново."""
    d = await _doc(db, _kind(kind))
    if not d.draft_md.strip():
        raise HTTPException(status_code=422, detail="Текст документа пуст: нечего публиковать")
    d.version += 1
    d.content_md, d.published_title, d.published, d.published_at = d.draft_md, d.title or LEGAL_KINDS[kind], True, utcnow()
    for r in (await db.execute(select(LegalRevision).where(LegalRevision.kind == kind, LegalRevision.unpublished_at.is_(None)))).scalars().all():
        r.unpublished_at = utcnow()                       # прежняя редакция перестала действовать
    db.add(LegalRevision(kind=kind, version=d.version, title=d.published_title, content_md=d.content_md, published_by=su.display_name, published_at=utcnow()))
    d.updated_by = su.display_name
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="site.legal_publish", target_type="site", target_id=kind, ip=client_ip(request),
                      details={"version": d.version, "require_consent": d.require_consent})
    await db.commit()
    return _doc_out(d)


@router.post("/admin/site/legal/{kind}/unpublish")
async def unpublish_legal(kind: str, request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    d = await _doc(db, _kind(kind))
    if d.published:
        d.published = False
        for r in (await db.execute(select(LegalRevision).where(LegalRevision.kind == kind, LegalRevision.unpublished_at.is_(None)))).scalars().all():
            r.unpublished_at = utcnow()
    d.updated_by = su.display_name
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="site.legal_unpublish", target_type="site", target_id=kind, ip=client_ip(request),
                      details={"version": d.version})
    await db.commit()
    return _doc_out(d)


@router.get("/admin/site/legal/{kind}/revisions")
async def legal_revisions(kind: str, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(LegalRevision).where(LegalRevision.kind == _kind(kind)).order_by(LegalRevision.version.desc()))).scalars().all()
    return {"items": [{"version": r.version, "title": r.title, "content_md": r.content_md, "published_at": r.published_at, "published_by": r.published_by,
                       "unpublished_at": r.unpublished_at} for r in rows]}


@router.get("/admin/site/legal/{kind}/consents")
async def legal_consents(kind: str, limit: int = 100, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Последние подтверждения документа (кто, версия, когда): основание, что пользователь ознакомился именно с этой редакцией."""
    rows = (await db.execute(select(LegalConsent).where(LegalConsent.kind == _kind(kind)).order_by(LegalConsent.id.desc()).limit(max(1, min(limit, 500))))).scalars().all()
    total = (await db.execute(select(func.count()).select_from(LegalConsent).where(LegalConsent.kind == kind))).scalar_one()
    return {"total": total, "items": [{"subject_type": r.subject_type, "subject_name": r.subject_name, "version": r.version, "accepted_at": r.accepted_at, "ip": r.ip} for r in rows]}


# ======================================================================================= экспорт, импорт, сброс
@router.get("/admin/site/export")
async def export_site(request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Архив оформления: настройки сайта, документы с редакциями и изображения. Секретов и подтверждений пользователей в нём нет."""
    cfg = (await request.app.state.settings_svc.get(db, "site")).model_dump()
    for f in VERSION_FIELDS:
        cfg.pop(f, None)
    docs = []
    for k in LEGAL_KINDS:
        d = await _doc(db, k)
        revs = (await db.execute(select(LegalRevision).where(LegalRevision.kind == k).order_by(LegalRevision.version))).scalars().all()
        docs.append({"kind": k, "title": d.title, "draft_md": d.draft_md, "content_md": d.content_md, "published_title": d.published_title, "version": d.version,
                     "published": d.published, "require_consent": d.require_consent, "published_at": d.published_at,
                     "revisions": [{"version": r.version, "title": r.title, "content_md": r.content_md, "published_at": r.published_at, "published_by": r.published_by,
                                    "unpublished_at": r.unpublished_at} for r in revs]})
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("site.json", json.dumps({"format": 1, "app": "peregovorka-site", "settings": cfg, "legal": docs}, ensure_ascii=False, indent=1, default=str))
        for k in KINDS:
            png = request.app.state.branding.read(k)
            if png:
                z.writestr(f"assets/{k}.png", png)
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="site.export", target_type="site", target_id="site", ip=client_ip(request), details={})
    await db.commit()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    return Response(buf.getvalue(), media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="peregovorka-site-{stamp}.zip"'})


def _dt(v: Any) -> datetime | None:
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v))
    except ValueError:
        return None


@router.put("/admin/site/import")
async def import_site(request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Применить архив оформления (тело запроса — сам .zip). Всё проверяется до сохранения: настройки — теми же правилами, изображения — той же безопасной обработкой."""
    raw = await _body(request, MAX_IMPORT, "Архив")
    try:
        z = zipfile.ZipFile(io.BytesIO(raw))
        names = set(z.namelist())
        if "site.json" not in names:
            raise ValueError("нет site.json")
        if any(i.file_size > MAX_IMPORT for i in z.infolist()):
            raise ValueError("слишком большой файл внутри архива")
        data = json.loads(z.read("site.json"))
        if data.get("app") != "peregovorka-site" or data.get("format") != 1:
            raise ValueError("это не архив оформления Peregovorka")
        settings = {k: v for k, v in (data.get("settings") or {}).items() if k in GROUPS["site"].model_fields and k not in VERSION_FIELDS}
        pngs: dict[str, bytes] = {}
        for k in KINDS:
            if f"assets/{k}.png" in names:
                pngs[k] = process(k, z.read(f"assets/{k}.png"))
        legal = [d for d in (data.get("legal") or []) if isinstance(d, dict) and d.get("kind") in LEGAL_KINDS]
    except (zipfile.BadZipFile, ValueError, KeyError, BrandingError) as exc:
        raise HTTPException(status_code=422, detail=f"Не удалось прочитать архив: {exc}") from None
    svc = request.app.state.settings_svc
    try:
        await svc.preview(db, "site", settings)
    except SettingsError as exc:
        raise HTTPException(status_code=422, detail=f"Настройки в архиве не прошли проверку: {exc}") from None
    versions = {f: "" for f in VERSION_FIELDS}
    for k in KINDS:
        if k in pngs:
            versions[ASSET_FIELD[k]] = request.app.state.branding.save(k, pngs[k])
        else:
            request.app.state.branding.delete(k)
    await svc.update(db, "site", {**settings, **versions}, actor=su.display_name)
    for d in legal:
        row = await _doc(db, d["kind"])
        row.title, row.draft_md, row.content_md = str(d.get("title") or "")[:200], str(d.get("draft_md") or "")[:MAX_DOC], str(d.get("content_md") or "")[:MAX_DOC]
        row.published_title, row.version = str(d.get("published_title") or "")[:200], int(d.get("version") or 0)
        row.published, row.require_consent, row.published_at = bool(d.get("published")), bool(d.get("require_consent")), _dt(d.get("published_at"))
        row.updated_by = su.display_name
        for r in (await db.execute(select(LegalRevision).where(LegalRevision.kind == d["kind"]))).scalars().all():
            await db.delete(r)
        for r in d.get("revisions") or []:
            db.add(LegalRevision(kind=d["kind"], version=int(r.get("version") or 0), title=str(r.get("title") or "")[:200], content_md=str(r.get("content_md") or "")[:MAX_DOC],
                                 published_at=_dt(r.get("published_at")) or utcnow(), published_by=r.get("published_by"), unpublished_at=_dt(r.get("unpublished_at"))))
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="site.import", target_type="site", target_id="site", ip=client_ip(request),
                      details={"settings": sorted(settings), "assets": sorted(pngs), "documents": [d["kind"] for d in legal]})
    await db.commit()
    return {"ok": True, "assets": sorted(pngs), "documents": [d["kind"] for d in legal]}


@router.post("/admin/site/reset")
async def reset_site(request: Request, body: dict[str, Any] = Body(...), su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Вернуть значения по умолчанию для группы полей: brand | org | contacts | texts (изображения удаляются вместе с группой brand)."""
    groups = {
        "brand": ("name", "short_name", "subtitle", "description", "primary_color", "accent2_color", "theme", *VERSION_FIELDS),
        "org": ("org_full", "org_short", "org_url", "org_unit", "org_legal_name", "org_address", "footer_text"),
        "contacts": ("support_mode", "support_email", "support_phone", "support_url", "portal_url", "support_text"),
        "texts": ("welcome_text", "guest_text", "recording_text"),
    }
    g = body.get("group")
    if g not in groups:
        raise HTTPException(status_code=422, detail="group: brand | org | contacts | texts")
    defaults = GROUPS["site"]().model_dump()
    if g == "brand":
        for k in KINDS:
            request.app.state.branding.delete(k)
    await request.app.state.settings_svc.update(db, "site", {f: defaults[f] for f in groups[g]}, actor=su.display_name)
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="site.reset", target_type="site", target_id=g, ip=client_ip(request), details={"group": g})
    await db.commit()
    return {"ok": True}
