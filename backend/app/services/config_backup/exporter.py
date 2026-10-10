"""Экспорт конфигурации: собирает содержимое архива (словарь) по реестру. Ничего не пишет на диск.

Секреты (пароли подключений, секретные заголовки, секреты подписи) расшифровываются ключом ЭТОГО сервера и кладутся в содержимое, которое затем целиком шифруется паролем
архива (`container.seal`); при импорте они шифруются ключом нового сервера. Ключ шифрования сервера (APP_MASTER_KEY) в архив не попадает никогда.
"""
from __future__ import annotations

import base64
import json
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models import AppSetting, Base
from ..settings import GROUPS, SettingsService
from . import coverage
from . import registry as R


class ExportError(Exception):
    pass


def models_by_table() -> dict[str, type]:
    return {m.class_.__tablename__: m.class_ for m in Base.registry.mappers}


def py_type(col) -> type | None:
    t = col.type
    t = getattr(t, "impl", t)                  # TypeDecorator (даты в UTC, JSON) → базовый тип
    try:
        return t.python_type
    except (NotImplementedError, AttributeError):
        return None


def ser(v: Any) -> Any:
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, uuid.UUID):
        return str(v)
    if isinstance(v, (bytes, bytearray, memoryview)):
        return {"__b64": base64.b64encode(bytes(v)).decode("ascii")}
    return v


def deser(v: Any, col) -> Any:
    if v is None:
        return None
    if isinstance(v, dict) and set(v) == {"__b64"}:
        return base64.b64decode(v["__b64"])
    pt = py_type(col)
    if pt is uuid.UUID and isinstance(v, str):
        return uuid.UUID(v)
    if pt is datetime and isinstance(v, str):
        return datetime.fromisoformat(v)
    return v


def table_filter(name: str, model):
    """Условие отбора строк переносимой таблицы (см. TablePolicy.where)."""
    if name == "rooms":
        return model.lifetime == "permanent"
    if name == "protocol_templates":
        return model.scope != "user"
    return None


async def _exported_room_ids(db: AsyncSession, models: dict[str, type]) -> set:
    room = models["rooms"]
    return set((await db.execute(select(room.id).where(room.lifetime == "permanent"))).scalars().all())


def environment_snapshot(env_settings) -> dict[str, str]:
    """Параметры окружения исходного сервера для сравнения при импорте. Секреты не передаются даже в шифрованном виде — только «задан»/«не задан»."""
    out: dict[str, str] = {}
    if env_settings is None:
        return out
    for name in R.ENV:
        val = getattr(env_settings, name, None)
        if R.is_secret_env(name):
            out[name] = "задан" if val else "не задан"
        else:
            out[name] = "" if val is None else str(val)[:200]
    return out


async def build_payload(db: AsyncSession, settings_svc: SettingsService, *, app_version: str, public_url: str, branding=None, env_settings=None) -> dict:
    problems = coverage.all_problems()
    if problems:
        raise ExportError("Реестр конфигурации неполон — копия не создаётся, чтобы не потерять настройки молча:\n" + "\n".join(problems[:10]))
    box = settings_svc.box
    models = models_by_table()
    tables: dict[str, list[dict]] = {}
    warnings: list[str] = []
    rooms_ok = await _exported_room_ids(db, models)
    for name in R.exported_tables():
        pol, model = R.TABLES[name], models[name]
        stmt = select(model)
        cond = table_filter(name, model)
        if cond is not None:
            stmt = stmt.where(cond)
        rows = (await db.execute(stmt)).scalars().all()
        out: list[dict] = []
        for obj in rows:
            if name in ("room_acl", "room_moderators") and obj.room_id not in rooms_ok:
                continue
            row: dict[str, Any] = {}
            for col, policy in pol.columns.items():
                val = getattr(obj, col)
                if policy in (R.RESET, R.USER_REF):
                    continue
                if policy == R.SECRET:
                    if val:
                        if box is None:
                            raise ExportError("APP_MASTER_KEY не задан — секреты расшифровать нельзя")
                        try:
                            val = box.decrypt(val, aad=pol.aad({"id": str(getattr(obj, "id"))}))
                        except Exception:  # noqa: BLE001
                            warnings.append(f"{name}: секрет записи {getattr(obj, 'id')} не расшифрован (повреждён) — перенесён пустым")
                            val = ""
                    else:
                        val = ""
                row[col] = ser(val)
            out.append(row)
        tables[name] = out
    # api_keys отозванных/просроченных ключей не нужны, но и не мешают: переносим как есть (состояние сохраняется)
    settings: dict[str, dict[str, Any]] = {}
    secret_fields: dict[str, list[str]] = {}
    skipped_unknown: list[str] = []
    rows = (await db.execute(select(AppSetting))).scalars().all()
    for r in rows:
        group, _, field = r.key.partition(".")
        model = GROUPS.get(group)
        if model is None or field not in model.model_fields:
            skipped_unknown.append(r.key)
            continue
        pol = R.GROUPS_POLICY[group]
        if not pol.export or field in pol.skip:
            continue
        if r.is_secret or field in model.SECRETS:
            val = ""
            if r.value:
                if box is None:
                    raise ExportError("APP_MASTER_KEY не задан — секреты расшифровать нельзя")
                try:
                    val = box.decrypt(r.value, aad=r.key.encode())
                except Exception:  # noqa: BLE001
                    warnings.append(f"настройка {r.key}: секрет не расшифрован (повреждён) — перенесён пустым")
            secret_fields.setdefault(group, []).append(field)
        else:
            try:
                val = json.loads(r.value)
            except ValueError:
                continue
        settings.setdefault(group, {})[field] = val
    files: dict[str, str] = {}
    if branding is not None:
        for kind in ("logo", "logo_compact", "favicon"):
            data = branding.read(kind)
            if data:
                files[f"branding/{kind}"] = base64.b64encode(data).decode("ascii")
    manifest = {
        "kind": "peregovorka-config", "schema": R.SCHEMA_VERSION, "format": R.FORMAT_VERSION, "app_version": app_version, "created_at": datetime.utcnow().isoformat() + "Z",
        "source_url": public_url, "environment": environment_snapshot(env_settings),
        "components": {"tables": {n: len(v) for n, v in tables.items()}, "settings": {g: len(v) for g, v in settings.items()}, "files": sorted(files)},
        "excluded_tables": {n: p.reason for n, p in R.TABLES.items() if not p.export},
        "warnings": warnings + ([f"в базе есть настройки неизвестных групп/полей, они не вошли в копию: {', '.join(sorted(skipped_unknown)[:10])}"] if skipped_unknown else []),
    }
    return {"manifest": manifest, "settings": settings, "secret_fields": secret_fields, "tables": tables, "files": files}


async def describe(db: AsyncSession) -> dict:
    """Что войдёт и что НЕ войдёт в копию — показывается перед скачиванием (без секретов)."""
    models = models_by_table()
    included, hashed = [], []
    room_ids = await _exported_room_ids(db, models)
    for name in R.exported_tables():
        pol, model = R.TABLES[name], models[name]
        stmt = select(model)
        cond = table_filter(name, model)
        if cond is not None:
            stmt = stmt.where(cond)
        rows = (await db.execute(stmt)).scalars().all()
        if name in ("room_acl", "room_moderators"):
            rows = [r for r in rows if r.room_id in room_ids]
        included.append({"name": name, "title": pol.title or name, "count": len(rows), "note": pol.reason})
        if pol.hashes and rows:
            if name == "rooms":
                n = sum(1 for r in rows if r.password_hash)
                if n:
                    hashed.append({"what": "пароли переговорок", "count": n, "how": "хранятся в системе только в виде хэша: переносится хэш — пароль продолжит работать, но показать его невозможно"})
            elif name == "api_keys":
                hashed.append({"what": "ключи интеграций API", "count": len(rows), "how": "хранятся только в виде хэша: переносится хэш — ключи продолжат работать; сами ключи восстановить нельзя"})
    groups = [{"name": g, "title": p.title or g} for g, p in R.GROUPS_POLICY.items() if p.export]
    return {"included": included, "settings_groups": groups, "hashed": hashed, "files": [{"name": k, "title": v} for k, v in R.FILES.items()],
            "excluded": [{"name": n, "reason": p.reason} for n, p in R.TABLES.items() if not p.export and p.reason and n != "app_settings"],
            "environment": "Параметры окружения сервера (.env: адреса базы, LiveKit, ключ шифрования APP_MASTER_KEY и др.) не переносятся — они задаются при развёртывании нового сервера. "
                           "TLS-сертификат веб-сервера (nginx) лежит вне приложения и в архив не входит."}
