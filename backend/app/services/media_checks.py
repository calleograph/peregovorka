"""Диагностика видеозаписи и трансляций с учётом зависимостей компонентов.

Раньше разделы «Видеозапись» и «Трансляции» показывали сразу несколько ошибок («Egress не установлен», «нет связи с сервером звонков», «секрет HLS
не записан», «health-порт не отвечает»), хотя причина одна — видеоподсистема выключена. Теперь проверки идут по цепочке:

    видеоподсистема на сервере → функция в приложении → сервер звонков → служба Egress подключена → (эфир: секрет HLS, health-порт) → место → хранилище

Каждая проверка — одно из состояний: `ok`; `error` — настоящая неисправность; `off` — возможность выключена (это не ошибка); `skipped` — проверить
пока нельзя, потому что не работает то, от чего она зависит; `info` — к сведению. Для штатно устранимых состояний указано действие
(`action`: компонент и операция из services/server_components.py, исправление помощника или переход к настройке) и статья вики (`doc`).
"""
from __future__ import annotations

import asyncio
import shutil
import time
from pathlib import Path
from typing import Any

from ..db import release
from . import server_components as sc

WIKI = sc.WIKI
DOC_MEDIA = WIKI + "Решение-проблем-видеозаписи-и-трансляций"
PROBE_S = 4.0                  # дольше служба уже считается недоступной: страница состояния не должна ждать её
DOC_STORAGE = WIKI + "Решение-проблем-файловых-хранилищ"


def _chk(cid: str, title: str, status: str, message: str, *, action: dict | None = None, doc: str | None = None) -> dict:
    return {"id": cid, "title": title, "status": status, "message": message, "action": action, "doc": doc}


def _skip(cid: str, title: str, why: str) -> dict:
    return _chk(cid, title, "skipped", f"Проверится, когда {why}.")


def _comp_action(name: str, op: str) -> dict:
    o = sc.OPS[op]
    return {"kind": "component", "component": name, "op": op, "label": o["label"], "title": o["title"], "consequences": o["consequences"],
            "interrupts_calls": o["calls"], "danger": bool(o.get("danger"))}


REPAIR_DIRS = {"kind": "repair", "repair": "data_dirs", "label": "Исправить права каталогов"}
WEB_STATE_TIMEOUT_S = 3
WEB_URL = "http://web:8080"          # сервис web в сети compose (deployment/compose.yml)


async def web_hls_state(url: str) -> tuple[str, str] | None:
    """Что сообщает nginx контейнера web о секрете: (состояние, поколение) или None — web не ответил."""
    import httpx  # noqa: PLC0415

    try:
        async with httpx.AsyncClient(timeout=WEB_STATE_TIMEOUT_S) as c:
            r = await c.get(url.rstrip("/") + "/__hls/state")
        if r.status_code != 200:
            return None
        state, _, g = r.text.strip().partition(" ")
        return state, g.strip()
    except Exception:  # noqa: BLE001
        return None


async def secret_checks(live, settings) -> list[dict]:
    import hashlib  # noqa: PLC0415
    import os  # noqa: PLC0415

    from .live import SECRET_GID, SECRET_MODE, hls_secret, nginx_secret_conf  # noqa: PLC0415

    nd = live.live_dir() / "nginx"
    conf = nd / "secret.conf"
    why = live.prepare()                       # самовосстановление: создать каталог и секрет, поправить права файла — если backend это может
    want = nginx_secret_conf(hls_secret(settings))
    out = []
    t = "Секрет подписи трансляций: "
    if not nd.is_dir():
        out.append(_chk("secret_dir", t + "каталог", "error", f"Каталога {nd} нет. «Исправить права каталогов» создаст его с нужным владельцем.", action=REPAIR_DIRS, doc=DOC_MEDIA))
        return out + [_skip(i, t + n, "появится каталог") for i, n in (("secret_write", "запись backend"), ("secret_file", "файл"), ("secret_mode", "права"),
                                                                       ("secret_read", "чтение nginx"), ("secret_test", "проверка nginx -t"), ("secret_applied", "применение в nginx"))]
    out.append(_chk("secret_dir", t + "каталог", "ok", "Каталог есть."))
    can_write = os.access(nd, os.W_OK | os.X_OK) and why is None
    out.append(_chk("secret_write", t + "запись backend", "ok" if can_write else "error",
                    "Backend может создавать и обновлять секрет." if can_write else f"Backend не может записать секрет: {why or 'нет права записи в каталог'}. Обычно каталог создан Docker от root.",
                    action=None if can_write else REPAIR_DIRS, doc=None if can_write else DOC_MEDIA))
    try:
        st, text = conf.stat(), conf.read_text()
    except OSError:
        st, text = None, None
    if st is None:
        out.append(_chk("secret_file", t + "файл", "error", "Секрет не создан.", action=REPAIR_DIRS, doc=DOC_MEDIA))
        return out + [_skip(i, t + n, "секрет будет создан") for i, n in (("secret_mode", "права"), ("secret_read", "чтение nginx"), ("secret_test", "проверка nginx -t"),
                                                                          ("secret_applied", "применение в nginx"))]
    out.append(_chk("secret_file", t + "файл", "ok" if text == want else "error", "Создан." if text == want else "Содержимое устарело, а backend не может его обновить.",
                    action=None if text == want else REPAIR_DIRS))
    mode_ok = (st.st_mode & 0o777) == SECRET_MODE and st.st_gid == SECRET_GID
    out.append(_chk("secret_mode", t + "права", "ok" if mode_ok else "error",
                    f"0{SECRET_MODE:o}, группа {SECRET_GID}: читают только backend и nginx." if mode_ok else
                    f"Сейчас 0{st.st_mode & 0o777:o}, группа {st.st_gid}; нужно 0{SECRET_MODE:o} и группа {SECRET_GID} (посторонние локальные пользователи не должны читать секрет).",
                    action=None if mode_ok else REPAIR_DIRS, doc=None if mode_ok else DOC_MEDIA))
    gen = hashlib.sha256(want.encode()).hexdigest()[:12]
    ws = await web_hls_state(WEB_URL)
    if ws is None:
        return out + [_chk(i, t + n, "info", "Веб-сервер (web) не ответил на запрос состояния — проверить нельзя.") for i, n in
                      (("secret_read", "чтение nginx"), ("secret_test", "проверка nginx -t"), ("secret_applied", "применение в nginx"))]
    state, loaded = ws
    restart = _comp_action("web", "web_restart")
    if state in ("missing", "unreadable"):
        out.append(_chk("secret_read", t + "чтение nginx", "error", f"Nginx не видит файл секрета: нет права группы {SECRET_GID} на каталог live/nginx (или том не подключён)." if state == "missing" else
                        f"Nginx не может прочитать секрет: нет права чтения для группы {SECRET_GID}.", action=REPAIR_DIRS, doc=DOC_MEDIA))
    else:
        out.append(_chk("secret_read", t + "чтение nginx", "ok", "Nginx читает секрет."))
    if state == "test_failed":
        out.append(_chk("secret_test", t + "проверка nginx -t", "error", "nginx -t отклонил конфигурацию с новым секретом: работает прежняя. Подробности — в журнале контейнера web.",
                        action=restart, doc=DOC_MEDIA))
    elif state in ("ok", "reload_failed"):
        out.append(_chk("secret_test", t + "проверка nginx -t", "ok", "Конфигурация проверена."))
    else:
        out.append(_skip("secret_test", t + "проверка nginx -t", "nginx прочитает секрет"))
    if state == "ok" and loaded == gen:
        out.append(_chk("secret_applied", t + "применение в nginx", "ok", f"Работающий nginx использует актуальный секрет (поколение {gen})."))
    elif state == "reload_failed":
        out.append(_chk("secret_applied", t + "применение в nginx", "error", "Nginx не перечитал конфигурацию: зрители не откроют поток с новыми подписями.", action=restart, doc=DOC_MEDIA))
    elif state == "ok":
        fresh = time.time() - st.st_mtime < 30
        out.append(_chk("secret_applied", t + "применение в nginx", "info" if fresh else "error",
                        "Секрет только что обновлён: nginx применит его в течение нескольких секунд." if fresh else
                        f"Nginx использует другой секрет (поколение {loaded or '—'}, ожидается {gen}).", action=None if fresh else restart, doc=None if fresh else DOC_MEDIA))
    else:
        out.append(_skip("secret_applied", t + "применение в nginx", "nginx прочитает секрет"))
    return out


async def probe(coro) -> Exception | None:
    """Опрос службы с пределом времени. Без предела зависшая служба держала бы запрос десятки секунд; ответ — ошибка или None."""
    try:
        await asyncio.wait_for(coro, PROBE_S)
    except Exception as exc:  # noqa: BLE001
        return exc
    return None


async def checks(st, db, purpose: str) -> list[dict]:
    """purpose: video | live"""
    settings = st.settings
    ch = sc.channel_for(settings)
    f = sc.facts(ch)
    comp = sc.describe("video", f, settings)
    cfg = await st.settings_svc.get(db, purpose)
    await release(db)                          # дальше только сеть и файлы: соединение с базой на время опроса служб не держим
    out: list[dict] = []
    server_up = comp["state"] in ("ok", "unknown") and (comp["state"] == "ok" or comp.get("enabled"))
    lk_err, eg_err = await asyncio.gather(probe(st.video.api.probe_livekit()), probe(st.video.api.probe()) if server_up else asyncio.sleep(0))

    # 1. видеоподсистема на сервере — от неё зависит всё остальное
    srv_title = "Видеоподсистема на сервере"
    if comp["state"] == "off":
        out.append(_chk("server", srv_title, "off", "Выключена. Видеозапись и трансляции используют одну службу LiveKit Egress — включите её.",
                        action=_comp_action("video", "video_enable"), doc=DOC_MEDIA))
    elif comp["state"] in ("error", "starting"):
        primary = next((o for o in comp["ops"] if o.get("primary")), None)
        out.append(_chk("server", srv_title, "error" if comp["state"] == "error" else "info", comp["message"],
                        action=_comp_action("video", primary["id"]) if primary else None, doc=DOC_MEDIA))
    elif comp["state"] == "unknown":
        out.append(_chk("server", srv_title, "info", comp["message"], doc=WIKI + "Решение-проблем-обновления"))
    else:
        out.append(_chk("server", srv_title, "ok", "Служба LiveKit Egress установлена и работает."))
    server_ok = server_up

    # 2. функция в приложении
    app_title = "Видеозапись в приложении" if purpose == "video" else "Трансляции в приложении"
    if not cfg.enabled:
        out.append(_chk("app", app_title, "off", "Выключена в настройках ниже: в комнатах кнопки нет." if purpose == "video" else "Выключены в настройках ниже: эфир в комнатах не начать.",
                        action={"kind": "anchor", "label": "К настройкам", "target": "settings"}))
    else:
        out.append(_chk("app", app_title, "ok", "Включена." if purpose == "video" else "Включены."))

    # 3. сервер звонков — сам по себе, без Egress
    lk_ok = lk_err is None
    if lk_ok:
        out.append(_chk("livekit", "Сервер звонков (LiveKit)", "ok", "Отвечает."))
    else:
        out.append(_chk("livekit", "Сервер звонков (LiveKit)", "error", f"Не отвечает ({type(lk_err).__name__}): встречи и запись недоступны.",
                        action=_comp_action("livekit", "livekit_restart") if f is not None else None, doc=WIKI + "Решение-проблем-звонков-и-сети"))

    # 4. служба Egress подключена к LiveKit (задания на запись доходят)
    eg_title = "Служба записи подключена к LiveKit"
    if not server_ok:
        out.append(_skip("egress", eg_title, "заработает видеоподсистема"))
    elif not lk_ok:
        out.append(_skip("egress", eg_title, "ответит сервер звонков"))
    elif eg_err is None:
        out.append(_chk("egress", eg_title, "ok", "LiveKit принимает задания на запись."))
    else:
        out.append(_chk("egress", eg_title, "error", f"LiveKit не видит службу записи ({type(eg_err).__name__}). Обычно помогает перезапуск службы; если нет — «Восстановить настройки».",
                        action=_comp_action("video", "video_restart") if f is not None else None, doc=DOC_MEDIA))

    if purpose == "live":
        live = st.live
        base = live.live_dir()
        # 5. секрет подписи HLS — по этапам: каталог, запись backend, файл, права, чтение nginx, проверка nginx -t, применение. Последние три — по состоянию,
        # которое сообщает сам web (live-secret.sh): поколение загруженного секрета сверяется с файлом, а не время записи со временем запуска контейнера.
        out += await secret_checks(live, settings)
        # 6. health-порт Egress (контроль нагрузки) — настройка необязательная
        hp_title = "Контроль нагрузки Egress"
        if not cfg.egress_health_url:
            out.append(_chk("health", hp_title, "info", "Адрес health-порта не задан: нагрузка службы перед началом эфира не проверяется."))
        elif not server_ok:
            out.append(_skip("health", hp_title, "заработает видеоподсистема"))
        else:
            try:
                load = await asyncio.wait_for(st.video.api.cpu_load(cfg.egress_health_url), PROBE_S)
            except asyncio.TimeoutError:
                load = None
            if load is None:
                out.append(_chk("health", hp_title, "error", "Health-порт не отвечает: проверьте адрес в настройках ниже (обычно http://livekit-egress:8082/).", doc=DOC_MEDIA))
            else:
                out.append(_chk("health", hp_title, "ok" if load < cfg.max_cpu_load else "error", f"Загрузка процессора {load}% (предел {cfg.max_cpu_load}%)."))
        root = base
    else:
        root = Path(settings.recordings_path)

    # 7. место на томе
    try:
        free = shutil.disk_usage(root if root.exists() else root.parent).free
    except OSError:
        free = None
    need = cfg.min_free_gb * 1024**3
    if free is None:
        out.append(_chk("space", "Свободное место", "info", "Не удалось узнать свободное место на томе."))
    else:
        gb = free / 1024**3
        out.append(_chk("space", "Свободное место", "ok" if free >= need else "error", f"{gb:.1f} ГБ свободно (нужно не меньше {cfg.min_free_gb} ГБ)" + ("" if free >= need else ": новые записи и эфиры не начнутся") + ".",
                        doc=None if free >= need else DOC_MEDIA))
    return out


def warnings_of(items: list[dict]) -> list[str]:
    """Строки для прежнего поля `warnings`: только настоящие ошибки, без выключенных возможностей и пропущенных проверок."""
    return [f"{c['title']}: {c['message']}" for c in items if c["status"] == "error"]


def summary_of(items: list[dict]) -> dict[str, Any]:
    errs = [c for c in items if c["status"] == "error"]
    off = [c for c in items if c["status"] == "off"]
    return {"state": "error" if errs else "off" if off else "ok", "errors": len(errs), "checked_at": int(time.time())}
