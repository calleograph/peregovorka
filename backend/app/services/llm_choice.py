"""Выбор языковой модели: системная по умолчанию → настройка комнаты → настройка конкретной встречи.

Варианты на уровне комнаты и встречи:
  * inherit — использовать системную модель по умолчанию;
  * local   — конкретная локальная модель (например, Qwen3 0.6B), если она загружена;
  * profile — конкретный внешний профиль LLM (Администрирование → Языковая модель → профили API);
  * off     — языковая модель для этой комнаты/встречи отключена.

Если выбранное недоступно (профиль удалён или выключен, локальная модель не загружена), комната не ломается: действует политика `llm.on_missing` —
«system» (по умолчанию) возвращает системную модель с пометкой, «unavailable» оставляет состояние «модель недоступна» с понятной причиной.
"""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Meeting, Room
from .api_profiles import ProfileService
from .local_llm import LOCAL_MODELS, LocalLlm
from .settings import LlmSettings, SettingsError

MODES = ("inherit", "local", "profile", "off")


@dataclass
class LlmChoice:
    settings: LlmSettings       # настройки для LocalLlm.effective()/client()
    name: str                   # как показывать пользователю
    source: str                 # system | room | meeting
    available: bool             # False — «модель недоступна» (есть причина в reason)
    reason: str | None = None   # почему недоступна
    note: str | None = None     # пометка: например, использована системная модель вместо недоступной выбранной


def room_choice(room: Room) -> dict:
    """Выбор комнаты в виде словаря; комнаты старых версий (внешний профиль без режима) считаются выбравшими профиль."""
    mode = room.llm_mode if room.llm_mode in MODES else "inherit"
    if mode == "inherit" and room.llm_profile_id:
        mode = "profile"
    return {"mode": mode, "profile_id": str(room.llm_profile_id) if room.llm_profile_id else None, "local_model": room.llm_local_model}


def clean_choice(raw: dict | None) -> dict | None:
    """Проверка выбора модели от клиента. None — «как в комнате» (для встречи)."""
    if raw is None:
        return None
    mode = str(raw.get("mode") or "inherit")
    if mode not in MODES:
        raise ValueError("Режим модели: inherit | local | profile | off")
    out: dict = {"mode": mode, "profile_id": None, "local_model": None}
    if mode == "profile":
        pid = str(raw.get("profile_id") or "")
        if pid != "main":
            try:
                pid = str(uuid.UUID(pid))
            except ValueError:
                raise ValueError("Выберите профиль внешней языковой модели") from None
        out["profile_id"] = pid
    if mode == "local":
        lm = str(raw.get("local_model") or "")
        if lm not in LOCAL_MODELS:
            raise ValueError("Выберите локальную модель из списка")
        out["local_model"] = lm
    return out


async def system_llm(profiles: ProfileService, db: AsyncSession):
    """Системная модель по умолчанию. Первичен РЕЖИМ из общих настроек (Локальная / Внешняя / Отключено).
    Профиль, отмеченный «Внешний API по умолчанию», применяется только при режиме «Внешняя» и никогда не перебивает выбранную локальную модель или «Отключено»."""
    main = await profiles.get_settings(db, "llm", None)
    if main.settings.effective_provider == "external":    # type: ignore[union-attr]
        return await profiles.resolve(db, "llm", None)
    return main


async def resolve_llm(profiles: ProfileService, local: LocalLlm, db: AsyncSession, room: Room, meeting: Meeting | None = None) -> LlmChoice:
    system = await system_llm(profiles, db)
    sys_cfg: LlmSettings = system.settings   # type: ignore[assignment]
    override = meeting.llm_override if (meeting is not None and meeting.llm_override) else None
    choice = override or room_choice(room)
    source = "meeting" if override else ("room" if choice["mode"] != "inherit" else "system")
    mode = choice["mode"]
    policy = getattr(sys_cfg, "on_missing", "system")

    def system_choice(note: str | None = None) -> LlmChoice:
        eff, is_local = local.effective(sys_cfg)
        ok = bool(eff.enabled)
        name = local.model(getattr(eff, "local_model", None)).title if (ok and is_local) else system.name     # «Qwen3 0.6B…», а не «Основной»
        return LlmChoice(sys_cfg, name, "system", ok, None if ok else "Языковая модель по умолчанию отключена или не настроена", note)

    if mode == "inherit":
        return system_choice()
    if mode == "off":
        return LlmChoice(sys_cfg.model_copy(update={"provider": "off", "enabled": False}), "Отключена", source, False,
                         "Языковая модель для этой " + ("встречи" if source == "meeting" else "комнаты") + " отключена")

    problem: str | None = None
    picked: LlmChoice | None = None
    if mode == "local":
        m = local.model(choice.get("local_model"))
        fs = await asyncio.to_thread(local.file_state, m)
        if not local.model_enabled(m):
            problem = f"Локальная модель {m.title} не включена на сервере"
        elif fs["state"] == "ok":
            picked = LlmChoice(sys_cfg.model_copy(update={"provider": "local", "enabled": True, "local_model": choice.get("local_model") or m.id}), m.title, source, True)
        else:
            problem = f"Локальная модель {m.title} не загружена или повреждена"
    elif mode == "profile" and not choice.get("profile_id"):
        problem = "Выбранный профиль языковой модели удалён"      # ссылка очищается при удалении профиля
    elif mode == "profile":
        try:
            res = await profiles.get_settings(db, "llm", str(choice.get("profile_id")))
            cfg: LlmSettings = res.settings   # type: ignore[assignment]
            if local.effective(cfg)[0].enabled:
                picked = LlmChoice(cfg, res.name, source, True)
            else:
                problem = f"Профиль «{res.name}» отключён"
        except SettingsError:
            problem = "Выбранный профиль языковой модели удалён"
    if picked is not None:
        return picked
    # выбранная модель недоступна: политика
    if policy == "unavailable":
        return LlmChoice(sys_cfg.model_copy(update={"provider": "off", "enabled": False}), "Модель недоступна", source, False, problem)
    fallback = system_choice(f"{problem}. Использована системная модель по умолчанию.")
    return fallback


def describe(ch: LlmChoice) -> dict:
    return {"name": ch.name, "source": ch.source, "available": ch.available, "reason": ch.reason, "note": ch.note}


async def llm_options(profiles: ProfileService, local: LocalLlm, db: AsyncSession) -> dict:
    """Что можно выбрать в настройках комнаты/встречи: системная модель по умолчанию, установленные локальные модели, внешние профили. Секретов нет."""
    system = await system_llm(profiles, db)
    eff, is_local = local.effective(system.settings)    # type: ignore[arg-type]
    policy = getattr(system.settings, "on_missing", "system")
    locals_: list[dict] = []
    for mid, m in LOCAL_MODELS.items():
        fs = await asyncio.to_thread(local.file_state, local.model(mid))
        locals_.append({"id": mid, "title": m.title, "light": m.light, "installed": fs["state"] == "ok" and local.model_enabled(local.model(mid)), "optional": m.optional})
    ext = []
    for p in await profiles.list(db, "llm"):
        cfg = p["config"]
        ext.append({"id": p["id"], "name": p["name"], "model": cfg.get("model") or "", "provider": cfg.get("provider"), "is_default": p["is_default"]})
    return {"system": {"name": system.name, "provider": "local" if is_local else ("off" if not eff.enabled else "external"), "model": eff.model if eff.enabled else None},
            "local": locals_, "profiles": ext, "on_missing": policy}
