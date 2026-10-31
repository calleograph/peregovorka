"""Администрирование → Обезличивание: справочник категорий и правил, состояние моделей распознавания имён, лаборатория проверки.

Лаборатория работает только на этом сервере: текст и файлы обрабатываются в памяти (на диск не пишутся) и никуда не отправляются; в журнал аудита
попадают объём, формат, профиль и число замен — без текста. Черновик настроек (словарь, исключения, свой профиль) можно проверить до сохранения.
"""
from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import SessionUser, client_ip, get_db, require_admin
from ..privacy import fetch_models
from ..privacy.engine import ACTIONS, CATEGORIES, PROFILES, Anonymizer, PrivacyError
from ..privacy.extract import SUPPORTED, ExtractError, extract
from ..privacy.names import NLP_DIR, NlpUnavailable
from ..privacy.patterns import find_structured  # noqa: F401  (справочник правил ниже — по этим модулям)
from ..privacy.policy import PROFILE_TITLES, build_policy
from ..privacy.secrets import EXACT
from ..services.audit import write_audit
from ..services.settings import AnonymizerSettings

router = APIRouter(prefix="/admin/privacy", tags=["admin-privacy"])
LAB_TIMEOUT_S = 120
_lab_slots = asyncio.Semaphore(2)              # проверки в лаборатории не отнимают процессор у встреч: не больше двух одновременно на процесс

STRUCTURED_RULES = [
    ("email", "email", "Адрес электронной почты"), ("phone", "phone", "Телефон (российский и международный формат, внутренние номера)"),
    ("ipv4", "ip", "IPv4-адрес и подсеть"), ("ipv6", "ip", "IPv6-адрес"), ("mac", "ip", "MAC-адрес"),
    ("internal_url", "url", "Ссылка на внутренний сервер, по IP, с учётными данными или с секретом в параметрах"),
    ("internal_host", "host", "Имя сервера во внутреннем домене (*.local, *.corp, *.lan и домены из настроек)"), ("host_context", "host", "Имя сервера после слов «сервер», «хост», «ВМ»"),
    ("unc_path", "host", "Сетевой путь \\\\сервер\\папка"), ("domain_account", "login", "Учётная запись ДОМЕН\\имя"), ("login_context", "login", "Логин после слов «логин», «учётная запись»"),
    ("inn", "id_number", "ИНН (проверка контрольных разрядов)"), ("kpp", "id_number", "КПП"), ("ogrn", "id_number", "ОГРН и ОГРНИП (контрольная сумма)"),
    ("snils", "id_number", "СНИЛС (контрольное число)"), ("passport", "id_number", "Паспорт РФ (серия и номер рядом со словом «паспорт»)"),
    ("birth_date", "id_number", "Дата рождения"), ("card_luhn", "bank", "Номер банковской карты (алгоритм Луна)"), ("bank_account", "bank", "Расчётный и корреспондентский счёт"),
    ("bik", "bank", "БИК"), ("document_number", "doc_number", "Номер договора, счёта, заявки, инцидента, КП"), ("money", "money", "Сумма с валютой или «тыс./млн/млрд»"),
    ("address", "address", "Адрес: город, улица, дом, квартира или офис"), ("legal_entity", "organization", "Организация с правовой формой: ООО «…», АО «…»"),
    ("ner", "person", "Люди — распознавание имён (NER) и морфология: падежи, инициалы, сокращения"), ("ner", "organization", "Организации — распознавание (строгий профиль)"),
    ("ner", "location", "Населённые пункты и места — распознавание (строгий профиль)"), ("dictionary", "project", "Словарь: проекты, клиенты, внутренние системы, свои термины"),
]


@router.get("/catalog")
async def catalog(request: Request, su: SessionUser = Depends(require_admin)):
    """Что распознаётся и как заменяется: категории, встроенные профили, правила (без регулярных выражений секретов — только названия)."""
    ready = await asyncio.to_thread(fetch_models.ready, NLP_DIR)
    return {"categories": [{"id": k, "title": v, "mandatory": k == "secret"} for k, v in CATEGORIES.items()], "actions": list(ACTIONS),
            "profiles": {k: {"title": PROFILE_TITLES[k], "person_format": v["person_format"], "org_scope": v["org_scope"], "actions": v["actions"]} for k, v in PROFILES.items()},
            "profile_titles": PROFILE_TITLES,
            "rules": [{"id": rid, "category": cat, "title": title} for rid, cat, title in STRUCTURED_RULES],
            "secret_rules": [rid for rid, _ in EXACT] + ["context_keyword", "keyword_near_value", "entropy_near_marker"],
            "formats": SUPPORTED, "nlp": {"ready": ready, "engine": "slovnet NER + navec (модели проекта Natasha, MIT), pymorphy3 (MIT)",
                                          "note": None if ready else "Модели распознавания имён не установлены: обезличивание с ними работать не будет (документы не сформируются). Обновите сервер или выполните python -m app.privacy.fetch_models в контейнере backend."}}


async def _cfg(request: Request, db: AsyncSession, draft: dict | None) -> AnonymizerSettings:
    cfg = await request.app.state.settings_svc.get(db, "anonymizer")
    if not draft:
        return cfg
    try:
        return AnonymizerSettings(**{**cfg.model_dump(), **{k: v for k, v in draft.items() if k in AnonymizerSettings.model_fields}})
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"Черновик настроек: {exc}") from None


async def _run(request: Request, db: AsyncSession, su: SessionUser, text: str, profile: str, participants: list[str], draft: dict | None, info: dict) -> dict:
    cfg = await _cfg(request, db, draft)
    if profile not in PROFILE_TITLES:
        raise HTTPException(status_code=422, detail="Профиль: basic, strict или custom")
    if len(text) > cfg.lab_max_chars:
        raise HTTPException(status_code=413, detail=f"Текст длиннее {cfg.lab_max_chars:,} знаков — разбейте его на части".replace(",", " "))
    names = [p.strip() for p in participants if p.strip()][:200]

    def work():
        t0 = time.monotonic()
        res = Anonymizer(build_policy(cfg, profile), names).process(text)
        return res, int((time.monotonic() - t0) * 1000)

    async with _lab_slots:
        try:
            res, ms = await asyncio.wait_for(asyncio.to_thread(work), LAB_TIMEOUT_S)
        except asyncio.TimeoutError:
            raise HTTPException(status_code=504, detail=f"Обработка дольше {LAB_TIMEOUT_S} с — сократите текст") from None
        except NlpUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from None
        except PrivacyError as exc:
            raise HTTPException(status_code=422, detail=exc.message) from None
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="privacy.lab", target_type="system", target_id="anonymizer",
                      ip=client_ip(request), details={"chars": len(text), "profile": profile, "replaced": len(res.replacements), "draft": bool(draft), **info})
    await db.commit()
    return {"source": text, "text": res.text, "ms": ms, "profile": profile, "stats": res.stats(), "warnings": res.warnings, "info": info,
            "replacements": [{"start": r.start, "end": r.end, "category": r.category, "rule": r.rule, "original": r.original, "replacement": r.replacement}
                             for r in res.replacements]}


@router.post("/lab")
async def lab_text(request: Request, body: dict = Body(...), su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Проверка текста: {text, profile, participants?: [ФИО], draft?: {terms, exceptions, custom_actions, …}}."""
    text = body.get("text")
    if not isinstance(text, str) or not text.strip():
        raise HTTPException(status_code=422, detail="Вставьте текст")
    parts = body.get("participants") or []
    if not isinstance(parts, list):
        raise HTTPException(status_code=422, detail="participants: список ФИО")
    return await _run(request, db, su, text, str(body.get("profile") or "basic"), [str(p) for p in parts], body.get("draft") if isinstance(body.get("draft"), dict) else None,
                      {"format": "текст"})


@router.post("/lab/file")
async def lab_file(request: Request, name: str = Query(..., max_length=200), profile: str = Query("basic"), participants: str = Query("", max_length=20000),
                   su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Проверка файла (тело запроса — содержимое файла; имя — для определения формата). Файл читается в память и на диск не записывается."""
    cfg = await request.app.state.settings_svc.get(db, "anonymizer")
    limit = cfg.lab_max_mb * 1024 * 1024
    buf = bytearray()
    async for chunk in request.stream():
        buf += chunk
        if len(buf) > limit:
            raise HTTPException(status_code=413, detail=f"Файл больше {cfg.lab_max_mb} МБ")
    if not buf:
        raise HTTPException(status_code=422, detail="Файл пустой")
    try:
        text, info = await asyncio.wait_for(asyncio.to_thread(extract, name, bytes(buf)), 60)
    except ExtractError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Текст из файла не извлечён за 60 с") from None
    finally:
        buf.clear()
    if not text.strip():
        raise HTTPException(status_code=422, detail=info.get("note") or "В файле нет текста")
    return await _run(request, db, su, text, profile, participants.split("\n"), None, {**info, "name_len": len(name)})
