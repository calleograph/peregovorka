"""Источники центра внимания (services/attention.py) для основных модулей: службы, ключи, место, видеозапись и трансляции, хранилища,
экспорт записей, резервная копия конфигурации, вход, обезличивание, целостность хранилищ, почта. Новый модуль добавляет свой источник так же —
`@source(...)` в своём коде; «Обзор» менять не нужно."""
from __future__ import annotations

import asyncio
import shutil
from datetime import timedelta

import httpx
from sqlalchemy import func, select

from ..models.entities import MailMessage, Recording, StorageSyncRun
from ..models.base import utcnow
from .attention import item, source

LOW_DISK_GB = 5


@source("Службы")
async def services(app, db) -> list[dict]:
    st, s = app.state, app.state.settings
    out = []
    try:
        await st.redis.ping()
    except Exception as exc:  # noqa: BLE001
        out.append(item("svc.redis", "critical", "Redis", f"Redis недоступен ({type(exc).__name__})", "Не работают сеансы, очереди распознавания и уведомления.",
                        "Проверьте службу redis на сервере; раздел «Технические показатели» покажет подробности.", "system_tech"))
    try:
        async with httpx.AsyncClient(timeout=2.0) as c:
            ok = (await c.get(s.livekit_http_url + "/")).status_code == 200
    except Exception:  # noqa: BLE001
        ok = False
    if not ok:
        out.append(item("svc.livekit", "critical", "Сервер звонков (LiveKit)", "Сервер звонков не отвечает", "Встречи, звонки и запись недоступны.",
                        "Проверьте сетевую доступность и службу livekit; раздел «Сетевая доступность» показывает порты.", "network"))
    from . import asr_state  # noqa: PLC0415

    if not asr_state.state(await st.bridge.heartbeat())["ok"]:
        out.append(item("svc.asr", "critical", "Распознавание речи (ASR)", "Распознавание речи не работает", "Стенограммы встреч не создаются.",
                        "Откройте «Распознавание речи (ASR)»: там видно, готова ли модель и что с конвейером.", "asr"))
    from ..auth.directory import DirectoryError  # noqa: PLC0415

    try:
        await asyncio.to_thread(st.directory.check_service_account)
    except DirectoryError as exc:
        if exc.code != "not_configured":
            out.append(item("svc.ldap", "critical", "Каталог (LDAP/AD)", f"Нет подключения к каталогу ({exc.code})", "Сотрудники не могут войти; профили не обновляются.",
                            "Проверьте подключение и учётную запись службы.", "ldap"))
    except Exception as exc:  # noqa: BLE001
        out.append(item("svc.ldap", "critical", "Каталог (LDAP/AD)", f"Нет подключения к каталогу ({type(exc).__name__})", "Сотрудники не могут войти; профили не обновляются.",
                        "Проверьте подключение и учётную запись службы.", "ldap"))
    if st.settings_svc._box is None:  # noqa: SLF001
        out.append(item("sec.master_key", "critical", "Ключ шифрования настроек", "Главный ключ шифрования не загружен", "Секреты в настройках (пароли SMTP, ключи API) не читаются и не сохраняются.",
                        "Проверьте APP_MASTER_KEY в .env сервера; подробности — «Шифрование хранилища».", "crypto"))
    try:
        free = shutil.disk_usage(s.data_dir).free
    except OSError:
        free = None
    if free is not None and free < LOW_DISK_GB * 1024**3:
        out.append(item("disk.low", "warning", "Диск данных", f"На диске данных осталось {free / 1024**3:.1f} ГБ", "Новые записи, эфиры и выгрузки могут не начаться.",
                        "Освободите место или перенесите записи в файловое хранилище; сроки хранения — в настройках комнат.", "storages"))
    return out


@source("Видеозапись и трансляции")
async def media(app, db) -> list[dict]:
    from . import media_checks  # noqa: PLC0415

    out = []
    for purpose, tab, comp in (("video", "video", "Видеозапись"), ("live", "live", "Трансляции")):
        if not (await app.state.settings_svc.get(db, purpose)).enabled:
            continue                                                 # выключенная возможность — не проблема
        for c in await media_checks.checks(app.state, db, purpose):
            if c["status"] != "error" or c["id"] == "livekit":
                continue                                             # сервер звонков — пунктом источника «Службы», без повтора
            a = c.get("action") or {}
            fix = None
            if a.get("kind") == "repair":
                fix = {"kind": "repair", "repair": a["repair"], "label": a.get("label") or "Исправить",
                       "confirm": "Помощник обновлений на сервере восстановит владельца и права каталогов данных. Встречи не прерываются. После исправления проверка повторится."}
            out.append(item(f"{purpose}.{c['id']}", "warning", comp, f"{c['title']}: {c['message']}",
                            "Видеозапись в комнатах может не начаться." if purpose == "video" else "Эфиры в комнатах могут не начаться или зрители не откроют поток.",
                            "Перейдите к проверке в разделе — там же действие, которое исправляет это состояние.", tab, anchor=f"chk-{c['id']}", fix=fix, doc=c.get("doc")))
    return out


@source("Хранилища")
async def storages(app, db) -> list[dict]:
    from ..api.admin_system import probe_storage  # noqa: PLC0415

    out = []
    for group, tab, comp, impact in (
        ("storage", "storage", "Хранилище документов встреч", "Стенограммы, протоколы и материалы встреч не выгружаются."),
        ("audio_storage", "audio_storage", "Хранилище аудиозаписей", "Аудиозаписи встреч остаются только на сервере."),
        ("chat_files", "chat_files", "Хранилище вложений чата", "Файлы в чатах не загружаются."),
    ):
        cfg = await app.state.settings_svc.get(db, group)
        if not getattr(cfg, "enabled", False):
            continue
        r = await probe_storage(app, db, group, cfg)
        if not r["ok"]:
            out.append(item(f"storage.{group}", "warning", comp, f"Хранилище недоступно: {r['message']}", impact,
                            "Проверьте выбранное хранилище и права на папку; «Проверить подключение» в разделе повторит пробную запись.", tab))
    return out


@source("Записи встреч")
async def recordings(app, db) -> list[dict]:
    n = (await db.execute(select(func.count()).select_from(Recording).where(Recording.export_status == "failed"))).scalar_one()
    if not n:
        return []
    return [item("recordings.export", "warning", "Записи встреч", f"Не выгружено в хранилище записей: {n}", "Эти записи хранятся только на сервере и занимают место.",
                 "Откройте «Записи встреч»: у каждой записи указана причина; после исправления хранилища выгрузка повторится.", "recordings")]


@source("Резервная копия конфигурации")
async def config_backup(app, db) -> list[dict]:
    st = await app.state.auto_backup.status(db)
    if not st["settings"]["enabled"]:
        return [item("backup.off", "info", "Резервная копия конфигурации", "Автоматическое резервное копирование конфигурации выключено",
                     "При сбое сервера настройки придётся восстанавливать вручную.", "Включите расписание и выберите хранилище для копий.", "config_backup")]
    if st["stale"]:
        return [item("backup.stale", "warning", "Резервная копия конфигурации", "Нет свежей резервной копии конфигурации",
                     "Последняя удачная копия старше допустимого срока: изменения настроек с тех пор не сохранены.",
                     "Откройте раздел: в журнале копий видна причина; «Создать копию» проверит хранилище.", "config_backup")]
    return []


@source("Вход")
async def login_access(app, db) -> list[dict]:
    from ..auth.directory import DirectoryError  # noqa: PLC0415

    s = app.state.settings
    try:
        await asyncio.to_thread(app.state.directory.check_service_account)
    except DirectoryError as exc:
        if exc.code == "not_configured":
            return []                                               # каталог не подключён — входят только локальные учётные записи
    except Exception:  # noqa: BLE001
        pass                                                        # каталог недоступен — об этом пункт источника «Службы»
    acc = await app.state.settings_svc.get(db, "access")
    if acc.user_groups or (s.ldap_access_group_dn or "").strip():  # type: ignore[attr-defined]
        return []
    return [item("login.open", "info", "Кто может входить", "Ограничение входа не настроено",
                 "Войти может любой активный пользователь каталога.", "Добавьте разрешённые группы — после первой группы войдут только её члены и администраторы.",
                 "login_access")]


@source("Обезличивание")
async def anonymizer(app, db) -> list[dict]:
    from ..privacy import fetch_models  # noqa: PLC0415
    from ..privacy.names import NLP_DIR  # noqa: PLC0415

    cfg = await app.state.settings_svc.get(db, "anonymizer")
    if not (cfg.enabled or cfg.required) or await asyncio.to_thread(fetch_models.ready, NLP_DIR):  # type: ignore[attr-defined]
        return []
    return [item("anon.models", "warning", "Обезличивание", "Модели распознавания имён не установлены",
                 "В комнатах с обезличиванием протоколы и резюме не формируются (в модель ничего не уходит).",
                 "Обновите сервер при доступе к интернету — модели ставятся в образ; состояние — на вкладке «Общие».", "anon")]


@source("Целостность хранилищ")
async def storage_sync(app, db) -> list[dict]:
    run = (await db.execute(select(StorageSyncRun).where(StorageSyncRun.status != "running").order_by(StorageSyncRun.started_at.desc()).limit(1))).scalar_one_or_none()
    if run is None or not (run.missing or run.unavailable or run.status == "failed"):
        return []
    what = "сверка не выполнена" if run.status == "failed" else ", ".join(x for x in (
        f"не найдено файлов: {run.missing}" if run.missing else "", f"недоступно для проверки: {run.unavailable}" if run.unavailable else "") if x)
    return [item("sync.problems", "warning", "Целостность хранилищ", f"Последняя сверка хранилищ: {what}",
                 "Часть материалов встреч может не открываться.", "Откройте отчёт сверки; «Восстановление материалов» поможет найти перенесённые файлы.", "storage_sync")]


@source("Почта")
async def mail(app, db) -> list[dict]:
    since = utcnow() - timedelta(days=1)
    n = (await db.execute(select(func.count()).select_from(MailMessage).where(MailMessage.state == "failed", MailMessage.created_at >= since))).scalar_one()
    if not n:
        return []
    return [item("mail.failed", "warning", "Почта", f"Не отправлено писем за сутки: {n}", "Участники не получили материалы встреч.",
                 "Журнал отправки покажет причину; проверьте сервер SMTP кнопкой «Проверить».", "mail_log")]
