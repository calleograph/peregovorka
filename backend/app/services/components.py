"""«Версии компонентов»: что РАБОТАЕТ на сервере, с чем проверен этот релиз, что вышло нового и что решено по каждому обновлению.

Источник правды — манифест релиза `app/components.json` (его ведёт `scripts/deps.py`; в нём закреплённые и проверенные версии, дата ревизии, решения и отсрочки с причиной,
задачей и датой повторной проверки). Установленные версии берутся из работающих контейнеров: библиотеки backend — из самого процесса, ASR — из его «пульса» (Redis),
PostgreSQL и Redis — запросом к серверу, LiveKit — из образа; версии frontend интерфейс добавляет из собранного приложения (`/version.json`). Доступные версии —
по данным ревизии релиза и (если у сервера есть интернет) актуальные из публичных источников.
"""
from __future__ import annotations

import asyncio
import json
import re
import subprocess
import sys
from importlib import metadata
from pathlib import Path
from typing import Any

import httpx

MANIFEST = Path(__file__).resolve().parent.parent / "components.json"
STATUSES = ("current", "available", "deferred", "review", "error", "manual", "off")


def load_manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def _vt(v: str | None) -> tuple:
    return tuple(int(n) for n in re.findall(r"\d+", (v or "").split("@")[0].split("+")[0])[:4])


def compare(installed: str | None, latest: str | None) -> str:
    """'ok' — совпадает, 'newer' — есть более новая, 'ahead' — установленная новее известной, 'unknown' — нет данных."""
    if not installed or not latest:
        return "unknown"
    if re.fullmatch(r"[0-9a-f]{7,40}", installed) and re.fullmatch(r"[0-9a-f]{7,40}", latest):
        return "ok" if installed == latest else "newer"
    a, b = _vt(installed), _vt(latest)
    if not a or not b:
        return "unknown"
    n = max(len(a), len(b))
    a, b = a + (0,) * (n - len(a)), b + (0,) * (n - len(b))
    return "ok" if a == b else "newer" if a < b else "ahead"


def tested_map(m: dict | None = None) -> dict[str, str]:
    return {c["key"]: c["tested"] for c in (m or load_manifest())["components"]}


# --------------------------------------------------------------------------- доступные версии (необязательно, если есть интернет)
async def fetch_latest(components: list[dict], project_repo: str, transport: httpx.AsyncBaseTransport | None = None) -> dict[str, dict]:
    """Свежие версии из публичных реестров для компонентов, у которых источник — PyPI, npm или GitHub; остальные (образы) берутся из ревизии релиза. Недоступность сети — не ошибка."""
    async with httpx.AsyncClient(timeout=httpx.Timeout(6.0, connect=4.0), transport=transport, follow_redirects=True,
                                 headers={"User-Agent": "peregovorka-version-check", "Accept": "application/json"}) as c:
        async def get(url: str) -> Any:
            r = await c.get(url)
            r.raise_for_status()
            return r.json()

        async def one(key: str, src: dict) -> tuple[str, dict]:
            try:
                t = src["type"]
                if t == "pypi":
                    v = (await get(f"https://pypi.org/pypi/{src['name']}/json"))["info"]["version"]
                elif t == "npm":
                    v = (await get(f"https://registry.npmjs.org/{src['name'].replace('/', '%2F')}/latest"))["version"]
                elif t == "github-release":
                    v = (await get(f"https://api.github.com/repos/{src['repo']}/releases/latest"))["tag_name"]
                elif t == "github-commit":
                    v = (await get(f"https://api.github.com/repos/{src['repo']}/commits/{src.get('branch', 'main')}"))["sha"]
                else:
                    return key, {"latest": None, "error": None}
                return key, {"latest": str(v), "error": None}
            except Exception as exc:  # noqa: BLE001
                return key, {"latest": None, "error": type(exc).__name__}

        jobs = [one(x["key"], x["source"]) for x in components if x.get("source", {}).get("type") in ("pypi", "npm", "github-release", "github-commit")]
        jobs.append(one("project", {"type": "github-commit", "repo": project_repo, "branch": "main"}))
        res = dict(await asyncio.gather(*jobs))
        if res.get("project", {}).get("latest"):
            res["project"]["latest"] = res["project"]["latest"][:12]
        return res


# --------------------------------------------------------------------------- работающие версии
def _pkg(name: str) -> str | None:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None


def _ffmpeg() -> str | None:
    try:
        out = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, timeout=5).stdout
        m = re.search(r"ffmpeg version (\S+)", out)
        return m.group(1) if m else None
    except (OSError, subprocess.SubprocessError):
        return None


async def installed_versions(app, m: dict | None = None) -> dict[str, str | None]:
    """Работающие версии по ключам манифеста; None — определить не удалось (у необязательных служб — не запущены)."""
    m = m or load_manifest()
    s = app.state.settings
    asr: dict[str, str | None] = {}
    try:
        hb = await app.state.bridge.heartbeat()
        asr = dict((hb or {}).get("packages") or {})
        if not asr and hb:                                          # прежняя версия ASR: только две библиотеки
            asr = {"livekit": hb.get("livekit_sdk"), "transcribe-cpp": hb.get("transcribe_cpp")}
    except Exception:  # noqa: BLE001
        pass
    out: dict[str, str | None] = {}
    for c in m["components"]:
        rt = c.get("runtime") or {}
        pkg = rt.get("python_package")
        if pkg:
            out[c["key"]] = _pkg(pkg) if rt.get("service") == "backend" else asr.get(pkg)
    out["python_backend"] = ".".join(map(str, sys.version_info[:3]))
    out["python_asr"] = asr.get("python")
    out["gigaam"] = asr.get("gigaam")
    out["livekit_server"] = s.livekit_server_version or None
    out["ffmpeg"] = _ffmpeg()
    out.update(await _web_versions(app, m))
    try:
        info = await app.state.redis.info("server")
        out["redis"] = info.get("redis_version")
    except Exception:  # noqa: BLE001
        pass
    try:
        from sqlalchemy import text  # noqa: PLC0415

        async with app.state.session_maker() as db:
            v = (await db.execute(text("select version()"))).scalar_one()
        mm = re.search(r"PostgreSQL (\d+(?:\.\d+)*)", str(v))
        out["postgres"] = mm.group(1) if mm else None
    except Exception:  # noqa: BLE001
        pass
    return out


async def _web_versions(app, m: dict) -> dict[str, str | None]:
    """Версии frontend из СОБРАННОГО приложения: /version.json (npm-пакеты и Node, записанные при сборке), /nginx-version.txt и /drawio/VERSION отдаёт контейнер web."""
    base = getattr(app.state.settings, "web_internal_url", "") or "http://web:8080"
    transport = (getattr(app.state, "test_transports", {}) or {}).get("web")
    out: dict[str, str | None] = {}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(3.0, connect=2.0), transport=transport) as c:
            vj = (await c.get(f"{base}/version.json")).json()
            comps = vj.get("components") or {}
            try:
                out["nginx"] = (await c.get(f"{base}/nginx-version.txt")).text.strip() or None
                out["drawio"] = (await c.get(f"{base}/drawio/VERSION")).text.strip() or None
            except httpx.HTTPError:
                pass
            out["node"] = vj.get("node")
            for comp in m["components"]:
                rt = comp.get("runtime") or {}
                if rt.get("how") == "web-version-json" and rt.get("name") in comps:
                    out[comp["key"]] = comps[rt["name"]]
    except (httpx.HTTPError, ValueError):
        pass
    return out


# --------------------------------------------------------------------------- строки таблицы
def _security(m: dict, c: dict) -> dict:
    """Статус безопасности по данным ревизии (OSV): известные уязвимости закреплённых версий именно этого компонента."""
    sec = m.get("security") or {}
    adv = sec.get("advisories") or {}
    names = {(loc.get("name") or "").lower() for loc in c["locations"] if loc.get("name")}
    ids = sorted({i for k, v in adv.items() for i in v if k.split("==")[0].lower() in names})
    if not sec:
        return {"state": "unknown", "ids": [], "checked_at": None}
    return {"state": "advisories" if ids else "ok", "ids": ids[:6], "checked_at": sec.get("checked_at")}


def build_rows(m: dict, installed: dict[str, str | None], live: dict[str, dict], sip_enabled: bool, llm_enabled: bool = False, video_enabled: bool = False) -> list[dict]:
    rows = []
    for c in m["components"]:
        key = c["key"]
        inst = installed.get(key)
        lat_live = (live.get(key) or {}).get("latest")
        err = (live.get(key) or {}).get("error")
        latest = lat_live or c["reviewed_latest"]
        d = c.get("deferred")
        src = (c.get("source") or {}).get("type")
        rt = c.get("runtime")
        pinned = c["pinned"]
        optional = key in ("livekit_sip", "llama_cpp", "livekit_egress")
        if src == "none":
            status, note = "manual", c.get("note") or "Версия определяется образом или внешним сервисом; автоматической проверки нет."
        elif optional and ((key == "livekit_sip" and not sip_enabled) or (key == "livekit_egress" and not video_enabled) or key == "llama_cpp"):
            status, note = "off", "Необязательная служба: сейчас не запущена на этом сервере, версия образа закреплена релизом."
        elif rt and not inst:
            status, note = "error", "Не удалось определить версию работающего компонента (служба не запущена или не отвечает)."
        else:
            running_ok = (not rt) or _same(inst, pinned, bool((rt or {}).get("prefix_match")))
            newer = compare(pinned, latest) == "newer"
            if rt and not running_ok:
                status, note = "review", f"Работает {inst}, а релиз проверен с {pinned}: возможно, образ не пересобран. Выполните «Обновить проект» с полной пересборкой или scripts/update.sh --force-build."
            elif newer and d and d.get("version") == latest:
                status, note = "deferred", d["reason"]
            elif newer:
                status, note = "available", "Вышла новая версия. Она попадёт в проект при подготовке следующего релиза после проверки совместимости; на сервере отдельно её ставить не нужно."
            else:
                status, note = "current", c.get("note") or ""
        if err and status == "current":
            note = (note + " " if note else "") + "Свежие данные из интернета получить не удалось; показана ревизия релиза."
        rows.append({"key": key, "title": c["title"], "group": c["group"], "installed": inst, "pinned": pinned, "tested": c["tested"], "latest": latest, "status": status, "note": note,
                     "checked_at": m["reviewed_at"], "changelog": c.get("changelog"), "bundle": c.get("bundle"),
                     "deferred": {k: d.get(k) for k in ("version", "reason", "issue", "recheck", "plan")} if d and status == "deferred" else None,
                     "security": _security(m, c)})
    return rows


def _same(running: str | None, pinned: str, prefix: bool) -> bool:
    if running is None:
        return False
    a, b = running.lstrip("v"), pinned.lstrip("v")
    return a == b or (prefix and a.startswith(b))
