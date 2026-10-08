"""Перенос прежней настройки LDAP (`LDAP_*` в `.env`, версии до 0.3.0) в управляемые настройки (подключения, CA, группы доступа).

Прежняя настройка продолжает работать как запасная («legacy fallback») — вход по домену не ломается после обычного обновления. Перенос
выполняется автоматически при запуске (фоном) либо кнопкой «Импортировать в управляемые настройки», и считается выполненным ТОЛЬКО если
после импорта проверка подключения прошла: иначе всё откатывается, а прежняя настройка остаётся рабочей и видимой.

Пароль сервисной учётной записи берётся из окружения процесса и сразу шифруется (AES-GCM, ключ `APP_MASTER_KEY`); в ответах, журнале и
аудите его нет. В файл `.env` ничего не записывается.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings
from ..models import LdapProfile
from ..util_errors import describe_error
from .ca_bundle import CaBundleService, CertError
from .ldap_profiles import LdapService
from .settings import SettingsError, SettingsService

log = logging.getLogger("app.ldap.legacy")
LOCK_KEY, LOCK_TTL = "ldap:legacy-import", 120


class LegacyImportError(Exception):
    """Перенос не выполнен; текст безопасен для показа администратору. `stages` — результат проверки подключения, если она была."""

    def __init__(self, message: str, stages: list[dict] | None = None):
        super().__init__(message)
        self.stages = stages or []


def _split_uri(uri: str) -> tuple[str, int]:
    rest = uri.split("://", 1)[1].split("/")[0]
    host, _, port = rest.partition(":")
    return host, int(port) if port.isdigit() else 636


class LegacyLdapMigrator:
    def __init__(self, settings: Settings, ldap: LdapService, ca: CaBundleService, svc: SettingsService):
        self._s, self._ldap, self._ca, self._svc = settings, ldap, ca, svc
        self.last_error: str = ""           # итог последней автоматической попытки (для баннера в админке)

    def present(self) -> bool:
        return bool(self._s.ldap_uri_list)

    async def status(self, db: AsyncSession, directory: Any = None) -> dict:
        n = (await db.execute(select(func.count()).select_from(LdapProfile))).scalar_one()
        ca_file = self._s.ldap_ca_file
        readable = bool(ca_file) and Path(ca_file).is_file()
        migrated = n > 0
        return {
            "present": self.present(), "migrated": migrated,
            "active": self.present() and not migrated and bool(getattr(directory, "configured", False)),     # прямо сейчас вход идёт по старой настройке
            "needs_import": self.present() and not migrated,
            "uris": self._s.ldap_uri_list, "base_dn": self._s.ldap_base_dn, "bind_dn": self._s.ldap_bind_dn,
            "password_set": bool(self._s.ldap_bind_password), "ca_file": ca_file or None, "ca_file_readable": readable,
            "admin_group_dn": self._s.ldap_admin_group_dn or None, "access_group_dn": self._s.ldap_access_group_dn or None,
            "last_error": self.last_error or None,
        }

    async def import_now(self, db: AsyncSession, actor: str) -> dict:
        """Импорт одной транзакцией. При любой неудаче (в том числе проверки подключения) — откат и LegacyImportError."""
        if not self.present():
            raise LegacyImportError("Прежней настройки LDAP в .env нет — переносить нечего.")
        if (await db.execute(select(func.count()).select_from(LdapProfile))).scalar_one():
            raise LegacyImportError("Управляемые подключения уже есть — перенос не требуется.")
        if not (self._s.ldap_bind_dn and self._s.ldap_bind_password and self._s.ldap_base_dn):
            raise LegacyImportError("В .env неполная настройка LDAP (нужны LDAP_BASE_DN, LDAP_BIND_DN и LDAP_BIND_PASSWORD) — создайте подключение вручную.")
        if self._svc.box is None:
            raise LegacyImportError("Не задан ключ шифрования APP_MASTER_KEY — пароль сервисной учётной записи сохранить нельзя.")
        try:
            return await self._do_import(db, actor)
        except (LegacyImportError, SettingsError, CertError, OSError) as exc:
            await db.rollback()
            await self._safe_rebuild(db)           # файл набора CA вернуть к состоянию базы
            if isinstance(exc, LegacyImportError):
                raise
            raise LegacyImportError(describe_error(exc) if isinstance(exc, OSError) else str(exc)) from None

    async def _safe_rebuild(self, db: AsyncSession) -> None:
        try:
            await self._ca.rebuild(db)
        except Exception as exc:  # noqa: BLE001
            log.warning("Не удалось пересобрать набор CA после отката: %s", describe_error(exc))

    async def _do_import(self, db: AsyncSession, actor: str) -> dict:
        s = self._s
        added = 0
        ca_file = s.ldap_ca_file
        if ca_file and Path(ca_file).is_file():
            data = Path(ca_file).read_bytes()
            new, _existing = await self._ca.add(db, data, "Импорт из прежней настройки (LDAP_CA_FILE)", actor, confirm_non_ca=True)
            added = len(new)
        elif ca_file:
            raise LegacyImportError(f"Файл CA из LDAP_CA_FILE недоступен процессу приложения ({ca_file}): положите сертификат на сервере или загрузите его в разделе «Сертификаты CA».")
        created: list[LdapProfile] = []
        uris = s.ldap_uri_list
        for n, uri in enumerate(uris, 1):
            host, port = _split_uri(uri)
            name = "Основной каталог (из .env)" if len(uris) == 1 else f"Каталог из .env ({n})"
            out = await self._ldap.create(db, {
                "name": name, "host": host, "port": port, "protocol": "ldaps", "base_dn": s.ldap_base_dn, "bind_dn": s.ldap_bind_dn,
                "login_attribute": s.ldap_login_attribute, "display_name_attribute": s.ldap_display_name_attribute, "timeout_s": s.ldap_timeout_seconds,
            }, s.ldap_bind_password)
            created.append(await self._ldap.row(db, out["id"]))
        await self._ca.rebuild(db)
        # проверка: хотя бы одно из перенесённых подключений должно реально отвечать (остальные — запасные контроллеры)
        results = [await asyncio.to_thread(self._ldap.diagnose, r) for r in created]
        if not any(r["ok"] for r in results):
            bad = next((st for st in results[0]["stages"] if not st.get("ok")), None) if results else None
            why = f" Этап «{bad.get('stage')}»: {bad.get('detail') or bad.get('message') or 'ошибка'}." if bad else ""
            raise LegacyImportError("Проверка подключения после переноса не прошла — настройки не перенесены, прежняя настройка продолжает работать." + why,
                                    stages=results[0]["stages"] if results else [])
        groups_added = await self._import_groups(db, actor)
        await db.commit()
        return {"profiles": [r.name for r in created], "ca_added": added, "groups_added": groups_added,
                "stages": results[0]["stages"] if results else []}

    async def _import_groups(self, db: AsyncSession, actor: str) -> int:
        s = self._s
        acc = await self._svc.get(db, "access")
        patch: dict = {}
        n = 0
        for key, dn in (("admin_groups", s.ldap_admin_group_dn), ("user_groups", s.ldap_access_group_dn)):
            have = list(getattr(acc, key))
            if dn.strip() and dn.strip().lower() not in {g.lower() for g in have}:
                patch[key] = have + [dn.strip()]
                n += 1
        if patch:
            await self._svc.update(db, "access", patch, actor=actor)
        return n

    async def auto_import(self, session_maker, redis, directory, actor: str = "система (обновление)") -> bool:
        """Фоновая попытка при запуске. Один запуск на кластер (блокировка в Redis). Возвращает True, если перенос выполнен."""
        if not self.present():
            return False
        try:
            if not await redis.set(LOCK_KEY, "1", nx=True, ex=LOCK_TTL):
                return False
        except Exception:  # noqa: BLE001
            pass
        try:
            async with session_maker() as db:
                try:
                    out = await self.import_now(db, actor)
                except LegacyImportError as exc:
                    self.last_error = str(exc)
                    log.warning("Автоматический перенос LDAP из .env не выполнен: %s", self.last_error)
                    return False
                if hasattr(directory, "reload"):
                    await directory.reload(db)
                self.last_error = ""
                log.info("Прежняя настройка LDAP перенесена в управляемые: %s", ", ".join(out["profiles"]))
                return True
        finally:
            try:
                await redis.delete(LOCK_KEY)
            except Exception:  # noqa: BLE001
                pass
