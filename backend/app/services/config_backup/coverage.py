"""Проверка полноты реестра конфигурации: находит параметры, для которых не выбрана политика переноса. Используется тестами и самим экспортом (неполный реестр — отказ, а не «тихая» неполная копия)."""
from __future__ import annotations

from pathlib import Path
import re

from sqlalchemy import MetaData

from . import registry as R


def table_problems(metadata: MetaData) -> list[str]:
    out: list[str] = []
    names = set(metadata.tables)
    for n in sorted(names - set(R.TABLES)):
        out.append(f"таблица «{n}» не описана в реестре конфигурации (config_backup/registry.py: TABLES) — выберите: переносится или нет, и почему")
    for n in sorted(set(R.TABLES) - names):
        out.append(f"в реестре описана несуществующая таблица «{n}»")
    for n, p in R.TABLES.items():
        t = metadata.tables.get(n)
        if t is None or not p.export:
            continue
        cols = {c.name for c in t.columns}
        for c in sorted(cols - set(p.columns)):
            out.append(f"столбец «{n}.{c}» не описан в реестре: data / secret / hash / reset / user_ref")
        for c in sorted(set(p.columns) - cols):
            out.append(f"в реестре описан несуществующий столбец «{n}.{c}»")
        for c, pol in p.columns.items():
            if pol not in R.POLICIES:
                out.append(f"«{n}.{c}»: неизвестная политика «{pol}»")
        if any(pol == R.SECRET for pol in p.columns.values()) and p.aad is None:
            out.append(f"«{n}»: есть зашифрованные столбцы, но не задано правило AAD")
        for c in p.hashes:
            if p.columns.get(c) != R.HASH:
                out.append(f"«{n}.{c}» указан как хэш, но политика столбца другая")
        for c in p.bound:
            if c not in cols:
                out.append(f"«{n}.{c}»: привязка к серверу указана для несуществующего столбца")
        if not any(c.primary_key for c in t.columns):
            out.append(f"«{n}»: нет первичного ключа")
    return out


def group_problems(groups: dict) -> list[str]:
    out: list[str] = []
    for g in sorted(set(groups) - set(R.GROUPS_POLICY)):
        out.append(f"группа настроек «{g}» не описана в реестре конфигурации (GROUPS_POLICY)")
    for g in sorted(set(R.GROUPS_POLICY) - set(groups)):
        out.append(f"в реестре описана несуществующая группа настроек «{g}»")
    for g, model in groups.items():
        pol = R.GROUPS_POLICY.get(g)
        if pol is None:
            continue
        fields = set(model.model_fields)
        for name in (*pol.skip, *pol.bound):
            if name not in fields:
                out.append(f"«{g}.{name}»: указан в реестре, но такого поля нет")
        for s in model.SECRETS:
            if s not in fields:
                out.append(f"«{g}»: секретное поле «{s}» не существует")
    return out


def env_problems(settings_fields: set[str]) -> list[str]:
    out = [f"параметр окружения «{n}» не описан в реестре конфигурации (ENV) — укажите, почему он не переносится" for n in sorted(settings_fields - set(R.ENV))]
    out += [f"в реестре описан несуществующий параметр окружения «{n}»" for n in sorted(set(R.ENV) - settings_fields)]
    return out


_ENV_READ = re.compile(r"""(?:os\.environ(?:\.get)?\(|os\.environ\[|os\.getenv\()\s*["']([A-Za-z_][A-Za-z0-9_]*)["']""")


def env_read_problems(app_dir: Path) -> list[str]:
    """Переменные окружения, которые код читает напрямую (мимо Settings), тоже должны быть описаны."""
    found: dict[str, str] = {}
    for p in app_dir.rglob("*.py"):
        if "config_backup" in p.parts:
            continue
        for m in _ENV_READ.finditer(p.read_text(encoding="utf-8", errors="ignore")):
            found.setdefault(m.group(1), str(p.relative_to(app_dir.parent)))
    return [f"код читает переменную окружения «{n}» ({where}), но она не описана в реестре (ENV_OTHER)" for n, where in sorted(found.items()) if n not in R.ENV_OTHER]


def all_problems() -> list[str]:
    from ...config import Settings  # noqa: PLC0415
    from ...models import Base  # noqa: PLC0415
    from ..settings import GROUPS  # noqa: PLC0415

    return (table_problems(Base.metadata) + group_problems(GROUPS) + env_problems(set(Settings.model_fields))
            + env_read_problems(Path(__file__).resolve().parents[2]))
