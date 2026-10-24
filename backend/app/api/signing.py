"""«Администрирование → Windows-клиент → Подпись и корпоративные сборки» и протокол подписывающего агента.

Устройство (docs/DESKTOP_SIGNING.md):
• закрытые ключи подписи живут только в хранилище Windows на узле агента (desktop/signing/Peregovorka-Signer.ps1); сервер хранит сведения о сертификатах,
  их открытую часть (CER), задания и готовые пакеты;
• агент авторизуется своим токеном (на сервере — только SHA-256), сам опрашивает сервер и выполняет лишь задания фиксированных видов с данными
  (а не командами): создать собственный сертификат, зарегистрировать сертификат организации по отпечатку, проверить, собрать корпоративный выпуск;
• исходные пакеты агент скачивает из своего, локально заданного источника выпусков и сверяет с SHA256SUMS и подписью издателя — сервер не может
  подсунуть ему произвольный файл или адрес;
• корпоративный канал обновлений — копия подписанного издателем манифеста и установщика (подпись Ed25519 проверяется здесь, при публикации, и в клиенте).
Все действия администратора — в журнале аудита; пароли, PFX и закрытые ключи через браузер и этот сервер не передаются вовсе.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import shutil
import uuid
from datetime import datetime, timezone

import httpx
from cryptography import x509
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import SessionUser, client_ip, get_db, require_admin
from ..models import SigningAgent, SigningCert, SigningJob, utcnow
from ..services.audit import write_audit

router = APIRouter(tags=["admin"])
_NO_STORE = {"Cache-Control": "no-store"}
AGENT_PREFIX = "pgs_"
ENROLL_TTL = 3600
KINDS = ("create_cert", "register_cert", "check_cert", "build")
FILE_RE = re.compile(r"^[A-Za-z0-9._()-][A-Za-z0-9._ ()-]{0,119}$")
FILE_EXT = (".exe", ".msi", ".cer", ".reg", ".txt", ".json", ".md")
MAX_UPLOAD = 600 * 1024 * 1024
VERSION_RE = re.compile(r"^\d{1,3}\.\d{1,3}\.\d{1,4}$")
THUMB_RE = re.compile(r"^[0-9A-F]{40}$")
CODE_SIGNING = "1.3.6.1.5.5.7.3.3"


def _dir(request: Request, *parts: str) -> str:
    return os.path.join(request.app.state.settings.data_dir, "signing", *parts)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _iso(d: datetime | None) -> str | None:
    return d.astimezone(timezone.utc).isoformat() if d else None


def _origin_https(url: str) -> str | None:
    m = re.fullmatch(r"https://([a-z0-9.-]+|\[[0-9a-f:]+\])(:\d{1,5})?/?", (url or "").strip().lower())
    return f"https://{m.group(1)}{m.group(2) or ''}" if m else None


# ---------------------------------------------------------------------------------------------------- представление для интерфейса

def _agent_view(a: SigningAgent) -> dict:
    online = bool(a.last_seen_at and (utcnow() - a.last_seen_at).total_seconds() < 90)
    return {"id": str(a.id), "name": a.name, "host": a.host, "version": a.version, "info": a.info or {}, "created_at": _iso(a.created_at),
            "last_seen_at": _iso(a.last_seen_at), "online": online and not a.revoked_at, "revoked": bool(a.revoked_at)}


def _cert_state(c: SigningCert) -> str:
    now = utcnow()
    chk = c.check or {}
    if c.retired_at:
        return "retired"
    if c.not_after <= now:
        return "expired"
    if c.not_before > now:
        return "not_yet_valid"
    if chk and (not chk.get("code_signing") or not chk.get("has_private_key")):
        return "invalid"
    if chk and chk.get("can_sign") is False:
        return "cannot_sign"
    if c.kind == "corporate" and chk and not chk.get("chain_ok"):
        return "untrusted_chain"
    return "ok"


def _cert_view(c: SigningCert) -> dict:
    days = int((c.not_after - utcnow()).total_seconds() // 86400)
    return {"id": str(c.id), "agent_id": str(c.agent_id), "kind": c.kind, "thumbprint": c.thumbprint, "sha256": c.sha256, "subject": c.subject, "issuer": c.issuer,
            "serial": c.serial, "not_before": _iso(c.not_before), "not_after": _iso(c.not_after), "days_left": days, "key_alg": c.key_alg, "sig_alg": c.sig_alg,
            "check": c.check or {}, "checked_at": _iso(c.checked_at), "active": c.active, "state": _cert_state(c), "has_cer": bool(c.cer), "retired": bool(c.retired_at)}


def _job_view(j: SigningJob) -> dict:
    r = j.result or {}
    return {"id": str(j.id), "kind": j.kind, "status": j.status, "agent_id": str(j.agent_id), "cert_id": str(j.cert_id) if j.cert_id else None,
            "params": j.params, "progress": j.progress, "error": j.error, "created_by": j.created_by_name, "created_at": _iso(j.created_at),
            "started_at": _iso(j.started_at), "finished_at": _iso(j.finished_at),
            "files": [{k: f.get(k) for k in ("name", "size", "sha256", "signature")} for f in r.get("files", [])]}


# ---------------------------------------------------------------------------------------------------- раздел администратора

@router.get("/admin/signing")
async def overview(request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    agents = (await db.execute(select(SigningAgent).order_by(SigningAgent.created_at))).scalars().all()
    certs = (await db.execute(select(SigningCert).order_by(SigningCert.created_at.desc()))).scalars().all()
    jobs = (await db.execute(select(SigningJob).order_by(SigningJob.created_at.desc()).limit(50))).scalars().all()
    return JSONResponse({"agents": [_agent_view(a) for a in agents], "certs": [_cert_view(c) for c in certs], "jobs": [_job_view(j) for j in jobs],
                         "channel": await _channel_state(request, db)}, headers=_NO_STORE)


@router.post("/admin/signing/enroll")
async def enroll_code(request: Request, body: dict = Body(default={}), su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Одноразовый код подключения агента (действует час). Показывается один раз; на сервере — только его хеш."""
    name = str(body.get("name") or "").strip()[:120] or "Подписывающий узел"
    code = "PGE-" + base64.b32encode(secrets.token_bytes(10)).decode().rstrip("=")
    await request.app.state.redis.set(f"signing:enroll:{_hash(code)}", json.dumps({"name": name, "by": str(su.user_id)}), ex=ENROLL_TTL)
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="signing.enroll_code", target_type="signing_agent", target_id=name, ip=client_ip(request))
    await db.commit()
    return JSONResponse({"code": code, "expires_in": ENROLL_TTL, "server": request.app.state.settings.public_origin}, headers=_NO_STORE)


@router.delete("/admin/signing/agents/{agent_id}")
async def revoke_agent(agent_id: uuid.UUID, request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    a = await db.get(SigningAgent, agent_id)
    if a is None:
        raise HTTPException(status_code=404, detail="Агент не найден")
    a.revoked_at = utcnow()
    await db.execute(update(SigningJob).where(SigningJob.agent_id == a.id, SigningJob.status.in_(("queued", "running"))).values(status="cancelled", finished_at=utcnow(),
                                                                                                                                  error="Агент отключён"))
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="signing.agent_revoke", target_type="signing_agent", target_id=str(a.id),
                      ip=client_ip(request), details={"name": a.name})
    await db.commit()
    return {"ok": True}


async def _agent(db: AsyncSession, agent_id) -> SigningAgent:
    try:
        a = await db.get(SigningAgent, uuid.UUID(str(agent_id)))
    except ValueError:
        a = None
    if a is None or a.revoked_at:
        raise HTTPException(status_code=422, detail="Выберите подключённый подписывающий узел")
    return a


async def _cert(db: AsyncSession, cert_id, *, usable: bool = False) -> SigningCert:
    try:
        c = await db.get(SigningCert, uuid.UUID(str(cert_id)))
    except ValueError:
        c = None
    if c is None:
        raise HTTPException(status_code=404, detail="Сертификат не найден")
    if usable and _cert_state(c) != "ok":
        raise HTTPException(status_code=409, detail=f"Сертификатом нельзя подписывать: {_STATE_TEXT[_cert_state(c)]}")
    return c


_STATE_TEXT = {"ok": "действует", "retired": "заменён", "expired": "срок действия истёк", "not_yet_valid": "срок действия ещё не начался",
               "invalid": "нет назначения «Подпись кода» или закрытого ключа", "cannot_sign": "пробная подпись не удалась", "untrusted_chain": "цепочка доверия не проверена"}


async def _job(request: Request, db: AsyncSession, su: SessionUser, kind: str, agent: SigningAgent, params: dict, cert: SigningCert | None = None) -> SigningJob:
    busy = (await db.execute(select(SigningJob).where(SigningJob.agent_id == agent.id, SigningJob.kind == kind, SigningJob.status.in_(("queued", "running"))))).scalars().first()
    if busy is not None and kind != "check_cert":
        raise HTTPException(status_code=409, detail="Такое задание этому узлу уже поставлено и ещё не выполнено")
    j = SigningJob(kind=kind, agent_id=agent.id, cert_id=cert.id if cert else None, params=params, created_by_user_id=su.user_id, created_by_name=su.display_name)
    db.add(j)
    await db.flush()
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action=f"signing.{kind}", target_type="signing_job", target_id=str(j.id),
                      ip=client_ip(request), details={"agent": agent.name, **{k: v for k, v in params.items() if k in ("subject", "thumbprint", "version", "org_name", "server_url", "types")}})
    await db.commit()
    return j


@router.post("/admin/signing/certs/create")
async def create_cert(request: Request, body: dict = Body(...), su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """«Создать собственный сертификат»: агент создаёт самоподписанный сертификат Code Signing в хранилище своего узла (ключ не экспортируется)."""
    agent = await _agent(db, body.get("agent_id"))
    subject = re.sub(r"[,=+<>#;\"\\]", " ", str(body.get("subject") or "")).strip()[:120]
    if len(subject) < 2:
        raise HTTPException(status_code=422, detail="Укажите название издателя")
    years = int(body.get("years") or 3)
    if years not in (1, 2, 3, 5):
        raise HTTPException(status_code=422, detail="Срок действия: 1, 2, 3 или 5 лет")
    j = await _job(request, db, su, "create_cert", agent, {"subject": subject, "years": years})
    return _job_view(j)


@router.post("/admin/signing/certs/register")
async def register_cert(request: Request, body: dict = Body(...), su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """«Подключить сертификат компании»: сертификат уже установлен в хранилище Windows узла (выпущен УЦ организации); агент находит его по отпечатку."""
    agent = await _agent(db, body.get("agent_id"))
    tp = re.sub(r"[^0-9A-Fa-f]", "", str(body.get("thumbprint") or "")).upper()
    if not THUMB_RE.fullmatch(tp):
        raise HTTPException(status_code=422, detail="Отпечаток — 40 шестнадцатеричных символов (SHA-1, как в свойствах сертификата Windows)")
    j = await _job(request, db, su, "register_cert", agent, {"thumbprint": tp})
    return _job_view(j)


@router.post("/admin/signing/certs/{cert_id}/check")
async def check_cert(cert_id: uuid.UUID, request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """«Проверить сертификат»: назначение, срок, цепочка, наличие ключа и пробная подпись — на узле агента."""
    c = await _cert(db, cert_id)
    agent = await _agent(db, c.agent_id)
    j = await _job(request, db, su, "check_cert", agent, {"thumbprint": c.thumbprint}, c)
    return _job_view(j)


@router.post("/admin/signing/certs/{cert_id}/activate")
async def activate_cert(cert_id: uuid.UUID, request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """«Заменить сертификат»: новые сборки подписываются выбранным. Уже установленные клиенты продолжают работать: обновление сверяет издателя
    по имени субъекта, поэтому новый сертификат выпускают на того же издателя (docs/DESKTOP_SIGNING.md, «Замена сертификата»)."""
    c = await _cert(db, cert_id, usable=True)
    prev = (await db.execute(select(SigningCert).where(SigningCert.active.is_(True)))).scalars().all()
    for p in prev:
        p.active = False
        if p.id != c.id:
            p.retired_at = utcnow()
    c.active = True
    c.retired_at = None
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="signing.cert_activate", target_type="signing_cert", target_id=c.thumbprint,
                      ip=client_ip(request), details={"subject": c.subject, "replaced": [p.thumbprint for p in prev if p.id != c.id],
                                                      "same_subject": all(p.subject == c.subject for p in prev)})
    await db.commit()
    return _cert_view(c)


@router.get("/admin/signing/certs/{cert_id}/cer")
async def download_cer(cert_id: uuid.UUID, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """«Скачать публичный сертификат»: только открытая часть (DER .cer) — для распространения доверия групповой политикой."""
    c = await _cert(db, cert_id)
    if not c.cer:
        raise HTTPException(status_code=404, detail="Открытая часть сертификата ещё не получена от агента")
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", c.subject.split(",")[0].replace("CN=", "")).strip("-") or "publisher"
    return Response(c.cer, media_type="application/pkix-cert", headers={"Content-Disposition": f'attachment; filename="{name}.cer"', **_NO_STORE})


@router.post("/admin/signing/builds")
async def create_build(request: Request, body: dict = Body(...), su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """«Создать корпоративный выпуск»: агент берёт проверенную версию клиента из своего источника, вписывает настройки организации в MSI (свойства
    установщика — без отдельного кода приложения), подписывает EXE/MSI сертификатом узла, проверяет подписи и возвращает пакеты и SHA-256."""
    cert = await _cert(db, body.get("cert_id"), usable=True)
    agent = await _agent(db, cert.agent_id)
    version = str(body.get("version") or "").strip()
    if not VERSION_RE.fullmatch(version):
        raise HTTPException(status_code=422, detail="Версия клиента — X.Y.Z")
    server = _origin_https(str(body.get("server_url") or ""))
    if not server:
        raise HTTPException(status_code=422, detail="Адрес сервера — https://имя[:порт], без пути")
    org = re.sub(r"[|;\r\n\t]", " ", str(body.get("org_name") or "")).strip()[:60]
    if not org:
        raise HTTPException(status_code=422, detail="Укажите название организации")
    types = [t for t in ("exe", "msi") if t in (body.get("types") or [])]
    if not types:
        raise HTTPException(status_code=422, detail="Выберите хотя бы один тип установщика")
    upd = str(body.get("update_policy") or "notify")
    autostart = str(body.get("autostart") or "tray")
    if upd not in ("auto", "notify", "off", "channel") or autostart not in ("off", "tray", "window"):
        raise HTTPException(status_code=422, detail="Недопустимая политика обновлений или автозапуска")
    params = {"version": version, "org_name": org, "server_url": server, "types": types, "update_policy": upd, "autostart": autostart,
              "allow_add": bool(body.get("allow_add")), "thumbprint": cert.thumbprint, "cert_kind": cert.kind,
              "update_url": f"{request.app.state.settings.public_origin}/api/v1/desktop/channel/update.json" if upd == "channel" else ""}
    j = await _job(request, db, su, "build", agent, params, cert)
    return _job_view(j)


@router.get("/admin/signing/jobs/{job_id}")
async def get_job(job_id: uuid.UUID, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    j = await db.get(SigningJob, job_id)
    if j is None:
        raise HTTPException(status_code=404, detail="Задание не найдено")
    return JSONResponse(_job_view(j), headers=_NO_STORE)


@router.post("/admin/signing/jobs/{job_id}/cancel")
async def cancel_job(job_id: uuid.UUID, request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    j = await db.get(SigningJob, job_id)
    if j is None or j.status not in ("queued", "running"):
        raise HTTPException(status_code=409, detail="Задание уже завершено")
    j.status, j.finished_at, j.error = "cancelled", utcnow(), "Отменено администратором"
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="signing.job_cancel", target_type="signing_job", target_id=str(j.id), ip=client_ip(request))
    await db.commit()
    return _job_view(j)


@router.get("/admin/signing/jobs/{job_id}/files/{name}")
async def download_file(job_id: uuid.UUID, name: str, request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    j = await db.get(SigningJob, job_id)
    if j is None or j.status != "done" or not FILE_RE.fullmatch(name) or name not in {f["name"] for f in (j.result or {}).get("files", [])}:
        raise HTTPException(status_code=404, detail="Файл не найден")
    path = _dir(request, str(j.id), name)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="Файл не найден")
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="signing.download", target_type="signing_job", target_id=str(j.id),
                      ip=client_ip(request), details={"file": name})
    await db.commit()
    return FileResponse(path, filename=name, media_type="application/octet-stream", headers=_NO_STORE)


# ---------------------------------------------------------------------------------------------------- протокол агента

async def _agent_auth(request: Request, db: AsyncSession) -> SigningAgent:
    # агент — не браузер: запрос с Origin (страница, расширение) отклоняется, cookie не используются
    if request.headers.get("origin") is not None:
        raise HTTPException(status_code=403, detail="Недоступно из браузера")
    h = request.headers.get("authorization", "")
    tok = h[7:].strip() if h.lower().startswith("bearer ") else ""
    if not tok.startswith(AGENT_PREFIX):
        raise HTTPException(status_code=401, detail="Нужен токен агента")
    a = (await db.execute(select(SigningAgent).where(SigningAgent.token_hash == _hash(tok)))).scalars().first()
    if a is None or a.revoked_at:
        raise HTTPException(status_code=401, detail="Агент отключён или токен недействителен")
    a.last_seen_at = utcnow()
    a.last_ip = client_ip(request)
    return a


async def _own_job(db: AsyncSession, agent: SigningAgent, job_id: uuid.UUID) -> SigningJob:
    j = await db.get(SigningJob, job_id)
    if j is None or j.agent_id != agent.id:
        raise HTTPException(status_code=404, detail="Задание не найдено")
    if j.status != "running":
        raise HTTPException(status_code=409, detail="Задание не выполняется (отменено или завершено)")
    return j


def _clean_info(info) -> dict:
    if not isinstance(info, dict):
        return {}
    return {str(k)[:40]: str(v)[:200] for k, v in list(info.items())[:20] if isinstance(v, (str, int, float, bool))}


@router.post("/signing/agent/enroll")
async def agent_enroll(request: Request, body: dict = Body(...), db: AsyncSession = Depends(get_db)):
    if request.headers.get("origin") is not None:
        raise HTTPException(status_code=403, detail="Недоступно из браузера")
    code = str(body.get("code") or "").strip().upper()
    raw = await request.app.state.redis.getdel(f"signing:enroll:{_hash(code)}") if code else None
    if not raw:
        raise HTTPException(status_code=401, detail="Код подключения недействителен или истёк — получите новый в разделе администрирования")
    meta = json.loads(raw)
    tok = AGENT_PREFIX + secrets.token_urlsafe(32)
    a = SigningAgent(name=meta["name"], host=str(body.get("host") or "")[:200], token_hash=_hash(tok), version=str(body.get("version") or "")[:40],
                     info=_clean_info(body.get("info")), created_by_user_id=uuid.UUID(meta["by"]), last_seen_at=utcnow(), last_ip=client_ip(request))
    db.add(a)
    await db.flush()
    await write_audit(db, actor_user_id=None, actor_name=f"агент {a.name}", action="signing.agent_enrolled", target_type="signing_agent", target_id=str(a.id),
                      ip=client_ip(request), details={"host": a.host}, actor_type="service")
    await db.commit()
    return JSONResponse({"agent_id": str(a.id), "token": tok, "name": a.name}, headers=_NO_STORE)


@router.post("/signing/agent/poll")
async def agent_poll(request: Request, body: dict = Body(default={}), db: AsyncSession = Depends(get_db)):
    """Агент спрашивает работу (раз в несколько секунд). Задание выдаётся одно, из очереди этого агента, и переходит в «выполняется»."""
    a = await _agent_auth(request, db)
    if body.get("version"):
        a.version = str(body["version"])[:40]
    if body.get("info"):
        a.info = _clean_info(body["info"])
    j = (await db.execute(select(SigningJob).where(SigningJob.agent_id == a.id, SigningJob.status == "queued").order_by(SigningJob.created_at).limit(1))).scalars().first()
    if j is not None:
        j.status, j.started_at, j.progress = "running", utcnow(), "Принято агентом"
    await db.commit()
    return JSONResponse({"job": {"id": str(j.id), "kind": j.kind, "params": j.params} if j else None}, headers=_NO_STORE)


@router.post("/signing/agent/jobs/{job_id}/progress")
async def agent_progress(job_id: uuid.UUID, request: Request, body: dict = Body(...), db: AsyncSession = Depends(get_db)):
    a = await _agent_auth(request, db)
    j = await _own_job(db, a, job_id)
    j.progress = str(body.get("text") or "")[:200]
    await db.commit()
    return {"ok": True}


@router.put("/signing/agent/jobs/{job_id}/files/{name}")
async def agent_upload(job_id: uuid.UUID, name: str, request: Request, db: AsyncSession = Depends(get_db)):
    a = await _agent_auth(request, db)
    j = await _own_job(db, a, job_id)
    if j.kind != "build" or not FILE_RE.fullmatch(name) or not name.lower().endswith(FILE_EXT) or name.startswith("."):
        raise HTTPException(status_code=422, detail="Недопустимое имя файла")
    d = _dir(request, str(j.id))
    os.makedirs(d, exist_ok=True)
    tmp = os.path.join(d, f".{name}.part")
    h, size = hashlib.sha256(), 0
    with open(tmp, "wb") as f:
        async for chunk in request.stream():
            size += len(chunk)
            if size > MAX_UPLOAD:
                f.close()
                os.remove(tmp)
                raise HTTPException(status_code=413, detail="Файл слишком большой")
            h.update(chunk)
            f.write(chunk)
    os.replace(tmp, os.path.join(d, name))
    up = dict((j.result or {}).get("uploaded", {}))
    up[name] = {"size": size, "sha256": h.hexdigest()}
    j.result = {**(j.result or {}), "uploaded": up}
    await db.commit()
    return {"name": name, "size": size, "sha256": h.hexdigest()}


def _parse_cer(der: bytes) -> dict:
    """Сведения берутся из самого сертификата (DER), а не со слов агента."""
    c = x509.load_der_x509_certificate(der)
    try:
        ekus = [u.dotted_string for u in c.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value]
    except x509.ExtensionNotFound:
        ekus = []
    pk = c.public_key()
    key = f"{type(pk).__name__.replace('PublicKey', '').replace('_', '')} {getattr(pk, 'key_size', '')}".strip()
    dn = lambda n: ", ".join(a.rfc4514_string() for a in n)[:400]        # noqa: E731 — порядок как в свойствах сертификата Windows (CN первым)
    return {"subject": dn(c.subject), "issuer": dn(c.issuer), "serial": format(c.serial_number, "X")[:80],
            "not_before": c.not_valid_before_utc, "not_after": c.not_valid_after_utc, "sha256": hashlib.sha256(der).hexdigest().upper(),
            "thumbprint": hashlib.sha1(der).hexdigest().upper(), "ekus": ekus, "key_alg": key[:60],  # noqa: S324 — отпечаток Windows (SHA-1) для поиска в хранилище
            "sig_alg": (c.signature_algorithm_oid._name or c.signature_algorithm_oid.dotted_string)[:60], "self_signed": c.issuer == c.subject}


@router.post("/signing/agent/jobs/{job_id}/finish")
async def agent_finish(job_id: uuid.UUID, request: Request, body: dict = Body(...), db: AsyncSession = Depends(get_db)):
    a = await _agent_auth(request, db)
    j = await _own_job(db, a, job_id)
    ok = body.get("ok") is True
    j.finished_at = utcnow()
    if not ok:
        j.status, j.error = "failed", str(body.get("error") or "Ошибка на подписывающем узле")[:500]
        await _finish_audit(db, a, j, request)
        await db.commit()
        return {"ok": True}
    try:
        if j.kind in ("create_cert", "register_cert", "check_cert"):
            await _apply_cert(db, a, j, body)
        elif j.kind == "build":
            _apply_build(request, j, body)
        j.status, j.progress = "done", "Готово"
    except HTTPException as exc:
        j.status, j.error = "failed", str(exc.detail)[:500]
    await _finish_audit(db, a, j, request)
    await db.commit()
    return {"ok": True, "status": j.status}


async def _finish_audit(db, a: SigningAgent, j: SigningJob, request: Request) -> None:
    await write_audit(db, actor_user_id=None, actor_name=f"агент {a.name}", action=f"signing.{j.kind}_{j.status}", target_type="signing_job", target_id=str(j.id),
                      ip=client_ip(request), details={"error": j.error} if j.error else None, result="success" if j.status == "done" else "failure", actor_type="service")


async def _apply_cert(db: AsyncSession, a: SigningAgent, j: SigningJob, body: dict) -> None:
    info = body.get("cert") or {}
    try:
        der = base64.b64decode(str(info.get("cer_b64") or ""), validate=True)
        meta = _parse_cer(der)
    except Exception:  # noqa: BLE001 — любой мусор вместо сертификата
        raise HTTPException(status_code=422, detail="Агент не прислал открытую часть сертификата") from None
    want = (j.params or {}).get("thumbprint")
    if want and meta["thumbprint"] != want:
        raise HTTPException(status_code=422, detail="Отпечаток присланного сертификата не совпадает с заданием")
    raw = info.get("check") if isinstance(info.get("check"), dict) else {}
    check = {k: bool(raw.get(k)) for k in ("has_private_key", "chain_ok", "can_sign", "key_exportable", "trusted_on_node")}
    check["code_signing"] = CODE_SIGNING in meta["ekus"]
    check["time_valid"] = meta["not_before"] <= utcnow() < meta["not_after"]
    check["chain_status"] = str(raw.get("chain_status") or "")[:300]
    check["self_signed"] = meta["self_signed"]
    c = (await db.execute(select(SigningCert).where(SigningCert.agent_id == a.id, SigningCert.thumbprint == meta["thumbprint"]))).scalars().first()
    if c is None:
        c = SigningCert(agent_id=a.id, kind="self" if j.kind == "create_cert" else "corporate", thumbprint=meta["thumbprint"], sha256=meta["sha256"], subject=meta["subject"],
                        issuer=meta["issuer"], serial=meta["serial"], not_before=meta["not_before"], not_after=meta["not_after"], key_alg=meta["key_alg"], sig_alg=meta["sig_alg"])
        db.add(c)
    c.cer, c.check, c.checked_at = der, check, utcnow()
    await db.flush()
    j.cert_id = c.id
    j.result = {"cert_id": str(c.id), "state": _cert_state(c)}
    if not check["code_signing"]:
        raise HTTPException(status_code=422, detail="У сертификата нет назначения «Подпись кода» (1.3.6.1.5.5.7.3.3) — подписывать им нельзя")
    if not check["time_valid"]:
        raise HTTPException(status_code=422, detail="Срок действия сертификата истёк или ещё не начался")
    if not check["has_private_key"]:
        raise HTTPException(status_code=422, detail="На узле нет закрытого ключа этого сертификата")


def _apply_build(request: Request, j: SigningJob, body: dict) -> None:
    files = body.get("files")
    up = (j.result or {}).get("uploaded", {})
    if not isinstance(files, list) or not files:
        raise HTTPException(status_code=422, detail="Агент не прислал пакеты")
    out = []
    thumb = (j.params or {}).get("thumbprint")
    for f in files[:20]:
        name = str(f.get("name") or "")
        got = up.get(name)
        if not got or got["sha256"] != str(f.get("sha256") or "").lower():
            raise HTTPException(status_code=422, detail=f"Файл {name or '?'} не загружен или его SHA-256 не совпадает")
        sig = f.get("signature") if isinstance(f.get("signature"), dict) else None
        if name.lower().endswith((".exe", ".msi")):
            if not sig or sig.get("status") != "Valid" or str(sig.get("thumbprint") or "").upper() != thumb:
                raise HTTPException(status_code=422, detail=f"Подпись {name} не подтверждена: {(sig or {}).get('status', 'нет подписи')}")
        out.append({"name": name, "size": got["size"], "sha256": got["sha256"],
                    "signature": {k: str(sig.get(k))[:300] for k in ("status", "thumbprint", "subject", "timestamp")} if sig else None})
    j.result = {**(j.result or {}), "files": out, "source": str(body.get("source") or "")[:300]}


# ---------------------------------------------------------------------------------------------------- корпоративный канал обновлений

def _channel_dir(request: Request) -> str:
    return os.path.join(request.app.state.settings.data_dir, "desktop-channel")


def _verify_manifest(body: bytes, sig_b64: str, keys: str) -> dict:
    sig = base64.b64decode(sig_b64.strip())
    ok = False
    for k in [x for x in re.split(r"[\s,]+", keys) if x]:
        try:
            Ed25519PublicKey.from_public_bytes(base64.b64decode(k)).verify(sig, body)
            ok = True
            break
        except Exception:  # noqa: BLE001 — неверная подпись или ключ
            continue
    if not ok:
        raise HTTPException(status_code=422, detail="Подпись манифеста не сходится с ключом издателя — версия не принята")
    m = json.loads(body)
    if m.get("product") != "peregovorka-desktop" or not VERSION_RE.fullmatch(str(m.get("version"))) or not m.get("files"):
        raise HTTPException(status_code=422, detail="Манифест обновления повреждён")
    return m


async def _channel_state(request: Request, db: AsyncSession) -> dict:
    s = await request.app.state.settings_svc.get(db, "desktop_channel")
    meta = {}
    try:
        with open(os.path.join(_channel_dir(request), "channel.json"), encoding="utf-8") as f:
            meta = json.load(f)
    except (OSError, ValueError):
        pass
    return {"feed_url": s.feed_url, "pubkeys": s.pubkeys, "published": meta or None,
            "client_url": f"{request.app.state.settings.public_origin}/api/v1/desktop/channel/update.json"}


@router.post("/admin/signing/channel/check")
async def channel_check(request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Что предлагает канал издателя сейчас: версия и проверка подписи — без публикации."""
    s = await request.app.state.settings_svc.get(db, "desktop_channel")
    if not s.pubkeys:
        raise HTTPException(status_code=409, detail="Укажите открытый ключ проверки обновлений издателя")
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as c:
        try:
            body = (await c.get(s.feed_url)).raise_for_status().content
            sig = (await c.get(s.feed_url + ".sig")).raise_for_status().text
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"Канал издателя недоступен: {type(exc).__name__}") from None
    m = _verify_manifest(body, sig, s.pubkeys)
    return {"version": m["version"], "released": m.get("released"), "notes": (m.get("notes") or "")[:4000], "verified": True}


@router.post("/admin/signing/channel/publish")
async def channel_publish(request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Опубликовать текущую версию издателя в корпоративный канал этого сервера: манифест и подпись — как есть (подпись издателя), установщик —
    рядом (клиент с политикой UpdateUrl берёт его отсюда и сверяет SHA-256 из подписанного манифеста)."""
    s = await request.app.state.settings_svc.get(db, "desktop_channel")
    if not s.pubkeys:
        raise HTTPException(status_code=409, detail="Укажите открытый ключ проверки обновлений издателя")
    d = _channel_dir(request)
    tmp = d + ".new"
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    async with httpx.AsyncClient(timeout=httpx.Timeout(30, read=300), follow_redirects=True) as c:
        try:
            body = (await c.get(s.feed_url)).raise_for_status().content
            sig = (await c.get(s.feed_url + ".sig")).raise_for_status().text
            m = _verify_manifest(body, sig, s.pubkeys)
            f = next((x for x in m["files"] if x.get("kind") == "nsis"), None)
            if not f or not FILE_RE.fullmatch(str(f.get("name"))) or not str(f.get("url", "")).startswith("https://"):
                raise HTTPException(status_code=422, detail="В манифесте нет установщика")
            h = hashlib.sha256()
            async with c.stream("GET", f["url"]) as r:
                r.raise_for_status()
                with open(os.path.join(tmp, f["name"]), "wb") as out:
                    async for chunk in r.aiter_bytes():
                        h.update(chunk)
                        out.write(chunk)
        except httpx.HTTPError as exc:
            shutil.rmtree(tmp, ignore_errors=True)
            raise HTTPException(status_code=502, detail=f"Канал издателя недоступен: {type(exc).__name__}") from None
    if h.hexdigest() != f["sha256"]:
        shutil.rmtree(tmp, ignore_errors=True)
        raise HTTPException(status_code=422, detail="Установщик не совпал с подписанным манифестом — публикация отменена")
    with open(os.path.join(tmp, "update.json"), "wb") as out:
        out.write(body)
    with open(os.path.join(tmp, "update.json.sig"), "w", encoding="ascii") as out:
        out.write(sig)
    meta = {"version": m["version"], "file": f["name"], "sha256": f["sha256"], "published_at": utcnow().isoformat(), "by": su.display_name}
    with open(os.path.join(tmp, "channel.json"), "w", encoding="utf-8") as out:
        json.dump(meta, out, ensure_ascii=False)
    old = d + ".old"
    shutil.rmtree(old, ignore_errors=True)
    if os.path.isdir(d):
        os.replace(d, old)
    os.replace(tmp, d)
    shutil.rmtree(old, ignore_errors=True)
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="signing.channel_publish", target_type="desktop_channel", target_id=m["version"],
                      ip=client_ip(request), details={"sha256": f["sha256"]})
    await db.commit()
    return meta


@router.get("/desktop/channel/{name}")
async def channel_file(name: str, request: Request):
    """Корпоративный канал для клиентов (без входа: проверка обновлений идёт до авторизации). Подлинность — подпись издателя, а не этот сервер."""
    d = _channel_dir(request)
    try:
        with open(os.path.join(d, "channel.json"), encoding="utf-8") as f:
            meta = json.load(f)
    except (OSError, ValueError):
        raise HTTPException(status_code=404, detail="Корпоративный канал обновлений не опубликован") from None
    if name not in ("update.json", "update.json.sig", meta.get("file")):
        raise HTTPException(status_code=404, detail="Нет такого файла")
    return FileResponse(os.path.join(d, name), media_type="application/json" if name == "update.json" else "application/octet-stream",
                        headers={"Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff"})
