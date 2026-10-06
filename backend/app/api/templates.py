"""Шаблоны инструкций для протокола: общие (создаёт администратор) и личные (видны только владельцу)."""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import SessionUser, client_ip, get_db, require_user
from ..models import ProtocolTemplate
from ..services.audit import write_audit

router = APIRouter(prefix="/protocol-templates", tags=["protocols"])


def _out(t: ProtocolTemplate, su: SessionUser) -> dict:
    return {"id": str(t.id), "name": t.name, "kind": t.kind, "instruction": t.instruction, "scope": t.scope,
            "can_edit": su.is_admin if t.scope == "global" else t.owner_user_id == su.user_id}


def _validate(body: dict[str, Any], partial: bool = False) -> dict:
    out: dict[str, Any] = {}
    if "name" in body or not partial:
        n = body.get("name")
        if not isinstance(n, str) or not (1 <= len(n.strip()) <= 200):
            raise HTTPException(status_code=422, detail="name: 1–200 символов")
        out["name"] = n.strip()
    if "instruction" in body or not partial:
        i = body.get("instruction")
        if not isinstance(i, str) or not (1 <= len(i.strip()) <= 20000):
            raise HTTPException(status_code=422, detail="instruction: 1–20000 символов")
        out["instruction"] = i.strip()
    if "kind" in body:
        if body["kind"] not in ("summary", "protocol", "any"):
            raise HTTPException(status_code=422, detail="kind: summary | protocol | any")
        out["kind"] = body["kind"]
    return out


@router.get("")
async def list_templates(su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(ProtocolTemplate).where(
        or_(ProtocolTemplate.scope == "global", ProtocolTemplate.owner_user_id == su.user_id))
        .order_by(ProtocolTemplate.scope, ProtocolTemplate.name))).scalars().all()
    return [_out(t, su) for t in rows]


@router.post("", status_code=201)
async def create_template(request: Request, body: dict[str, Any] = Body(...), su: SessionUser = Depends(require_user),
                          db: AsyncSession = Depends(get_db)):
    """scope=global — только администратор; иначе шаблон личный."""
    scope = body.get("scope", "user")
    if scope not in ("global", "user"):
        raise HTTPException(status_code=422, detail="scope: global | user")
    if scope == "global" and not su.is_admin:
        raise HTTPException(status_code=403, detail="Общие шаблоны создаёт администратор")
    data = _validate(body)
    data.setdefault("kind", "any")
    t = ProtocolTemplate(scope=scope, owner_user_id=None if scope == "global" else su.user_id, **data)
    db.add(t)
    if scope == "global":
        await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="protocol_template.create",
                          target_type="protocol_template", target_id=str(t.id), ip=client_ip(request), details={"name": t.name})
    await db.commit()
    return _out(t, su)


async def _own(db: AsyncSession, tid: uuid.UUID, su: SessionUser) -> ProtocolTemplate:
    t = await db.get(ProtocolTemplate, tid)
    if t is None or (t.scope == "user" and t.owner_user_id != su.user_id):
        raise HTTPException(status_code=404, detail="Шаблон не найден")
    if (t.scope == "global" and not su.is_admin) or (t.scope == "user" and t.owner_user_id != su.user_id):
        raise HTTPException(status_code=403, detail="Недостаточно прав на изменение шаблона")
    return t


@router.put("/{template_id}")
async def update_template(template_id: uuid.UUID, request: Request, body: dict[str, Any] = Body(...),
                          su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    t = await _own(db, template_id, su)
    for k, v in _validate(body, partial=True).items():
        setattr(t, k, v)
    if t.scope == "global":
        await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="protocol_template.update",
                          target_type="protocol_template", target_id=str(t.id), ip=client_ip(request), details={"name": t.name})
    await db.commit()
    return _out(t, su)


@router.delete("/{template_id}", status_code=204)
async def delete_template(template_id: uuid.UUID, request: Request, su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    t = await _own(db, template_id, su)
    if t.scope == "global":
        await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="protocol_template.delete",
                          target_type="protocol_template", target_id=str(t.id), ip=client_ip(request), details={"name": t.name})
    await db.delete(t)
    await db.commit()
