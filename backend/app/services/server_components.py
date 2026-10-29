"""Штатные дополнительные компоненты сервера (видеоподсистема Egress, SIP-телефония, LiveKit, web): состояние и действия из администрирования.

Единый подход для функций, которыми раньше управляли по SSH (`sudo scripts/video.sh enable` и т. п.):

* **Права.** Контейнер backend не имеет доступа к Docker и не получает его. Действие выполняет помощник обновлений на хосте (служба от root,
  scripts/updater.sh): backend кладёт в каталог обмена запрос `action=component op=ID`. ID проверяется по белому списку дважды — здесь (OPS)
  и на хосте (COMPONENT_OPS в scripts/lib/complib.sh, совпадение проверяет тест). Произвольную команду, путь или имя контейнера передать нельзя.
* **Конфликты.** Помощник выполняет одно действие за раз; пока идёт обновление, исправление или другое действие, новый запрос отклоняется (409).
* **Подтверждение.** У каждого действия перечислены последствия (перезапуск LiveKit прерывает звонки и т. п.); без `confirm=true` запрос не принимается.
* **Журнал.** Каждый запрос пишется в аудит (`system.component`) и журнал событий; ход выполнения — в журнал помощника (как у обновления).
* **Истина о сервере.** Переменные окружения backend (`VIDEO_ENABLED`, `SIP_ENABLED`) задаются при запуске контейнера и после включения функции
  из панели устаревают. Поэтому фактическое состояние берётся из `components.json`, который пишет помощник; переменные окружения — только запасной вариант.

Новый компонент добавляется так: действие в COMPONENT_OPS (complib.sh) и OPS ниже, описание в COMPONENTS, вычисление состояния в `_state_*`.
"""
from __future__ import annotations

import time
from typing import Any

from . import updates as upd

WIKI = "https://github.com/calleograph/peregovorka/wiki/"

# Последствия показываются в окне подтверждения дословно: короткие фразы для администратора, без команд Linux.
OPS: dict[str, dict[str, Any]] = {
    "video_enable": {"label": "Установить и включить", "title": "Включить видеоподсистему", "calls": True, "consequences": [
        "Будет скачан образ службы LiveKit Egress (около 1,3 ГБ), если его ещё нет на сервере.",
        "Сервер звонков (LiveKit) перезапустится: идущие звонки прервутся на несколько секунд, участники переподключатся сами.",
        "Каждая одновременная видеозапись или эфир занимает около 2–4 ядер процессора и 1–2 ГБ памяти."]},
    "video_disable": {"label": "Выключить", "title": "Выключить видеоподсистему", "calls": True, "danger": True, "consequences": [
        "Идущие видеозаписи и эфиры будут остановлены; уже записанная часть сохранится.",
        "Сервер звонков (LiveKit) перезапустится: идущие звонки прервутся на несколько секунд.",
        "Готовые видеозаписи, настройки комнат и трансляций сохраняются."]},
    "video_restart": {"label": "Перезапустить службу", "title": "Перезапустить службу записи и эфира", "calls": False, "consequences": [
        "Идущие видеозаписи и эфиры будут закрыты; уже записанная часть сохранится.",
        "Звонки не прерываются."]},
    "sip_enable": {"label": "Установить и включить", "title": "Включить SIP-телефонию", "calls": True, "consequences": [
        "Будет скачан образ службы LiveKit SIP, если его ещё нет на сервере.",
        "Сервер начнёт принимать SIP и RTP на портах из настроек; межсетевой экран не меняется — откройте порты только для адресов АТС.",
        "Сервер звонков (LiveKit) перезапустится: идущие звонки прервутся на несколько секунд."]},
    "sip_disable": {"label": "Выключить", "title": "Выключить SIP-телефонию", "calls": True, "danger": True, "consequences": [
        "Идущие телефонные звонки завершатся, порты SIP и RTP перестанут публиковаться.",
        "Сервер звонков (LiveKit) перезапустится: идущие звонки прервутся на несколько секунд.",
        "Профили АТС и номера комнат сохраняются."]},
    "sip_restart": {"label": "Перезапустить службу", "title": "Перезапустить службу телефонии", "calls": False, "consequences": [
        "Идущие телефонные звонки прервутся; звонки из браузера и приложений не затрагиваются."]},
    "livekit_restart": {"label": "Перезапустить LiveKit", "title": "Перезапустить сервер звонков", "calls": True, "danger": True, "consequences": [
        "Все идущие звонки прервутся на несколько секунд, участники переподключатся сами.",
        "Идущие видеозаписи и эфиры могут завершиться."]},
    "web_restart": {"label": "Перезапустить web", "title": "Перезапустить веб-сервер приложения", "calls": False, "consequences": [
        "Сайт будет недоступен несколько секунд; открытые страницы и встречи переподключатся сами.",
        "Перечитываются сертификаты и секрет подписи трансляций."]},
}
assert set(OPS) | {"state"} == set(upd.COMPONENT_OPS)

COMPONENTS: dict[str, dict[str, Any]] = {
    "video": {"title": "Видеоподсистема (LiveKit Egress)", "what": "Одна служба для видеозаписи встреч (MP4) и прямых эфиров (HLS и внешние площадки).",
              "container": "livekit-egress", "doc": "Решение-проблем-видеозаписи-и-трансляций"},
    "sip": {"title": "SIP-телефония (LiveKit SIP)", "what": "Звонки из комнат на телефоны и входящие звонки через АТС.", "container": "livekit-sip",
            "doc": "Как-подключить-SIP-телефонию"},
    "livekit": {"title": "Сервер звонков (LiveKit)", "what": "Передача звука и видео между участниками.", "container": "livekit", "doc": "Решение-проблем-звонков-и-сети"},
    "web": {"title": "Веб-сервер приложения (web)", "what": "Страницы, API, раздача трансляций зрителям.", "container": "web", "doc": "Решение-проблем-HTTPS-и-прокси"},
}

FACTS_MAX_AGE = 86400 * 3      # с: сведения помощника старше — не используются (помощник давно не работал), берётся окружение backend


def _truthy(v: Any) -> bool:
    return str(v).strip().lower() in ("yes", "true", "1", "on")


def facts(channel: upd.Channel) -> dict | None:
    f = channel.components()
    if not f or not isinstance(f.get("env"), dict):
        return None
    try:
        if time.time() - float(f.get("ts") or 0) > FACTS_MAX_AGE:
            return None
    except (TypeError, ValueError):
        return None
    return f


def enabled_on_server(settings, channel: upd.Channel, name: str) -> bool:
    """Включена ли функция на сервере (VIDEO_ENABLED / SIP_ENABLED в .env): по сведениям помощника, иначе — по окружению backend."""
    f = facts(channel)
    key = {"video": "video_enabled", "sip": "sip_enabled"}[name]
    if f is not None and key in f["env"]:
        return bool(f["env"][key])
    return _truthy(getattr(settings, key, "no"))


def channel_for(settings) -> upd.Channel:
    return upd.Channel(f"{settings.data_dir}/updater")


def _c(f: dict | None, name: str) -> dict | None:
    cs = (f or {}).get("containers")
    return cs.get(name) if isinstance(cs, dict) else None


def _container_text(c: dict | None) -> str:
    if not c or c.get("state") == "absent":
        return "контейнер не создан"
    st, hl = c.get("state"), c.get("health")
    if st == "running":
        return {"healthy": "работает", "starting": "запускается", "unhealthy": "работает, но не проходит проверку здоровья"}.get(hl or "", "работает")
    return {"exited": "остановлен", "restarting": "перезапускается", "created": "создан, не запущен", "dead": "аварийно остановлен"}.get(st or "", st or "неизвестно")


def _op(op: str, *, primary: bool = False, label: str | None = None) -> dict:
    o = OPS[op]
    return {"id": op, "label": label or o["label"], "title": o["title"], "consequences": o["consequences"], "interrupts_calls": o["calls"], "danger": bool(o.get("danger")), "primary": primary}


def _optional_state(f: dict | None, settings, name: str, container: str, image_key: str, fallback_env: str) -> dict:
    """Общая логика для компонентов, которые включаются и выключаются (video, sip)."""
    if f is None:
        on = _truthy(getattr(settings, fallback_env, "no"))
        return {"state": "unknown", "enabled": on, "message": "Помощник обновлений не передал сведения о службах сервера: состояние неизвестно. Проверьте его в «Обновления и версии».",
                "ops": [_op(f"{name}_enable", primary=not on)] if not on else []}
    on = bool(f["env"].get(f"{name}_enabled"))
    if not on:
        return {"state": "off", "enabled": False, "message": "Выключена на сервере.", "ops": [_op(f"{name}_enable", primary=True)]}
    c = _c(f, container)
    if f.get("docker") is False:
        return {"state": "error", "enabled": True, "message": "Помощник не смог обратиться к Docker на сервере: состояние контейнеров неизвестно.", "ops": []}
    images = f.get("images") or {}
    if images.get(image_key) is False:
        return {"state": "error", "enabled": True, "message": "Включена, но образ службы не скачан (нет доступа к Docker Hub или места на диске при прошлой попытке).",
                "ops": [_op(f"{name}_enable", primary=True, label="Повторить установку")]}
    if not f["env"].get("livekit_redis"):
        return {"state": "error", "enabled": True, "message": "Включена, но сервер звонков не подключён к общей шине (Redis): служба не получает задания.",
                "ops": [_op(f"{name}_enable", primary=True, label="Восстановить настройки")]}
    text = _container_text(c)
    if not c or c.get("state") != "running":
        return {"state": "error", "enabled": True, "message": f"Включена, но служба не работает: {text}.", "ops": [_op(f"{name}_restart", primary=True, label="Повторить запуск"),
                _op(f"{name}_enable", label="Восстановить настройки"), _op(f"{name}_disable")]}
    if c.get("health") == "unhealthy":
        return {"state": "error", "enabled": True, "message": f"Служба {text}.", "ops": [_op(f"{name}_restart", primary=True), _op(f"{name}_disable")]}
    if c.get("health") == "starting":
        return {"state": "starting", "enabled": True, "message": "Служба запускается.", "ops": []}
    restarts = int(c.get("restarts") or 0)
    return {"state": "ok", "enabled": True, "message": "Работает." + (f" Перезапусков после создания: {restarts}." if restarts else ""), "ops": [_op(f"{name}_restart"), _op(f"{name}_disable")]}


def _core_state(f: dict | None, name: str, op: str) -> dict:
    c = _c(f, COMPONENTS[name]["container"])
    if f is None or c is None:
        return {"state": "unknown", "enabled": True, "message": "Состояние неизвестно (нет сведений от помощника обновлений).", "ops": [_op(op)] if f is not None else []}
    ok = c.get("state") == "running" and c.get("health") in ("healthy", "none", None)
    return {"state": "ok" if ok else "error", "enabled": True, "message": _container_text(c).capitalize() + ".", "ops": [_op(op, primary=not ok)]}


def describe(name: str, f: dict | None, settings) -> dict:
    meta = COMPONENTS[name]
    if name == "video":
        st = _optional_state(f, settings, "video", "livekit-egress", "egress", "video_enabled")
    elif name == "sip":
        st = _optional_state(f, settings, "sip", "livekit-sip", "sip", "sip_enabled")
    elif name == "livekit":
        st = _core_state(f, "livekit", "livekit_restart")
    else:
        st = _core_state(f, "web", "web_restart")
    return {"id": name, "title": meta["title"], "what": meta["what"], "doc": WIKI + meta["doc"], **st}


def allowed_ops(name: str, f: dict | None, settings) -> set[str]:
    """Действия, допустимые для компонента сейчас (кнопки, которые показывает интерфейс). Выключить можно и при ошибке."""
    return {o["id"] for o in describe(name, f, settings)["ops"]}


def helper_block(channel: upd.Channel) -> dict:
    st = channel.status()
    h = upd.helper_state(st)
    busy = st.get("state") in upd.BUSY_STATES or bool(st.get("request_pending"))
    last = None
    if st.get("action") == "component" and st.get("repair_id"):
        last = {"op": st.get("repair_id"), "title": OPS.get(st.get("repair_id"), {}).get("title", st.get("repair_id")), "result": st.get("result") or None,
                "running": st.get("state") == "component", "step_name": st.get("step_name"), "started_at": st.get("started_at"), "finished_at": st.get("finished_at"), "exit_code": st.get("exit_code")}
    return {"available": h["available"], "privileged": h["privileged"], "state": h["state"], "message": h["message"], "fix": h.get("fix"), "busy": busy,
            "busy_with": st.get("action") if busy else None, "last": last}
