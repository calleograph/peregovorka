"""Ручные правки профиля пользователем: приоритет над данными каталога (LDAP) и портала.

Хранение — в `User.profile_sources`: значения пользователя лежат в источнике `local`, а даты правок — в `manual_at` {поле: ISO-время}. Поле с отметкой в
`manual_at` — «переопределено»: значение пользователя восстанавливается поверх любой синхронизации (вход, «обновить из AD», фоновое обновление с портала).
«Восстановить из LDAP» убирает правку, и поле снова считается по обычным приоритетам источников. Значения каталога всегда сохраняются отдельно (`ad`) и показываются рядом.
"""
from __future__ import annotations

from datetime import datetime, timezone

from ..models import User
from .base import FIELDS, clean


class ManualError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def editable_set(policy) -> set[str]:
    """Поля, которые разрешено править по политике (`profile_policy.editable_fields`); неизвестные имена игнорируются."""
    raw = getattr(policy, "editable_fields", "") if policy is not None else ""
    return {f.strip() for f in raw.split(",") if f.strip() in FIELDS}


def manual_fields(user: User) -> dict[str, str]:
    src = user.profile_sources or {}
    local, at = src.get("local") or {}, src.get("manual_at") or {}
    return {f: local[f] for f in FIELDS if f in at and local.get(f)}


def manual_date(user: User, field: str) -> str | None:
    return ((user.profile_sources or {}).get("manual_at") or {}).get(field)


def set_manual(user: User, changes: dict[str, str], allowed: set[str], *, now: datetime | None = None) -> dict[str, tuple[str | None, str]]:
    """Применяет правки; возвращает {поле: (прежнее, новое)} только по реально изменившимся полям. Нельзя править то, что не разрешено политикой."""
    now = now or datetime.now(timezone.utc)
    out: dict[str, tuple[str | None, str]] = {}
    clean_vals: dict[str, str] = {}
    for f, raw in changes.items():
        if f not in FIELDS:
            raise ManualError(f"Неизвестное поле: {f}")
        if f not in allowed:
            raise ManualError("Это поле нельзя изменять: администратор запретил его правку.")
        v = clean(f, raw)
        if not v:
            raise ManualError("Значение не может быть пустым. Чтобы вернуть данные каталога, используйте «Восстановить из LDAP».")
        if f == "email" and ("@" not in v or " " in v):
            raise ManualError("Укажите корректный адрес электронной почты.")
        clean_vals[f] = v
    src = {k: dict(v) if isinstance(v, dict) else v for k, v in (user.profile_sources or {}).items()}
    local, at = dict(src.get("local") or {}), dict(src.get("manual_at") or {})
    for f, v in clean_vals.items():
        old = getattr(user, f, None)
        if f in at and local.get(f) == v:
            continue                                               # то же значение, уже переопределённое: дата не меняется
        local[f], at[f] = v, now.isoformat()
        out[f] = (old, v)
        setattr(user, f, v)
    src["local"], src["manual_at"] = local, at
    user.profile_sources = src
    return out


def restore(user: User, fields: list[str], *, ad_fallback: dict[str, str] | None = None) -> list[str]:
    """Снимает правки по полям: значение берётся из каталога, затем из портала; если источники его не знают — поле пустеет. Возвращает список восстановленных полей."""
    src = {k: dict(v) if isinstance(v, dict) else v for k, v in (user.profile_sources or {}).items()}
    local, at = dict(src.get("local") or {}), dict(src.get("manual_at") or {})
    done: list[str] = []
    for f in fields:
        if f in at:
            at.pop(f), local.pop(f, None)
            done.append(f)
            val = ((src.get("ad") or {}).get(f)) or ((src.get("bitrix") or {}).get(f)) or (ad_fallback or {}).get(f)
            if val:
                setattr(user, f, val)
            elif f != "display_name":                              # источники значения не знают: поле очищается (ФИО обязательно и остаётся прежним)
                setattr(user, f, None)
    src["local"], src["manual_at"] = local, at
    user.profile_sources = src
    return done


def apply_manual(user: User) -> None:
    """Вызывается после любой синхронизации: значения пользователя возвращаются на место."""
    for f, v in manual_fields(user).items():
        if getattr(user, f, None) != v:
            setattr(user, f, v)


def field_view(user: User, policy) -> list[dict]:
    """Карточка поля для интерфейса: итог, откуда он, значение каталога, дата правки, можно ли править."""
    src = user.profile_sources or {}
    ad, allowed = src.get("ad") or {}, editable_set(policy)
    out = []
    for f in FIELDS:
        cur = getattr(user, f, None)
        manual = f in (src.get("manual_at") or {})
        source = "local" if manual else "bitrix" if (src.get("bitrix") or {}).get(f) == cur and cur and ad.get(f) != cur else "ad"
        out.append({"name": f, "value": cur, "source": source if user.auth_source != "local" else "local", "manual": manual, "manual_at": manual_date(user, f),
                    "ad_value": ad.get(f), "editable": f in allowed})
    return out
