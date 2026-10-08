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
REPAIR_IDS = ("data_dirs", "nginx_site", "sysctl", "prereq_missing", "image_commit", "migrations", "reverify", "llm_model", "llm17_enable", "llm17_disable", "sip_enable", "sip_disable")
BUSY_STATES = ("updating", "repairing")

# Версии, с которыми проект проверен (зеркало deployment/compat.env; совпадение проверяется тестом).
TESTED = {"livekit_server": "v1.13.7", "livekit_client_js": "2.22.3", "livekit_python_sdk": "1.1.20", "livekit_api_python": "1.2.1"}
REPO = "leonheard/peregovorka"


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
        st = self._json("status.json") or {}
        ts = float(st.get("ts") or 0)
        age = time.time() - ts if ts else None
        return {**st, "available": bool(ts) and age is not None and age <= HEARTBEAT_MAX_AGE, "heartbeat_age_s": None if age is None else int(age),
                "request_pending": (self.dir / "request.txt").exists()}

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
                out.append({k: d.get(k) for k in ("at", "started", "result", "stage", "from_version", "to_version", "from_commit", "to_commit", "source", "by")})
            if len(out) >= limit:
                break
        return out

    def remote(self) -> dict | None:
        r = self._json("remote.json")
        if r and r.get("checked_at"):
            r["age_s"] = int(time.time() - float(r["checked_at"]))
        return r

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
        if action not in ("check", "update", "scan", "repair"):
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
