#!/usr/bin/env python3
"""Контроль версий сторонних компонентов Peregovorka (только стандартная библиотека Python 3.9+).

Единственный источник правды — `backend/app/components.json` (манифест компонентов релиза; он же показывается в «Администрирование → Обзор → Версии компонентов»).
Для каждого компонента в нём записано: какая версия закреплена в файлах проекта (`pinned`), с какой проверен релиз (`tested`), какая была новейшей на дату ревизии
(`reviewed_latest`) и решение: актуально либо отложено с причиной, задачей и датой повторной проверки. Сборка берёт версии из закреплённых файлов (constraints, lock,
digest образов), поэтому «полная пересборка» воспроизводит релиз, а новые версии приходят только через ревизию.

Команды:
  verify    [--online] [--max-age ДНЕЙ] [--release X.Y.Z]   проверка согласованности (по умолчанию без сети): закрепления в файлах = манифест, решения оформлены, constraints покрывают requirements.
                                          С --online дополнительно: нет версий новее ревизии без решения, нет известных уязвимостей (OSV) в закреплённых версиях. Код 1 при проблемах.
  check     [--json]                      сеть: что вышло нового по каждому компоненту (таблица «закреплено · проверено · доступно · решение»), без изменений файлов.
  review    [--release X.Y.Z]             сеть: обновляет в манифесте reviewed_latest/reviewed_at/security; решения не трогает. Дальше — обновить или отложить каждое «нужно решение».
  freeze                                  пересобирает backend/constraints.txt и asr-service/constraints.txt (точные версии всех зависимостей, включая транзитивные) для Python 3.12/linux.
  report    [--from REF]                  «Было → Стало» между манифестом в git REF (по умолчанию — последний тег) и текущим; Markdown.
  postcheck [--env FILE] [--json]         на сервере после обновления: версии РАБОТАЮЩИХ контейнеров против манифеста релиза; код 0 — совпало, 1 — расхождения, 2 — критические.
  pins                                    показывает, где закреплена каждая версия.
  pin-images                              записывает в манифест digest образов для закреплённых тегов.
  sync                                    переносит версии из constraints и package-lock в манифест (pinned/tested).
  apply                                   записывает версии и digest из манифеста в файлы проекта (env, ARG Dockerfile, образы в compose); constraints — `freeze`, npm — `npm install`.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "backend" / "app" / "components.json"
UA = {"User-Agent": "peregovorka-deps/1", "Accept": "application/json"}
LEVEL = {"major": 3, "minor": 2, "patch": 1, "same": 0, "older": -1, "unknown": -2}
CRITICAL_DEFAULT = ("livekit_server", "livekit_python_sdk", "livekit_api_python", "livekit_client_js", "postgres", "redis")


# --------------------------------------------------------------------------- манифест
def load() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def save(m: dict) -> None:
    MANIFEST.write_text(json.dumps(m, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def today() -> dt.date:
    return dt.date.fromisoformat(os.environ.get("DEPS_TODAY") or dt.date.today().isoformat())


# --------------------------------------------------------------------------- версии
def nums(v: str | None) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", (v or "").split("@")[0].split("+")[0])[:4])


def level(pinned: str | None, other: str | None) -> str:
    """Насколько `other` новее `pinned`: major | minor | patch | same | older | unknown."""
    if re.fullmatch(r"[0-9a-f]{7,40}", pinned or "") and re.fullmatch(r"[0-9a-f]{7,40}", other or ""):     # коммиты: сравнивается только равенство
        return "same" if pinned == other else "minor"
    a, b = nums(pinned), nums(other)
    if not a or not b:
        return "unknown"
    n = max(len(a), len(b))
    a, b = a + (0,) * (n - len(a)), b + (0,) * (n - len(b))
    if a == b:
        return "same"
    if b < a:
        return "older"
    for i, name in ((0, "major"), (1, "minor")):
        if a[i] != b[i]:
            # для 0.x минорный выпуск считается «ломающим»: как major (семантика нулевых версий)
            return "major" if (name == "minor" and a[0] == 0) else name
    return "patch"


def norm_name(n: str) -> str:
    return re.sub(r"[-_.]+", "-", n).lower()


# --------------------------------------------------------------------------- чтение закреплений из файлов
def _read(rel: str) -> str:
    """Текст файла БЕЗ преобразования переводов строк: в скриптах бывают одиночные CR (tr '\\r'), `apply` обязан сохранять файл байт в байт, кроме заменяемого значения."""
    return (ROOT / rel).read_bytes().decode("utf-8")


def pin_value(loc: dict) -> str | None:
    """Версия, которая сейчас записана в файле проекта по описанию `loc`; None — не найдена."""
    t, rel = loc["type"], loc.get("file", "")
    try:
        text = _read(rel) if rel else ""
    except OSError:
        return None
    if t == "constraints":
        for ln in text.splitlines():
            m = re.match(r"^([A-Za-z0-9_.\-\[\]]+)==([^\s;#]+)", ln.strip())
            if m and norm_name(m.group(1).split("[")[0]) == norm_name(loc["name"]):
                return m.group(2)
        return None
    if t == "npm-lock":
        pk = json.loads(text).get("packages", {}).get("node_modules/" + loc["name"])
        return pk.get("version") if pk else None
    if t == "package-json":
        sect = json.loads(text)
        for k in ("dependencies", "devDependencies"):
            if loc["name"] in sect.get(k, {}):
                return sect[k][loc["name"]]
        return None
    if t in ("env", "dockerfile-arg", "compat-env"):
        key = loc["key"]
        pat = r"^\s*(?:ARG\s+)?#?\s*" + re.escape(key) + r"=(\S+)" if t != "dockerfile-arg" else r"^ARG\s+" + re.escape(key) + r"=(\S+)"
        m = re.search(pat, text, re.M)
        return m.group(1) if m else None
    if t == "image-ref":                     # compose/Dockerfile: строка с образом, группа 1 регулярного выражения
        m = re.search(loc["regex"], text, re.M)
        return m.group(1) if m else None
    if t == "text":
        m = re.search(loc["regex"], text, re.M)
        return m.group(1) if m else None
    return None


def expected_for(c: dict, loc: dict) -> str:
    """Что должно стоять в файле: для образов — тег@digest, для остальных — pinned (с префиксом/суффиксом из описания)."""
    base = loc.get("prefix", "") + c["pinned"] + loc.get("suffix", "")
    if loc.get("with_digest") and c.get("digest"):
        base += "@" + c["digest"]
    return base


# --------------------------------------------------------------------------- сеть: источники
def http_json(url: str, headers: dict | None = None, data: bytes | None = None, timeout: int = 25):
    req = urllib.request.Request(url, headers={**UA, **(headers or {})}, data=data)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r), r.headers


def latest_for(c: dict) -> str | None:
    s = c.get("source") or {}
    t = s.get("type")
    try:
        if t == "pypi":
            return http_json(f"https://pypi.org/pypi/{s['name']}/json")[0]["info"]["version"]
        if t == "npm":
            return http_json(f"https://registry.npmjs.org/{s['name'].replace('/', '%2F')}/latest")[0]["version"]
        if t == "github-release":
            return http_json(f"https://api.github.com/repos/{s['repo']}/releases/latest", _gh())[0]["tag_name"]
        if t == "github-tag":
            tags = http_json(f"https://api.github.com/repos/{s['repo']}/tags?per_page=100", _gh())[0]
            pat = re.compile(s.get("pattern", r"^v?\d+\.\d+\.\d+$"))
            vs = [x["name"] for x in tags if pat.match(x["name"])]
            return max(vs, key=nums) if vs else None
        if t == "github-commit":
            return http_json(f"https://api.github.com/repos/{s['repo']}/commits/{s.get('branch', 'main')}", _gh())[0]["sha"]
        if t == "docker":
            return docker_latest(s)
    except (urllib.error.URLError, OSError, KeyError, ValueError):
        return None
    return None


def _gh() -> dict:
    tok = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    return {"Authorization": f"Bearer {tok}"} if tok else {}


def docker_latest(s: dict) -> str | None:
    """Новейший тег в пределах `track` (регулярное выражение, группа 1 — число для сравнения) для Docker Hub (`hub`) или ghcr.io (`ghcr`)."""
    pat = re.compile(s["track"])
    names: list[str] = []
    if s["registry"] == "hub":
        url = f"https://hub.docker.com/v2/repositories/{s['repo']}/tags?page_size=100&ordering=last_updated"
        for _ in range(int(s.get("pages", 3))):
            j, _h = http_json(url)
            names += [t["name"] for t in j["results"]]
            url = j.get("next")
            if not url:
                break
    else:
        tok = http_json(f"https://ghcr.io/token?scope=repository:{s['repo']}:pull")[0]["token"]
        url = f"https://ghcr.io/v2/{s['repo']}/tags/list?n=1000"
        for _ in range(int(s.get("pages", 40))):
            req = urllib.request.Request(url, headers={**UA, "Authorization": "Bearer " + tok})
            with urllib.request.urlopen(req, timeout=25) as r:
                names += json.load(r).get("tags", [])
                link = r.headers.get("Link", "")
            m = re.search(r"<([^>]+)>;\s*rel=\"next\"", link)
            if not m:
                break
            url = "https://ghcr.io" + m.group(1)
    cand = [n for n in names if pat.match(n)]
    return max((pat.match(n).group(1) for n in cand), key=nums) if cand else None


def registry_digest(c: dict) -> str | None:
    """Digest индекса образа для pinned-тега (Docker Hub / ghcr) — чтобы заметить обновление образа под тем же тегом (исправления безопасности)."""
    s = c.get("image")
    if not s:
        return None
    repo, tag = s["repo"], s.get("prefix", "") + c["pinned"] + s.get("suffix", "")
    acc = "application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.list.v2+json, application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.v2+json"
    try:
        if s["registry"] == "hub":
            tok = http_json(f"https://auth.docker.io/token?service=registry.docker.io&scope=repository:{repo}:pull")[0]["token"]
            base = "https://registry-1.docker.io"
        else:
            tok = http_json(f"https://ghcr.io/token?scope=repository:{repo}:pull")[0]["token"]
            base = "https://ghcr.io"
        req = urllib.request.Request(f"{base}/v2/{repo}/manifests/{tag}", method="HEAD", headers={**UA, "Authorization": "Bearer " + tok, "Accept": acc})
        with urllib.request.urlopen(req, timeout=25) as r:
            return r.headers.get("Docker-Content-Digest")
    except (urllib.error.URLError, OSError, KeyError, ValueError):
        return None


def osv_query(items: list[tuple[str, str, str]]) -> dict[tuple[str, str], list[str]]:
    """(экосистема, имя, версия) → список идентификаторов уязвимостей OSV (osv.dev). Недоступность сети — пустой результат (проверяется отдельно)."""
    out: dict[tuple[str, str], list[str]] = {}
    for i in range(0, len(items), 100):
        chunk = items[i:i + 100]
        body = json.dumps({"queries": [{"package": {"ecosystem": e, "name": n}, "version": v} for e, n, v in chunk]}).encode()
        j, _ = http_json("https://api.osv.dev/v1/querybatch", {"Content-Type": "application/json"}, body, timeout=60)
        for (e, n, v), res in zip(chunk, j["results"]):
            ids = [x["id"] for x in res.get("vulns", [])]
            if ids:
                out[(n, v)] = ids
    return out


def security_targets(m: dict) -> list[tuple[str, str, str]]:
    """Что отправлять в OSV: прямые зависимости из манифеста и ВСЕ закреплённые пакеты constraints/npm-lock (включая транзитивные)."""
    seen: dict[tuple[str, str, str], None] = {}
    for rel in ("backend/constraints.txt", "asr-service/constraints.txt"):
        try:
            for ln in _read(rel).splitlines():
                mm = re.match(r"^([A-Za-z0-9_.\-]+)==([^\s;#]+)", ln.strip())
                if mm:
                    seen[("PyPI", norm_name(mm.group(1)), mm.group(2))] = None
        except OSError:
            pass
    try:
        for k, v in json.loads(_read("frontend/package-lock.json")).get("packages", {}).items():
            if k.startswith("node_modules/") and v.get("version") and not v.get("dev"):
                seen[("npm", k.split("node_modules/")[-1], v["version"])] = None
    except OSError:
        pass
    return list(seen)


# --------------------------------------------------------------------------- проверки
def problems(m: dict, *, online: bool, max_age: int | None) -> list[str]:
    out: list[str] = []
    keys = [c["key"] for c in m["components"]]
    if len(keys) != len(set(keys)):
        out.append("в манифесте повторяются ключи компонентов")
    rev = dt.date.fromisoformat(m["reviewed_at"])
    if max_age is not None and (today() - rev).days > max_age:
        out.append(f"ревизия версий устарела: {m['reviewed_at']} (допустимо не старше {max_age} дн.); выполните `scripts/deps.py review` и оформите решения")
    for c in m["components"]:
        k = c["key"]
        for need in ("title", "group", "pinned", "tested", "reviewed_latest", "locations", "source"):
            if need not in c:
                out.append(f"{k}: нет поля «{need}»")
        if c.get("tested") != c.get("pinned"):
            out.append(f"{k}: закреплено {c.get('pinned')}, а проверено {c.get('tested')} — в релиз нельзя включать непроверенную версию")
        for loc in c.get("locations", []):
            if loc["type"] == "image-ref" and c.get("source", {}).get("type") == "docker" and not c.get("digest") and loc.get("with_digest"):
                out.append(f"{k}: у образа нет digest в манифесте")
            have, want = pin_value(loc), expected_for(c, loc)
            if loc["type"] == "package-json":
                ok = have is not None and have.lstrip("^~") == want
            else:
                ok = have == want
            if not ok:
                out.append(f"{k}: в {loc.get('file')}{(':' + loc.get('key', loc.get('name', ''))) if loc.get('key') or loc.get('name') else ''} стоит «{have}», по манифесту должно быть «{want}»")
        lv = level(c["pinned"], c["reviewed_latest"])
        d = c.get("deferred")
        if lv in ("same", "older"):
            if d:
                out.append(f"{k}: актуальная версия закреплена, а запись об отсрочке осталась — удалите её")
        elif lv != "unknown":
            if not d:
                out.append(f"{k}: доступна {c['reviewed_latest']} (закреплено {c['pinned']}), решения нет — обновите или оформите отсрочку (причина, задача, дата повторной проверки)")
            else:
                if d.get("version") != c["reviewed_latest"]:
                    out.append(f"{k}: отсрочка относится к {d.get('version')}, а новейшая теперь {c['reviewed_latest']} — пересмотрите решение")
                for f in ("reason", "issue", "recheck"):
                    if not str(d.get(f) or "").strip():
                        out.append(f"{k}: в отсрочке нет поля «{f}»")
                iss = str(d.get("issue") or "")
                if iss and not iss.startswith("https://"):
                    if not re.fullmatch(r"DEP-\d+", iss):
                        out.append(f"{k}: задача отсрочки «{iss}» — укажите номер DEP-N из docs/DEPENDENCIES.md или ссылку https://")
                    else:
                        try:
                            if f"### {iss}" not in _read("docs/DEPENDENCIES.md"):
                                out.append(f"{k}: задача {iss} не описана в docs/DEPENDENCIES.md (раздел «### {iss}»)")
                        except OSError:
                            out.append("нет docs/DEPENDENCIES.md с описанием задач отложенных обновлений")
                try:
                    rc = dt.date.fromisoformat(d.get("recheck", ""))
                    if rc < today():
                        out.append(f"{k}: срок повторной проверки отсрочки истёк ({d['recheck']})")
                    elif (rc - rev).days > 120:
                        out.append(f"{k}: повторная проверка через {(rc - rev).days} дн. — слишком далеко (не больше 120)")
                except ValueError:
                    out.append(f"{k}: дата повторной проверки в отсрочке записана неверно")
                if re.search(r"(?i)дожд(и|ё)тесь\s+(новой|следующ)", d.get("reason", "")) and not d.get("plan"):
                    out.append(f"{k}: «дождитесь следующего релиза» без плана — укажите, что и когда будет сделано (поле plan)")
    out += constraints_problems(m)
    if online:
        out += online_problems(m)
    return out


def constraints_problems(m: dict) -> list[str]:
    out: list[str] = []
    for svc in ("backend", "asr-service"):
        try:
            req, con = _read(f"{svc}/requirements.txt"), _read(f"{svc}/constraints.txt")
        except OSError:
            out.append(f"{svc}: нет requirements.txt или constraints.txt")
            continue
        pinned = {norm_name(re.match(r"^([A-Za-z0-9_.\-]+)==", ln.strip()).group(1)) for ln in con.splitlines() if re.match(r"^[A-Za-z0-9_.\-]+==", ln.strip())}
        for ln in req.splitlines():
            ln = ln.split("#")[0].strip()
            mm = re.match(r"^([A-Za-z0-9_.\-]+)", ln)
            if mm and norm_name(mm.group(1)) not in pinned and norm_name(mm.group(1)) not in ("torch", "torchaudio"):
                out.append(f"{svc}/constraints.txt: нет точной версии для {mm.group(1)} (выполните `scripts/deps.py freeze`)")
        for df in (f"{svc}/Dockerfile",):
            if "-c constraints.txt" not in _read(df):
                out.append(f"{df}: pip install должен использовать -c constraints.txt (воспроизводимая сборка)")
    return out


def online_problems(m: dict) -> list[str]:
    out: list[str] = []
    for c in m["components"]:
        if (c.get("source") or {}).get("type") == "none":
            continue                                   # версия определяется образом/внешним сервисом: автоматической проверки нет (в интерфейсе — «Не отслеживается»)
        lat = latest_for(c)
        if lat is None:
            out.append(f"{c['key']}: не удалось узнать последнюю версию (сеть/источник) — проверка не может считаться пройденной")
            continue
        if level(c["reviewed_latest"], lat) in ("patch", "minor", "major"):
            out.append(f"{c['key']}: вышла {lat} (на дату ревизии была {c['reviewed_latest']}) — выполните `scripts/deps.py review` и примите решение")
        dg = registry_digest(c) if c.get("digest") else None
        if dg and dg != c["digest"]:
            out.append(f"{c['key']}: образ {c['pinned']} обновлён в реестре (digest {dg[:19]}…, в проекте {c['digest'][:19]}…) — вероятно, исправления безопасности; обновите digest")
    ack = {a["id"] for a in m.get("security_ack", [])}
    try:
        found = osv_query(security_targets(m))
    except (urllib.error.URLError, OSError, ValueError, KeyError):
        return out + ["не удалось обратиться к базе уязвимостей OSV — проверка безопасности не выполнена"]
    for (n, v), ids in sorted(found.items()):
        new = [i for i in ids if i not in ack]
        if new:
            out.append(f"уязвимости в закреплённых {n}=={v}: {', '.join(new[:4])} — обновите пакет или подтвердите в security_ack с причиной")
    return out


# --------------------------------------------------------------------------- команды
def cmd_verify(a) -> int:
    m = load()
    pr = problems(m, online=a.online, max_age=a.max_age)
    if a.release and m.get("release") != a.release:
        pr.append(f"манифест относится к релизу {m.get('release')}, а выпускается {a.release}: выполните `scripts/deps.py review --release {a.release}`")
    for p in pr:
        print("✗", p)
    print(f"{'ПРОБЛЕМЫ' if pr else 'OK'}: компонентов {len(m['components'])}, ревизия {m['reviewed_at']}, релиз {m['release']}" + (f"; проблем: {len(pr)}" if pr else ""))
    return 1 if pr else 0


def cmd_check(a) -> int:
    m = load()
    rows = []
    for c in m["components"]:
        lat = latest_for(c)
        lv = level(c["pinned"], lat) if lat else "unknown"
        d = c.get("deferred")
        verdict = ("актуально" if lv in ("same", "older") else "нет данных" if lv == "unknown" else
                   f"отложено до {d['recheck']}" if d and d.get("version") == lat else f"НОВАЯ ({lv}): нужно решение")
        rows.append({"key": c["key"], "pinned": c["pinned"], "tested": c["tested"], "latest": lat, "level": lv, "verdict": verdict})
    if a.json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
    else:
        print(f"{'компонент':28} {'закреплено':16} {'проверено':16} {'доступно':16} решение")
        for r in rows:
            print(f"{r['key']:28} {str(r['pinned'])[:16]:16} {str(r['tested'])[:16]:16} {str(r['latest'])[:16]:16} {r['verdict']}")
    return 0


def cmd_review(a) -> int:
    m = load()
    unresolved = 0
    for c in m["components"]:
        lat = latest_for(c)
        if lat is None:
            print(f"? {c['key']}: версию узнать не удалось, значение не изменено")
            continue
        c["reviewed_latest"] = lat
        lv = level(c["pinned"], lat)
        d = c.get("deferred")
        if lv in ("same", "older"):
            c.pop("deferred", None)
        elif not d or d.get("version") != lat:
            unresolved += 1
            print(f"! {c['key']}: {c['pinned']} → {lat} ({lv}) — нужно решение: обновить (и проверить) или оформить `deferred`")
        if c.get("digest"):
            dg = registry_digest(c)
            if dg and dg != c["digest"]:
                print(f"! {c['key']}: digest образа {c['pinned']} изменился ({dg}); обновите вместе с проверкой")
    m["reviewed_at"] = today().isoformat()
    if a.release:
        m["release"] = a.release
    try:
        found = osv_query(security_targets(m))
        m["security"] = {"checked_at": today().isoformat(), "source": "osv.dev", "advisories": {f"{n}=={v}": ids for (n, v), ids in sorted(found.items())}}
        print(f"безопасность: уязвимых закреплённых пакетов — {len(found)}")
    except (urllib.error.URLError, OSError, ValueError, KeyError):
        print("? OSV недоступна — раздел security не обновлён")
    save(m)
    print(f"ревизия записана ({m['reviewed_at']}); без решения: {unresolved}")
    return 0


_OLD_FREEZE_FLAGS = ["--python-version", "3.12", "--implementation", "cp", "--abi", "cp312", "--platform", "manylinux2014_x86_64", "--platform", "manylinux_2_28_x86_64",
                "--platform", "manylinux_2_17_x86_64", "--platform", "any", "--only-binary=:all:"]


def cmd_freeze(a) -> int:
    """Разрешает зависимости так, как их разрешит сборка образа: внутри образа python:3.12-slim (тот же digest, что в Dockerfile), с git для GigaAM и индексом PyTorch CPU."""
    import tempfile
    m = load()
    py = next(c for c in m["components"] if c["key"] == "python_backend")
    image = f"python:{py['pinned']}-slim@{py['digest']}"
    for svc in ("backend", "asr-service"):
        with tempfile.TemporaryDirectory() as td:
            extra = "--extra-index-url https://download.pytorch.org/whl/cpu" if svc == "asr-service" else ""     # torch подтягивается silero-vad: берём CPU-сборку, как в образе
            if svc == "asr-service":      # GigaAM — из git по коммиту из .env.example, С зависимостями (onnx, hydra…): они должны попасть в constraints
                sha = re.search(r"^GIGAAM_GIT_COMMIT=(\w+)", (ROOT / ".env.example").read_text(encoding="utf-8"), re.M).group(1)
                (Path(td) / "extra.txt").write_text(f"gigaam @ git+https://github.com/salute-developers/GigaAM.git@{sha}\n", encoding="utf-8")
            else:
                (Path(td) / "extra.txt").write_text("", encoding="utf-8")
            script = ("apt-get -qq update >/dev/null && apt-get -qq install -y --no-install-recommends git ca-certificates >/dev/null 2>&1; "
                      f"pip install -q --dry-run --ignore-installed --report /out/r.json -r /src/{svc}/requirements.txt -r /out/extra.txt {extra} 2>&1 | tail -15")
            os.chmod(td, 0o777)
            r = subprocess.run(["docker", "run", "--rm", "-v", f"{ROOT}:/src:ro", "-v", f"{td}:/out", image, "sh", "-c", script], capture_output=True, text=True)
            rep = Path(td) / "r.json"
            if r.returncode != 0 or not rep.exists():
                print(r.stdout[-2500:], r.stderr[-1500:])
                print(f"✗ {svc}: не удалось разрешить зависимости в контейнере {image}")
                return 1
            pk = sorted({(norm_name(i["metadata"]["name"]), i["metadata"]["version"]) for i in json.loads(rep.read_text())["install"]})
        skip = {"torch", "torchaudio"} if svc == "asr-service" else set()
        body = [f"{n}=={v}" for n, v in pk if n not in skip]
        head = (f"# Точные версии ВСЕХ зависимостей {svc} (в том числе транзитивных) — воспроизводимая сборка образа. Формируется командой `scripts/deps.py freeze` при ревизии релиза;\n"
                f"# руками не правится. Платформа: Python 3.12 (образ {image.split('@')[0]}), linux x86_64. Диапазоны в requirements.txt — допустимые границы; сборка использует именно эти версии (pip install -c).\n")
        if skip:
            head += "# torch и torchaudio ставятся отдельно из индекса PyTorch (CPU/CUDA): их версии закреплены в Dockerfile (TORCH_VERSION, TORCHAUDIO_VERSION); gigaam ставится из git по коммиту (его зависимости, включая onnx 1.19.x, — в этом файле).\n"
        (ROOT / svc / "constraints.txt").write_text(head + "\n".join(body) + "\n", encoding="utf-8")
        print(f"✓ {svc}/constraints.txt: {len(body)} пакетов")
    return 0


def cmd_pin_images(a) -> int:
    """Записывает в манифест digest (индекс манифестов) образа для закреплённого тега. Файлы проекта обновляет `apply`."""
    m = load()
    for c in m["components"]:
        if c.get("image"):
            dg = registry_digest(c)
            if not dg:
                print(f"? {c['key']}: digest получить не удалось")
                continue
            if c.get("digest") != dg:
                print(f"{c['key']}: {c.get('digest', '—')[:19]} → {dg[:19]}")
            c["digest"] = dg
    save(m)
    return 0


def _replace_group(text: str, regex: str, value: str) -> tuple[str, bool]:
    m = re.search(regex, text, re.M)
    if not m:
        return text, False
    return text[:m.start(1)] + value + text[m.end(1):], True


def cmd_apply(a) -> int:
    """Записывает версии из манифеста в файлы проекта (env, ARG Dockerfile, ссылки на образы, compat.env). constraints и package-lock создают freeze и npm."""
    m = load()
    bad = 0
    for c in m["components"]:
        for loc in c["locations"]:
            t = loc["type"]
            if t not in ("env", "dockerfile-arg", "compat-env", "image-ref", "text"):
                continue
            rel = loc["file"]
            text = _read(rel)
            if t in ("env", "compat-env"):
                rx = r"^\s*(?:ARG\s+)?#?\s*" + re.escape(loc["key"]) + r"=(\S+)"
            elif t == "dockerfile-arg":
                rx = r"^ARG\s+" + re.escape(loc["key"]) + r"=(\S+)"
            else:
                rx = loc["regex"]
            new, ok = _replace_group(text, rx, expected_for(c, loc))
            if not ok:
                print(f"✗ {c['key']}: в {rel} не найдено место для записи ({t})")
                bad += 1
            elif new != text:
                (ROOT / rel).write_bytes(new.encode("utf-8"))
                print(f"✓ {c['key']}: {rel} обновлён")
    return 1 if bad else 0


def cmd_sync(a) -> int:
    """Берёт закреплённые версии из файлов, которые создают инструменты (constraints, package-lock), и записывает их в манифест как pinned/tested."""
    m = load()
    for c in m["components"]:
        for loc in c["locations"]:
            if loc["type"] in ("constraints", "npm-lock"):
                v = pin_value(loc)
                if v and v != c["pinned"]:
                    print(f"{c['key']}: {c['pinned']} → {v}")
                    c["pinned"] = c["tested"] = v
                break
    save(m)
    return 0


def cmd_pins(a) -> int:
    m = load()
    for c in m["components"]:
        print(f"{c['key']} = {c['pinned']}")
        for loc in c["locations"]:
            print(f"    {loc['type']:14} {loc.get('file', '')} {loc.get('key', loc.get('name', loc.get('regex', '')))}")
    return 0


def _manifest_at(ref: str) -> dict | None:
    r = subprocess.run(["git", "-C", str(ROOT), "show", f"{ref}:backend/app/components.json"], capture_output=True, text=True)
    return json.loads(r.stdout) if r.returncode == 0 else None


def cmd_report(a) -> int:
    ref = a.frm
    if not ref:
        r = subprocess.run(["git", "-C", str(ROOT), "describe", "--tags", "--abbrev=0"], capture_output=True, text=True)
        ref = r.stdout.strip() or "HEAD~1"
    old, new = _manifest_at(ref), load()
    olds = {c["key"]: c for c in (old or {}).get("components", [])}
    print(f"### Версии компонентов: {ref} → {new['release']} (ревизия {new['reviewed_at']})\n")
    print("| Компонент | Было | Стало | Доступно | Решение |\n|---|---|---|---|---|")
    for c in new["components"]:
        o = olds.get(c["key"])
        was = o["pinned"] if o else "—"
        d = c.get("deferred")
        dec = f"отложено до {d['recheck']}: {d['reason']}" if d else ("обновлено" if o and o["pinned"] != c["pinned"] else "актуально")
        print(f"| {c['title']} | {was} | {c['pinned']} | {c['reviewed_latest']} | {dec} |")
    return 0


# --------------------------------------------------------------------------- после обновления на сервере
def _run(cmd: list[str], timeout: int = 60) -> tuple[int, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)


PY_PROBE = ("import json,importlib.metadata as m,sys\n"
            "n=json.loads(sys.argv[1]); o={}\n"
            "for k in n:\n"
            "  try: o[k]=m.version(k)\n"
            "  except Exception: o[k]=None\n"
            "o['python']='.'.join(map(str,sys.version_info[:3])); print(json.dumps(o))")


def _compose(env_file: str | None) -> list[str]:
    explicit = os.environ.get("DEPS_COMPOSE_JSON")        # update.sh передаёт точную команду compose (проект, файлы, профили) списком — пути могут содержать пробелы
    if explicit:
        return json.loads(explicit)
    base = ["docker", "compose", "-f", str(ROOT / "deployment" / "compose.yml")]
    return base + (["--env-file", env_file] if env_file else [])


def cmd_postcheck(a) -> int:
    """Версии работающих контейнеров против манифеста: что запущено, а не что записано в исходниках. Расхождения — в отчёт, критические — код 2."""
    m = load()
    comp = _compose(a.env)
    found: dict[str, str | None] = {}
    notes: list[str] = []
    svc_of = {"backend": [], "asr": []}
    for c in m["components"]:
        rt = c.get("runtime") or {}
        if rt.get("service") in svc_of and rt.get("python_package"):
            svc_of[rt["service"]].append(c)
    for svc, comps in svc_of.items():
        names = json.dumps([c["runtime"]["python_package"] for c in comps])
        rc, out = _run(comp + ["exec", "-T", svc, "python", "-c", PY_PROBE, names])
        if rc != 0:
            notes.append(f"{svc}: контейнер не ответил ({out.strip()[:120]})")
            for c in comps:
                found[c["key"]] = None
            continue
        try:
            data = json.loads(out.strip().splitlines()[-1])
        except (ValueError, IndexError):
            notes.append(f"{svc}: непонятный ответ")
            continue
        for c in comps:
            found[c["key"]] = data.get(c["runtime"]["python_package"])
        for c in m["components"]:
            if c["key"] == f"python_{svc}":
                found[c["key"]] = data.get("python")
    for c in m["components"]:
        rt = c.get("runtime") or {}
        if rt.get("how") == "exec-version":
            rc, out = _run(comp + ["exec", "-T", rt["service"], *rt["cmd"]])
            mm = re.search(rt.get("regex", r"(\d+\.\d+(?:\.\d+)?)"), out)
            found[c["key"]] = mm.group(1) if rc == 0 and mm else None
        elif rt.get("how") == "web-version-json":
            rc, out = _run(comp + ["exec", "-T", "web", "wget", "-qO-", "http://127.0.0.1:8080/version.json"])
            try:
                found[c["key"]] = json.loads(out).get("components", {}).get(rt["name"])
            except ValueError:
                found[c["key"]] = None
    rep = []
    worst = 0
    for c in m["components"]:
        rt = c.get("runtime") or {}
        if not rt:
            continue
        got = found.get(c["key"])
        want = c["pinned"].lstrip("v")
        ok = got is not None and (got.lstrip("v") == want or (rt.get("prefix_match") and got.lstrip("v").startswith(want)))
        crit = c["key"] in CRITICAL_DEFAULT or rt.get("critical")
        status = "ok" if ok else ("unknown" if got is None else "mismatch")
        if not ok:
            worst = max(worst, 2 if crit and status == "mismatch" else 1)
        rep.append({"key": c["key"], "title": c["title"], "expected": c["pinned"], "running": got, "status": status, "critical": bool(crit)})
    # образы без пересборки (postgres, redis): digest РАБОТАЮЩЕГО контейнера должен быть тем, что закреплён в релизе
    for c in m["components"]:
        svc = (c.get("runtime") or {}).get("digest_service")
        if not (svc and c.get("digest")):
            continue
        rc, cid = _run(comp + ["ps", "-q", svc])
        cid = cid.strip().splitlines()[0] if rc == 0 and cid.strip() else ""
        rc2, out = _run(["docker", "inspect", "--format", "{{.Image}}", cid]) if cid else (1, "")
        rc3, dg = _run(["docker", "image", "inspect", "--format", "{{json .RepoDigests}}", out.strip()]) if rc2 == 0 else (1, "")
        ok = rc3 == 0 and c["digest"] in dg
        rep.append({"key": c["key"] + "_digest", "title": c["title"] + " (образ по digest)", "expected": c["digest"][:19], "running": (lambda mm: mm.group(0) if mm else None)(re.search(r"sha256:[0-9a-f]{12}", dg)) if rc3 == 0 else None,
                    "status": "ok" if ok else ("unknown" if rc3 != 0 else "mismatch"), "critical": False})
        if not ok:
            worst = max(worst, 1)
    rc, out = _run(comp + ["exec", "-T", "asr", "python", "-m", "app.selfcheck"])      # pip check и реальная инициализация Silero VAD в РАБОТАЮЩЕМ контейнере (регрессия 0.24.1)
    if rc != 0:
        notes.append(f"ASR: самопроверка конвейера не пройдена — {out.strip().splitlines()[-1][:300] if out.strip() else 'контейнер не ответил'}")
        worst = 2
    data = {"release": m["release"], "checked_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "worst": worst, "components": rep, "notes": notes}
    if a.json:
        print(json.dumps(data, ensure_ascii=False))
    else:
        print(f"Сверка работающих компонентов с релизом {m['release']}:")
        for r in rep:
            sign = {"ok": "✓", "mismatch": "✗", "unknown": "?"}[r["status"]]
            print(f"  {sign} {r['title']}: работает {r['running']}, по релизу {r['expected']}" + (" [критично]" if r["critical"] and r["status"] != "ok" else ""))
        for n in notes:
            print("  !", n)
        print({0: "Комплект соответствует релизу.", 1: "Есть расхождения (не критичные).", 2: "КРИТИЧЕСКИЕ расхождения: работает не тот комплект, что в релизе."}[worst])
    if a.out:
        try:
            Path(a.out).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
    return worst


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("verify"); v.add_argument("--online", action="store_true"); v.add_argument("--max-age", type=int, default=None); v.add_argument("--release"); v.set_defaults(f=cmd_verify)
    c = sub.add_parser("check"); c.add_argument("--json", action="store_true"); c.set_defaults(f=cmd_check)
    rv = sub.add_parser("review"); rv.add_argument("--release"); rv.set_defaults(f=cmd_review)
    sub.add_parser("freeze").set_defaults(f=cmd_freeze)
    sub.add_parser("pins").set_defaults(f=cmd_pins)
    sub.add_parser("pin-images").set_defaults(f=cmd_pin_images)
    sub.add_parser("apply").set_defaults(f=cmd_apply)
    sub.add_parser("sync").set_defaults(f=cmd_sync)
    r = sub.add_parser("report"); r.add_argument("--from", dest="frm"); r.set_defaults(f=cmd_report)
    p = sub.add_parser("postcheck"); p.add_argument("--env"); p.add_argument("--json", action="store_true"); p.add_argument("--out"); p.set_defaults(f=cmd_postcheck)
    a = ap.parse_args()
    return a.f(a)


if __name__ == "__main__":
    sys.exit(main())
