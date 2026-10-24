"""Канал диктовки `/ws/dictation`: звук уходит в ASR через Redis, фрагменты возвращаются клиенту; ASR недоступен или выключен — понятная ошибка;
предел одновременных диктовок; после завершения звук в Redis не остаётся. ASR подменён: сценарий читает команду и звук из Redis и отвечает."""
from __future__ import annotations

import asyncio
import base64
import json

from .conftest import login

API = "/api/v1"


def _asr_alive(client, alive=True):
    r = client.app_obj.state.redis

    async def go():
        if alive:
            await r.set("asr:heartbeat", json.dumps({"model_loaded": True}), ex=30)
        else:
            await r.delete("asr:heartbeat")
    client.portal.call(go)


def _fake_asr(client, texts: list[str]):
    """Как служба распознавания: команда start → звук во входном потоке → фрагменты и done в выходном."""
    r = client.app_obj.state.redis

    async def go():
        for _ in range(100):
            items = await r.xrange("asr:dictation:control")
            if items:
                break
            await asyncio.sleep(0.02)
        sid = items[-1][1]["sid"]
        for _ in range(100):
            got = await r.xrange(f"asr:dict:{sid}:in")
            if got:
                break
            await asyncio.sleep(0.02)
        pcm = base64.b64decode(got[0][1]["pcm"])
        for i, t in enumerate(texts, 1):
            await r.xadd(f"asr:dict:{sid}:out", {"type": "final", "seq": str(i), "text": t})
        await r.xadd(f"asr:dict:{sid}:out", {"type": "done", "segments": str(len(texts)), "busy": "0", "timeout": "0"})
        return sid, len(pcm)
    return client.portal.call(go)


def test_dictation_roundtrip_and_cleanup(client):
    login(client, "alice")
    _asr_alive(client)
    with client.websocket_connect(f"{API}/ws/dictation") as ws:
        ready = ws.receive_json()
        assert ready["type"] == "ready" and ready["max_seconds"] == 120
        ws.send_bytes(b"\x10\x00" * 1600)                       # 100 мс тишины-образца PCM int16
        sid, n = _fake_asr(client, ["Добрый день", "отчёт готов"])
        assert n == 3200, "звук дошёл до ASR без искажений"
        ws.send_json({"type": "stop"})
        got = [ws.receive_json() for _ in range(3)]
    assert got == [{"type": "final", "seq": 1, "text": "Добрый день"}, {"type": "final", "seq": 2, "text": "отчёт готов"}, {"type": "done", "timeout": False}]
    r = client.app_obj.state.redis

    async def left():
        return await r.exists(f"asr:dict:{sid}:out"), await r.zcard("dictation:active"), await r.ttl(f"asr:dict:{sid}:in")
    out, active, ttl = client.portal.call(left)
    assert out == 0 and active == 0 and 0 < ttl <= 60, "результаты удалены, звук истекает за минуту, место освобождено"


def test_dictation_unavailable_disabled_and_limited(client):
    login(client, "alice")
    _asr_alive(client, False)
    with client.websocket_connect(f"{API}/ws/dictation") as ws:
        assert ws.receive_json()["code"] == "asr_unavailable"
    login(client, "root")
    assert client.put(f"{API}/admin/settings/chats", json={"dictation_max_concurrent": 1}).status_code == 200
    _asr_alive(client)
    login(client, "alice")
    with client.websocket_connect(f"{API}/ws/dictation") as first:
        assert first.receive_json()["type"] == "ready"
        login(client, "bob")
        with client.websocket_connect(f"{API}/ws/dictation") as second:
            assert second.receive_json()["code"] == "busy", "одновременных диктовок не больше настройки"
        first.send_json({"type": "cancel"})
    login(client, "root")
    assert client.put(f"{API}/admin/settings/chats", json={"dictation_enabled": False}).status_code == 200
    login(client, "alice")
    with client.websocket_connect(f"{API}/ws/dictation") as ws:
        assert ws.receive_json()["code"] == "disabled"
    client.cookies.clear()
    try:
        with client.websocket_connect(f"{API}/ws/dictation") as ws:
            ws.receive_json()
        raise AssertionError("без входа диктовка недоступна")
    except Exception as exc:  # noqa: BLE001 — закрытие с кодом 4401 до принятия соединения
        assert "4401" in repr(exc) or "WebSocketDisconnect" in type(exc).__name__
