"""Диагностика видеозаписи и трансляций с учётом зависимостей компонентов.

Раньше разделы «Видеозапись» и «Трансляции» показывали сразу несколько ошибок («Egress не установлен», «нет связи с сервером звонков», «секрет HLS
не записан», «health-порт не отвечает»), хотя причина одна — видеоподсистема выключена. Теперь проверки идут по цепочке:

    видеоподсистема на сервере → функция в приложении → сервер звонков → служба Egress подключена → (эфир: секрет HLS, health-порт) → место → хранилище

Каждая проверка — одно из состояний: `ok`; `error` — настоящая неисправность; `off` — возможность выключена (это не ошибка); `skipped` — проверить
пока нельзя, потому что не работает то, от чего она зависит; `info` — к сведению. Для штатно устранимых состояний указано действие
(`action`: компонент и операция из services/server_components.py, исправление помощника или переход к настройке) и статья вики (`doc`).
"""
from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import Any

from . import server_components as sc

WIKI = sc.WIKI
DOC_MEDIA = WIKI + "Решение-проблем-видеозаписи-и-трансляций"
DOC_STORAGE = WIKI + "Решение-проблем-файловых-хранилищ"


def _chk(cid: str, title: str, status: str, message: str, *, action: dict | None = None, doc: str | None = None) -> dict:
    return {"id": cid, "title": title, "status": status, "message": message, "action": action, "doc": doc}


def _skip(cid: str, title: str, why: str) -> dict:
    return _chk(cid, title, "skipped", f"Проверится, когда {why}.")


def _comp_action(name: str, op: str) -> dict:
    o = sc.OPS[op]
    return {"kind": "component", "component": name, "op": op, "label": o["label"], "title": o["title"], "consequences": o["consequences"],
            "interrupts_calls": o["calls"], "danger": bool(o.get("danger"))}


def _parse_ts(v: str | None) -> float | None:
    """StartedAt Docker («2026-10-29T20:11:03.123456789Z», всегда UTC) → секунды; до секунд этого достаточно."""
    from datetime import datetime, timezone  # noqa: PLC0415

    if not v or v.startswith("0001-"):
        return None
    try:
        return datetime.strptime(v[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return None


async def checks(st, db, purpose: str) -> list[dict]:
    """purpose: video | live"""
    settings = st.settings
    ch = sc.channel_for(settings)
    f = sc.facts(ch)
    comp = sc.describe("video", f, settings)
    cfg = await st.settings_svc.get(db, purpose)
    out: list[dict] = []

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
    server_ok = comp["state"] in ("ok", "unknown") and (comp["state"] == "ok" or comp.get("enabled"))

    # 2. функция в приложении
    app_title = "Видеозапись в приложении" if purpose == "video" else "Трансляции в приложении"
    if not cfg.enabled:
        out.append(_chk("app", app_title, "off", "Выключена в настройках ниже: в комнатах кнопки нет." if purpose == "video" else "Выключены в настройках ниже: эфир в комнатах не начать.",
                        action={"kind": "anchor", "label": "К настройкам", "target": "settings"}))
    else:
        out.append(_chk("app", app_title, "ok", "Включена." if purpose == "video" else "Включены."))

    # 3. сервер звонков — сам по себе, без Egress
    lk_ok = True
    try:
        await st.video.api.probe_livekit()
        out.append(_chk("livekit", "Сервер звонков (LiveKit)", "ok", "Отвечает."))
    except Exception as exc:  # noqa: BLE001
        lk_ok = False
        out.append(_chk("livekit", "Сервер звонков (LiveKit)", "error", f"Не отвечает ({type(exc).__name__}): встречи и запись недоступны.",
                        action=_comp_action("livekit", "livekit_restart") if f is not None else None, doc=WIKI + "Решение-проблем-звонков-и-сети"))

    # 4. служба Egress подключена к LiveKit (задания на запись доходят)
    eg_title = "Служба записи подключена к LiveKit"
    if not server_ok:
        out.append(_skip("egress", eg_title, "заработает видеоподсистема"))
    elif not lk_ok:
        out.append(_skip("egress", eg_title, "ответит сервер звонков"))
    else:
        try:
            await st.video.api.probe()
            out.append(_chk("egress", eg_title, "ok", "LiveKit принимает задания на запись."))
        except Exception as exc:  # noqa: BLE001
            out.append(_chk("egress", eg_title, "error", f"LiveKit не видит службу записи ({type(exc).__name__}). Обычно помогает перезапуск службы; если нет — «Восстановить настройки».",
                            action=_comp_action("video", "video_restart") if f is not None else None, doc=DOC_MEDIA))

    if purpose == "live":
        live = st.live
        base = live.live_dir()
        # 5. секрет подписи HLS: пишет backend при запуске, nginx (web) читает при своём запуске
        sec_title = "Секрет подписи трансляций"
        conf = base / "nginx" / "secret.conf"
        if not conf.is_file():
            try:
                live.prepare()                         # самовосстановление: каталог мог появиться после запуска backend
            except Exception:  # noqa: BLE001
                pass
        if not conf.is_file():
            out.append(_chk("secret", sec_title, "error", "Не записан: каталог трансляций недоступен для записи. «Исправить автоматически» в разделе «Обновления и версии» восстановит права каталогов.",
                            action={"kind": "repair", "repair": "data_dirs", "label": "Исправить права каталогов"}, doc=DOC_MEDIA))
        else:
            web = (f or {}).get("containers", {}).get("web") if isinstance((f or {}).get("containers"), dict) else None
            web_started = _parse_ts((web or {}).get("started_at"))
            if web_started is not None and conf.stat().st_mtime > web_started + 1:
                out.append(_chk("secret", sec_title, "error", "Записан после запуска веб-сервера: зрители не откроют поток, пока web не перечитает его.",
                                action=_comp_action("web", "web_restart"), doc=DOC_MEDIA))
            else:
                out.append(_chk("secret", sec_title, "ok", "Записан."))
        # 6. health-порт Egress (контроль нагрузки) — настройка необязательная
        hp_title = "Контроль нагрузки Egress"
        if not cfg.egress_health_url:
            out.append(_chk("health", hp_title, "info", "Адрес health-порта не задан: нагрузка службы перед началом эфира не проверяется."))
        elif not server_ok:
            out.append(_skip("health", hp_title, "заработает видеоподсистема"))
        else:
            load = await st.video.api.cpu_load(cfg.egress_health_url)
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
