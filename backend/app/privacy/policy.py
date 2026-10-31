"""Настройки → правила обезличивания: общие настройки администратора (группа anonymizer) и выбор комнаты."""
from __future__ import annotations

from .engine import PROFILES, Policy, Term

PROFILE_TITLES = {"basic": "Базовый", "strict": "Строгий", "custom": "Настраиваемый"}


def build_policy(cfg, profile: str) -> Policy:
    """Правила для профиля: базовый и строгий — встроенные действия; настраиваемый — действия администратора поверх базового."""
    common = {"terms": [Term(t["term"], t.get("category") or "custom", t.get("replacement") or "") for t in (cfg.terms or [])],
              "exceptions": list(cfg.exceptions or []), "internal_domains": list(cfg.internal_domains or [])}
    if profile == "custom":
        actions = {**PROFILES["basic"]["actions"], **(cfg.custom_actions or {})}
        return Policy(actions=actions, person_format=cfg.custom_person_format, org_scope=cfg.custom_org_scope, **common)
    return Policy.from_profile(profile if profile in PROFILES else "basic", **common)


def room_privacy(cfg, room) -> tuple[bool, str]:
    """(обезличивать ли материалы этой комнаты, профиль). Обязательное у администратора сильнее выбора комнаты; профиль комнаты — только из разрешённых."""
    on = bool(cfg.required) or (bool(cfg.enabled) and getattr(room, "anonymize_mode", "off") == "on")
    want = (getattr(room, "anonymize_profile", "") or "").strip()
    profile = want if want in (cfg.allowed_profiles or []) else cfg.default_profile
    return on, profile
