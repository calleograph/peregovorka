#!/usr/bin/env python3
"""Проверка ссылок загрузки клиентов в подвале сайта до публикации выпуска сервера.

    python3 scripts/check_download_links.py [--repo OWNER/NAME]

Читает версии из frontend/src/desktopDist.ts и frontend/src/androidDist.ts и через GitHub API проверяет:
  * выпуск desktop-vX.Y.Z опубликован (не черновик) и содержит Peregovorka-Setup-X.Y.Z.exe ненулевого размера;
  * выпуск android-vX.Y.Z опубликован и содержит хотя бы один файл .apk ненулевого размера.
Иначе ссылка в подвале выглядела бы рабочей, а вела бы на отсутствующий файл.

Коды возврата: 0 — всё на месте; 1 — выпуск или файл отсутствует (публиковать сервер нельзя); 2 — GitHub недоступен или
ограничил запросы (проверка не выполнена, повторите позже). Переменная GITHUB_TOKEN (если задана) снимает ограничение частоты.
Только чтение; только стандартная библиотека.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
API = "https://api.github.com"


class Unavailable(Exception):
    """GitHub не ответил по существу: сеть, 5xx, ограничение частоты."""


def read_const(path: Path, name: str) -> str:
    m = re.search(rf'export const {name} = "([0-9]+\.[0-9]+\.[0-9]+)";', path.read_text(encoding="utf-8"))
    if not m:
        raise SystemExit(f"в {path.relative_to(ROOT)} нет строки {name} = \"X.Y.Z\"")
    return m.group(1)


def get_release(repo: str, tag: str, opener=urllib.request.urlopen, attempts: int = 3, pause: float = 2.0) -> dict | None:
    """Выпуск по тегу; None — выпуска нет (404)."""
    req = urllib.request.Request(f"{API}/repos/{repo}/releases/tags/{tag}", headers={"Accept": "application/vnd.github+json", "User-Agent": "peregovorka-release-check"})
    if os.environ.get("GITHUB_TOKEN"):
        req.add_header("Authorization", f"Bearer {os.environ['GITHUB_TOKEN']}")
    last = ""
    for i in range(attempts):
        try:
            with opener(req, timeout=20) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if e.code == 403 and e.headers.get("X-RateLimit-Remaining") == "0":
                raise Unavailable("GitHub ограничил частоту запросов (задайте GITHUB_TOKEN)") from e
            last = f"HTTP {e.code}"
            if e.code < 500 and e.code != 429:
                raise Unavailable(f"GitHub ответил {last}") from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last = type(e).__name__
        if i + 1 < attempts:
            time.sleep(pause * (i + 1))
    raise Unavailable(f"GitHub недоступен ({last})")


def check(repo: str, desktop: str, android: str, opener=urllib.request.urlopen, pause: float = 2.0) -> list[str]:
    """Список проблем (пустой — всё на месте)."""
    problems: list[str] = []
    for tag, want in ((f"desktop-v{desktop}", lambda n: n == f"Peregovorka-Setup-{desktop}.exe"), (f"android-v{android}", lambda n: n.lower().endswith(".apk"))):
        rel = get_release(repo, tag, opener, pause=pause)
        if rel is None:
            problems.append(f"{tag}: выпуска нет на GitHub")
            continue
        if rel.get("draft"):
            problems.append(f"{tag}: выпуск — черновик, ссылка для пользователей не откроется")
        files = [a for a in rel.get("assets", []) if want(a.get("name", ""))]
        if not files:
            problems.append(f"{tag}: нет нужного файла (есть: {', '.join(a.get('name', '?') for a in rel.get('assets', [])) or 'ничего'})")
        elif not any(a.get("size", 0) > 0 and a.get("state", "uploaded") == "uploaded" for a in files):
            problems.append(f"{tag}: файл {files[0].get('name')} пустой или не догружен")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", default="calleograph/peregovorka")
    a = ap.parse_args()
    desktop = read_const(ROOT / "frontend/src/desktopDist.ts", "DESKTOP_VERSION")
    android = read_const(ROOT / "frontend/src/androidDist.ts", "ANDROID_VERSION")
    try:
        problems = check(a.repo, desktop, android)
    except Unavailable as e:
        print(f"проверка ссылок загрузки не выполнена: {e}", file=sys.stderr)
        return 2
    if problems:
        for p in problems:
            print(f"ОШИБКА: {p}", file=sys.stderr)
        print("Ссылка в подвале сайта вела бы на отсутствующий файл: опубликуйте выпуск клиента или верните прежнюю версию в frontend/src/*Dist.ts.", file=sys.stderr)
        return 1
    print(f"ссылки загрузки в порядке: Windows {desktop}, Android {android}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
