"""Выбор языковой модели: системная по умолчанию → настройка комнаты → настройка конкретной встречи.

Варианты на уровне комнаты и встречи:
  * inherit — использовать системную модель по умолчанию;
  * profile — конкретное подключение к модели по API (Администрирование → Языковая модель → подключения);
  * off     — языковая модель для этой комнаты/встречи отключена.

Если выбранное недоступно (подключение удалено или выключено), комната не ломается: действует политика `llm.on_missing` —
«system» (по умолчанию) возвращает системную модель с пометкой, «unavailable» оставляет состояние «модель недоступна» с понятной причиной.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Meeting, Room
from .api_profiles import ProfileService, Resolved
from .llm_gateway import LlmGateway
from .settings import TASK_LIMIT_DEFAULTS, LlmSettings, SettingsError

MODES = ("inherit", "profile", "off")


def _mode(raw: str | None) -> str:
    """Режим выбора модели; прежний «local» (встроенная Qwen, удалена в 0.56.0) — как «системная по умолчанию»."""
    return raw if raw in MODES else "inherit"


@dataclass
class LlmChoice:
    settings: LlmSettings       # настройки для LlmGateway.effective()/client()
    name: str                   # как показывать пользователю
    source: str                 # system | room | meeting
    available: bool             # False — «модель недоступна» (есть причина в reason)
    reason: str | None = None   # почему недоступна
    note: str | None = None     # пометка: например, использована системная модель вместо недоступной выбранной
    limit_requested: int | None = None    # запрошенный предел ответа: ГЛОБАЛЬНАЯ настройка задачи на момент запуска
    limit_note: str | None = None         # чем и почему предел ограничен у подключения (если ограничен)


def room_choice(room: Room, purpose: str = "protocol") -> dict:
    """Выбор комнаты (для протокола или для резюме) в виде словаря; комнаты старых версий (внешний профиль без режима) считаются выбравшими профиль."""
    if purpose == "map":
        return {"mode": "inherit", "profile_id": None}     # для карты разговора модель задаётся только в системных настройках (этап 1)
    if purpose == "summary":
        return {"mode": _mode(room.llm_summary_mode), "profile_id": str(room.llm_summary_profile_id) if room.llm_summary_profile_id else None}
    mode = _mode(room.llm_mode)
    if mode == "inherit" and room.llm_profile_id:
        mode = "profile"
    return {"mode": mode, "profile_id": str(room.llm_profile_id) if room.llm_profile_id else None}


def clean_choice(raw: dict | None) -> dict | None:
    """Проверка выбора модели от клиента. None — «как в комнате» (для встречи)."""
    if raw is None:
        return None
    mode = str(raw.get("mode") or "inherit")
    if mode not in MODES:
        raise ValueError("Режим модели: inherit | profile | off")
    out: dict = {"mode": mode, "profile_id": None}
    if mode == "profile":
        pid = str(raw.get("profile_id") or "")
        if pid != "main":
            try:
                pid = str(uuid.UUID(pid))
            except ValueError:
                raise ValueError("Выберите профиль внешней языковой модели") from None
        out["profile_id"] = pid
    return out


def task_mode(cfg: LlmSettings, purpose: str) -> tuple[str, str | None]:
    """(режим, профиль) системного назначения задачи: external | off и идентификатор подключения ("main" или id профиля; None — по умолчанию)."""
    if purpose == "protocol":
        return cfg.effective_provider, cfg.protocol_profile or None
    sp = getattr(cfg, f"{purpose}_provider", "same")
    if sp == "same":
        return cfg.effective_provider, getattr(cfg, f"{purpose}_profile", None) or cfg.protocol_profile or None
    return sp, getattr(cfg, f"{purpose}_profile", None) or None


async def system_llm(profiles: ProfileService, db: AsyncSession, purpose: str = "protocol"):
    """Системная модель задачи («Назначения» в разделе LLM): режим (подключение / отключена) и конкретное подключение.
    Первичен РЕЖИМ задачи; профиль, отмеченный «по умолчанию», применяется только если у задачи подключение не выбрано явно, и никогда не перебивает «Отключено».
    Для резюме и карты разговора режим `same` = как у протокола."""
    main = await profiles.get_settings(db, "llm", None)
    cfg = main.settings
    mode, pid = task_mode(cfg, purpose)                       # type: ignore[arg-type]
    if mode == "off":
        return Resolved(cfg.model_copy(update={"provider": "off", "enabled": False}), main.profile_id, "Отключено")      # type: ignore[union-attr]
    ext = None
    if pid:
        try:
            ext = await profiles.get_settings(db, "llm", pid)
        except SettingsError:
            ext = None                                         # выбранное подключение удалено — как «по умолчанию» (политика on_missing — на уровне комнат)
    if ext is None:
        ext = await profiles.resolve(db, "llm", None)
    es = ext.settings
    if es.effective_provider != "external":                    # type: ignore[union-attr]
        es = es.model_copy(update={"provider": "external", "enabled": True})      # type: ignore[union-attr]
    return Resolved(es, ext.profile_id, ext.name)


async def apply_task_limit(profiles: ProfileService, db: AsyncSession, ch: "LlmChoice", purpose: str) -> "LlmChoice":
    """Предел ответа по задаче. Глобальная настройка (`limit_*`, по умолчанию 60 000 / 40 000 / 60 000) читается при КАЖДОМ запуске, а не берётся из комнаты,
    встречи или прежнего задания. Итог = min(глобальный предел, предел подключения, если администратор задал его явно); далее — окно контекста (в output_limit).
    Чем и почему предел уменьшен, фиксируется в `limit_note` и попадает в диагностику задания."""
    main = await profiles.get_settings(db, "llm", None)
    cap = getattr(main.settings, f"limit_{purpose}", None) or TASK_LIMIT_DEFAULTS[purpose]
    ch.limit_requested = cap
    if not ch.available:
        return ch
    field = f"max_tokens_{purpose}"
    cur = getattr(ch.settings, field, None)
    if cur is None and purpose == "protocol" and ch.settings.max_tokens != 4000:
        cur = ch.settings.max_tokens                                    # прежняя единая настройка подключения, если её меняли вручную
    if cur is not None and cur < cap:
        ch.limit_note = f"ограничено настройкой подключения «{ch.name}» ({cur} токенов); общая настройка — {cap}"
    ch.settings = ch.settings.model_copy(update={field: cap if cur is None else min(cur, cap)})
    return ch


def limit_report(ch: "LlmChoice", eff: LlmSettings, purpose: str, stats: dict) -> str | None:
    """Почему фактический предел ответа отличается от запрошенного общей настройкой (для диагностики задания): подключение, окно контекста, отказ/оборыв на стороне модели."""
    parts: list[str] = []
    if ch.limit_note:
        parts.append(ch.limit_note)
    note = eff.output_limit(purpose)[1]
    if note:
        parts.append(note)
    for c in stats.get("clamps") or []:
        why = {"provider": "предел назвал провайдер", "rejected": "провайдер отклонил запрошенную длину и не назвал предел — взята ближайшая ступень", "context": "остальное окно контекста занято стенограммой"}.get(c.get("reason"), "")
        parts.append(f"модель {c.get('model') or ''}: запрошено {c['requested']} токенов ответа — применено {c['applied']} ({why})".replace("модель  ", "модель "))
    if stats.get("reasoning") == "off_auto":
        parts.append(f"модель {eff.model} тратила лимит ответа на «рассуждения» — они отключены на время этого документа (настройка «Рассуждения модели»: автоматически)")
    if stats.get("provider_cap"):
        parts.append(f"провайдер оборвал ответ на {stats['provider_cap']} токенах при запрошенных {stats.get('requested') or '?'}: фактический предел задаёт модель или шлюз")
    return "; ".join(dict.fromkeys(parts)) or None


def parse_once(key: str | None) -> dict | None:
    """Разовый выбор модели для одного формирования документа: «profile:<id профиля|main>»; пусто — не задан."""
    key = (key or "").strip()
    if not key:
        return None
    kind, _, ref = key.partition(":")
    if kind == "profile":
        return clean_choice({"mode": "profile", "profile_id": ref})
    raise ValueError("Модель для разового формирования: profile:<подключение>")


async def resolve_llm(profiles: ProfileService, gateway: LlmGateway, db: AsyncSession, room: Room, meeting: Meeting | None = None, purpose: str = "protocol",
                      once: dict | None = None) -> LlmChoice:
    """Модель для задачи (протокол / резюме / карта): разовый выбор → встреча → комната → системное назначение; затем применяется потолок ответа по задаче."""
    ch = await _resolve_llm(profiles, gateway, db, room, meeting, purpose, once)
    return await apply_task_limit(profiles, db, ch, purpose)


async def _resolve_llm(profiles: ProfileService, gateway: LlmGateway, db: AsyncSession, room: Room, meeting: Meeting | None = None, purpose: str = "protocol",
                       once: dict | None = None) -> LlmChoice:
    """Модель для протокола (purpose="protocol") или краткого резюме (purpose="summary"): система → комната → встреча, для каждой задачи своя цепочка."""
    system = await system_llm(profiles, db, purpose)
    sys_cfg: LlmSettings = system.settings   # type: ignore[assignment]
    ov = None
    if meeting is not None and purpose != "map":
        ov = meeting.llm_summary_override if purpose == "summary" else meeting.llm_override
    override = ov or None
    choice = once or override or room_choice(room, purpose)           # разовый выбор при формировании — сильнее встречи, комнаты и системы
    source = "once" if once else ("meeting" if override else ("room" if choice["mode"] != "inherit" else "system"))
    mode = _mode(choice["mode"])
    policy = getattr((await profiles.get_settings(db, "llm", None)).settings, "on_missing", "system")      # общая настройка, а не подключения по умолчанию

    def system_choice(note: str | None = None) -> LlmChoice:
        ok = bool(gateway.effective(sys_cfg).enabled)
        name = system.name
        return LlmChoice(sys_cfg, name, "system", ok, None if ok else "Языковая модель по умолчанию отключена или не настроена", note)

    if mode == "inherit":
        return system_choice()
    if mode == "off":
        return LlmChoice(sys_cfg.model_copy(update={"provider": "off", "enabled": False}), "Отключена", source, False,
                         "Языковая модель для этой " + ("встречи" if source == "meeting" else "комнаты") + " отключена")

    problem: str | None = None
    picked: LlmChoice | None = None
    if mode == "profile" and not choice.get("profile_id"):
        problem = "Выбранный профиль языковой модели удалён"      # ссылка очищается при удалении профиля
    elif mode == "profile":
        try:
            res = await profiles.get_settings(db, "llm", str(choice.get("profile_id")))
            cfg: LlmSettings = res.settings   # type: ignore[assignment]
            if gateway.effective(cfg).enabled:
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


async def once_options(profiles: ProfileService, db: AsyncSession) -> list[dict]:
    """Модели, из которых можно выбрать для одного формирования документа (в окне «Сформировать»): включённые подключения."""
    out: list[dict] = []
    for p in await profiles.list(db, "llm"):
        cfg = p["config"]
        if p["virtual"] and cfg.get("provider") != "external" and not cfg.get("api_key_set") and not p.get("secret_set"):
            continue
        if not cfg.get("model") or cfg.get("enabled") is False:
            continue
        out.append({"key": f"profile:{p['id']}", "label": f"{p['name']} — {cfg.get('model')}"})
    return out


def describe(ch: LlmChoice) -> dict:
    return {"name": ch.name, "source": ch.source, "available": ch.available, "reason": ch.reason, "note": ch.note}


async def llm_options(profiles: ProfileService, db: AsyncSession) -> dict:
    """Что можно выбрать в настройках комнаты/встречи: системная модель по умолчанию и подключения. Секретов нет."""
    system = await system_llm(profiles, db)
    eff = LlmGateway.effective(system.settings)    # type: ignore[arg-type]
    ssys = await system_llm(profiles, db, "summary")
    seff = LlmGateway.effective(ssys.settings)      # type: ignore[arg-type]
    policy = getattr((await profiles.get_settings(db, "llm", None)).settings, "on_missing", "system")
    ext = []
    for p in await profiles.list(db, "llm"):
        cfg = p["config"]
        ext.append({"id": p["id"], "name": p["name"], "model": cfg.get("model") or "", "provider": cfg.get("provider"), "is_default": p["is_default"]})
    return {"system": {"name": system.name, "provider": "off" if not eff.enabled else "external", "model": eff.model if eff.enabled else None},
            "system_summary": {"name": ssys.name, "provider": "off" if not seff.enabled else "external", "model": seff.model if seff.enabled else None},
            "profiles": ext, "on_missing": policy}
