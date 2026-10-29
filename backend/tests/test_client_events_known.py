"""Каждое событие, которое отправляет интерфейс (reportEvent), сервер принимает: иначе POST /client/events отвечает 422,
а интерфейс молча теряет событие (так в 0.49.9 пропадали mic_lost/mic_recovered и noise_suppression_fallback)."""
import re
from pathlib import Path

import pytest

from app.api.client import EVENTS

SRC = Path(__file__).resolve().parents[2] / "frontend" / "src"


def frontend_events() -> set[str]:
    names: set[str] = set()
    for p in SRC.rglob("*.ts*"):
        if ".test." in p.name:
            continue
        s = p.read_text(encoding="utf-8")
        for m in re.finditer(r"reportEvent\(([^,]*?)(?:,|\))", s):
            arg = re.sub(r'===?\s*"[^"]*"', "", m.group(1))          # сравнения в тернарном выборе имени — не события
            names |= set(re.findall(r'"([a-z][a-z0-9_]+)"', arg))
        names |= set(re.findall(r'\bfail\("[a-z]+", "[a-z]+", "([a-z_]+)"', s))
    return names


@pytest.mark.skipif(not SRC.is_dir(), reason="исходники frontend недоступны")
def test_every_frontend_event_is_accepted_by_server():
    found = frontend_events()
    assert len(found) > 30, "разбор исходников сломался — слишком мало событий"
    assert found - EVENTS == set(), f"сервер не примет события: {sorted(found - EVENTS)}"


def test_microphone_by_client_compares_edge_and_windows_app(client):
    """Сравнение сбоев микрофона по клиентам из журнала событий: строки Edge и приложения Windows раздельно, версия браузера не дробит строки."""
    from .conftest import login

    edge = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0 Safari/537.36 Edg/148.0"
    edge2 = edge.replace("Edg/148.0", "Edg/149.0")
    app_ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0 Safari/537.36 PeregovorkaDesktop/1.3.1"
    login(client, "alice")
    send = lambda ua, ev: client.post("/api/v1/client/events", json={"event": ev}, headers={"User-Agent": ua})  # noqa: E731
    for ua, ev in [(edge, "join_attempt"), (edge2, "join_attempt"), (edge, "join_attempt"), (edge, "mic_busy"),
                   (app_ua, "join_attempt"), (app_ua, "join_attempt"), (app_ua, "mic_lost"), (app_ua, "mic_recovered"), (app_ua, "mic_busy")]:
        assert send(ua, ev).status_code == 204, ev
    client.portal.call(client.app_obj.state.journal.flush)
    assert client.get("/api/v1/admin/client-diagnostics/microphone").status_code == 403
    login(client, "root")
    d = client.get("/api/v1/admin/client-diagnostics/microphone?days=7").json()
    rows = {r["client"]: r for r in d["rows"]}
    e, a = rows["Edge · Windows 10/11"], rows["Peregovorka для Windows 1.3.1 · Windows 10/11"]
    assert (e["joins"], e["mic_busy"], e["problems_per_100_joins"]) == (3, 1, 33.3)
    assert (a["joins"], a["mic_busy"], a["mic_lost"], a["mic_recovered"], a["problems_per_100_joins"]) == (2, 1, 1, 1, 100.0)
