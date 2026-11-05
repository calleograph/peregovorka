#!/usr/bin/env python3
"""Ждать результат CI коммита на GitHub: 0 — зелёный, 1 — красный или нет результата. Использует release.sh push: тег уходит только после зелёного CI.
Серверы обновляются по тегу, поэтому проверка после отправки тега уже ничего бы не защитила."""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
import urllib.request

WORKFLOW = "CI"
BAD = {"failure", "cancelled", "timed_out", "startup_failure", "action_required"}


def verdict(runs: list[dict]) -> str | None:
    """'green', 'red' или None — ещё идёт. Перезапуск обновляет ту же запись, поэтому повторный прогон меняет ответ."""
    runs = [r for r in runs if r.get("name") == WORKFLOW]
    if not runs:
        return None
    if any(r.get("status") != "completed" for r in runs):
        return None
    return "red" if any(r.get("conclusion") in BAD for r in runs) else "green"


def token() -> str:
    try:
        out = subprocess.run(["git", "credential", "fill"], input="protocol=https\nhost=github.com\n\n", capture_output=True, text=True, timeout=20).stdout
        return dict(ln.split("=", 1) for ln in out.splitlines() if "=" in ln).get("password", "")
    except (OSError, subprocess.SubprocessError):
        return ""


def fetch(repo: str, sha: str, tok: str) -> list[dict]:
    req = urllib.request.Request(f"https://api.github.com/repos/{repo}/actions/runs?head_sha={sha}&per_page=50", headers={"Accept": "application/vnd.github+json", **({"Authorization": f"Bearer {tok}"} if tok else {})})
    with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310
        return json.load(r).get("workflow_runs", [])


def main(argv: list[str]) -> int:
    sha, origin = argv[1], argv[2]
    limit = float(argv[3]) if len(argv) > 3 else 5400
    m = re.search(r"github\.com[:/](.+?/.+?)(?:\.git)?$", origin)
    if not m:
        print(f"Не удалось определить репозиторий GitHub по адресу {origin}", file=sys.stderr)
        return 1
    tok, end = token(), time.time() + limit
    while time.time() < end:
        try:
            v = verdict(fetch(m.group(1), sha, tok))
        except OSError as exc:
            print(f"GitHub не ответил: {exc}", flush=True)
            v = None
        if v == "green":
            return 0
        if v == "red":
            print(f"CI коммита {sha[:12]} красный", file=sys.stderr)
            return 1
        time.sleep(30)
    print(f"CI коммита {sha[:12]} не закончился за отведённое время", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
