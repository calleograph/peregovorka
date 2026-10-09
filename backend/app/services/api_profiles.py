"""Несколько API для языковой модели и обезличивания: профили, выбор «по умолчанию» и своя привязка у комнаты.

Профиль «main» — виртуальный: это прежние общие настройки групп `llm` / `anonymizer` (после обновления всё работает как раньше).
Остальные профили — строки `api_profiles` (секрет зашифрован). По умолчанию используется профиль с `is_default`, а если такого нет — «main».
Комната может выбрать конкретный профиль (`llm_profile_id`, `anonymizer_profile_id`); удалённый профиль = «по умолчанию».
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import ApiProfile, Room, utcnow
from .settings import AnonymizerSettings, LlmSettings, SettingsError, SettingsService, _first_error, merge_secret_headers

Kind = Literal["llm", "anonymizer"]
MAIN = "main"
MAIN_NAME = "Основной (из общих настроек)"
_MODELS = {"llm": LlmSettings, "anonymizer": AnonymizerSettings}
_SECRET = {"llm": "api_key", "anonymizer": "token"}
_GROUP = {"llm": "llm", "anonymizer": "anonymizer"}


@dataclass
class Resolved:
    settings: LlmSettings | AnonymizerSettings
    profile_id: str
    name: str


class ProfileService:
    def __init__(self, svc: SettingsService):
        self._svc = svc

    # ------------------------------------------------------------------ вспомогательное
    def _check_kind(self, kind: str) -> Kind:
        if kind not in _MODELS:
            raise SettingsError("Тип профиля: llm или anonymizer")
        return kind  # type: ignore[return-value]

    def _aad(self, pid: uuid.UUID | str) -> bytes:
        return f"api_profile.{pid}".encode()

    def _encrypt(self, pid: uuid.UUID, secret: str) -> str:
        if not secret:
            return ""
        if self._svc.box is None:
            raise SettingsError("APP_MASTER_KEY не задан — секреты сохранить нельзя")
        return self._svc.box.encrypt(secret, aad=self._aad(pid))

    def _decrypt(self, row: ApiProfile) -> str:
        if not row.secret_enc:
            return ""
        if self._svc.box is None:
            return ""
        try:
            return self._svc.box.decrypt(row.secret_enc, aad=self._aad(row.id))
        except Exception:  # noqa: BLE001 — повреждённый секрет не должен ронять выбор профиля
            return ""

    # Секрет профиля хранится одним зашифрованным значением: ключ API, а у профилей LLM с секретными заголовками — JSON {"__p":1,"k":ключ,"h":{заголовки}}.
    @staticmethod
    def _pack(secret: str, headers: dict[str, str]) -> str:
        return json.dumps({"__p": 1, "k": secret, "h": headers}, ensure_ascii=False) if headers else secret

    @staticmethod
    def _unpack(payload: str) -> tuple[str, dict[str, str]]:
        if payload.startswith('{"__p"'):
            try:
                d = json.loads(payload)
                return str(d.get("k") or ""), {str(k): str(v) for k, v in (d.get("h") or {}).items()}
            except ValueError:
                return payload, {}
        return payload, {}

    @staticmethod
    def _split_headers(kind: Kind, config: dict, old: dict[str, str] | None = None) -> tuple[dict, dict[str, str] | None]:
        """Из конфигурации профиля убирает secret_headers: возвращает (конфигурация без них, новые секретные заголовки или None — не менять)."""
        cfg = dict(config or {})
        raw = cfg.pop("secret_headers", None)
        cfg.pop("secret_header_names", None)
        if kind != "llm" or raw is None:
            return cfg, None
        try:
            new = json.loads(raw or "{}") if isinstance(raw, str) else dict(raw)
        except ValueError:
            raise SettingsError("secret_headers: ожидается объект {имя: значение}") from None
        try:
            return cfg, merge_secret_headers(old or {}, new)
        except ValueError as exc:
            raise SettingsError(str(exc)) from None

    def _build(self, kind: Kind, config: dict, secret: str, headers: dict[str, str] | None = None):
        model = _MODELS[kind]
        data = {k: v for k, v in (config or {}).items() if k in model.model_fields and k not in (_SECRET[kind], "secret_headers", "secret_header_names")}
        data.setdefault("enabled", True)
        data[_SECRET[kind]] = secret
        if kind == "llm" and headers:
            data["secret_headers"] = json.dumps(headers, ensure_ascii=False)
            data["secret_header_names"] = sorted(headers, key=str.lower)
        try:
            return model(**data)
        except ValueError as exc:
            raise SettingsError(_first_error(exc)) from None

    def _key_set(self, row: ApiProfile) -> bool:
        plain = self._decrypt(row)
        return bool(self._unpack(plain)[0]) if plain else bool(row.secret_enc)

    def _public(self, row: ApiProfile, default_id: str) -> dict:
        cfg = dict(row.config or {})
        cfg.pop(_SECRET[row.kind], None)
        cfg.pop("secret_headers", None)
        if row.kind == "llm":
            cfg["secret_header_names"] = sorted(self._unpack(self._decrypt(row))[1], key=str.lower)
        return {"id": str(row.id), "kind": row.kind, "name": row.name, "config": cfg, "secret_set": self._key_set(row),
                "is_default": str(row.id) == default_id, "virtual": False}

    # ------------------------------------------------------------------------ чтение
    async def default_id(self, db: AsyncSession, kind: str) -> str:
        row = (await db.execute(select(ApiProfile).where(ApiProfile.kind == kind, ApiProfile.is_default.is_(True)))).scalars().first()
        return str(row.id) if row else MAIN

    async def list(self, db: AsyncSession, kind: str) -> list[dict]:
        kind = self._check_kind(kind)
        default = await self.default_id(db, kind)
        main = await self._svc.public(db, _GROUP[kind])
        secret_name = _SECRET[kind]
        main_cfg = {k: v for k, v in main.items() if not k.endswith("_set")}
        out = [{"id": MAIN, "kind": kind, "name": MAIN_NAME, "config": main_cfg, "secret_set": bool(main.get(f"{secret_name}_set")),
                "is_default": default == MAIN, "virtual": True}]
        rows = (await db.execute(select(ApiProfile).where(ApiProfile.kind == kind).order_by(ApiProfile.name))).scalars().all()
        return out + [self._public(r, default) for r in rows]

    async def get_settings(self, db: AsyncSession, kind: str, profile_id: str | uuid.UUID | None) -> Resolved:
        """Настройки конкретного профиля («main» — общие настройки группы). Неизвестный id → SettingsError."""
        kind = self._check_kind(kind)
        pid = str(profile_id) if profile_id else MAIN
        if pid == MAIN:
            return Resolved(await self._svc.get(db, _GROUP[kind]), MAIN, MAIN_NAME)  # type: ignore[arg-type]
        try:
            row = await db.get(ApiProfile, uuid.UUID(pid))
        except ValueError:
            row = None
        if row is None or row.kind != kind:
            raise SettingsError("Профиль API не найден")
        key, hdrs = self._unpack(self._decrypt(row))
        return Resolved(self._build(kind, row.config or {}, key, hdrs), str(row.id), row.name)

    async def resolve(self, db: AsyncSession, kind: str, room: Room | None = None) -> Resolved:
        """Профиль для комнаты: её собственный → общий по умолчанию → «main». Битая ссылка комнаты = «по умолчанию»."""
        kind = self._check_kind(kind)
        wanted = None
        if room is not None:
            wanted = room.llm_profile_id if kind == "llm" else room.anonymizer_profile_id
        if wanted:
            try:
                return await self.get_settings(db, kind, wanted)
            except SettingsError:
                pass
        return await self.get_settings(db, kind, await self.default_id(db, kind))

    # ------------------------------------------------------------------------- запись
    async def create(self, db: AsyncSession, kind: str, name: str, config: dict, secret: str, *, make_default: bool = False) -> dict:
        kind = self._check_kind(kind)
        name = (name or "").strip()
        if not name or len(name) > 120:
            raise SettingsError("Название профиля: 1–120 символов")
        if name == MAIN_NAME:
            raise SettingsError("Это название зарезервировано")
        dup = (await db.execute(select(ApiProfile).where(ApiProfile.kind == kind, ApiProfile.name == name))).scalars().first()
        if dup:
            raise SettingsError("Профиль с таким названием уже есть")
        config, hdrs = self._split_headers(kind, config)
        self._build(kind, config, secret, hdrs)  # проверка значений
        pid = uuid.uuid4()
        clean = {k: v for k, v in config.items() if k in _MODELS[kind].model_fields and k != _SECRET[kind]}
        row = ApiProfile(id=pid, kind=kind, name=name, config=clean, secret_enc=self._encrypt(pid, self._pack(secret, hdrs or {})), is_default=False)
        db.add(row)
        await db.flush()
        if make_default:
            await self._set_default(db, kind, str(pid))
        await db.commit()
        return self._public(row, await self.default_id(db, kind))

    async def update(self, db: AsyncSession, profile_id: str, patch: dict) -> dict:
        row = await self._row(db, profile_id)
        kind = self._check_kind(row.kind)
        new_name = patch.get("name")
        if new_name is not None:
            new_name = new_name.strip()
            if not new_name or len(new_name) > 120 or new_name == MAIN_NAME:
                raise SettingsError("Название профиля: 1–120 символов")
            dup = (await db.execute(select(ApiProfile).where(ApiProfile.kind == kind, ApiProfile.name == new_name,
                                                              ApiProfile.id != row.id))).scalars().first()
            if dup:
                raise SettingsError("Профиль с таким названием уже есть")
        cfg = dict(row.config or {})
        old_key, old_hdrs = self._unpack(self._decrypt(row))
        new_cfg, new_hdrs = self._split_headers(kind, patch.get("config") or {}, old_hdrs)
        for k, v in new_cfg.items():
            if k in _MODELS[kind].model_fields and k != _SECRET[kind]:
                cfg[k] = v
        secret = old_key
        if "secret" in patch and patch["secret"] is not None:   # None = не менять, "" = очистить
            secret = patch["secret"]
        hdrs = old_hdrs if new_hdrs is None else new_hdrs
        self._build(kind, cfg, secret, hdrs)
        if new_name is not None:
            row.name = new_name
        row.config = cfg
        if ("secret" in patch and patch["secret"] is not None) or new_hdrs is not None:
            row.secret_enc = self._encrypt(row.id, self._pack(secret, hdrs))
        row.updated_at = utcnow()
        await db.commit()
        return self._public(row, await self.default_id(db, kind))

    async def delete(self, db: AsyncSession, profile_id: str) -> None:
        row = await self._row(db, profile_id)
        await db.execute(update(Room).where(Room.llm_profile_id == row.id).values(llm_profile_id=None))
        await db.execute(update(Room).where(Room.anonymizer_profile_id == row.id).values(anonymizer_profile_id=None))
        await db.delete(row)
        await db.commit()

    async def set_default(self, db: AsyncSession, kind: str, profile_id: str) -> None:
        kind = self._check_kind(kind)
        if profile_id != MAIN:
            row = await self._row(db, profile_id)
            if row.kind != kind:
                raise SettingsError("Профиль другого типа")
        await self._set_default(db, kind, profile_id)
        await db.commit()

    async def _set_default(self, db: AsyncSession, kind: str, profile_id: str) -> None:
        await db.execute(update(ApiProfile).where(ApiProfile.kind == kind).values(is_default=False))
        if profile_id != MAIN:
            await db.execute(update(ApiProfile).where(ApiProfile.id == uuid.UUID(profile_id)).values(is_default=True))

    async def _row(self, db: AsyncSession, profile_id: str) -> ApiProfile:
        try:
            row = await db.get(ApiProfile, uuid.UUID(profile_id))
        except ValueError:
            row = None
        if row is None:
            raise SettingsError("Профиль API не найден")
        return row
