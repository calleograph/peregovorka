"""Импорт конфигурации: проверка содержимого архива, предпросмотр и применение одной транзакцией (при любой ошибке — полный откат).

Принципы: содержимое архива — недоверенные данные (проверяются структура, типы, имена таблиц/столбцов/файлов, размеры, идентификаторы); секреты шифруются ключом ЭТОГО сервера;
пользователи, сессии и локальный администратор не затрагиваются; идентификаторы записей сохраняются (на них ссылаются настройки комнат и AAD секретов); существующие записи с тем же
уникальным именем или адресом переговорки заменяются (если у переговорки нет встреч), всё остальное, чего нет в архиве, остаётся. Файлы оформления пишутся после успешной фиксации в базе.
"""
from __future__ import annotations

import base64
import ipaddress
import re
import uuid
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models import AppSetting, Base, Meeting, utcnow
from ..settings import GROUPS, SettingsService
from . import registry as R
from .exporter import deser, models_by_table, py_type

MAX_ROWS_PER_TABLE = 20_000
MAX_FILE_BYTES = 8 << 20
IMPORT_ACTOR = "импорт конфигурации"


class ImportError_(Exception):
    """Ошибка проверки или применения архива; текст показывается администратору."""

    def __init__(self, message: str, *, details: list[str] | None = None):
        super().__init__(message)
        self.message, self.details = message, details or []


# ------------------------------------------------------------------------------------------------ структура
def validate_payload(payload: dict) -> dict:
    """Проверка недоверенного содержимого; возвращает payload, преобразованный до текущей схемы."""
    man = payload.get("manifest")
    if not isinstance(man, dict) or man.get("kind") != "peregovorka-config":
        raise ImportError_("Архив не содержит описания (manifest): это не копия конфигурации Peregovorka.")
    try:
        schema = int(man.get("schema"))
    except (TypeError, ValueError):
        raise ImportError_("В описании архива нет версии схемы.") from None
    if schema > R.SCHEMA_VERSION:
        raise ImportError_(f"Архив создан более новой версией Peregovorka (схема {schema}, эта версия понимает до {R.SCHEMA_VERSION}). Обновите сервер и повторите импорт.")
    try:
        payload = R.upgrade_payload(payload, schema)
    except ValueError as exc:
        raise ImportError_(str(exc)) from None
    problems: list[str] = []
    settings = payload.get("settings", {})
    tables = payload.get("tables", {})
    files = payload.get("files", {})
    if not isinstance(settings, dict) or not isinstance(tables, dict) or not isinstance(files, dict):
        raise ImportError_("Содержимое архива имеет неожиданную структуру.")
    models = models_by_table()
    for g, vals in settings.items():
        if g not in GROUPS:
            problems.append(f"неизвестная группа настроек «{g}»")
            continue
        if not isinstance(vals, dict):
            problems.append(f"настройки «{g}»: неверная структура")
            continue
        for f in vals:
            if f not in GROUPS[g].model_fields:
                problems.append(f"настройка «{g}.{f}» неизвестна этой версии")
    for name, rows in tables.items():
        pol = R.TABLES.get(name)
        if pol is None or not pol.export:
            problems.append(f"таблица «{name}» не входит в состав конфигурации")
            continue
        if not isinstance(rows, list) or len(rows) > MAX_ROWS_PER_TABLE:
            problems.append(f"таблица «{name}»: неверная структура или слишком много записей")
            continue
        cols = set(pol.columns)
        t = models[name].__table__
        for i, row in enumerate(rows):
            if not isinstance(row, dict):
                problems.append(f"{name}[{i}]: запись должна быть объектом")
                break
            extra = set(row) - cols
            if extra:
                problems.append(f"{name}[{i}]: неизвестные столбцы {sorted(extra)}")
                break
            try:
                for c, v in row.items():
                    deser(v, t.columns[c])
            except Exception:  # noqa: BLE001
                problems.append(f"{name}[{i}]: значение столбца не соответствует типу")
                break
    for k, v in files.items():
        if k not in R.FILES:
            problems.append(f"файл «{k}» не входит в состав конфигурации")
        else:
            try:
                if len(base64.b64decode(v, validate=True)) > MAX_FILE_BYTES:
                    problems.append(f"файл «{k}» слишком велик")
            except Exception:  # noqa: BLE001
                problems.append(f"файл «{k}»: повреждённые данные")
    if problems:
        raise ImportError_("Архив отклонён: содержимое не прошло проверку.", details=problems[:30])
    payload.setdefault("secret_fields", {})
    return payload


# ------------------------------------------------------------------------------------------------ предпросмотр
_IP = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")
_URL = re.compile(r"\bhttps?://[^\s\"']+|\bwss?://[^\s\"']+", re.I)
_UNC_OR_PATH = re.compile(r"^(?:/[\w.\-/]+|[A-Za-z]:\\.+|\\\\[\w.\-]+\\.+)$")


def _scan_hint(val: Any) -> str | None:
    if not isinstance(val, str) or not val or len(val) > 500:
        return None
    if _URL.search(val):
        return R.BOUND_URL
    for m in _IP.findall(val):
        try:
            ipaddress.ip_address(m)
            return R.BOUND_ADDRESS
        except ValueError:
            continue
    if _UNC_OR_PATH.match(val.strip()):
        return R.BOUND_PATH
    return None


# параметры окружения, отличия которых заметно меняют поведение (остальные — просто считаются)
ENV_IMPORTANT = {
    "ldap_admin_group_dn": "группа администраторов каталога из .env действует вместе с настройками «Доступ к системе» — без неё администраторов из каталога не будет",
    "ldap_access_group_dn": "группа допуска из .env действует вместе с настройками «Кто может входить»",
    "app_public_url": "внешний адрес: от него строятся ссылки в письмах и приглашениях", "livekit_public_url": "адрес сервера звонков, который получают браузеры",
    "default_text_retention_days": "срок хранения текста по умолчанию", "default_audio_retention_days": "срок хранения аудио по умолчанию",
    "trusted_proxy_hops": "доверенные прокси: определение адреса клиента", "trusted_proxy_cidrs": "доверенные прокси: определение адреса клиента",
    "cookie_secure": "защищённые cookie (нужен HTTPS)", "local_llm_enabled": "встроенная локальная модель", "sip_enabled": "SIP-телефония",
}


def environment_diff(source: dict, current) -> dict:
    """Чем окружение исходного сервера отличается от этого. Переносить значения автоматически нельзя (.env задаёт администратор при развёртывании) — их нужно сверить вручную."""
    important, other = [], 0
    for name, src in (source or {}).items():
        if current is None or not hasattr(current, name):
            continue
        here = getattr(current, name)
        if R.is_secret_env(name):
            now = "задан" if here else "не задан"
        else:
            now = "" if here is None else str(here)[:200]
        if str(src) == now:
            continue
        if name in ENV_IMPORTANT:
            important.append({"name": name.upper(), "source": src, "here": now, "note": ENV_IMPORTANT[name]})
        else:
            other += 1
    return {"important": important, "other_differences": other}


async def preview(payload: dict, db: AsyncSession, *, current_version: str, current_url: str, current_env=None, local_ids_note: bool = True) -> dict:
    man = payload["manifest"]
    models = models_by_table()
    warnings: list[dict] = []

    def warn(kind: str, where: str, value: Any = None, note: str = "") -> None:
        warnings.append({"kind": kind, "title": R.BOUND_TITLE.get(kind, kind), "where": where, "value": (str(value)[:200] if value not in (None, "") else None), "note": note})

    # --- привязка к серверу: объявленная в реестре
    for name, rows in payload["tables"].items():
        pol = R.TABLES[name]
        for row in rows:
            label = row.get("name") or row.get("slug") or row.get("label") or str(row.get("id", ""))[:8]
            for col, kind in pol.bound.items():
                v = row.get(col)
                if v in (None, "", [], {}):
                    continue
                if col == "config" and isinstance(v, dict):
                    v = ", ".join(f"{k}={x}" for k, x in v.items() if k in ("local_path", "smb_server", "smb_share", "smb_base_path", "base_url", "url") and x)
                if col in ("lk_outbound_trunk_id", "lk_inbound_trunk_id", "sip_dispatch_rule_id"):
                    continue
                warn(kind, f"{pol.title or name}: «{label}» — {col}", v)
    for g, vals in payload["settings"].items():
        gp = R.GROUPS_POLICY[g]
        for f, kind in gp.bound.items():
            v = vals.get(f)
            if v in (None, "", [], {}) or f in payload["secret_fields"].get(g, []):
                continue
            warn(kind, f"{gp.title or g} — {f}", v)
        # эвристика: значения, похожие на адреса/пути, но не объявленные привязанными
        for f, v in vals.items():
            if f in gp.bound or f in payload["secret_fields"].get(g, []):
                continue
            hint = _scan_hint(v)
            if hint:
                warn(hint, f"{gp.title or g} — {f}", v, "похоже на адрес или путь старого сервера — проверьте")
    ext = [r.get("name") for r in payload["tables"].get("storage_profiles", []) if isinstance(r.get("config"), dict) and r["config"].get("external_volume")]
    if ext:
        warn(R.BOUND_PATH, "Файловые хранилища", ", ".join(map(str, ext)), "отмечены как внешний том: после восстановления смонтируйте том на новом сервере и нажмите «Проверить» — метка тома и защита от записи на системный диск ставятся заново")
    sips = len(payload["tables"].get("sip_profiles", []))
    if sips:
        warn(R.BOUND_LIVEKIT, "SIP-телефония", None, f"подключений: {sips}. Идентификаторы транков LiveKit старого сервера не переносятся — после импорта синхронизируйте SIP в разделе «SIP-телефония»")
    if payload["tables"].get("ca_certificates"):
        warn(R.BOUND_CERT, "Сертификаты УЦ", len(payload["tables"]["ca_certificates"]), "сертификаты переносятся; убедитесь, что имя нового сервера соответствует сертификатам каталога/почты, а TLS-сертификат веб-сервера (nginx) установлен отдельно")
    src = str(man.get("source_url") or "")
    if src and current_url and src.rstrip("/") != current_url.rstrip("/"):
        warn(R.BOUND_URL, "Адрес установки", src, f"архив создан на {src}, этот сервер — {current_url}. Приглашения, ссылки в письмах и гостевые ссылки строятся от адреса нового сервера")
    users = sum(1 for t in ("room_acl", "room_moderators") for r in payload["tables"].get(t, []) if r.get("subject_type") == "user")
    notes: list[str] = []
    if users:
        notes.append(f"{users} назначений отдельным пользователям (доступ и руководители переговорок): пользователи каталога появятся на новом сервере при первом входе, назначения сопоставятся по идентификатору каталога")
    # --- что будет заменено на этом сервере
    conflicts: list[str] = []
    replace: dict[str, int] = {}
    for name, rows in payload["tables"].items():
        model = models[name]
        ids = [deser(r["id"], model.__table__.columns["id"]) for r in rows if "id" in r]
        if ids:
            n = (await db.execute(select(func.count()).select_from(model).where(model.id.in_(ids)))).scalar_one()
            if n:
                replace[R.TABLES[name].title or name] = n
    if "rooms" in payload["tables"]:
        room = models["rooms"]
        for r in payload["tables"]["rooms"]:
            ex = (await db.execute(select(room).where(room.slug == r["slug"], room.id != uuid.UUID(r["id"])))).scalars().first()
            if ex is not None:
                has = (await db.execute(select(func.count()).select_from(Meeting).where(Meeting.room_id == ex.id))).scalar_one()
                conflicts.append(f"переговорка с адресом «{r['slug']}» уже есть на этом сервере" + (f" и у неё есть встречи ({has}) — импорт невозможен, пока адрес занят" if has else " (без встреч): будет заменена"))
    existing_settings = (await db.execute(select(func.count()).select_from(AppSetting))).scalar_one()
    old, cur = str(man.get("app_version", "")), current_version
    compat = {"ok": True, "notes": []}
    if old and cur and old != cur:
        compat["notes"].append(f"архив создан на версии {old}, этот сервер — {cur}: содержимое будет преобразовано к текущей схеме настроек (схема архива {man.get('schema')}, текущая {R.SCHEMA_VERSION})")
    hashed = []
    rooms_pw = sum(1 for r in payload["tables"].get("rooms", []) if r.get("password_hash"))
    if rooms_pw:
        hashed.append({"what": "пароли переговорок", "count": rooms_pw, "how": "перенесены как хэш — пароли продолжат работать; показать их невозможно"})
    if payload["tables"].get("api_keys"):
        hashed.append({"what": "ключи интеграций API", "count": len(payload["tables"]["api_keys"]), "how": "перенесены как хэш — ключи продолжат работать; сами ключи восстановить нельзя"})
    return {
        "manifest": {k: man.get(k) for k in ("app_version", "created_at", "schema", "source_url")}, "compat": compat,
        "components": {"tables": [{"name": n, "title": R.TABLES[n].title or n, "count": len(r)} for n, r in payload["tables"].items()],
                       "settings": [{"name": g, "title": R.GROUPS_POLICY[g].title or g, "fields": len(v)} for g, v in payload["settings"].items()],
                       "files": [{"name": k, "title": R.FILES[k]} for k in payload["files"]]},
        "warnings": warnings, "notes": notes, "hashed": hashed, "conflicts": conflicts, "replace": replace, "excluded": man.get("excluded_tables", {}), "manifest_warnings": man.get("warnings", []),
        "target_clean": existing_settings == 0, "needs_ack": bool(warnings or conflicts or replace), "environment": environment_diff(man.get("environment", {}), current_env),
    }


# ------------------------------------------------------------------------------------------------ применение
def _unique_sets(table) -> list[tuple[str, ...]]:
    out: list[tuple[str, ...]] = []
    for c in table.constraints:
        if c.__class__.__name__ == "UniqueConstraint":
            out.append(tuple(col.name for col in c.columns))
    for c in table.columns:
        if c.unique and not c.primary_key:
            out.append((c.name,))
    return out


async def apply(payload: dict, db: AsyncSession, settings_svc: SettingsService, *, branding=None) -> dict:
    """Применить в ОДНОЙ транзакции (коммит делает вызывающий после успеха; при исключении — откат). Возвращает счётчики и список файлов для записи после коммита."""
    box = settings_svc.box
    models = models_by_table()
    counts: dict[str, int] = {}
    replaced: list[str] = []
    for name in R.exported_tables():
        rows = payload["tables"].get(name)
        if not rows:
            continue
        pol, model = R.TABLES[name], models[name]
        table = model.__table__
        uniques = _unique_sets(table)
        for row in rows:
            data: dict[str, Any] = {}
            for col, policy in pol.columns.items():
                if policy in (R.RESET, R.USER_REF):
                    if table.columns[col].nullable:    # в архиве таких столбцов нет: новая запись получает пустое значение, существующая — сбрасывается
                        data[col] = None
                    continue
                if col not in row:
                    continue
                v = deser(row[col], table.columns[col])
                if name == "storage_profiles" and col == "config" and isinstance(v, dict):
                    v = {k: x for k, x in v.items() if k not in ("volume_marker", "volume_mount")}
                if policy == R.SECRET:
                    if v:
                        if box is None:
                            raise ImportError_("APP_MASTER_KEY не задан на этом сервере — секреты сохранить нельзя.")
                        v = box.encrypt(v, aad=pol.aad({"id": str(row["id"])}))
                    else:
                        v = "" if not table.columns[col].nullable else None
                data[col] = v
            pk_col = [c.name for c in table.primary_key.columns][0]
            pk_val = data.get(pk_col)
            # записи с тем же уникальным значением, но другим первичным ключом, заменяются (для переговорок — только без встреч)
            for u in uniques:
                if any(c not in data or data[c] is None for c in u):      # NULL не считается совпадением (несколько записей с пустым значением допустимы)
                    continue
                cond = [getattr(model, c) == data[c] for c in u]
                dup = (await db.execute(select(model).where(*cond, getattr(model, pk_col) != pk_val))).scalars().all()
                for d in dup:
                    if name == "rooms":
                        has = (await db.execute(select(func.count()).select_from(Meeting).where(Meeting.room_id == d.id))).scalar_one()
                        if has:
                            raise ImportError_(f"Переговорка с адресом «{data.get('slug')}» уже есть на этом сервере и у неё есть встречи: импорт отменён, ничего не изменено.")
                    replaced.append(f"{pol.title or name}: «{getattr(d, 'name', None) or getattr(d, 'slug', None) or getattr(d, pk_col)}» заменена записью из архива")
                    await db.delete(d)
                await db.flush()
            existing = await db.get(model, pk_val)
            if existing is None:
                db.add(model(**data))
            else:
                for k, v in data.items():
                    setattr(existing, k, v)
            counts[name] = counts.get(name, 0) + 1
        await db.flush()
    # настройки: поля из архива перезаписывают значения этого сервера; секреты шифруются ключом этого сервера
    set_counts: dict[str, int] = {}
    for g, vals in payload["settings"].items():
        model = GROUPS[g]
        try:
            model(**{k: v for k, v in vals.items()})              # проверка типов и границ
        except Exception as exc:  # noqa: BLE001
            raise ImportError_(f"Настройки «{R.GROUPS_POLICY[g].title or g}» не прошли проверку: {str(exc)[:200]}") from None
        for f, v in vals.items():
            key = f"{g}.{f}"
            is_secret = f in model.SECRETS
            if is_secret:
                if v and box is None:
                    raise ImportError_("APP_MASTER_KEY не задан на этом сервере — секреты сохранить нельзя.")
                stored = box.encrypt(str(v), aad=key.encode()) if v else ""
            else:
                import json as _json  # noqa: PLC0415

                stored = _json.dumps(v, ensure_ascii=False)
            row = await db.get(AppSetting, key)
            if row is None:
                db.add(AppSetting(key=key, value=stored, is_secret=is_secret, updated_by=IMPORT_ACTOR))
            else:
                row.value, row.is_secret, row.updated_by, row.updated_at = stored, is_secret, IMPORT_ACTOR, utcnow()
            set_counts[g] = set_counts.get(g, 0) + 1
    await db.flush()
    files = {k: base64.b64decode(v) for k, v in payload["files"].items()}
    return {"tables": counts, "settings": set_counts, "files": sorted(files), "replaced": replaced, "_files": files}


async def stage_files(files: dict[str, bytes], branding, ca, db: AsyncSession) -> list:
    """Подготовить файлы (изображения оформления и набор сертификатов) рядом с боевыми — до фиксации базы. Ошибка здесь отменяет импорт целиком."""
    from ..branding import BrandingError, process  # noqa: PLC0415
    from .files import Staged, discard  # noqa: PLC0415

    staged: list = []
    try:
        if branding is not None:
            for k, data in files.items():
                kind = k.split("/", 1)[1]
                try:
                    tmp, final = branding.stage(kind, process(kind, data))
                except (BrandingError, OSError) as exc:
                    raise ImportError_(f"Не удалось подготовить изображение «{R.FILES.get(k, k)}»: {exc}") from None
                staged.append(Staged(final=final, tmp=tmp, label=R.FILES.get(k, k)))
        if ca is not None:
            try:
                tmp, final = await ca.stage(db)
            except OSError as exc:
                raise ImportError_(f"Не удалось подготовить набор сертификатов: {exc}") from None
            staged.append(Staged(final=final, tmp=tmp, remove=tmp is None, label="набор доверенных сертификатов"))
    except Exception:
        discard(staged)
        raise
    return staged
