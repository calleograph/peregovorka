"""Администрирование → Трансляции → «Узлы CDN»: подключение серверов по SSH, установка, состояние, проверка, обновление, удаление, расчёт оборудования.
Устройство — services/cdn.py (подпись, выбор узла, опрос), services/cdn_ssh.py (SSH), services/cdn_jobs.py (задания), пакет узла — assets/cdn_node."""
from __future__ import annotations

import ipaddress
import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import SessionUser, client_ip, get_db, require_admin
from ..models import CdnNode
from ..services import cdn_ssh
from ..services.audit import write_audit
from ..services.cdn import LIVE_KBPS_DEFAULT, NODE_VERSION, assess, kid_of, new_node, public_keys, recommend

router = APIRouter(prefix="/admin/cdn", tags=["admin"])

NAME_RE = re.compile(r"^(?=.{1,253}$)([A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$")
USER_RE = re.compile(r"^[a-z_][a-z0-9_.-]{0,31}$")
QUALITY_KBPS = {"720p": 3000, "1080p": 5000, "1440p": 9000}


def _host_ok(v: str) -> str:
    v = (v or "").strip().lower()
    try:
        ipaddress.ip_address(v)
        return v
    except ValueError:
        pass
    if not NAME_RE.match(v) and v != "localhost":
        raise ValueError("Адрес сервера: имя или IP-адрес")
    return v


class Creds(BaseModel):
    password: str | None = Field(default=None, max_length=512)
    private_key: str | None = Field(default=None, max_length=20000)
    tls_cert_pem: str | None = Field(default=None, max_length=40000)
    tls_key_pem: str | None = Field(default=None, max_length=20000)


class HostKeyIn(BaseModel):
    ssh_host: str
    ssh_port: int = Field(default=22, ge=1, le=65535)

    @field_validator("ssh_host")
    @classmethod
    def _host(cls, v: str) -> str:
        return _host_ok(v)


class NodeIn(Creds):
    name: str = Field(min_length=1, max_length=80)
    public_name: str
    region: str | None = Field(default=None, max_length=60)
    ssh_host: str
    ssh_port: int = Field(default=22, ge=1, le=65535)
    ssh_user: str = "root"
    host_key: str = Field(min_length=20, max_length=2000)
    fingerprint: str = Field(min_length=10, max_length=100)          # отпечаток, который администратор видел и подтвердил
    tls_mode: Literal["acme", "files", "selfsigned"] = "acme"
    acme_email: str | None = Field(default=None, max_length=200)
    quality: Literal["720p", "1080p", "1440p"] = "1080p"
    manual: bool = False                                               # установка скриптом вручную: SSH-вход администратора не нужен

    @field_validator("ssh_host")
    @classmethod
    def _host(cls, v: str) -> str:
        return _host_ok(v)

    @field_validator("public_name")
    @classmethod
    def _pn(cls, v: str) -> str:
        v = (v or "").strip().lower().rstrip(".")
        if not NAME_RE.match(v):
            raise ValueError("Публичное имя узла: доменное имя, например cdn1.example.org")
        return v

    @field_validator("ssh_user")
    @classmethod
    def _u(cls, v: str) -> str:
        if not USER_RE.match(v or ""):
            raise ValueError("Пользователь SSH: латинские буквы, цифры, _ . -")
        return v


class NodePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    region: str | None = Field(default=None, max_length=60)
    weight: int | None = Field(default=None, ge=1, le=10_000)
    enabled: bool | None = None


class CdnSettingsIn(BaseModel):
    cdn_nodes_enabled: bool | None = None
    cdn_origin_url: str | None = None
    cdn_origin_tls_verify: bool | None = None
    cdn_fallback_origin: bool | None = None


def _bad(code: str, message: str, status: int = 400):
    raise HTTPException(status_code=status, detail={"code": code, "message": message})


def _out(n: CdnNode, busy: bool = False) -> dict[str, Any]:
    st = dict(n.stats or {})
    st.pop("_raw", None)
    return {"id": str(n.id), "name": n.name, "public_name": n.public_name, "region": n.region, "weight": n.weight, "capacity": n.capacity, "enabled": n.enabled,
            "state": n.state, "health": n.health, "ssh": {"host": n.ssh_host, "port": n.ssh_port, "user": n.ssh_user, "fingerprint": n.host_fp},
            "tls_mode": n.tls_mode, "facts": n.facts, "assessment": n.assessment, "stages": n.stages or [], "last_error": n.last_error,
            "last_check_at": n.last_check_at, "last_ok_at": n.last_ok_at, "installed_at": n.installed_at, "version": n.version, "generation": n.generation,
            "stats": st, "busy": busy, "outdated": bool(n.version and n.version != NODE_VERSION)}


async def _node(db, node_id) -> CdnNode:
    n = await db.get(CdnNode, node_id)
    if n is None:
        _bad("not_found", "Узел не найден.", 404)
    return n


async def _audit(request: Request, db, su: SessionUser, action: str, node: CdnNode, **details) -> None:
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action=f"cdn.{action}", target_type="cdn_node", target_id=str(node.id),
                      ip=client_ip(request), details={"node": node.public_name, **details})


@router.get("")
async def overview(request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    cdn, jobs = request.app.state.cdn, request.app.state.cdn_jobs
    cfg = await cdn.cfg(db)
    rows = list((await db.execute(select(CdnNode).order_by(CdnNode.created_at))).scalars())
    nodes = [_out(n, await jobs.busy(n.id)) for n in rows]
    up = [n for n in rows if n.enabled and n.state == "ready" and n.health == "up"]
    return {"settings": {"cdn_nodes_enabled": cfg.cdn_nodes_enabled, "cdn_origin_url": cfg.cdn_origin_url, "cdn_origin_tls_verify": cfg.cdn_origin_tls_verify,
                         "cdn_fallback_origin": cfg.cdn_fallback_origin, "cdn_key_gen": cfg.cdn_key_gen},
            "origin_effective": cdn.origin_url(cfg), "keys": list(public_keys(cdn.master, int(cfg.cdn_key_gen))), "node_version": NODE_VERSION,
            "mode": ("nodes" if up else "fallback") if cfg.cdn_nodes_enabled else ("external" if cfg.cdn_enabled and cfg.cdn_base_url else "origin"),
            "nodes": nodes, "nodes_up": len(up),
            "profiles": [{"name": n, **recommend(v, QUALITY_KBPS[q])} for n, v in (("Начальный", 300), ("Средний", 700), ("Большой", 1500)) for q in ("1080p", "1440p")]}


@router.get("/calc")
async def calc(viewers: int, quality: Literal["720p", "1080p", "1440p"] = "1080p", streams: int = 1, link_mbit: int | None = None,
               su: SessionUser = Depends(require_admin)) -> dict[str, Any]:
    if not 1 <= viewers <= 1_000_000 or not 1 <= streams <= 50:
        _bad("bad_value", "Зрителей 1–1 000 000, эфиров 1–50.")
    return recommend(viewers, QUALITY_KBPS[quality], streams, link_mbit=link_mbit)


@router.put("/settings")
async def save_settings(body: CdnSettingsIn, request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    svc = request.app.state.settings_svc
    before = await svc.get(db, "live")
    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    try:
        await svc.update(db, "live", patch, actor=su.display_name)
    except ValueError as exc:
        _bad("bad_value", str(exc))
    after = await svc.get(db, "live")
    applied = None
    if (before.cdn_origin_url, before.cdn_origin_tls_verify) != (after.cdn_origin_url, after.cdn_origin_tls_verify):
        applied = await request.app.state.cdn_jobs.apply_all()
    request.app.state.cdn.invalidate()
    return {"ok": True, "applied": applied}


@router.post("/hostkey")
async def hostkey(body: HostKeyIn, su: SessionUser = Depends(require_admin)) -> dict[str, Any]:
    """Шаг 1: ключ сервера SSH и его отпечаток — администратор сверяет его с выводом `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub` на сервере."""
    try:
        key, fp = await cdn_ssh.host_key(body.ssh_host, body.ssh_port)
    except cdn_ssh.SshError as e:
        _bad(e.code, e.message, 502)
    return {"host_key": key, "fingerprint": fp, "type": key.split(" ", 1)[0]}


def _check_fp(host_key: str, fingerprint: str) -> str:
    import asyncssh  # noqa: PLC0415

    try:
        fp = asyncssh.import_public_key(host_key).get_fingerprint("sha256")
    except (asyncssh.KeyImportError, ValueError):
        _bad("bad_host_key", "Ключ сервера не читается.")
    if fp != fingerprint.strip():
        _bad("fingerprint_mismatch", "Отпечаток не совпадает с ключом сервера: подтвердите отпечаток, который показал шаг «Получить отпечаток».")
    return fp


async def _preflight(request: Request, node: CdnNode, body: Creds, quality: str) -> None:
    s = await cdn_ssh.connect(node.ssh_host, node.ssh_port, node.ssh_user, node.host_key, password=body.password, private_key=body.private_key)
    try:
        facts = await s.facts()
    finally:
        await s.close()
    node.facts = facts
    node.assessment = assess(facts, QUALITY_KBPS.get(quality, LIVE_KBPS_DEFAULT))
    node.capacity = node.assessment["viewers"]
    node.state = "checked" if node.assessment["ok"] else "error"
    node.last_error = None if node.assessment["ok"] else "; ".join(node.assessment["blockers"])[:500]


@router.post("/nodes", status_code=201)
async def add_node(body: NodeIn, request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """Шаг 2: узел с подтверждённым ключом сервера и предварительной проверкой (сведения о сервере, оценка аудитории). Ничего не устанавливает."""
    fp = _check_fp(body.host_key, body.fingerprint)
    if (await db.execute(select(CdnNode).where(CdnNode.public_name == body.public_name))).scalar_one_or_none():
        _bad("exists", "Узел с таким публичным именем уже подключён.", 409)
    if body.tls_mode == "files" and not (body.tls_cert_pem and body.tls_key_pem) and not body.manual:
        _bad("tls_files", "Для своего сертификата нужны сертификат и закрытый ключ (PEM).")
    if not body.manual and not (body.password or body.private_key):
        _bad("no_creds", "Нужен пароль или закрытый ключ учётной записи сервера.")
    node = new_node(name=body.name.strip(), public_name=body.public_name, region=(body.region or "").strip() or None, ssh_host=body.ssh_host, ssh_port=body.ssh_port,
                    ssh_user="pgcdn" if body.manual else body.ssh_user, host_key=body.host_key.strip(), host_fp=fp, tls_mode=body.tls_mode,
                    acme_email=(body.acme_email or "").strip() or None, state="new", stages=[])
    request.app.state.cdn.new_secrets(node)
    if not body.manual:
        try:
            await _preflight(request, node, body, body.quality)
        except cdn_ssh.SshError as e:
            _bad(e.code, e.message, 502)
        await request.app.state.cdn_jobs.put_creds(node.id, body.model_dump(include={"password", "private_key", "tls_cert_pem", "tls_key_pem"}))
    else:
        priv, _pub = cdn_ssh.new_mgmt_key()
        node.mgmt_key_enc = request.app.state.cdn.enc(node.id, "mgmt_key", priv)
        node.state = "checked"
    db.add(node)
    await _audit(request, db, su, "add", node, ssh=f"{node.ssh_user}@{node.ssh_host}:{node.ssh_port}", fingerprint=fp, manual=body.manual)
    await db.commit()
    await db.refresh(node)
    return _out(node)


@router.post("/nodes/{node_id}/preflight")
async def recheck(node_id: uuid.UUID, body: Creds, request: Request, quality: Literal["720p", "1080p", "1440p"] = "1080p", su: SessionUser = Depends(require_admin),
                  db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    node = await _node(db, node_id)
    creds = body.model_dump(include={"password", "private_key", "tls_cert_pem", "tls_key_pem"})
    if not (body.password or body.private_key):
        creds = await request.app.state.cdn_jobs.get_creds(node.id) or {}
    try:
        await _preflight(request, node, Creds(**creds), quality)
    except cdn_ssh.SshError as e:
        _bad(e.code, e.message, 502)
    await request.app.state.cdn_jobs.put_creds(node.id, creds)
    await db.commit()
    return _out(node)


@router.post("/nodes/{node_id}/install", status_code=202)
async def install(node_id: uuid.UUID, body: Creds, request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """Шаг 3: установка в фоне (этапы видны в карточке узла). Повторный запуск при незавершённой установке — отказ; после ошибки — безопасный повтор."""
    jobs = request.app.state.cdn_jobs
    node = await _node(db, node_id)
    if node.state in ("removing",) or node.ssh_user == "pgcdn":
        _bad("bad_state", "Этот узел устанавливается скриптом на сервере: после установки нажмите «Подключить установленный».", 409)
    if body.password or body.private_key or body.tls_cert_pem:
        cur = await jobs.get_creds(node.id) or {}
        await jobs.put_creds(node.id, {**cur, **{k: v for k, v in body.model_dump().items() if v}})
    if not await jobs.get_creds(node.id):
        _bad("no_creds", "Учётные данные установки истекли (хранятся 30 минут): введите пароль или ключ ещё раз.", 409)
    if (node.assessment or {}).get("blockers"):
        _bad("not_suitable", "Сервер не прошёл предварительную проверку: " + "; ".join(node.assessment["blockers"]), 409)
    tok = await jobs.lock(node.id)
    if tok is None:
        _bad("busy", "Над узлом уже выполняется операция — дождитесь её завершения.", 409)
    node.state, node.stages = "installing", []
    await _audit(request, db, su, "install", node)
    await db.commit()
    jobs.spawn(node.id, jobs.install(node.id, tok, {"user": su.display_name}))
    return {"ok": True, "state": "installing"}


@router.post("/nodes/{node_id}/adopt")
async def adopt(node_id: uuid.UUID, request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """Узел, установленный скриптом вручную (с node.json из «Скачать настройки»): вход ключом pgcdn, состояние, сквозная проверка."""
    jobs, cdn = request.app.state.cdn_jobs, request.app.state.cdn
    node = await _node(db, node_id)
    tok = await jobs.lock(node.id)
    if tok is None:
        _bad("busy", "Над узлом уже выполняется операция.", 409)
    try:
        node.state = "installing"
        await db.commit()
        await cdn.write_origin_keys(db)
        try:
            rc, out, err = await cdn_ssh.mgmt(node.ssh_host, node.ssh_port, node.host_key, cdn.dec(node, "mgmt_key"), "status", timeout=30)
        except cdn_ssh.SshError as e:
            node.state, node.last_error = "error", f"Вход ключом pgcdn: {e.message}"
            await db.commit()
            _bad(e.code, node.last_error, 502)
        if rc != 0:
            node.state, node.last_error = "error", f"pgcdn-ctl status: {(err or out)[:300]}"
            await db.commit()
            _bad("mgmt", node.last_error, 502)
        st = json.loads(out)
        node.version, node.generation = st.get("version"), st.get("generation")
        await db.commit()
        ok, gen = await jobs.apply(node.id)
        res = await jobs.test(node.id) if ok else {"ok": False, "message": gen}
        await db.refresh(node)
        node.state = "ready" if res["ok"] else "error"
        node.health, node.oks, node.fails = ("up", 2, 0) if res["ok"] else (node.health, 0, node.fails)
        node.installed_at = node.installed_at or datetime.now(timezone.utc)
        node.last_error = None if res["ok"] else f"Проверка раздачи: {res['message']}"
        await _audit(request, db, su, "adopt", node, result=res["ok"])
        await db.commit()
        await cdn.write_origin_keys(db)
    finally:
        await jobs.unlock(node.id, tok)
    cdn.invalidate()
    return _out(node)


@router.get("/nodes/{node_id}/node.json")
async def manual_config(node_id: uuid.UUID, request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> Response:
    """Настройки для ручной установки (install.sh --config node.json): содержат ключ доступа к источнику и открытый ключ управления — передавайте как секрет."""
    import asyncssh  # noqa: PLC0415

    cdn = request.app.state.cdn
    node = await _node(db, node_id)
    if not node.mgmt_key_enc:
        _bad("bad_state", "Узел подключается по SSH: настройки для ручной установки не нужны.", 409)
    conf = cdn.node_config(node, await cdn.cfg(db))
    conf["mgmt_pubkey"] = asyncssh.import_private_key(cdn.dec(node, "mgmt_key")).export_public_key("openssh").decode().strip()
    await _audit(request, db, su, "manual_config", node)
    await db.commit()
    return Response(json.dumps(conf, ensure_ascii=False, indent=1), media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="pgcdn-{node.public_name}.json"', "Cache-Control": "no-store"})


@router.get("/package.tar.gz")
async def package(su: SessionUser = Depends(require_admin)) -> Response:
    return Response(cdn_ssh.bundle_bytes(), media_type="application/gzip",
                    headers={"Content-Disposition": f'attachment; filename="peregovorka-cdn-node-{NODE_VERSION}.tar.gz"', "Cache-Control": "no-store"})


@router.get("/nodes/{node_id}")
async def get_node(node_id: uuid.UUID, request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    node = await _node(db, node_id)
    out = _out(node, await request.app.state.cdn_jobs.busy(node.id))
    out["diag"] = node.diag
    return out


@router.patch("/nodes/{node_id}")
async def patch_node(node_id: uuid.UUID, body: NodePatch, request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    node = await _node(db, node_id)
    changes = {k: v for k, v in body.model_dump().items() if v is not None}
    for k, v in changes.items():
        setattr(node, k, v.strip() if isinstance(v, str) else v)
    await _audit(request, db, su, "update", node, **changes)
    await db.commit()
    request.app.state.cdn.invalidate()
    return _out(node)


@router.post("/nodes/{node_id}/test")
async def test_node(node_id: uuid.UUID, request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    jobs = request.app.state.cdn_jobs
    node = await _node(db, node_id)
    if node.state not in ("ready", "error"):
        _bad("bad_state", "Узел ещё не установлен.", 409)
    tok = await jobs.lock(node.id)
    if tok is None:
        _bad("busy", "Над узлом уже выполняется операция.", 409)
    try:
        res = await jobs.test(node.id)
    finally:
        await jobs.unlock(node.id, tok)
    await _audit(request, db, su, "test", node, ok=res["ok"])
    await db.commit()
    return res


@router.post("/nodes/{node_id}/poll")
async def poll_node(node_id: uuid.UUID, request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    node = await _node(db, node_id)
    await request.app.state.cdn.poll(db, node)
    await db.commit()
    request.app.state.cdn.invalidate()
    return _out(node)


@router.post("/nodes/{node_id}/apply")
async def apply_node(node_id: uuid.UUID, request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    node = await _node(db, node_id)
    ok, msg = await request.app.state.cdn_jobs.apply(node.id)
    await _audit(request, db, su, "apply", node, ok=ok)
    await db.commit()
    return {"ok": ok, "message": msg if not ok else f"Настройки применены (поколение {msg})."}


@router.post("/nodes/{node_id}/upgrade", status_code=202)
async def upgrade_node(node_id: uuid.UUID, request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    jobs = request.app.state.cdn_jobs
    node = await _node(db, node_id)
    if node.state != "ready":
        _bad("bad_state", "Обновлять можно установленный узел.", 409)
    tok = await jobs.lock(node.id)
    if tok is None:
        _bad("busy", "Над узлом уже выполняется операция.", 409)
    await _audit(request, db, su, "upgrade", node, to=NODE_VERSION)
    await db.commit()
    jobs.spawn(node.id, jobs.upgrade(node.id, tok))
    return {"ok": True}


@router.get("/nodes/{node_id}/logs")
async def node_logs(node_id: uuid.UUID, request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    node = await _node(db, node_id)
    if not node.mgmt_key_enc or node.state not in ("ready", "error"):
        return {"text": node.diag or ""}
    try:
        rc, out, err = await cdn_ssh.mgmt(node.ssh_host, node.ssh_port, node.host_key, request.app.state.cdn.dec(node, "mgmt_key"), "logs", timeout=30)
    except cdn_ssh.SshError as e:
        return {"text": node.diag or "", "error": e.message}
    return {"text": out[-20000:], "install": node.diag or ""}


@router.delete("/nodes/{node_id}")
async def delete_node(node_id: uuid.UUID, request: Request, force: bool = False, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """Удаление узла: сначала зрители перестают на него направляться, затем служба на сервере удаляется ключом pgcdn; ключ узла к источнику отзывается.
    Сервер недоступен — без force отказ с объяснением; с force узел забывается (на сервере остаётся nginx без доступа к источнику)."""
    jobs, cdn = request.app.state.cdn_jobs, request.app.state.cdn
    node = await _node(db, node_id)
    if await jobs.busy(node.id) and not force:
        _bad("busy", "Над узлом выполняется операция.", 409)
    node.enabled = False
    await db.commit()
    cdn.invalidate()
    ok, msg = await jobs.uninstall(node.id)
    if not ok and not force:
        _bad("uninstall_failed", f"Службу на сервере удалить не удалось ({msg}). Узел уже не получает зрителей. «Забыть узел» удалит его из списка "
                                 "и отзовёт доступ к источнику, а на сервере останется неработающий nginx — его можно удалить вручную: sudo pgcdn-ctl uninstall.", 409)
    await _audit(request, db, su, "delete", node, uninstalled=ok, force=force)
    await db.delete(node)
    await db.commit()
    await jobs.drop_creds(node.id)
    await cdn.write_origin_keys(db)
    cdn.invalidate()
    return {"ok": True, "uninstalled": ok, "message": msg}


@router.post("/keys/rotate")
async def rotate(request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """Новое поколение ключа подписи: узлы получают новый открытый ключ (и сохраняют предыдущий), затем ссылки подписываются новым.
    Ссылки позапрошлого поколения перестают открываться — двойная смена отзывает скомпрометированный ключ полностью."""
    svc, cdn, jobs = request.app.state.settings_svc, request.app.state.cdn, request.app.state.cdn_jobs
    cfg = await svc.get(db, "live")
    gen = int(cfg.cdn_key_gen) + 1
    applied = await jobs.apply_all(ahead=True)               # сначала узлы узнают новый ключ, ссылки ещё подписываются прежним
    await svc.update(db, "live", {"cdn_key_gen": gen}, actor=su.display_name)
    from ..services.cdn import _private_key, _spki  # noqa: PLC0415

    kid = kid_of(_spki(_private_key(cdn.master, gen)))
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="cdn.keys_rotate", target_type="cdn", target_id=kid, ip=client_ip(request),
                      details={"generation": gen, "applied": applied})
    await db.commit()
    cdn.invalidate()
    return {"ok": all(v == "ok" for v in applied.values()), "kid": kid, "generation": gen, "applied": applied}
