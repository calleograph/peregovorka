"""Выбор языковой модели: системная по умолчанию → настройка комнаты → настройка конкретной встречи.

Варианты на уровне комнаты и встречи:
  * inherit — использовать системную модель по умолчанию;
  * local   — конкретная локальная модель (Qwen3 1.7B), если она загружена;
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
from .api_profiles import ProfileService, Resolved
from .local_llm import LOCAL_MODELS, VISIBLE_MODELS, LocalLlm
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


def room_choice(room: Room, purpose: str = "protocol") -> dict:
    """Выбор комнаты (для протокола или для резюме) в виде словаря; комнаты старых версий (внешний профиль без режима) считаются выбравшими профиль."""
    if purpose == "summary":
        sm = room.llm_summary_mode if room.llm_summary_mode in MODES else "inherit"
        return {"mode": sm, "profile_id": str(room.llm_summary_profile_id) if room.llm_summary_profile_id else None, "local_model": room.llm_summary_local_model}
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


async def system_llm(profiles: ProfileService, db: AsyncSession, purpose: str = "protocol"):
    """Системная модель по умолчанию (для протокола или для резюме). Первичен РЕЖИМ из общих настроек (Локальная / Внешняя / Отключено).
    Профиль, отмеченный «Внешний API по умолчанию», применяется только при режиме «Внешняя» и никогда не перебивает выбранную локальную модель или «Отключено».
    Для резюме отдельная настройка `summary_provider`: same — как для протокола, иначе local / external / off."""
    main = await profiles.get_settings(db, "llm", None)
    cfg = main.settings
    if purpose == "summary":
        sp = getattr(cfg, "summary_provider", "same")
        if sp == "off":
            return Resolved(cfg.model_copy(update={"provider": "off", "enabled": False}), main.profile_id, "Отключено")      # type: ignore[union-attr]
        if sp == "local":
            return Resolved(cfg.model_copy(update={"provider": "local", "enabled": True}), main.profile_id, main.name)       # type: ignore[union-attr]
        if sp == "external":
            ext = await profiles.resolve(db, "llm", None)
            es = ext.settings
            if es.effective_provider != "external":                          # type: ignore[union-attr]
                es = es.model_copy(update={"provider": "external", "enabled": True})      # type: ignore[union-attr]
            return Resolved(es, ext.profile_id, ext.name)
    if cfg.effective_provider == "external":    # type: ignore[union-attr]
        return await profiles.resolve(db, "llm", None)
    return main


def parse_once(key: str | None) -> dict | None:
    """Разовый выбор модели для одного формирования документа: «local:<id модели>» или «profile:<id профиля|main>»; пусто — не задан."""
    key = (key or "").strip()
    if not key:
        return None
    kind, _, ref = key.partition(":")
    if kind == "local":
        return clean_choice({"mode": "local", "local_model": ref})
    if kind == "profile":
        return clean_choice({"mode": "profile", "profile_id": ref})
    raise ValueError("Модель для разового формирования: local:<модель> или profile:<профиль>")


async def resolve_llm(profiles: ProfileService, local: LocalLlm, db: AsyncSession, room: Room, meeting: Meeting | None = None, purpose: str = "protocol",
                      once: dict | None = None) -> LlmChoice:
    """Модель для протокола (purpose="protocol") или краткого резюме (purpose="summary"): система → комната → встреча, для каждой задачи своя цепочка."""
    system = await system_llm(profiles, db, purpose)
    sys_cfg: LlmSettings = system.settings   # type: ignore[assignment]
    ov = None
    if meeting is not None:
        ov = meeting.llm_summary_override if purpose == "summary" else meeting.llm_override
    override = ov or None
    choice = once or override or room_choice(room, purpose)           # разовый выбор при формировании — сильнее встречи, комнаты и системы
    source = "once" if once else ("meeting" if override else ("room" if choice["mode"] != "inherit" else "system"))
    mode = choice["mode"]
    policy = getattr(sys_cfg, "on_missing", "system")

    def system_choice(note: str | None = None) -> LlmChoice:
        eff, is_local = local.effective(sys_cfg)
        ok = bool(eff.enabled)
        name = local.model(getattr(eff, "local_model", None)).title if (ok and is_local) else system.name     # «Qwen3 1.7B…», а не «Основной»
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
    # выбранная модель недоступна: политика (явный разовый выбор не подменяется молча другой моделью)
    if policy == "unavailable" or once:
        return LlmChoice(sys_cfg.model_copy(update={"provider": "off", "enabled": False}), "Модель недоступна", source, False, problem)
    fallback = system_choice(f"{problem}. Использована системная модель по умолчанию.")
    return fallback


async def once_options(profiles: ProfileService, local: LocalLlm, db: AsyncSession) -> list[dict]:
    """Модели, из которых можно выбрать для одного формирования документа (в окне «Сформировать»): установленные локальные и включённые внешние профили."""
    out: list[dict] = []
    for mid, m in VISIBLE_MODELS.items():
        fs = await asyncio.to_thread(local.file_state, local.model(mid))
        if fs["state"] == "ok" and local.model_enabled(local.model(mid)):
            out.append({"key": f"local:{mid}", "label": f"{m.title} (локальная, данные остаются на сервере)", "local": True})
    for p in await profiles.list(db, "llm"):
        cfg = p["config"]
        if p["virtual"] and cfg.get("provider") != "external" and not cfg.get("api_key_set") and not p.get("secret_set"):
            continue
        if not cfg.get("model") or cfg.get("enabled") is False:
            continue
        out.append({"key": f"profile:{p['id']}", "label": f"{p['name']} — {cfg.get('model')} (внешняя API)", "local": False})
    return out


def describe(ch: LlmChoice) -> dict:
    return {"name": ch.name, "source": ch.source, "available": ch.available, "reason": ch.reason, "note": ch.note}


async def llm_options(profiles: ProfileService, local: LocalLlm, db: AsyncSession) -> dict:
    """Что можно выбрать в настройках комнаты/встречи: системная модель по умолчанию, установленные локальные модели, внешние профили. Секретов нет."""
    system = await system_llm(profiles, db)
    eff, is_local = local.effective(system.settings)    # type: ignore[arg-type]
    ssys = await system_llm(profiles, db, "summary")
    seff, s_local = local.effective(ssys.settings)      # type: ignore[arg-type]
    policy = getattr(system.settings, "on_missing", "system")
    locals_: list[dict] = []
    for mid, m in VISIBLE_MODELS.items():
        fs = await asyncio.to_thread(local.file_state, local.model(mid))
        locals_.append({"id": mid, "title": m.title, "light": m.light, "installed": fs["state"] == "ok" and local.model_enabled(local.model(mid))})
    ext = []
    for p in await profiles.list(db, "llm"):
        cfg = p["config"]
        ext.append({"id": p["id"], "name": p["name"], "model": cfg.get("model") or "", "provider": cfg.get("provider"), "is_default": p["is_default"]})
    return {"system": {"name": system.name, "provider": "local" if is_local else ("off" if not eff.enabled else "external"), "model": eff.model if eff.enabled else None},
            "system_summary": {"name": ssys.name, "provider": "local" if s_local else ("off" if not seff.enabled else "external"), "model": seff.model if seff.enabled else None},
            "local": locals_, "profiles": ext, "on_missing": policy}
