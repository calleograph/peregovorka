"""Обновление из веб-интерфейса: обмен с «исполнителем» на хосте (scripts/updater.sh) и сведения о версиях компонентов.

Контейнер backend НЕ имеет доступа к Docker и git хоста (и не должен его иметь). Поэтому обновление устроено через общий каталог
`/data/updater` (на хосте — `$DATA_ROOT/updater`):

    backend  →  request.txt   запрос: проверить / обновить (простые строки key=value, без исполняемого содержимого)
    updater  →  status.json   «пульс» исполнителя и состояние текущего обновления (этап, код завершения)
    updater  →  remote.json   что нового в репозитории (список commit'ов, миграции, параметры .env)
    updater  →  update.log    построчный вывод scripts/update.sh (его показывает окно обновления)

Исполнитель (служба от root) выполняет только фиксированный набор действий: `check`, `scan`, `update` (scripts/update.sh --yes с разрешёнными флагами)
и `repair` с ID из белого списка REPAIR_IDS (зеркало scripts/lib/repairlib.sh; совпадение проверяется тестом). Произвольные команды из
веб-интерфейса выполнить нельзя. Найденные проблемы исполнитель пишет в `repairs.json`.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from importlib import metadata
from pathlib import Path
from typing import Any

import httpx

log = logging.getLogger("app.updates")

HEARTBEAT_MAX_AGE = 40          # с: исполнитель считается работающим, если «пульс» свежее
REQUEST_MAX_AGE = 600           # с: старый запрос исполнитель игнорирует
LOG_CHUNK = 64 * 1024
_SAFE = re.compile(r"[^A-Za-z0-9._-]")

# Белый список исправлений («Исправить автоматически»): зеркало REPAIR_IDS в scripts/lib/repairlib.sh. Backend передаёт исполнителю только эти ID.
REPAIR_IDS = ("data_dirs", "nginx_site", "sysctl", "prereq_missing", "image_commit", "migrations", "reverify", "llm_model", "sip_enable", "sip_disable")
BUSY_STATES = ("updating", "repairing")

# Версии, с которыми проект проверен (зеркало deployment/compat.env; совпадение проверяется тестом).
TESTED = {"livekit_server": "v1.13.7", "livekit_client_js": "2.22.3", "livekit_python_sdk": "1.1.20", "livekit_api_python": "1.2.1"}
REPO = "calleograph/peregovorka"
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")


class Channel:
    def __init__(self, directory: str):
        self.dir = Path(directory)

    # ---------------------------------------------------------------- чтение
    def _json(self, name: str) -> dict | None:
        try:
            data = json.loads((self.dir / name).read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else None
        except (OSError, ValueError):
            return None

    def status(self) -> dict:
        """Состояние исполнителя по файлам канала. Живость подтверждают ДВА независимых признака: status.json (меняется при смене этапа) и helper.json (пульс отдельным процессом
        каждые ~10 с). Во время долгой сборки образов status.json может не обновляться минутами — раньше это выглядело как «помощник не установлен»."""
        st = self._json("status.json") or {}
        pulse = self._json("helper.json") or {}
        now = time.time()
        ts, pts = float(st.get("ts") or 0), float(pulse.get("ts") or 0)
        age_s = now - ts if ts else None
        age_p = now - pts if pts else None
        status_fresh = age_s is not None and age_s <= HEARTBEAT_MAX_AGE
        pulse_fresh = age_p is not None and age_p <= HEARTBEAT_MAX_AGE and not pulse.get("stopped")
        ages = [a for a in (age_s, age_p) if a is not None]
        out = {**st, "available": status_fresh or pulse_fresh, "heartbeat_age_s": int(min(ages)) if ages else None, "status_age_s": None if age_s is None else int(age_s),
               "status_fresh": status_fresh, "pulse": pulse or None, "pulse_fresh": pulse_fresh, "install": self._json("install-state.json"), "unit_state": self._json("unit-state.json"),
               "request_pending": (self.dir / "request.txt").exists()}
        if pulse_fresh and pulse.get("uid") is not None:
            out["uid"], out["pid"] = pulse.get("uid"), pulse.get("pid")      # пользователь и PID берём из пульса: он свежее и принадлежит реальному процессу
        return out

    def history(self, limit: int = 15) -> list[dict]:
        """Попытки обновления (history.ndjson пишет update.sh при ЛЮБОМ запуске — из терминала и из веб-интерфейса), новые первыми."""
        out: list[dict] = []
        try:
            lines = (self.dir / "history.ndjson").read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return out
        for ln in reversed(lines):
            try:
                d = json.loads(ln)
            except ValueError:
                continue
            if isinstance(d, dict) and d.get("result") in ("ok", "failed"):
                row = {k: d.get(k) for k in ("at", "started", "result", "stage", "from_version", "to_version", "from_commit", "to_commit", "source", "by")}
                p = self._changes_path(str(d.get("from_version") or ""), str(d.get("to_version") or ""))
                row["has_changes"] = bool(p and p.exists())
                out.append(row)
            if len(out) >= limit:
                break
        return out

    def remote(self) -> dict | None:
        r = self._json("remote.json")
        if r and r.get("checked_at"):
            r["age_s"] = int(time.time() - float(r["checked_at"]))
        return r

    # ----------------------------------------------------------- «что изменилось» в конкретном переходе версий
    def _changes_path(self, from_v: str, to_v: str) -> Path | None:
        if not (VERSION_RE.match(from_v or "") and VERSION_RE.match(to_v or "")):
            return None
        return self.dir / "changes" / f"{from_v}__{to_v}.json"

    def save_changes(self, from_v: str, to_v: str, data: dict) -> bool:
        """Сохраняет описание перехода версий (то, что администратор видел перед обновлением), чтобы позже открыть его из истории."""
        p = self._changes_path(from_v, to_v)
        if p is None:
            return False
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            tmp.replace(p)
            return True
        except OSError:
            return False

    def load_changes(self, from_v: str, to_v: str) -> dict | None:
        p = self._changes_path(from_v, to_v)
        if p is None:
            return None
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            return d if isinstance(d, dict) else None
        except (OSError, ValueError):
            return None

    def read_log(self, offset: int) -> dict:
        path = self.dir / "update.log"
        try:
            size = path.stat().st_size
        except OSError:
            return {"offset": 0, "size": 0, "text": "", "reset": False}
        reset = offset > size or offset < 0           # журнал начат заново (новое обновление)
        if reset:
            offset = 0
        with path.open("rb") as fh:
            fh.seek(offset)
            raw = fh.read(LOG_CHUNK)
        # не режем многобайтовый символ посередине: обрываем по последнему переводу строки, если есть
        if len(raw) == LOG_CHUNK and b"\n" in raw:
            raw = raw[: raw.rindex(b"\n") + 1]
        return {"offset": offset + len(raw), "size": size, "text": raw.decode("utf-8", errors="replace"), "reset": reset}

    # ---------------------------------------------------------------- запись
    def repairs(self) -> dict | None:
        r = self._json("repairs.json")
        if r and r.get("checked_at"):
            r["age_s"] = int(time.time() - float(r["checked_at"]))
        return r

    def request(self, action: str, *, by: str, force_build: bool = False, pull: bool = False, repair: str | None = None) -> str:
        if action not in ("check", "update", "scan", "repair", "ping"):
            raise ValueError("action")
        if action == "repair" and repair not in REPAIR_IDS:
            raise ValueError("repair")
        rid = uuid.uuid4().hex[:16]
        extra = [f"repair={repair}"] if action == "repair" else []
        body = "\n".join([f"id={rid}", f"action={action}", f"force_build={int(force_build)}", f"pull={int(pull)}", *extra,
                          f"by={_SAFE.sub('_', by)[:60]}", f"at={int(time.time())}"]) + "\n"
        tmp = self.dir / f".request.{rid}.tmp"
        tmp.write_text(body, encoding="utf-8")
        tmp.replace(self.dir / "request.txt")
        return rid


INSTALL_FIX = "sudo ./scripts/updater.sh install --yes"
UNRESPONSIVE_STATUS_AGE = 120      # с: пульс идёт, а status.json не меняется и обновления нет — основной цикл исполнителя завис


def _ago(seconds: int | None) -> str:
    if seconds is None:
        return "неизвестно когда"
    if seconds < 90:
        return f"{seconds} с назад"
    if seconds < 5400:
        return f"{seconds // 60} мин назад"
    return f"{seconds // 3600} ч назад"


def helper_state(st: dict) -> dict:
    """Состояние помощника обновлений для интерфейса — РАЗДЕЛЬНО, а не одним «не установлен».

    ok · manual_process (работает, но не как служба) · wrong_user (процесс не от root) · unresponsive (пульс идёт, цикл завис) ·
    installed_inactive (служба установлена, но не запущена) · failed (служба аварийно остановлена) · stale_unit · not_installed.
    Контейнер не видит systemd, поэтому судит по файлам канала: пульс (helper.json), итог последней проверки установки на хосте (install-state.json),
    как завершилась служба (unit-state.json, пишет systemd) и status.json."""
    available = bool(st.get("available"))
    pulse, inst, unit = st.get("pulse") or {}, st.get("install") or {}, st.get("unit_state") or {}
    busy = st.get("state") in BUSY_STATES
    uid = st.get("uid")
    user = pulse.get("user") or (f"uid {uid}" if uid is not None else "неизвестный пользователь")
    details = {k: inst.get(k) for k in ("unit_exists", "unit_user", "enabled", "active", "substate", "result", "restarts", "main_pid", "proc_uid", "proc_user", "expected_uid", "ping", "checked_at") if inst}
    if available:
        if uid != 0:
            outside = pulse.get("under_systemd") is False
            return _h("wrong_user", True, False, f"Помощник работает от пользователя {user}, а не от root{' и вне службы systemd (запущен вручную прежней версией)' if outside else ''}: "
                      "прав на каталоги и настройки системы у него нет. Установка службы заменит этот процесс.", INSTALL_FIX, details, st)
        if pulse and pulse.get("under_systemd") is False:
            return _h("manual_process", True, True, "Помощник работает, но запущен вручную, а не службой systemd: после перезагрузки сервера он не запустится.", INSTALL_FIX, details, st)
        sa = st.get("status_age_s")
        if st.get("pulse_fresh") and not st.get("status_fresh") and not busy and sa is not None and sa > UNRESPONSIVE_STATUS_AGE:
            return _h("unresponsive", True, True, f"Помощник запущен и пишет пульс, но не отвечает на запросы уже {_ago(sa)}: основной цикл завис.", "sudo systemctl restart peregovorka-updater-<проект>", details, st)
        return _h("ok", True, True, "Помощник работает." + (" Сейчас выполняет обновление или исправление." if busy else ""), None, details, st)
    # не отвечает: различаем по тому, что оставила установка и сама служба
    age = st.get("heartbeat_age_s")
    verdict = inst.get("verdict")
    if verdict == "not_installed" or (not inst and not unit and not st.get("ts") and not pulse):
        return _h("not_installed", False, False, "Помощник обновлений не установлен: службы на сервере нет.", INSTALL_FIX, details, st)
    if verdict in ("failed", "wrong_user", "stale_unit", "not_enabled", "manual_process", "unresponsive", "permission_error") and inst.get("why"):
        state = "failed" if verdict not in ("wrong_user", "stale_unit") else verdict
        return _h(state, False, False, f"{inst['why']} (проверка на сервере {_ago(int(time.time() - float(inst['checked_at']))) if inst.get('checked_at') else 'ранее'})", inst.get("fix") or INSTALL_FIX, details, st)
    last = ""
    if unit:
        last = f" Последняя остановка службы: результат «{unit.get('result') or '?'}»" + (f", код {unit['exit_status']}" if unit.get("exit_status") else "") + "."
    if pulse.get("stopped"):
        return _h("installed_inactive", False, False, "Помощник обновлений установлен, но остановлен." + last, INSTALL_FIX, details, st)
    return _h("installed_inactive", False, False, f"Помощник обновлений установлен, но не запущен: пульс пропал, последний был {_ago(age)}." + last, INSTALL_FIX, details, st)


def _h(state: str, available: bool, privileged: bool, message: str, fix: str | None, details: dict, st: dict) -> dict:
    legacy = {"ok": None, "manual_process": None, "wrong_user": "no_privileges"}.get(state, state)
    return {"state": state, "available": available, "privileged": privileged, "message": message, "fix": fix, "details": details or None, "uid": st.get("uid"),
            "heartbeat_age_s": st.get("heartbeat_age_s"), "problem": legacy}


def outcome_summary(st: dict) -> dict | None:
    """Итог последнего обновления ПО раздельно: обновление · развёртывание · работоспособность · интеграции. Сбой интеграции (LDAP и т. п.) —
    не «обновление завершено с ошибкой»: версия установлена, сервисы работают, нужна точечная проверка настроек."""
    if st.get("action") == "repair" or not st.get("finished_at") or st.get("state") in BUSY_STATES:
        return None
    res = st.get("result")
    upd = st.get("update_status") or ("ok" if res == "ok" else "failed")
    dep = st.get("deploy_status") or ("ok" if res == "ok" else "unknown")
    health = st.get("health_status") or ("ok" if res == "ok" else "unknown")
    integ = st.get("integration_status") or ("ok" if res == "ok" else "unknown")
    return {"update": upd, "deployment": dep, "health": health, "integrations": integ, "integration_issues": st.get("integration_issues") or "",
            "needs_attention": integ == "fail" or res != "ok"}


# --------------------------------------------------------------------------------- версии компонентов
def _vt(v: str | None) -> tuple:
    """Сравнимый вид версии: 'v1.13.7' → (1, 13, 7); нечисловые хвосты игнорируются."""
    nums = re.findall(r"\d+", (v or "").split("-")[0].split("+")[0])
    return tuple(int(n) for n in nums[:4])


def compare(installed: str | None, latest: str | None) -> str:
    """'ok' — актуально, 'newer' — в интернете есть более новая, 'ahead' — установленная новее известной, 'unknown' — нет данных."""
    if not installed or not latest:
        return "unknown"
    a, b = _vt(installed), _vt(latest)
    if not a or not b:
        return "unknown"
    return "ok" if a == b else "newer" if a < b else "ahead"


async def _get_json(client: httpx.AsyncClient, url: str, headers: dict | None = None) -> Any:
    r = await client.get(url, headers=headers)
    r.raise_for_status()
    return r.json()


async def fetch_latest(transport: httpx.AsyncBaseTransport | None = None) -> dict[str, dict]:
    """Актуальные версии из публичных источников; недоступность интернета не ошибка — значение None и причина."""
    async with httpx.AsyncClient(timeout=httpx.Timeout(6.0, connect=4.0), transport=transport, follow_redirects=True,
                                 headers={"User-Agent": "peregovorka-version-check", "Accept": "application/json"}) as c:
        async def one(key: str, url: str, pick) -> tuple[str, dict]:
            try:
                return key, {"latest": str(pick(await _get_json(c, url))), "error": None}
            except Exception as exc:  # noqa: BLE001
                return key, {"latest": None, "error": type(exc).__name__}

        jobs = [
            one("livekit_server", "https://api.github.com/repos/livekit/livekit/releases/latest", lambda j: j["tag_name"]),
            one("livekit_client_js", "https://registry.npmjs.org/livekit-client/latest", lambda j: j["version"]),
            one("livekit_python_sdk", "https://pypi.org/pypi/livekit/json", lambda j: j["info"]["version"]),
            one("livekit_api_python", "https://pypi.org/pypi/livekit-api/json", lambda j: j["info"]["version"]),
            one("fastapi", "https://pypi.org/pypi/fastapi/json", lambda j: j["info"]["version"]),
            one("sqlalchemy", "https://pypi.org/pypi/sqlalchemy/json", lambda j: j["info"]["version"]),
            one("pydantic", "https://pypi.org/pypi/pydantic/json", lambda j: j["info"]["version"]),
            one("transcribe_cpp", "https://pypi.org/pypi/transcribe-cpp/json", lambda j: j["info"]["version"]),
            one("project", f"https://api.github.com/repos/{REPO}/commits/main", lambda j: j["sha"][:12]),
        ]
        return dict(await asyncio.gather(*jobs))


def _pkg(name: str) -> str | None:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None


def build_rows(installed: dict[str, str | None], latest: dict[str, dict]) -> list[dict]:
    """Строки таблицы «Версии»: установлено · проверено с проектом · актуально в интернете · статус · пояснение."""
    spec = [
        ("livekit_server", "LiveKit Server (сервер звонков)", True, "Закреплён на проверенной версии. Обновляется вместе с проектом."),
        ("livekit_client_js", "livekit-client (браузер)", True, "Зашит в веб-приложение; обновляется вместе с проектом."),
        ("livekit_python_sdk", "livekit (Python SDK, ASR)", True, "Обновляется вместе с проектом."),
        ("livekit_api_python", "livekit-api (Python, backend)", True, "Обновляется вместе с проектом."),
        ("transcribe_cpp", "transcribe-cpp (движок GGUF, ASR)", False, "Диапазон версий задан проектом; обновляется при сборке образа."),
        ("fastapi", "FastAPI (backend)", False, "Диапазон версий задан проектом; обновляется при сборке образа."),
        ("sqlalchemy", "SQLAlchemy (backend)", False, "Диапазон версий задан проектом; обновляется при сборке образа."),
        ("pydantic", "Pydantic (backend)", False, "Диапазон версий задан проектом; обновляется при сборке образа."),
        ("postgres", "PostgreSQL", False, "Мажорная версия закреплена намеренно (смена требует миграции каталога данных); обновления безопасности — в образе."),
        ("redis", "Redis", False, "Мажорная версия закреплена намеренно."),
        ("python", "Python (backend)", False, "Версия задана образом."),
    ]
    rows = []
    for key, title, pinned, note in spec:
        inst = installed.get(key)
        lat = (latest.get(key) or {}).get("latest")
        err = (latest.get(key) or {}).get("error")
        tested = TESTED.get(key)
        st = compare(inst, lat) if lat else "unknown"
        if pinned and st == "newer":
            msg = "В интернете есть более новая версия, но она не проверена с этим проектом. Отдельно её ставить не рекомендуется — дождитесь новой редакции проекта (кнопка «Обновить проект»)."
        elif st == "newer":
            msg = "Доступна более новая версия. Она попадёт в образ при следующей пересборке («Обновить проект» с пересборкой); отдельно ставить не нужно."
        elif st == "unknown" and err:
            msg = "Нет данных: у сервера нет доступа в интернет или источник недоступен. " + note
        else:
            msg = note
        rows.append({"key": key, "title": title, "installed": inst, "tested": tested, "latest": lat, "status": st, "pinned": pinned, "note": msg})
    return rows


async def installed_versions(app) -> dict[str, str | None]:
    """Версии, установленные на сервере: библиотеки backend, LiveKit (из настроек), ASR (по «пульсу»), PostgreSQL и Redis (запросами)."""
    s = app.state.settings
    out: dict[str, str | None] = {
        "livekit_server": s.livekit_server_version or None, "livekit_api_python": _pkg("livekit-api"), "fastapi": _pkg("fastapi"),
        "sqlalchemy": _pkg("sqlalchemy"), "pydantic": _pkg("pydantic"), "python": ".".join(map(str, __import__("sys").version_info[:3])),
        "livekit_client_js": None, "livekit_python_sdk": None, "transcribe_cpp": None, "postgres": None, "redis": None,
    }
    try:
        hb = await app.state.bridge.heartbeat()
        out["livekit_python_sdk"] = (hb or {}).get("livekit_sdk")
        out["transcribe_cpp"] = (hb or {}).get("transcribe_cpp")
    except Exception:  # noqa: BLE001
        pass
    try:
        info = await app.state.redis.info("server")
        out["redis"] = info.get("redis_version")
    except Exception:  # noqa: BLE001
        pass
    try:
        from sqlalchemy import text  # noqa: PLC0415

        async with app.state.session_maker() as db:
            v = (await db.execute(text("select version()"))).scalar_one()
        m = re.search(r"PostgreSQL (\d+(?:\.\d+)*)", str(v))
        out["postgres"] = m.group(1) if m else None
    except Exception:  # noqa: BLE001
        pass
    return out
